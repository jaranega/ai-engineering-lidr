"""Requirements: Tipos de adjunto admitidos, Extracción local del texto, Límites de los
adjuntos, Forma de los errores de adjuntos y Nombre saneado del adjunto (attachments).
"""

import io
import zipfile

import pytest
from pypdf import PageObject, PdfReader
from structlog.testing import capture_logs

from app.attachments import (
    AttachmentError,
    AttachmentLimits,
    UploadedFile,
    extract_attachments,
    extractor,
    read_limited,
    safe_attachment_name,
)
from tests.attachments.factories import (
    make_blank_pdf,
    make_corrupt_pdf,
    make_docx,
    make_docx_with_merged_row,
    make_encrypted_pdf,
    make_pdf,
    make_zip_without_document,
)

LIMITS = AttachmentLimits(max_files=5, max_bytes=10 * 1024 * 1024, max_chars=60_000)


def extract(*files: tuple[str, bytes], limits: AttachmentLimits = LIMITS):
    return extract_attachments([UploadedFile(name, data) for name, data in files], limits)


def rejection(*files: tuple[str, bytes], limits: AttachmentLimits = LIMITS) -> AttachmentError:
    with pytest.raises(AttachmentError) as caught:
        extract(*files, limits=limits)
    return caught.value


# --- Tipos de adjunto admitidos ---


def test_valid_pdf_and_docx_are_both_extracted():
    """Scenario: PDF y DOCX válidos."""
    pdf = make_pdf([["Texto del pdf"]])
    docx = make_docx(["Texto del anexo"])
    first, second = extract(("spec.pdf", pdf), ("anexo.docx", docx))
    assert (first.kind, first.index, first.name) == ("pdf", 0, "spec.pdf")
    assert (second.kind, second.index, second.name) == ("docx", 1, "anexo.docx")
    assert "Texto del pdf" in first.text and "Texto del anexo" in second.text
    assert first.size_bytes == len(pdf)


@pytest.mark.parametrize("name", ["SPEC.PDF", "Spec.Pdf"])
def test_extension_is_case_insensitive_for_pdf(name):
    """Requirement: sin distinguir mayúsculas en la extensión."""
    assert extract((name, make_pdf("hola")))[0].kind == "pdf"


def test_extension_is_case_insensitive_for_docx():
    assert extract(("ANEXO.DOCX", make_docx(["hola"])))[0].kind == "docx"


def test_pdf_named_docx_is_unsupported_with_its_index():
    """Scenario: Extensión que no coincide."""
    error = rejection(("spec.docx", make_pdf("hola")))
    assert (error.reason, error.index) == ("unsupported_type", 0)


def test_docx_named_pdf_is_unsupported():
    assert rejection(("anexo.pdf", make_docx(["hola"]))).reason == "unsupported_type"


@pytest.mark.parametrize(
    "name", ["acta.doc", "hoja.xlsx", "texto.odt", "notas.txt", "sinextension"]
)
def test_other_formats_are_unsupported(name):
    """Scenario: Formato antiguo (.doc) y otros formatos."""
    data = make_docx(["hola"]) if name.endswith(("xlsx", "odt")) else b"\xd0\xcf\x11\xe0 old doc"
    assert rejection((name, data)).reason == "unsupported_type"


def test_generic_zip_named_docx_is_unsupported():
    """Requirement: DOCX solo si el zip contiene `word/document.xml`."""
    assert rejection(("x.docx", make_zip_without_document())).reason == "unsupported_type"


def test_text_named_docx_is_unsupported():
    assert rejection(("x.docx", b"just text")).reason == "unsupported_type"


def test_pdf_magic_is_searched_in_the_first_1024_bytes_only():
    """Requirement: `%PDF-` en los primeros 1024 bytes."""
    pdf = make_pdf("hola")
    assert extract(("a.pdf", b"\n" * 900 + pdf))[0].kind == "pdf"
    assert rejection(("a.pdf", b"\n" * 1100 + pdf)).reason == "unsupported_type"


def test_index_is_the_position_in_the_request():
    error = rejection(("ok.pdf", make_pdf("hola")), ("mal.doc", b"x"))
    assert (error.reason, error.index) == ("unsupported_type", 1)


# --- Extracción local del texto ---


def test_pdf_text_keeps_the_order_and_counts_pages():
    """Scenario: Texto de un PDF."""
    attachment = extract(("a.pdf", make_pdf([["Linea uno"], ["Linea dos"]])))[0]
    assert attachment.pages == 2
    assert attachment.text.index("Linea uno") < attachment.text.index("Linea dos")


