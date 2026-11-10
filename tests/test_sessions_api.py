"""The three integration tests of the S05 exercise (Paso 7) over `httpx.AsyncClient`.

Requirement: Tests de integración de sesiones (ci-pipeline). Asynchronous with the `anyio`
pytest plugin (asyncio backend); settings, provider, moderator and session store are swapped
with `app.dependency_overrides`. `ASGITransport` does not run the lifespan, so each test
builds its own store.
"""

import re

import httpx
import pytest

from app.main import app
from app.sessions import SessionStore
from tests.attachments.factories import PDF_MIME, make_pdf
from tests.conftest import (
    FakeModerator,
    FakeProvider,
    attachment_parts,
    install_overrides,
    make_settings,
    turn_data,
    valid_payload,
)

BASE = "/api/v1/sessions"
PDF_MARKER = "MARCADOR-PDF-UNICO"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _setup(provider: FakeProvider, **settings_kwargs) -> SessionStore:
    settings = make_settings(**settings_kwargs)
    store = SessionStore(
        max_sessions=settings.max_sessions, max_turns=settings.max_conversation_turns
    )
    install_overrides(settings, provider, FakeModerator(result=False), store)
    return store


async def _new_session(http: httpx.AsyncClient) -> str:
    response = await http.post(BASE)
    assert response.status_code == 201
    return response.json()["session_id"]


def _metadata_block(system: str) -> str:
    match = re.search(r"^<project_metadata>\n(.*?)^</project_metadata>", system, re.DOTALL | re.M)
    assert match, "the system prompt has no <project_metadata> block"
    return match.group(1)


@pytest.mark.anyio
async def test_two_chained_turns_update_the_project_metadata():
    """Requirement: Tests de integración / Scenario: Integración de sesiones (test 1),
    Requirement: Metadata del proyecto, Mensajes enviados al LLM / Scenario: Segundo turno.
    """
    turn1 = valid_payload(technologies=["Twilio"])
    turn2 = valid_payload(
        team=[{"role": "engineers", "count": 4, "part_time": False}],
        technologies=["Stripe", "twilio"],
    )
    provider = FakeProvider(structured=[turn1, turn2])
    _setup(provider)
    async with _http() as http:
        session_id = await _new_session(http)
        first = await http.post(f"{BASE}/{session_id}/estimate", data=turn_data())
        second = await http.post(
            f"{BASE}/{session_id}/estimate", data=turn_data(transcript="Add payments, please.")
        )
        info = await http.get(f"{BASE}/{session_id}")

    assert first.status_code == 200 and second.status_code == 200
    one, two = first.json(), second.json()
    assert (one["turn"], two["turn"]) == (1, 2)
    assert one["project_metadata"] == {
        "project_name": "Clinic booking platform",
        "assumed_team_size": 3,
        "mentioned_technologies": ["Twilio"],
        "agreed_scope": "Internal web platform with an agenda and reminders.",
    }
    assert two["project_metadata"]["assumed_team_size"] == 4
    assert two["project_metadata"]["mentioned_technologies"] == ["Twilio", "Stripe"]
    state = info.json()
    assert state["turn_count"] == 2
    assert state["project_metadata"] == two["project_metadata"]

    first_system, first_messages = provider.calls[0]
    second_system, second_messages = provider.calls[1]
    assert len(first_messages) == 1
    assert _metadata_block(first_system).strip() == ""
    assert [m["role"] for m in second_messages] == ["user", "assistant", "user"]
    assert second_messages[1]["content"] == one["text"]
    assert "Add payments, please." in second_messages[2]["content"]
    assert "project_name: Clinic booking platform" in _metadata_block(second_system)


@pytest.mark.anyio
async def test_a_pdf_attachment_changes_the_estimate_and_is_replaced_in_the_history():
    """Requirement: Tests de integración (test 2), Contenido del turno guardado /
    Scenario: Adjunto sustituido en el historial, Extracción local del texto.
    """

    def reacts_to_the_pdf(system: str, messages: list[dict]) -> dict:
        payload = valid_payload()
        if PDF_MARKER in messages[-1]["content"]:
            payload["rows"] = [
                *payload["rows"],
                {
                    "task": "Extra module",
                    "phase": "development",
                    "rate_profile": "dev",
                    "hours": 40,
                },
            ]
        return payload

    provider = FakeProvider(structured=reacts_to_the_pdf)
    _setup(provider)
    pdf = make_pdf([f"Requirement: {PDF_MARKER}", "Payments module"])
    async with _http() as http:
        plain_id = await _new_session(http)
        with_pdf_id = await _new_session(http)
        plain = await http.post(f"{BASE}/{plain_id}/estimate", data=turn_data())
        attached = await http.post(
            f"{BASE}/{with_pdf_id}/estimate",
            data=turn_data(),
            files=attachment_parts(("spec.pdf", pdf, PDF_MIME)),
        )
        follow_up = await http.post(
            f"{BASE}/{with_pdf_id}/estimate", data=turn_data(transcript="And what about QA?")
        )

    assert plain.status_code == attached.status_code == follow_up.status_code == 200
    assert attached.json()["result"]["total_hours"] > plain.json()["result"]["total_hours"]
    sent = provider.calls[1][1][-1]["content"]
    assert re.search(
        rf"--- attachment: spec\.pdf ---\n.*{PDF_MARKER}.*\n</attachments>", sent, re.DOTALL
    )
    assert sent.index("<attachments>") < sent.index("<transcript>")
    stored = provider.calls[2][1][0]["content"]
    assert "--- attachment: spec.pdf ---\n[…]" in stored
    assert PDF_MARKER not in stored
    assert all(PDF_MARKER not in m["content"] for m in provider.calls[2][1])


@pytest.mark.anyio
async def test_eight_turns_never_send_more_than_the_window_plus_the_new_message():
    """Requirement: Tests de integración (test 3), Mensajes enviados al LLM /
    Scenario: Ventana respetada.
    """
    provider = FakeProvider()
    _setup(provider, max_conversation_turns=6)
    async with _http() as http:
        session_id = await _new_session(http)
        for k in range(1, 9):
            response = await http.post(
                f"{BASE}/{session_id}/estimate",
                data=turn_data(transcript=f"MARCADOR-TURNO-{k}: keep the same project."),
            )
            assert response.status_code == 200, response.text
        info = await http.get(f"{BASE}/{session_id}")

    sizes = [len(messages) for _, messages in provider.calls]
    assert sizes == [2 * min(k - 1, 6) + 1 for k in range(1, 9)]
    assert max(sizes) == 13
    roles = [m["role"] for m in provider.calls[7][1]]
    assert roles == ["user", "assistant"] * 6 + ["user"]
    first_marker = "MARCADOR-TURNO-1:"
    assert any(first_marker in m["content"] for m in provider.calls[6][1])  # still in the window
    assert all(first_marker not in m["content"] for m in provider.calls[7][1])  # dropped
    assert info.json()["turn_count"] == 8
    assert info.json()["history_turns"] == 6
