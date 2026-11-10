"""Project structure required by the exercise (`ci-pipeline` spec)."""

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import CONFIG_ENV_VARS

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
    "app/domain.py",
    "app/prompts/__init__.py",
    "app/prompts/loader.py",
    "app/prompts/language.py",
    "app/prompts/estimation/v1/system.j2",
    "app/prompts/estimation/v1/user.j2",
    "app/prompts/estimation/v1/examples.j2",
    "app/prompts/estimation/v2/system.j2",
    "app/prompts/estimation/v2/user.j2",
    "app/prompts/estimation/v2/examples.j2",
    "streamlit_app.py",
    ".env.example",
    ".gitignore",
    "pyproject.toml",
    "README.md",
    "Dockerfile",
    ".dockerignore",
    "docker-compose.yml",
]


@pytest.mark.parametrize("relative_path", REQUIRED_FILES)
def test_required_file_exists(relative_path):
    """Requirement: Validación de la estructura / Scenarios: fichero, Streamlit, Docker."""
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
        "app/domain.py",
        "app/prompts/__init__.py",
        "app/prompts/loader.py",
        "app/prompts/language.py",
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


def test_streamlit_app_imports_nothing_from_the_service_nor_llm_libraries():
    """estimation-form-ui / Scenario: Sin dependencias del servicio."""
    roots = _imported_roots(REPO_ROOT / "streamlit_app.py")
    assert not roots & {"app", "litellm", "redis", "openai", "anthropic"}


def test_dockerignore_is_an_allowlist():
    """container-runtime / Requirement: Contexto de build en lista blanca."""
    lines = [
        line.strip()
        for line in (REPO_ROOT / ".dockerignore").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert lines[0] == "*"
    allowed = {line.removeprefix("!") for line in lines if line.startswith("!")}
    assert allowed == {"pyproject.toml", "uv.lock", "app/"}
    assert "**/__pycache__" in lines


def test_dockerfile_follows_the_image_requirements():
    """container-runtime / Requirement: Imagen Docker de la API (estática, sin construir)."""
    text = (REPO_ROOT / "Dockerfile").read_text()
    assert "--locked" in text and "--no-default-groups" in text
    assert "USER " in text and "USER root" not in text
    assert "EXPOSE 8000" in text
    assert "HEALTHCHECK" in text and "/health" in text
    assert "uvicorn" in text and "app.main:app" in text
    uv_images = [line for line in text.splitlines() if "astral-sh/uv:" in line]
    assert uv_images and all(":latest" not in line for line in uv_images)
    assert "streamlit" not in text
    assert ".env" not in text.replace(".env.example", "")


def test_compose_has_api_and_redis_with_local_ports_and_no_streamlit():
    """container-runtime / Requirement: docker-compose con Redis (estático, sin Docker)."""
    text = (REPO_ROOT / "docker-compose.yml").read_text()
    assert "streamlit" not in text.lower()
    assert "env_file" in text and "redis://redis:6379/0" in text
    assert "service_healthy" in text and "healthcheck" in text
    assert "redis:7.4-alpine" in text
    assert '"--save", ""' in text and '"--appendonly", "no"' in text
    assert '"127.0.0.1:8000:8000"' in text
    assert '"127.0.0.1:6379:6379"' in text
    assert text.count("ports:") == 2  # no other published port


def test_domain_module_imports_no_other_app_module():
    """Design D2 / tasks 1.3: `app.domain` depends only on pydantic and the standard library."""
    assert not {root for root in _imported_roots(REPO_ROOT / "app/domain.py") if root == "app"}
    code = (
        "import sys, app.domain; "
        "print(sorted(m for m in sys.modules if m == 'app' or m.startswith('app.')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env={k: v for k, v in os.environ.items() if k not in CONFIG_ENV_VARS},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "['app', 'app.domain']"


def test_retired_chat_and_streaming_artifacts_are_gone():
    """Specs REMOVED: SSE, chat y contexto CAG (S04 design D10)."""
    from app import schemas
    from app.services import llm_service

    for name in (
        "MessageText",
        "ConversationMessage",
        "StreamEstimationRequest",
        "ContextResponse",
    ):
        assert not hasattr(schemas, name)
    for name in ("two_phase", "stream_chat", "EstimationStream", "build_estimation_prompt"):
        assert not hasattr(llm_service, name)
    assert not hasattr(llm_service.EstimationService, "stream_chat")
