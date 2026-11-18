"""cag-stress-test / PDFs sintéticos deterministas. PDFs are built under tmp_path."""

import io

import pytest
from pypdf import PdfReader

from app.config import Settings
from evals.stress.fixtures import build_pdfs
from evals.stress.scenarios import ATTACHMENT_MARKERS


def text_of(path) -> str:
    reader = PdfReader(io.BytesIO(path.read_bytes()))
    return "\n".join(page.extract_text() for page in reader.pages)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    first = tmp_path_factory.mktemp("pdfs_a")
    second = tmp_path_factory.mktemp("pdfs_b")
    chars = build_pdfs.build_all(first)
    build_pdfs.build_all(second)
    return first, second, chars


def test_pdfs_are_deterministic(built):
    """Scenario: Determinismo (texto extraído idéntico en dos generaciones)."""
    first, second, _ = built
    for size in build_pdfs.SIZES_KB:
        name = build_pdfs.path_for(size, first).name
        assert text_of(first / name) == text_of(second / name), name


def test_pdf_of_20kb_is_in_range_and_has_the_markers(built):
    """Scenario: Tamaño de 20 KB."""
    first, _, _ = built
    text = text_of(build_pdfs.path_for(20, first))
    assert 17_000 <= len(text) <= 23_000
    flat = " ".join(text.split())
    for spellings in ATTACHMENT_MARKERS:
        assert any(s in flat for s in spellings), spellings


@pytest.mark.parametrize("size", build_pdfs.SIZES_KB)
def test_extracted_size_is_within_fifteen_percent(built, size):
    """Requirement: ±15 % del tamaño nominal (extractor de app/attachments)."""
    _, _, chars = built
    assert abs(chars[size] - size * 1000) <= size * 1000 * 0.15


def test_the_100kb_pdf_exceeds_max_attachment_chars(built):
    """Requirement: el de 100 KB supera MAX_ATTACHMENT_CHARS."""
    _, _, chars = built
    assert chars[100] > Settings(_env_file=None, openai_api_key="k").max_attachment_chars


def test_markers_are_at_start_middle_and_end(built):
    """Requirement: marcadores al principio, a la mitad y al final."""
    first, _, _ = built
    flat = " ".join(text_of(build_pdfs.path_for(50, first)).split())
    positions = [
        flat.index(s[0] if s[0] in flat else s[-1]) / len(flat) for s in ATTACHMENT_MARKERS
    ]
    assert positions[0] < 0.1 and 0.3 < positions[1] < 0.7 and positions[2] > 0.9


def test_missing_lists_absent_files_and_build_is_idempotent(tmp_path):
    assert build_pdfs.missing(tmp_path) == list(build_pdfs.SIZES_KB)
    build_pdfs.build_all(tmp_path)
    assert build_pdfs.missing(tmp_path) == []
    assert build_pdfs.build_all(tmp_path) == {}
