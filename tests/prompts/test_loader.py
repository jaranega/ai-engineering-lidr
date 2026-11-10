"""Tests of `app.prompts.loader` (capability prompt-templates). Offline and deterministic."""

import hashlib
import itertools
import re
from pathlib import Path

import jinja2
import pytest
from jinja2 import FileSystemLoader, StrictUndefined, UndefinedError
from structlog.testing import capture_logs

from app.domain import DetailLevel, OutputFormat, ProjectType
from app.prompts import loader
from app.prompts.loader import (
    DEFAULT_PROMPT_VERSION,
    PROMPT_VERSIONS,
    PUBLISHED_PROMPT_VERSIONS,
    SESSION_PROMPT_VERSIONS,
    TEMPLATES_DIR,
    UnknownPromptVersionError,
    render_estimation_prompt,
)
from tests.conftest import make_input

REFERENCES = [
    {"name": "Proyecto-Ref-A", "summary": "Resumen A", "actual_hours": 400, "duration_weeks": 8},
    {"name": "Proyecto-Ref-B", "summary": "Resumen B"},
]
LANGUAGE_RULE_OPENING = (
    "Write every text field of the estimate (title, summary, tasks, roles, assumptions and "
    "narrative) in the same language as the project description. If the project description "
    "is in English, write them entirely in English."
)
DETECTED_ENGLISH = (
    'The project description is in English: set `language` to "en" and write all text fields '
    "(title, summary, tasks, roles, assumptions, narrative) in English."
)
# The English rule closes the rules; only the detected-language line follows it (D14).
LANGUAGE_RULE_CLOSING = (
    "if the project description is in English, write them entirely in English.\n\n"
    + DETECTED_ENGLISH
)
ALL_COMBINATIONS = list(
    itertools.product(
        PROMPT_VERSIONS, ProjectType, DetailLevel, OutputFormat, (0, 3), (False, True)
    )
)


def render(version="v3", *, num_examples=3, **overrides):
    return render_estimation_prompt(make_input(**overrides), version, num_examples=num_examples)


def example_block(system: str) -> str:
    return system.split("\n<examples>\n", 1)[1].split("\n</examples>", 1)[0]


# --- Requirement: Templates versionados de estimación ---


def test_version_folders_match_the_single_list_of_versions():
    """Requirement: Templates versionados / Scenario: Estructura de una versión."""
    folders = {path.name for path in TEMPLATES_DIR.iterdir() if path.is_dir()}
    folders.discard("__pycache__")
    assert folders == set(PUBLISHED_PROMPT_VERSIONS) == {"v3", "v4"}
    assert PROMPT_VERSIONS == ("v3",)
    assert SESSION_PROMPT_VERSIONS == ("v4",)
    base = {"system.j2", "user.j2", "examples.j2"}
    for version in PUBLISHED_PROMPT_VERSIONS:
        files = {path.name for path in (TEMPLATES_DIR / version).iterdir()}
        assert files == (base | {"project_metadata.j2"} if version == "v4" else base)


def test_templates_never_reference_another_version_or_leave_their_folder():
    """Requirement: Templates versionados (cada versión es una copia independiente)."""
    pattern = re.compile(r"{%-?\s*(?:include|extends|import|from)\s+[\"']([^\"']+)[\"']")
    for version in PUBLISHED_PROMPT_VERSIONS:
        for path in (TEMPLATES_DIR / version).glob("*.j2"):
            for target in pattern.findall(path.read_text(encoding="utf-8")):
                assert "/" not in target
                assert ".." not in target
                assert (TEMPLATES_DIR / version / target).is_file()


def test_rendering_v3_only_loads_templates_from_the_v3_folder(monkeypatch):
    """Requirement: Templates versionados / Scenario: Versiones aisladas."""
    loaded: list[str] = []
    original = FileSystemLoader.get_source

    def spy(self, environment, template):
        source = original(self, environment, template)
        loaded.append(source[1])
        return source

    monkeypatch.setattr(FileSystemLoader, "get_source", spy)
    render("v3")
    assert {Path(name).name for name in loaded} == {"system.j2", "user.j2", "examples.j2"}
    assert all(Path(name).parent == TEMPLATES_DIR / "v3" for name in loaded)


# --- Requirement: Loader de prompts ---


def test_default_render_is_v3_and_stripped():
    """Requirement: Loader de prompts / Scenario: Render por defecto."""
    request = make_input()
    assert DEFAULT_PROMPT_VERSION == "v3"
    system, user = render_estimation_prompt(request)
    assert (system, user) == render_estimation_prompt(request, "v3")
    assert system == system.strip()
    assert user == user.strip()


def test_the_default_prompt_starts_with_the_language_rule():
    """Requirement: Loader de prompts (la regla de idioma abre el system)."""
    assert render_estimation_prompt(make_input())[0].startswith(LANGUAGE_RULE_OPENING)


