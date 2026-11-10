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
    "app/prompts/estimation/v3/system.j2",
    "app/prompts/estimation/v3/user.j2",
    "app/prompts/estimation/v3/examples.j2",
    "app/prompts/estimation/v4/system.j2",
    "app/prompts/estimation/v4/user.j2",
    "app/prompts/estimation/v4/examples.j2",
    "app/prompts/estimation/v4/project_metadata.j2",
    "app/routers/sessions.py",
    "app/services/session_service.py",
    "app/sessions/__init__.py",
    "app/sessions/models.py",
    "app/sessions/store.py",
    "app/sessions/metadata.py",
    "app/attachments/__init__.py",
    "app/attachments/extractor.py",
    "app/estimation/__init__.py",
    "app/estimation/schema.py",
    "app/estimation/compute.py",
    "app/estimation/render.py",
    "app/estimation/examples.py",
    "app/guardrails/__init__.py",
    "app/guardrails/input.py",
    "app/services/moderation.py",
    "app/services/cache.py",
    "app/schemas.py",
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
        "app/estimation/__init__.py",
        "app/estimation/schema.py",
        "app/estimation/compute.py",
        "app/estimation/render.py",
        "app/estimation/examples.py",
        "app/guardrails/__init__.py",
        "app/guardrails/input.py",
        "app/routers/sessions.py",
        "app/sessions/__init__.py",
        "app/sessions/models.py",
        "app/sessions/store.py",
        "app/sessions/metadata.py",
        "app/attachments/__init__.py",
        "app/attachments/extractor.py",
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
    """Design D1: the SDK adapters were removed; the app never imports the vendor SDKs.

    The only exception is `app/services/moderation.py`, which builds the OpenAI client with a
    short timeout that `litellm.moderation` accepts as `client` (structured output design D8).
    """
    allowed = {"app/services/moderation.py": {"openai"}}
    offenders = []
    for path in [*(REPO_ROOT / "app").rglob("*.py"), REPO_ROOT / "streamlit_app.py"]:
        relative = path.relative_to(REPO_ROOT).as_posix()
        used = _imported_roots(path) & {"openai", "anthropic"}
        if used - allowed.get(relative, set()):
            offenders.append(relative)
    assert offenders == []


def test_instructor_is_only_imported_by_the_services_layer():
    """Design D6: Instructor stays in `app/services` (the schema never depends on it)."""
    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "app").rglob("*.py")
        if "instructor" in _imported_roots(path)
        and not path.relative_to(REPO_ROOT).as_posix().startswith("app/services/")
    ]
    assert offenders == []


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize(
    "package", ["app/estimation", "app/guardrails"], ids=["estimation", "guardrails"]
)
def test_estimation_and_guardrails_never_import_services_or_prompts(package):
    """Tasks 5.4: `estimation` and `guardrails` have no cycles with `services` / `prompts`."""
    for path in (REPO_ROOT / package).glob("*.py"):
        modules = _imported_modules(path)
        assert not {
            m for m in modules if m.startswith(("app.services", "app.prompts", "app.routers"))
        }, path.name


def test_estimation_package_depends_only_on_domain_context_and_itself():
    """Design D4: layer rules of `app/estimation`."""
    for path in (REPO_ROOT / "app/estimation").glob("*.py"):
        internal = {m for m in _imported_modules(path) if m == "app" or m.startswith("app.")}
        assert all(
            m.startswith(("app.domain", "app.context", "app.estimation")) for m in internal
        ), (path.name, internal)


def test_guardrails_input_imports_nothing_from_the_app():
    """Design D8: `app/guardrails/input.py` is pure stdlib."""
    internal = {
        m
        for m in _imported_modules(REPO_ROOT / "app/guardrails/input.py")
        if m == "app" or m.startswith("app.")
    }
    assert internal == set()


def test_prompts_folder_has_v3_and_v4_with_their_templates():
    """prompt-templates / Scenario: Estructura de las versiones (v3 y v4)."""
    base = REPO_ROOT / "app/prompts/estimation"
    folders = {p.name for p in base.iterdir() if p.is_dir() and p.name != "__pycache__"}
    assert folders == {"v3", "v4"}
    assert {p.name for p in (base / "v3").iterdir()} == {"system.j2", "user.j2", "examples.j2"}
    assert {p.name for p in (base / "v4").iterdir()} == {
        "system.j2",
        "user.j2",
        "examples.j2",
        "project_metadata.j2",
    }


@pytest.mark.parametrize("package", ["app/sessions", "app/attachments"])
def test_sessions_and_attachments_do_not_import_services_llm_or_instructor(package):
    """ci-pipeline / Scenario: Capas sin dependencias del LLM."""
    for path in (REPO_ROOT / package).glob("*.py"):
        modules = _imported_modules(path)
        assert not {m for m in modules if m.startswith(("app.services", "app.prompts"))}, path.name
        assert not _imported_roots(path) & {"litellm", "instructor", "fastapi"}, path.name


def test_attachments_package_never_imports_fastapi_nor_services():
    """attachments / Requirement: Tipos admitidos (la extracción no importa FastAPI)."""
    for path in (REPO_ROOT / "app/attachments").glob("*.py"):
        assert "fastapi" not in _imported_roots(path)
        assert not {m for m in _imported_modules(path) if m.startswith("app.services")}


def test_session_prompt_versions_are_a_single_list_matching_the_folders():
    """prompt-templates / Scenario: Versiones publicadas."""
    from app.prompts.loader import PUBLISHED_PROMPT_VERSIONS

    base = REPO_ROOT / "app/prompts/estimation"
    folders = {p.name for p in base.iterdir() if p.is_dir() and p.name != "__pycache__"}
    assert folders == set(PUBLISHED_PROMPT_VERSIONS)


def test_retired_prompt_versions_and_thinking_are_gone():
    """Specs REMOVED: Versión v2 y Razonamiento extendido."""
    from app import schemas
    from app.services.llm_service import GenerationOptions, LLMCallOptions

    base = REPO_ROOT / "app/prompts/estimation"
    assert not (base / "v1").exists() and not (base / "v2").exists()
    assert "thinking_budget" not in GenerationOptions.__dataclass_fields__
    assert "thinking_budget" not in LLMCallOptions.__dataclass_fields__
    assert "thinking_budget" not in schemas.EstimationRequest.model_fields


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