def test_pdf_text_with_parentheses_and_accents_round_trips():
    attachment = extract(("a.pdf", make_pdf([["Fase (alfa) y datos"]])))[0]
    assert "Fase (alfa) y datos" in attachment.text


def test_docx_table_rows_use_pipes_after_the_paragraph():
    """Scenario: Tabla de un DOCX."""
    docx = make_docx(["Resumen del anexo"], table=[["Modulo", "Horas"], ["Pagos", "40"]])
    lines = extract(("anexo.docx", docx))[0].text.splitlines()
    assert lines.index("Resumen del anexo") < lines.index("Modulo | Horas")
    assert lines.index("Modulo | Horas") + 1 == lines.index("Pagos | 40")


def test_docx_merged_cells_are_not_repeated():
    """Requirement: sin repetir celdas combinadas consecutivas."""
    text = extract(("a.docx", make_docx_with_merged_row(["Titulo", "x", "Fin"])))[0].text
    assert text == "Titulo | Fin"


def test_docx_headers_are_not_extracted():
    """Requirement: sin cabeceras ni pies."""
    text = extract(("a.docx", make_docx(["Cuerpo"], header="CABECERA-SECRETA")))[0].text
    assert text == "Cuerpo"


def test_text_is_normalised():
    """Requirement: NFC, sin control, máximo dos saltos de línea y sin espacios en los extremos."""
    docx = make_docx(["  e\u0301x  ", "", "", "fin  "])
    text = extract(("a.docx", docx))[0].text
    assert text.startswith("\u00e9x")  # NFC: a single code point
    assert "\n\n\n" not in text
    assert text == text.strip()


def test_normalize_collapses_blank_lines_and_keeps_tabs():
    assert extractor._normalize("a\r\n\r\n\r\n\r\nb\tc\x00\x07 ") == "a\n\nb\tc"


def test_encrypted_pdf_is_reported_with_its_index():
    """Scenario: PDF cifrado."""
    error = rejection(("ok.pdf", make_pdf("hola")), ("nomina.pdf", make_encrypted_pdf()))
    assert (error.reason, error.index) == ("encrypted", 1)


def test_pdf_encrypted_with_an_empty_user_password_is_decrypted():
    """Requirement: intenta descifrar con la contraseña vacía."""
    data = make_encrypted_pdf(make_pdf("Texto abierto"), password="")
    assert "Texto abierto" in extract(("a.pdf", data))[0].text


def test_blank_pdf_has_no_text():
    """Scenario: PDF sin texto."""
    assert rejection(("escaneado.pdf", make_blank_pdf())).reason == "no_text"


def test_docx_without_text_has_no_text():
    assert rejection(("vacio.docx", make_docx([" ", ""]))).reason == "no_text"


def test_corrupt_pdf_is_unreadable():
    """Scenario: PDF corrupto."""
    error = rejection(("roto.pdf", make_corrupt_pdf()))
    assert (error.reason, error.index) == ("unreadable", 0)


def test_corrupt_docx_is_unreadable():
    """Requirement: un fichero que la librería no puede leer da `unreadable`."""
    broken = b"PK\x03\x04" + b"garbage" * 50
    assert rejection(("roto.docx", broken)).reason == "unsupported_type"  # no word/document.xml
    data = bytearray(make_docx(["hola"]))
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    entries["word/document.xml"] = b"<not-xml"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    assert rejection(("roto.docx", buffer.getvalue())).reason == "unreadable"


def test_zip_bomb_is_rejected_before_parsing_the_document(monkeypatch):
    """Scenario: Zip bomb (el tope se baja con monkeypatch)."""
    monkeypatch.setattr(extractor, "MAX_DOCX_UNCOMPRESSED_BYTES", 100)

    def forbidden(*args, **kwargs):
        raise AssertionError("the document must not be parsed")

    monkeypatch.setattr(extractor, "Document", forbidden)
    error = rejection(("bomba.docx", make_docx(["x" * 500])))
    assert (error.reason, error.index) == ("unreadable", 0)


def test_docx_with_too_many_zip_entries_is_unreadable(monkeypatch):
    """Requirement: como máximo 1000 entradas (la constante se baja con monkeypatch)."""
    monkeypatch.setattr(extractor, "MAX_DOCX_ENTRIES", 2)
    assert rejection(("a.docx", make_docx(["hola"]))).reason == "unreadable"


