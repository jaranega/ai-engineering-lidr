"""Static CAG context: past software estimations injected into the system prompt.

These examples are the system's "knowledge". They are fictitious but internally
consistent: each row cost is hours x the rate of its profile, the totals are the sums of
the rows, and each duration follows from the recommended team (about 30 productive hours
per full-time person per week). They cover three project types and sizes (medium web
platform, large mobile app, small integration) so the model can calibrate granularity
and order of magnitude; the order is fixed so the first N are the most different.

Each example keeps its structured data (single source of truth) and the Markdown
`estimation` rendered from it once at import time; `format_examples` derives the three
prompt formats (markdown, json, narrative) from the same data (design D1, D2).

Content is in Spanish (product language); the model answers in the language of the
transcription. Strings have no leading/trailing whitespace so they are injected verbatim.
"""

import json
from collections.abc import Sequence
from typing import Literal, TypedDict

# EUR per hour by profile. Only UX/UI design uses `design`; development, QA, deployment
# and management use `dev` (design D1). Shared with the output format of the prompt.
RATES_EUR_PER_HOUR: dict[str, float] = {"dev": 62.5, "design": 50.0}

Role = Literal["dev", "design"]
ExampleFormat = Literal["markdown", "json", "narrative"]
EXAMPLE_FORMATS: tuple[ExampleFormat, ...] = ("markdown", "json", "narrative")


class ExampleRow(TypedDict):
    task: str
    hours: int
    role: Role  # rate profile; never rendered in any format (design D1)
    cost_eur: float  # hours x RATES_EUR_PER_HOUR[role]


class EstimationExample(TypedDict):
    meeting_summary: str
    estimation: str  # Markdown rendered from the fields below
    title: str
    rows: list[ExampleRow]
    total_hours: int
    total_cost_eur: float
    team: str
    duration_weeks: tuple[int, int]
    risks: list[str]


def format_amount(value: float) -> str:
    """Spanish amount: no thousands separator, decimal comma only if not whole.

    `19500.0` -> `19500`, `937.5` -> `937,50`.
    """
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".replace(".", ",")


def _number(value: float) -> int | float:
    """JSON number without a spurious `.0` for whole amounts."""
    return int(value) if float(value).is_integer() else value


def _render_markdown(
    title: str,
    rows: Sequence[ExampleRow],
    total_hours: int,
    total_cost: float,
    team: str,
    duration_weeks: tuple[int, int],
    risks: Sequence[str],
) -> str:
    """Markdown with the output format of the prompt (design D3)."""
    table = [
        "| Tarea | Horas | Coste (EUR) |",
        "|---|---|---|",
        *(f"| {r['task']} | {r['hours']} | {format_amount(r['cost_eur'])} |" for r in rows),
    ]
    low, high = duration_weeks
    lines = [
        f"## Estimación: {title}",
        "",
        "### Desglose de tareas",
        *table,
        "",
        "### Totales",
        f"- **Total de horas:** {total_hours}",
        f"- **Coste total:** {format_amount(total_cost)} EUR",
        f"- **Equipo recomendado:** {team}",
        f"- **Duración estimada:** {low}-{high} semanas",
        "",
        "### Supuestos y riesgos",
        *(f"- {risk}" for risk in risks),
    ]
    return "\n".join(lines)


def _example(
    *,
    meeting_summary: str,
    title: str,
    rows: Sequence[tuple[str, int, Role]],
    team: str,
    duration_weeks: tuple[int, int],
    risks: Sequence[str],
) -> EstimationExample:
    """Build an example from its data: row costs, totals and the rendered Markdown."""
    full_rows: list[ExampleRow] = [
        {"task": task, "hours": hours, "role": role, "cost_eur": hours * RATES_EUR_PER_HOUR[role]}
        for task, hours, role in rows
    ]
    total_hours = sum(r["hours"] for r in full_rows)
    total_cost = sum(r["cost_eur"] for r in full_rows)
    return {
        "meeting_summary": meeting_summary,
        "estimation": _render_markdown(
            title, full_rows, total_hours, total_cost, team, duration_weeks, risks
        ),
        "title": title,
        "rows": full_rows,
        "total_hours": total_hours,
        "total_cost_eur": total_cost,
        "team": team,
        "duration_weeks": duration_weeks,
        "risks": list(risks),
    }


