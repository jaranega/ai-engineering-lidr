"""Project structure required by the exercise (`ci-pipeline` spec)."""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_FILES = [
    "app/__init__.py",
    "app/main.py",
    "app/config.py",
    "app/routers/__init__.py",
    "app/routers/estimations.py",
    "app/services/__init__.py",
    "app/services/llm_service.py",
    "app/context/__init__.py",
    "app/context/examples.py",
    "streamlit_app.py",
    ".env.example",
    ".gitignore",
    "pyproject.toml",
    "README.md",
]


@pytest.mark.parametrize("relative_path", REQUIRED_FILES)
def test_required_file_exists(relative_path):
    """Requirement: Validación de la estructura / Scenarios: fichero obligatorio y app Streamlit."""
    assert (REPO_ROOT / relative_path).is_file(), f"Missing required file: {relative_path}"


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


# Only app/services (and app/config.py, which resolves models offline) may use these.
FORBIDDEN_OUTSIDE_SERVICES = {"litellm", "redis", "openai", "anthropic"}


@pytest.mark.parametrize(
    "relative_path",
    [
        "app/main.py",
        "app/schemas.py",
        "app/routers/__init__.py",
        "app/routers/estimations.py",
        "app/context/__init__.py",
        "app/context/examples.py",
        "streamlit_app.py",
    ],
)
def test_layers_outside_services_do_not_import_llm_or_cache_libraries(relative_path):
    """Design D2: routers, schemas, UI and context never import litellm, redis or LLM SDKs."""
    assert not _imported_roots(REPO_ROOT / relative_path) & FORBIDDEN_OUTSIDE_SERVICES


def test_only_services_and_config_import_litellm_and_redis():
    """Tasks 2.8 / 5.7: `litellm` and `redis` stay inside `app/services` and `app/config.py`."""
    allowed = {"app/config.py"}
    offenders = []
    for path in (REPO_ROOT / "app").rglob("*.py"):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in allowed or relative.startswith("app/services/"):
            continue
        if _imported_roots(path) & FORBIDDEN_OUTSIDE_SERVICES:
            offenders.append(relative)
    assert offenders == []


def test_openai_and_anthropic_sdks_are_not_used_anywhere_in_the_app():
    """Design D1: the SDK adapters were removed; the app never imports the vendor SDKs."""
    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in [*(REPO_ROOT / "app").rglob("*.py"), REPO_ROOT / "streamlit_app.py"]
        if _imported_roots(path) & {"openai", "anthropic"}
    ]
    assert offenders == []


def test_provider_adapters_of_the_sdks_are_gone():
    """Spec REMOVED: Selección de proveedor por configuración (adaptadores de SDK retirados)."""
    from app.services import llm_service

    assert not hasattr(llm_service, "OpenAIProvider")
    assert not hasattr(llm_service, "AnthropicProvider")
    assert not hasattr(llm_service, "build_provider_by_name")