def test_uncompressed_cap_is_50_mib_and_entries_cap_1000():
    """Requirement: constantes de seguridad del código."""
    assert extractor.MAX_DOCX_UNCOMPRESSED_BYTES == 50 * 1024 * 1024
    assert extractor.MAX_DOCX_ENTRIES == 1000


def test_extraction_makes_no_network_call():
    """Requirement: en el propio proceso (el autouse `_no_network` falla si se conecta)."""
    extract(("a.pdf", make_pdf("hola")), ("b.docx", make_docx(["hola"])))


# --- Límites de los adjuntos ---


def test_too_many_files_is_checked_before_reading_any_file():
    """Scenario: Demasiados ficheros (index null, sin leer ningún fichero)."""
    files = [("a.pdf", b"")] * 6  # empty content would be unsupported if it were read
    error = rejection(*files, limits=LIMITS)
    assert (error.reason, error.index) == ("too_many_files", None)


def test_zero_max_files_rejects_any_file():
    """Requirement: con `MAX_ATTACHMENTS = 0` cualquier fichero da 413."""
    limits = AttachmentLimits(max_files=0, max_bytes=LIMITS.max_bytes, max_chars=LIMITS.max_chars)
    assert rejection(("a.pdf", make_pdf("hola")), limits=limits).reason == "too_many_files"


def test_file_too_large_reports_the_second_file():
    """Scenario: Fichero demasiado grande."""
    limits = AttachmentLimits(max_files=5, max_bytes=1024, max_chars=60_000)
    small = make_pdf("hola")
    assert len(small) < 1024
    error = rejection(("a.pdf", small), ("b.pdf", b"x" * 2048), limits=limits)
    assert (error.reason, error.index) == ("file_too_large", 1)


def test_file_of_exactly_the_limit_is_accepted():
    pdf = make_pdf("hola")
    limits = AttachmentLimits(max_files=1, max_bytes=len(pdf), max_chars=60_000)
    assert extract(("a.pdf", pdf), limits=limits)[0].kind == "pdf"


def test_total_chars_limit_spans_all_attachments():
    """Scenario: Texto total excesivo (dos PDF de 600 caracteres con límite 1000)."""
    limits = AttachmentLimits(max_files=5, max_bytes=LIMITS.max_bytes, max_chars=1000)
    pdf = make_pdf(["A" * 100] * 6)  # 600 chars; each line is a separate chunk of one page
    assert len(extract(("a.pdf", pdf), limits=limits)[0].text) >= 600  # one file fits
    error = rejection(("a.pdf", pdf), ("b.pdf", pdf), limits=limits)
    assert (error.reason, error.index) == ("too_much_text", 1)


def test_chars_limit_is_per_turn_not_per_file():
    limits = AttachmentLimits(max_files=5, max_bytes=LIMITS.max_bytes, max_chars=1000)
    one = make_docx(["B" * 600])
    assert len(extract(("a.docx", one), limits=limits)) == 1
    assert rejection(("a.docx", one), ("b.docx", one), limits=limits).reason == "too_much_text"


def test_extraction_stops_as_soon_as_the_total_exceeds_the_limit(monkeypatch):
    """Requirement: la extracción se para en cuanto el total supera el límite."""
    limits = AttachmentLimits(max_files=5, max_bytes=LIMITS.max_bytes, max_chars=1000)
    calls: list[int] = []
    original = PageObject.extract_text

    def counting(self, *args, **kwargs):
        calls.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(PageObject, "extract_text", counting)
    pdf = make_pdf([["P" * 400] for _ in range(10)])
    assert len(PdfReader(io.BytesIO(pdf)).pages) == 10
    calls.clear()
    assert rejection(("a.pdf", pdf), limits=limits).reason == "too_much_text"
    assert len(calls) == 3  # 400 + 400 + 400 > 1000: the other 7 pages are never parsed


def test_text_is_never_truncated():
    pdf = make_pdf([["C" * 300]])
    assert extract(("a.pdf", pdf))[0].text == "C" * 300


