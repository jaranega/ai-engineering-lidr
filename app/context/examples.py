"""Static CAG context: past software estimations injected into the system prompt.

These examples are the system's "knowledge". They are fictitious but internally
consistent: each row cost is hours x the rate of its profile, the totals are the sums of
the rows (and of the phases), and each duration follows from the recommended team (about
30 productive hours per full-time person per week). They cover three project types and
sizes (medium web SaaS, large mobile app, small data pipeline) so the model can calibrate
granularity and order of magnitude; the order is fixed so the first N are the most
different.

This module only holds data (S04 design D4, D5): the rows, their canonical phase, the
per-phase confidence and the per-phase aggregation computed once at import time. The
presentation lives in the prompt templates (`app/prompts/estimation/<version>/examples.j2`),
which render each example in the requested output format.

Content is in Spanish (product language); the model answers in the language of the
project description. Strings have no leading/trailing whitespace so they render verbatim.
This module imports nothing from `app`.
"""

from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict

# EUR per hour by profile. Only UX/UI design uses `design`; development, QA, deployment
# and management use `dev` (S02 design D1). Shared with the output format of the prompt.
RATES_EUR_PER_HOUR: dict[str, float] = {"dev": 62.5, "design": 50.0}

Role = Literal["dev", "design"]
PhaseKey = Literal["discovery", "setup", "development", "qa", "deployment"]
# Values of `app.domain.ProjectType` used by the examples (kept as strings: this module
# has no dependencies). There is deliberately no `internal_tool` example.
ExampleProjectType = Literal["mobile_app", "web_saas", "internal_tool", "data_pipeline"]


class Phase(TypedDict):
    key: PhaseKey
    label_es: str
    label_en: str


# Canonical phases, in order (S04 design D5). The model may omit phases that do not apply
# but must not add its own.
PHASES: list[Phase] = [
    {"key": "discovery", "label_es": "Descubrimiento y diseño", "label_en": "Discovery and design"},
    {"key": "setup", "label_es": "Arquitectura y setup", "label_en": "Architecture and setup"},
    {"key": "development", "label_es": "Desarrollo", "label_en": "Development"},
    {"key": "qa", "label_es": "QA y testing", "label_en": "QA and testing"},
    {
        "key": "deployment",
        "label_es": "Despliegue y traspaso",
        "label_en": "Deployment and handover",
    },
]
PHASE_KEYS: tuple[PhaseKey, ...] = tuple(phase["key"] for phase in PHASES)


class ExampleRow(TypedDict):
    task: str
    hours: int
    role: Role  # rate profile; never rendered in any format (S02 design D1)
    phase: PhaseKey
    cost_eur: float  # hours x RATES_EUR_PER_HOUR[role]


class PhaseSummary(TypedDict):
    """Aggregation of the rows of one phase of an example, computed at import time."""

    phase: PhaseKey
    hours: int
    cost_eur: float
    confidence_pct: int


class EstimationExample(TypedDict):
    meeting_summary: str  # what the client asked for
    project_type: ExampleProjectType
    title: str
    rows: list[ExampleRow]
    phases: list[PhaseSummary]  # only phases with rows, in canonical order
    total_hours: int
    total_cost_eur: float
    team: str
    duration_weeks: tuple[int, int]
    risks: list[str]


def format_amount(value: float) -> str:
    """Spanish amount: no thousands separator, decimal comma only if not whole.

    `19500.0` -> `19500`, `937.5` -> `937,50`. Registered as the `amount` Jinja filter.
    """
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".replace(".", ",")


def _phases(rows: Sequence[ExampleRow], confidence: Mapping[PhaseKey, int]) -> list[PhaseSummary]:
    """Per-phase hours, cost and confidence, in canonical order, only phases with rows."""
    summaries: list[PhaseSummary] = []
    for key in PHASE_KEYS:
        phase_rows = [row for row in rows if row["phase"] == key]
        if not phase_rows:
            continue
        summaries.append(
            {
                "phase": key,
                "hours": sum(row["hours"] for row in phase_rows),
                "cost_eur": sum(row["cost_eur"] for row in phase_rows),
                "confidence_pct": confidence[key],
            }
        )
    return summaries