@pytest.mark.parametrize("version", PROMPT_VERSIONS)
def test_environment_configuration(version):
    """Requirement: Loader de prompts (Environment: StrictUndefined, trim, lstrip)."""
    env = loader._environment(version)
    assert env.undefined is StrictUndefined
    assert env.trim_blocks is True
    assert env.lstrip_blocks is True
    assert env.autoescape is False
    assert isinstance(env.loader, FileSystemLoader)
    assert env.loader.searchpath == [str(TEMPLATES_DIR / version)]
    assert "amount" not in env.filters  # no amounts in the prompt
    assert loader._environment(version) is env  # one cached environment per version


SYSTEM_KEYS = [
    "project_type",
    "detail_level",
    "output_format",
    "examples",
    "phases",
    "response_language",
]


@pytest.mark.parametrize("key", SYSTEM_KEYS)
def test_system_template_fails_when_the_context_misses_a_variable(key):
    """Requirement: Loader de prompts / Scenario: Variable no definida (system)."""
    request = make_input(output_format="phases_table")
    context = loader._render_context(request, list(loader.ESTIMATION_EXAMPLES))
    del context[key]
    with pytest.raises(UndefinedError):
        loader._environment("v3").get_template("system.j2").render(context)


@pytest.mark.parametrize("key", ["description", "project_type", "reference_projects"])
def test_user_template_fails_when_the_context_misses_a_variable(key):
    """Requirement: Loader de prompts / Scenario: Variable no definida (user)."""
    context = loader._render_context(make_input(), [])
    del context[key]
    with pytest.raises(UndefinedError):
        loader._environment("v3").get_template("user.j2").render(context)


def test_an_undefined_variable_in_a_template_is_an_error_not_an_empty_text():
    """Requirement: Loader de prompts / Scenario: Variable no definida."""
    env = loader._environment("v3")
    with pytest.raises(jinja2.UndefinedError):
        env.from_string("Hola {{ missing }}").render()


@pytest.mark.parametrize(
    "version", ["v1", "v2", "v9", "../v3", "V3", "", "v3/", "v3\\..", "estimation/v3"]
)
def test_unknown_version_is_a_domain_error(version):
    """Requirement: Loader de prompts / Scenario: Versión desconocida."""
    with pytest.raises(UnknownPromptVersionError):
        render_estimation_prompt(make_input(), version)


def test_unknown_version_is_rejected_before_touching_the_filesystem(monkeypatch):
    """Requirement: Loader de prompts (error de dominio antes de acceder a los ficheros)."""

    def forbidden(version):
        raise AssertionError("filesystem or environment accessed")

    monkeypatch.setattr(loader, "_environment", forbidden)
    with pytest.raises(UnknownPromptVersionError):
        render_estimation_prompt(make_input(), "../v3")


def test_render_is_deterministic():
    """Requirement: Loader de prompts / Scenario: Determinismo."""
    first = render("v3", reference_projects=REFERENCES)
    second = render("v3", reference_projects=REFERENCES)
    assert first == second
    loader._environment.cache_clear()  # also across freshly compiled environments
    assert render("v3", reference_projects=REFERENCES) == first


@pytest.mark.parametrize(
    ("version", "project_type", "detail_level", "output_format", "num_examples", "refs"),
    ALL_COMBINATIONS,
)
def test_every_combination_renders_without_python_enum_representations(
    version, project_type, detail_level, output_format, num_examples, refs
):
    """Requirement: Loader de prompts / Scenario: Todas las combinaciones renderizan."""
    system, user = render(
        version,
        num_examples=num_examples,
        project_type=project_type.value,
        detail_level=detail_level.value,
        output_format=output_format.value,
        reference_projects=REFERENCES if refs else None,
    )
    for text in (system, user):
        assert text
        for marker in ("ProjectType.", "DetailLevel.", "OutputFormat."):
            assert marker not in text


def test_there_are_144_combinations():
    """Requirement: Loader de prompts (cobertura de la matriz completa)."""
    assert len(ALL_COMBINATIONS) == 144


@pytest.mark.parametrize(
    ("version", "project_type", "detail_level", "output_format", "num_examples", "refs"),
    ALL_COMBINATIONS,
)
def test_language_rule_opens_and_closes_every_system_prompt(
    version, project_type, detail_level, output_format, num_examples, refs
):
    """Requirement: System prompt con contexto inyectado / Scenario: Idioma de la estimación."""
    system, _ = render(
        version,
        num_examples=num_examples,
        project_type=project_type.value,
        detail_level=detail_level.value,
        output_format=output_format.value,
        reference_projects=REFERENCES if refs else None,
    )
    assert system.startswith(LANGUAGE_RULE_OPENING)
    assert system.endswith(LANGUAGE_RULE_CLOSING)