ESTIMATION_EXAMPLES: list[EstimationExample] = [
    _example(
        meeting_summary=(
            "Una red de 5 clínicas de fisioterapia gestiona las citas por teléfono y con hojas "
            "de cálculo. Quiere una plataforma web interna para recepción y fisioterapeutas: "
            "agenda por sede y profesional, alta y ficha de pacientes con historial de "
            "sesiones, recordatorios automáticos de cita por email y SMS para reducir "
            "ausencias, y un panel con ocupación y cancelaciones por sede. Tres perfiles de "
            "acceso: administración, recepción y fisioterapeuta. No hay diseño previo. "
            "Esperan unos 40 usuarios internos y les gustaría tenerla en unos dos meses."
        ),
        title="Plataforma web de gestión de citas para clínicas",
        rows=[
            ("Descubrimiento y diseño UI/UX (flujos, wireframes y prototipo)", 40, "design"),
            ("Arquitectura, repositorio, CI/CD y entornos", 15, "dev"),
            ("Autenticación y roles (administración, recepción, fisioterapeuta)", 25, "dev"),
            ("Agenda y reservas por sede y profesional (calendario, huecos, cambios)", 80, "dev"),
            ("Fichas de pacientes e historial de sesiones", 50, "dev"),
            ("Recordatorios de cita por email y SMS (proveedor externo)", 25, "dev"),
            ("Panel de métricas (ocupación y cancelaciones por sede)", 30, "dev"),
            ("Testing y QA", 40, "dev"),
            ("Despliegue en producción y formación a usuarios", 15, "dev"),
        ],
        team="2 desarrolladores full-stack + 1 diseñador UX/UI (part-time)",
        duration_weeks=(6, 7),
        risks=[
            "Se usa un proveedor de SMS de pago por uso; su coste no está incluido.",
            "No se migran datos históricos de las hojas de cálculo; si se requiere, añadir "
            "20-30 horas.",
            "Los datos de salud exigen cumplir el RGPD (cifrado y registro de accesos).",
        ],
    ),
    _example(
        meeting_summary=(
            "Una cadena de 30 cafeterías quiere una app móvil de fidelización para iOS y "
            "Android. Los clientes deben poder registrarse (email, Apple o Google), acumular "
            "puntos mostrando un código QR en caja, ver el catálogo y las promociones, y hacer "
            "pedidos anticipados con pago en la app para recogerlos en tienda. El TPV actual "
            "tiene una API documentada para registrar compras y canjes. Marketing necesita un "
            "backoffice web para crear promociones, enviar notificaciones push segmentadas y "
            "ver informes de uso. Tienen la identidad de marca, pero no diseños de la app. "
            "Quieren lanzarla antes de la campaña de Navidad, en unos tres meses."
        ),
        title="App móvil de fidelización y pedido anticipado",
        rows=[
            ("Descubrimiento y diseño UI/UX (app y backoffice)", 70, "design"),
            ("Setup multiplataforma (React Native), CI/CD y entornos", 25, "dev"),
            ("Backend API (usuarios, puntos, catálogo, promociones y pedidos)", 90, "dev"),
            ("Registro, login social (Apple y Google) y perfil", 35, "dev"),
            ("Tarjeta de fidelización con QR y acumulación de puntos", 45, "dev"),
            ("Catálogo, pedido anticipado y pago integrado (Stripe)", 100, "dev"),
            ("Integración con la API del TPV (compras y canjes)", 60, "dev"),
            ("Notificaciones push segmentadas", 30, "dev"),
            ("Backoffice web (promociones, usuarios e informes)", 70, "dev"),
            ("Testing y QA en dispositivos iOS y Android", 70, "dev"),
            ("Publicación en App Store y Google Play", 20, "dev"),
        ],
        team=(
            "2 desarrolladores móviles (React Native) + 1 desarrollador backend + "
            "1 diseñador UX/UI (part-time) + 1 QA (part-time)"
        ),
        duration_weeks=(9, 10),
        risks=[
            "La API del TPV es estable y tiene entorno de pruebas; si no, la integración puede "
            "duplicarse.",
            "La revisión de las tiendas de apps puede tardar varios días; conviene reservar "
            "margen.",
            "No incluye pedidos a domicilio ni integración con plataformas de reparto.",
        ],
    ),
    _example(
        meeting_summary=(
            "Una tienda online en Shopify con unos 300 pedidos diarios introduce los pedidos "
            "a mano en su ERP (Odoo) y actualiza el stock en Shopify una vez al día, lo que "
            "provoca ventas sin stock. Quieren sincronizar automáticamente pedidos y clientes "
            "de Shopify a Odoo, actualizar el stock de Odoo a Shopify cada pocos minutos, "
            "recibir una alerta cuando falle una sincronización y tener un informe diario de "
            "ventas en Looker Studio. No necesitan interfaz propia. Prioridad: estar en "
            "marcha en un mes."
        ),
        title="Integración Shopify-Odoo y reporting de ventas",
        rows=[
            ("Análisis de las APIs y mapeo de datos (pedidos, clientes, productos)", 15, "dev"),
            ("Infraestructura serverless, cola de mensajes y gestión de secretos", 10, "dev"),
            ("Sincronización de pedidos y clientes Shopify → Odoo (webhooks)", 30, "dev"),
            ("Sincronización de stock Odoo → Shopify (cada 15 minutos)", 20, "dev"),
            ("Gestión de errores, reintentos y alertas por email", 15, "dev"),
            ("Informe diario de ventas en Looker Studio", 15, "dev"),
            ("Pruebas con datos reales y QA", 15, "dev"),
            ("Documentación y traspaso al equipo del cliente", 5, "dev"),
        ],
        team="1 desarrollador backend con experiencia en integraciones",
        duration_weeks=(4, 5),
        risks=[
            "El cliente facilita acceso de API a Shopify y a Odoo, con un entorno de pruebas "
            "de Odoo.",
            "Los productos comparten SKU en ambos sistemas; si no, añadir 10-15 horas de "
            "normalización.",
            "Los límites de peticiones de la API de Shopify pueden obligar a procesar por lotes.",
        ],
    ),
]