def _example(
    *,
    meeting_summary: str,
    project_type: ExampleProjectType,
    title: str,
    rows: Sequence[tuple[str, int, Role, PhaseKey]],
    phase_confidence: Mapping[PhaseKey, int],
    team: str,
    duration_weeks: tuple[int, int],
    risks: Sequence[str],
) -> EstimationExample:
    """Build an example from its data: row costs, per-phase aggregation and totals."""
    full_rows: list[ExampleRow] = [
        {
            "task": task,
            "hours": hours,
            "role": role,
            "phase": phase,
            "cost_eur": hours * RATES_EUR_PER_HOUR[role],
        }
        for task, hours, role, phase in rows
    ]
    return {
        "meeting_summary": meeting_summary,
        "project_type": project_type,
        "title": title,
        "rows": full_rows,
        "phases": _phases(full_rows, phase_confidence),
        "total_hours": sum(row["hours"] for row in full_rows),
        "total_cost_eur": sum(row["cost_eur"] for row in full_rows),
        "team": team,
        "duration_weeks": duration_weeks,
        "risks": list(risks),
    }


# Confidence (`confidence_pct`, S04 design D5): probability of finishing the phase within
# its hours. 85-95 for known phases, 70-85 for standard development, 50-70 where external
# integrations or ambiguity are; it drops where each example declares its risks.
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
        project_type="web_saas",
        title="Plataforma web de gestión de citas para clínicas",
        rows=[
            (
                "Descubrimiento y diseño UI/UX (flujos, wireframes y prototipo)",
                40,
                "design",
                "discovery",
            ),
            ("Arquitectura, repositorio, CI/CD y entornos", 15, "dev", "setup"),
            (
                "Autenticación y roles (administración, recepción, fisioterapeuta)",
                25,
                "dev",
                "development",
            ),
            (
                "Agenda y reservas por sede y profesional (calendario, huecos, cambios)",
                80,
                "dev",
                "development",
            ),
            ("Fichas de pacientes e historial de sesiones", 50, "dev", "development"),
            (
                "Recordatorios de cita por email y SMS (proveedor externo)",
                25,
                "dev",
                "development",
            ),
            ("Panel de métricas (ocupación y cancelaciones por sede)", 30, "dev", "development"),
            ("Testing y QA", 40, "dev", "qa"),
            ("Despliegue en producción y formación a usuarios", 15, "dev", "deployment"),
        ],
        # Development carries the external SMS provider and the GDPR requirements.
        phase_confidence={
            "discovery": 85,
            "setup": 90,
            "development": 75,
            "qa": 80,
            "deployment": 90,
        },
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
        project_type="mobile_app",
        title="App móvil de fidelización y pedido anticipado",
        rows=[
            ("Descubrimiento y diseño UI/UX (app y backoffice)", 70, "design", "discovery"),
            ("Setup multiplataforma (React Native), CI/CD y entornos", 25, "dev", "setup"),
            (
                "Backend API (usuarios, puntos, catálogo, promociones y pedidos)",
                90,
                "dev",
                "development",
            ),
            ("Registro, login social (Apple y Google) y perfil", 35, "dev", "development"),
            ("Tarjeta de fidelización con QR y acumulación de puntos", 45, "dev", "development"),
            (
                "Catálogo, pedido anticipado y pago integrado (Stripe)",
                100,
                "dev",
                "development",
            ),
            ("Integración con la API del TPV (compras y canjes)", 60, "dev", "development"),
            ("Notificaciones push segmentadas", 30, "dev", "development"),
            ("Backoffice web (promociones, usuarios e informes)", 70, "dev", "development"),
            ("Testing y QA en dispositivos iOS y Android", 70, "dev", "qa"),
            ("Publicación en App Store y Google Play", 20, "dev", "deployment"),
        ],
        # Development holds the POS integration risk; deployment, the app store review.
        phase_confidence={
            "discovery": 85,
            "setup": 90,
            "development": 60,
            "qa": 75,
            "deployment": 80,
        },
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
        project_type="data_pipeline",
        title="Integración Shopify-Odoo y reporting de ventas",
        rows=[
            (
                "Análisis de las APIs y mapeo de datos (pedidos, clientes, productos)",
                15,
                "dev",
                "discovery",
            ),
            (
                "Infraestructura serverless, cola de mensajes y gestión de secretos",
                10,
                "dev",
                "setup",
            ),
            (
                "Sincronización de pedidos y clientes Shopify → Odoo (webhooks)",
                30,
                "dev",
                "development",
            ),
            ("Sincronización de stock Odoo → Shopify (cada 15 minutos)", 20, "dev", "development"),
            ("Gestión de errores, reintentos y alertas por email", 15, "dev", "development"),
            ("Informe diario de ventas en Looker Studio", 15, "dev", "development"),
            ("Pruebas con datos reales y QA", 15, "dev", "qa"),
            ("Documentación y traspaso al equipo del cliente", 5, "dev", "deployment"),
        ],
        # Development holds the Shopify API rate limits and the SKU normalization risk.
        phase_confidence={
            "discovery": 85,
            "setup": 90,
            "development": 65,
            "qa": 75,
            "deployment": 95,
        },
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
