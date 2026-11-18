"""Build the attachment PDFs of the CAG stress test (design s06 D7).

    uv run python -m evals.stress.fixtures.build_pdfs [--force]

Writes `attach_5kb.pdf`, `attach_20kb.pdf`, `attach_50kb.pdf` and `attach_100kb.pdf` next
to this module (git-ignored). The content is a synthetic Spanish technical specification
generated with a fixed seed, so two builds extract the same text. A "KB" is 1,000
characters of text extracted by `app.attachments` (pypdf), calibrated within ±15 %; the
100 KB file exceeds `MAX_ATTACHMENT_CHARS` (60,000) on purpose. The markers of
`ATTACHMENT_MARKERS` go at the start, the middle and the end of the document.

The text avoids anything the input guardrails would flag (emails, phone-like digit runs,
IBANs, instruction-like phrases). Requires `fpdf2` (dev dependency).
"""

import argparse
import random
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent
SIZES_KB: tuple[int, ...] = (5, 20, 50, 100)
CHARS_PER_KB = 1000
TOLERANCE = 0.15
SEED = 20261006

START_MARKER = "Código de proyecto ATLAS-7: portal de clientes de la aseguradora."
MIDDLE_MARKER = (
    "Nivel de servicio comprometido: SLA 99,9 % mensual de disponibilidad del portal, "
    "medido fuera de las ventanas de mantenimiento pactadas."
)
END_MARKER = (
    "Integración final: el portal debe intercambiar pólizas y siniestros con el sistema "
    "central Kestrel mediante servicios REST."
)

_MODULES = (
    "consulta de pólizas",
    "partes de siniestro",
    "chat con gestor",
    "área de documentos",
    "pagos de recibos",
    "notificaciones",
    "panel de administración",
    "informes de actividad",
    "gestión de usuarios",
    "firma electrónica",
)
_ASPECTS = (
    "La interfaz debe ser responsive y accesible",
    "Los datos se validan en el servidor antes de guardarse",
    "Cada operación queda registrada en el registro de auditoría",
    "El tiempo de respuesta objetivo es inferior a dos segundos",
    "Los errores se muestran con mensajes claros para el asegurado",
    "Se reutilizan los componentes del sistema de diseño corporativo",
    "Las búsquedas admiten filtros por fecha, estado y producto",
    "Los listados se paginan y se pueden ordenar por cualquier columna",
    "Los textos de la interfaz se gestionan desde un fichero de traducciones",
    "Las pruebas automáticas cubren los flujos principales",
)
_DETAILS = (
    "según los criterios del área de seguridad de la compañía",
    "con trazabilidad completa para el equipo de cumplimiento",
    "siguiendo las guías de estilo del departamento de marketing",
    "sin interrumpir el servicio actual durante la migración",
    "de forma coherente con el resto de canales digitales",
    "y con revisión trimestral por parte del comité técnico",
)


def _paragraph(rng: random.Random, number: int) -> str:
    module = rng.choice(_MODULES)
    sentences = [f"Requisito {number}. Módulo de {module}."]
    for aspect in rng.sample(_ASPECTS, 3):
        sentences.append(f"{aspect}, {rng.choice(_DETAILS)}.")
    return " ".join(sentences)


def _body(paragraphs: int) -> list[str]:
    """`paragraphs` filler paragraphs with the middle marker in the middle (fixed seed)."""
    rng = random.Random(SEED)
    body = [_paragraph(rng, number) for number in range(1, paragraphs + 1)]
    body.insert(len(body) // 2, MIDDLE_MARKER)
    return [START_MARKER, *body, END_MARKER]


def render_pdf(paragraphs: list[str]) -> bytes:
    """A deterministic PDF (fixed creation date, core font) with one paragraph per block."""
    from datetime import UTC, datetime

    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_creation_date(datetime(2026, 1, 1, tzinfo=UTC))
    pdf.set_title("Pliego técnico sintético")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    for paragraph in paragraphs:
        pdf.multi_cell(0, 5, paragraph)
        pdf.ln(3)
    return bytes(pdf.output())


def extracted_chars(data: bytes) -> int:
    """Characters of text our extractor gets from `data` (no character cap)."""
    from app.attachments import AttachmentLimits, UploadedFile, extract_attachments

    limits = AttachmentLimits(max_files=1, max_bytes=len(data) + 1, max_chars=10_000_000)
    (attachment,) = extract_attachments([UploadedFile("calibration.pdf", data)], limits)
    return len(attachment.text)


def build_pdf(size_kb: int) -> tuple[bytes, int]:
    """PDF whose extracted text is about `size_kb` KB, and its extracted length.

    Starts from an estimate of the paragraphs needed and corrects it with the measured
    length until it falls within the tolerance; deterministic (same steps every time).
    """
    target = size_kb * CHARS_PER_KB
    sample = _body(20)
    per_paragraph = sum(len(p) for p in sample) / len(sample)
    paragraphs = max(1, round(target / per_paragraph))
    for _ in range(6):
        data = render_pdf(_body(paragraphs))
        chars = extracted_chars(data)
        if abs(chars - target) <= target * TOLERANCE / 3:
            break
        paragraphs = max(1, round(paragraphs * target / chars))
    return data, chars


def path_for(size_kb: int, directory: Path = FIXTURES_DIR) -> Path:
    return directory / f"attach_{size_kb}kb.pdf"


def build_all(directory: Path = FIXTURES_DIR, *, force: bool = False) -> dict[int, int]:
    """Write the missing PDFs (all with `force`); return the extracted chars per size
    of the files written.
    """
    directory.mkdir(parents=True, exist_ok=True)
    written: dict[int, int] = {}
    for size_kb in SIZES_KB:
        path = path_for(size_kb, directory)
        if path.exists() and not force:
            continue
        data, chars = build_pdf(size_kb)
        path.write_bytes(data)
        written[size_kb] = chars
    return written


def missing(directory: Path = FIXTURES_DIR) -> list[int]:
    return [size_kb for size_kb in SIZES_KB if not path_for(size_kb, directory).exists()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.stress.fixtures.build_pdfs")
    parser.add_argument("--force", action="store_true", help="Rebuild existing files.")
    parser.add_argument("--output-dir", type=Path, default=FIXTURES_DIR, metavar="DIR")
    args = parser.parse_args(argv)
    written = build_all(args.output_dir, force=args.force)
    for size_kb, chars in written.items():
        print(f"{path_for(size_kb, args.output_dir).name}: {chars} chars extracted")
    if not written:
        print("All PDFs already exist (use --force to rebuild).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