# --- Requirement: Mensaje del usuario ---


def test_user_message_has_the_type_the_references_and_the_description_last():
    """Requirement: Mensaje del usuario / Scenario: Proyectos de referencia."""
    system, user = render("v3", reference_projects=REFERENCES)
    assert user.startswith("<project_type>web_saas</project_type>")
    assert user.count("<reference_project>") == 2
    assert "<reference_projects>" in user
    assert user.index("<reference_projects>") < user.index("<project_description>")
    assert user.endswith("</project_description>")
    assert "<actual_hours>400</actual_hours>" in user
    assert "<duration_weeks>8</duration_weeks>" in user
    assert user.count("<actual_hours>") == 1  # optional fields are omitted when absent
    assert user.count("<duration_weeks>") == 1
    for project in REFERENCES:
        assert project["name"] not in system
        assert project["summary"] not in system


@pytest.mark.parametrize("references", [None, []], ids=["null", "empty-list"])
def test_user_message_without_references_has_no_block(references):
    """Requirement: Mensaje del usuario / Scenario: Sin proyectos de referencia."""
    _, user = render("v3", reference_projects=references)
    assert "<reference_projects>" not in user


def test_description_is_not_escaped_or_modified_in_the_user_message():
    """Requirement: Mensaje del usuario / Scenario: Descripción literal."""
    text = 'Quotes "x" & <b>tags</b> with {{ braces }} and {% raw %} jinja-looking text'
    system, user = render("v3", description=text)
    assert f"<project_description>\n{text}\n</project_description>" in user
    assert text not in system


@pytest.mark.parametrize("version", PROMPT_VERSIONS)
def test_neither_description_nor_references_leak_into_the_system_prompt(version):
    """Requirement: Mensaje del usuario (ni descripción ni referencias en el system)."""
    system, _ = render(
        version,
        description="DESCRIPCION-UNICA-XYZ para una web",
        reference_projects=[{"name": "REF-NOMBRE-UNICO", "summary": "REF-RESUMEN-UNICO"}],
    )
    for marker in ("DESCRIPCION-UNICA-XYZ", "REF-NOMBRE-UNICO", "REF-RESUMEN-UNICO"):
        assert marker not in system


# --- Requirement: Evento de render ---


def sha12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def test_prompt_rendered_event_has_version_format_and_twelve_hex_hashes():
    """Requirement: Evento de render / Scenario: Evento registrado."""
    with capture_logs() as logs:
        system, user = render("v3", output_format="phases_table", reference_projects=REFERENCES)
    events = [e for e in logs if e["event"] == "prompt_rendered"]
    assert len(events) == 1
    event = events[0]
    assert event["prompt"] == "estimation"
    assert event["version"] == "v3"
    assert event["output_format"] == "phases_table"
    assert event["detail_level"] == "medium"
    assert event["project_type"] == "web_saas"
    assert event["num_examples"] == 3
    assert event["reference_projects"] == 2
    assert event["response_language"] == "en"
    assert re.fullmatch(r"[0-9a-f]{12}", event["system_sha256"])
    assert re.fullmatch(r"[0-9a-f]{12}", event["user_sha256"])
    assert event["system_sha256"] == sha12(system)
    assert event["user_sha256"] == sha12(user)
    assert (event["system_chars"], event["user_chars"]) == (len(system), len(user))
    assert event["log_level"] == "info"


def test_prompt_rendered_hashes_are_stable_and_change_with_the_content():
    """Requirement: Evento de render / Scenario: Hash estable."""
    with capture_logs() as logs:
        render("v3")
        render("v3")
        render(
            "v3",
            description="Another booking platform for dental clinics, with an admin panel.",
        )
    first, second, third = [e for e in logs if e["event"] == "prompt_rendered"]
    assert first["system_sha256"] == second["system_sha256"]
    assert first["user_sha256"] == second["user_sha256"]
    assert third["user_sha256"] != first["user_sha256"]
    assert third["system_sha256"] == first["system_sha256"]


def test_prompt_rendered_event_never_contains_the_content():
    """Requirement: Evento de render / Scenario: Sin contenido."""
    with capture_logs() as logs:
        system, _ = render(
            "v3",
            description="DESCRIPCION-SECRETA-123",
            reference_projects=[{"name": "REFERENCIA-SECRETA-456", "summary": "RESUMEN-SECRETO"}],
        )
    dumped = repr(logs)
    assert "prompt_rendered" in dumped
    for secret in ("DESCRIPCION-SECRETA-123", "REFERENCIA-SECRETA-456", "RESUMEN-SECRETO"):
        assert secret not in dumped
    assert system[:80] not in dumped


def test_invalid_num_examples_is_rejected():
    """Requirement: Loader de prompts (num_examples fuera de rango es un error)."""
    with pytest.raises(ValueError):
        render("v3", num_examples=4)