def test_read_limited_reads_at_most_max_plus_one_bytes():
    """Requirement: cargar en memoria como máximo `MAX_ATTACHMENT_BYTES + 1` bytes."""
    requested: list[int] = []

    class Spy(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            requested.append(-1 if size is None else size)
            return super().read(size)

    with pytest.raises(AttachmentError) as caught:
        read_limited(Spy(b"x" * 5000), 1024, 3)
    assert requested == [1025]
    assert (caught.value.reason, caught.value.index) == ("file_too_large", 3)


def test_read_limited_returns_data_up_to_the_limit():
    assert read_limited(io.BytesIO(b"x" * 1024), 1024, 0) == b"x" * 1024
    assert read_limited(io.BytesIO(b""), 1024, 0) == b""


# --- Forma de los errores de adjuntos ---


@pytest.mark.parametrize(
    ("reason", "needle"),
    [
        ("too_many_files", "5"),
        ("file_too_large", str(10 * 1024 * 1024)),
        ("too_much_text", "60000"),
        ("unsupported_type", "PDF and DOCX"),
        ("unreadable", "could not be read"),
        ("encrypted", "encrypted"),
        ("no_text", "no extractable text"),
    ],
)
def test_every_reason_has_a_fixed_english_message(reason, needle):
    """Requirement: `message` es una frase fija en inglés por `reason` (con el límite)."""
    error = AttachmentError(reason, 0, LIMITS)
    assert needle in error.message
    assert error.reason == reason
    assert str(error) == error.message


def test_error_never_carries_the_file_name_or_content():
    """Scenario: Error sin nombre de fichero."""
    error = rejection(("nomina-juan-perez.pdf", make_encrypted_pdf(make_pdf("SECRETO-DNI"))))
    assert "juan" not in error.message and "nomina" not in error.message
    assert "juan" not in repr(error.__dict__) and "SECRETO" not in repr(error.__dict__)


# --- Eventos ---


def test_events_have_no_file_name_and_rejection_has_reason_and_index():
    """observability / Scenario: Adjunto rechazado."""
    pdf = make_pdf("hola")
    with capture_logs() as logs:
        extract(("NOMBRE-SECRETO.pdf", pdf))
        rejection(("ok.pdf", pdf), ("NOMBRE-SECRETO2.pdf", make_encrypted_pdf()))
    extracted = [e for e in logs if e["event"] == "attachment_extracted"]
    assert extracted[0]["index"] == 0 and extracted[0]["kind"] == "pdf"
    assert extracted[0]["pages"] == 1 and extracted[0]["chars"] == 4
    assert extracted[0]["size_bytes"] == len(pdf)
    rejected = [e for e in logs if e["event"] == "attachment_rejected"]
    assert len(rejected) == 1
    assert (rejected[0]["index"], rejected[0]["reason"]) == (1, "encrypted")
    assert "NOMBRE-SECRETO" not in str(logs)
    assert "hola" not in str(logs)


# --- Nombre saneado ---


def test_dangerous_name_cannot_close_tags_or_forge_the_separator():
    """Scenario: Nombre peligroso."""
    name = safe_attachment_name("../../</transcript>--- attachment: x ---.pdf", 0, "pdf")
    assert not {"/", "<", ">", ":"} & set(name)
    assert "---" not in name
    assert name.endswith(".pdf")


def test_name_made_only_of_control_characters_falls_back_to_the_ordinal():
    """Scenario: Nombre vacío (n desde 1)."""
    assert safe_attachment_name("\x00\x01\u202e\u200b", 0, "pdf") == "attachment-1.pdf"
    assert safe_attachment_name("", 2, "docx") == "attachment-3.docx"


@pytest.mark.parametrize("raw", ["C:\\docs\\spec.pdf", "/tmp/x/spec.pdf", "a/b\\spec.pdf"])
def test_only_the_base_name_is_kept(raw):
    assert safe_attachment_name(raw, 0, "pdf") == "spec.pdf"


def test_name_is_limited_to_80_chars_keeping_the_extension():
    name = safe_attachment_name("n" * 200 + ".docx", 0, "docx")
    assert len(name) == 80 and name.endswith(".docx")


def test_name_collapses_spaces_and_replaces_unusual_characters():
    assert safe_attachment_name("mi   informe: <v2>.pdf", 0, "pdf") == "mi informe_ _v2_.pdf"


def test_name_masks_personal_data():
    """Requirement: después MUST enmascararse en él los datos personales."""
    name = safe_attachment_name("jefe@cliente.es.pdf", 0, "pdf")
    assert name == "[EMAIL].pdf"  # the extension survives the masking
    assert "jefe" not in name


def test_extracted_attachment_name_is_sanitised():
    attachment = extract(("..\\x\\a<b>.pdf", make_pdf("hola")))[0]
    assert attachment.name == "a_b_.pdf"
