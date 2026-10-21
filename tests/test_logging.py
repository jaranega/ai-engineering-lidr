"""Tests for third-party logger levels set by the app lifespan (design decision 23)."""

import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import NOISY_LOGGERS, app


@pytest.fixture
def restore_logging() -> Iterator[None]:
    """Snapshot and restore the levels of the root and noisy loggers (global state)."""
    names = ("", *NOISY_LOGGERS)
    saved = {name: logging.getLogger(name).level for name in names}
    yield
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)


def _start_app(monkeypatch: pytest.MonkeyPatch, log_level: str) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-lifespan")
    monkeypatch.setenv("LOG_LEVEL", log_level)
    with TestClient(app):
        pass


@pytest.mark.usefixtures("restore_logging")
@pytest.mark.parametrize("name", NOISY_LOGGERS)
def test_noisy_loggers_never_below_info_when_app_level_is_debug(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Logging hardening: with LOG_LEVEL=DEBUG, third-party loggers stay at INFO or above.

    The root level is not asserted: `logging.basicConfig` is a no-op when the root already
    has handlers, which pytest installs, so the root is not reconfigured under test.
    """
    for noisy in NOISY_LOGGERS:  # start from a known-noisy state
        logging.getLogger(noisy).setLevel(logging.DEBUG)

    _start_app(monkeypatch, "DEBUG")

    assert logging.getLogger(name).getEffectiveLevel() >= logging.INFO
    assert not logging.getLogger(name).isEnabledFor(logging.DEBUG)


@pytest.mark.usefixtures("restore_logging")
@pytest.mark.parametrize("name", NOISY_LOGGERS)
def test_noisy_loggers_follow_app_level_when_above_info(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Logging hardening: with LOG_LEVEL=ERROR, third-party loggers are set to ERROR."""
    _start_app(monkeypatch, "ERROR")

    assert logging.getLogger(name).getEffectiveLevel() == logging.ERROR