# --- Prompt formats (design D2) -----------------------------------------------------------


def _format_markdown(example: EstimationExample) -> str:
    return (
        "<example>\n"
        f"<meeting_summary>\n{example['meeting_summary']}\n</meeting_summary>\n"
        f"<estimation>\n{example['estimation']}\n</estimation>\n"
        "</example>"
    )


def _json_item(example: EstimationExample) -> dict:
    return {
        "meeting_summary": example["meeting_summary"],
        "title": example["title"],
        "breakdown": [
            {"task": r["task"], "hours": r["hours"], "cost_eur": _number(r["cost_eur"])}
            for r in example["rows"]
        ],
        "totals": {
            "hours": example["total_hours"],
            "cost_eur": _number(example["total_cost_eur"]),
        },
        "team": example["team"],
        "duration_weeks": list(example["duration_weeks"]),
        "risks": example["risks"],
    }


def _format_narrative(example: EstimationExample) -> str:
    low, high = example["duration_weeks"]
    tasks = "; ".join(
        f"{r['task']} ({r['hours']} h, {format_amount(r['cost_eur'])} EUR)" for r in example["rows"]
    )
    risks = " ".join(example["risks"])
    return (
        f"En un proyecto anterior el cliente pedía: {example['meeting_summary']} "
        f"Propusimos «{example['title']}»: {example['total_hours']} horas y "
        f"{format_amount(example['total_cost_eur'])} EUR en {low}-{high} semanas con "
        f"{example['team']}. Tareas principales: {tasks}. Riesgos: {risks}"
    )


def format_examples(examples: Sequence[EstimationExample], fmt: ExampleFormat) -> str:
    """Render the examples inside `<examples>` in the given format (design D2).

    Pure and deterministic. All formats carry the same information (summary, tasks with
    hours and cost, totals, team, duration and risks); the rate profile is never shown.
    The caller omits the block when there are no examples.
    """
    if fmt == "markdown":
        body = "\n".join(_format_markdown(e) for e in examples)
    elif fmt == "json":
        body = json.dumps([_json_item(e) for e in examples], ensure_ascii=False, indent=2)
    elif fmt == "narrative":
        body = "\n\n".join(_format_narrative(e) for e in examples)
    else:
        raise ValueError(f"Unknown example format: {fmt!r}")
    return f"<examples>\n{body}\n</examples>"
