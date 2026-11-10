"""Static CAG context: past software estimations injected into the system prompt.

These examples are the system's "knowledge". They are fictitious but internally
consistent: hours are multiples of 5, each phase with rows has a confidence, the overall
confidence follows the phases (weighted by their hours), and each duration follows from
the recommended team (about 30 productive hours per full-time person per week). They
cover three project types and sizes (medium web SaaS, large mobile app, small data
pipeline) so the model can calibrate granularity and order of magnitude; the order is
fixed so the first N are the most different.

This module only holds data (S04 structured output design D4): no amounts, totals or
rendered text. `app.estimation.examples` turns each example into an `EstimationResult`,
`app.estimation.compute` computes its costs and totals, and the prompt templates show it
as JSON. The data are `medium` granularity; `phase_assumptions` (per-phase assumptions)
are only shown by prompt v4 when the `detailed` level is requested (S05 design D5), and
`technologies` (the ones named in `meeting_summary`, never the estimator's own choices
such as React Native or Stripe in example 2) only by v4: v3 renders without both.

Content is in Spanish (product language); the model answers in the language of the
project description. The `narrative` paragraphs have no figures: in the narrative format
the code adds the hours and costs. Strings have no leading/trailing whitespace so they
render verbatim. This module imports nothing from `app`.
"""

from typing import Literal, TypedDict

# EUR per hour by rate profile. Only UX/UI design uses `design`; development, QA,
# deployment and management use `dev` (S02 design D1). Never shown to the model.
RATES_EUR_PER_HOUR: dict[str, float] = {"dev": 62.5, "design": 50.0}

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
    phase: PhaseKey
    rate_profile: Literal["dev", "design"]
    hours: int  # multiple of 5


class ExampleTeamMember(TypedDict):
    role: str  # reads after the count: "2 desarrolladores full-stack"
    count: int
    part_time: bool


class EstimationExample(TypedDict):
    meeting_summary: str  # what the client asked for
    project_type: ExampleProjectType
    title: str
    summary: str  # 1-2 sentences of scope
    confidence_pct: int  # overall, multiple of 5
    technologies: list[str]  # named in `meeting_summary`; empty if none (S05 design D4)
    rows: list[ExampleRow]
    phase_confidence: dict[PhaseKey, int]  # one entry per phase used in `rows`
    phase_assumptions: dict[PhaseKey, list[str]]  # 1-2 per phase used in `rows`
    team: list[ExampleTeamMember]
    duration_weeks: tuple[int, int]  # (min, max)
    assumptions_and_risks: list[str]
    narrative: list[str]  # 2 paragraphs, no figures


def _row(
    task: str, hours: int, rate_profile: Literal["dev", "design"], phase: PhaseKey
) -> ExampleRow:
    return ExampleRow(task=task, phase=phase, rate_profile=rate_profile, hours=hours)


def _member(role: str, count: int, part_time: bool = False) -> ExampleTeamMember:
    return ExampleTeamMember(role=role, count=count, part_time=part_time)


# Confidence (`confidence_pct`, S04 design D5): probability of finishing the phase within
# its hours. 85-95 for known phases, 70-85 for standard development, 50-70 where external
# integrations or ambiguity are; it drops where each example declares its risks. The
# overall confidence is close to the hours-weighted mean of the phases.
ESTIMATION_EXAMPLES: list[EstimationExample] = [
    {
        "meeting_summary": (
            "Una red de 5 clínicas de fisioterapia gestiona las citas por teléfono y con hojas "
            "de cálculo. Quiere una plataforma web interna para recepción y fisioterapeutas: "
            "agenda por sede y profesional, alta y ficha de pacientes con historial de "
            "sesiones, recordatorios automáticos de cita por email y SMS para reducir "
            "ausencias, y un panel con ocupación y cancelaciones por sede. Tres perfiles de "
            "acceso: administración, recepción y fisioterapeuta. No hay diseño previo. "
            "Esperan unos 40 usuarios internos y les gustaría tenerla en unos dos meses."
        ),
        "project_type": "web_saas",
        "title": "Plataforma web de gestión de citas para clínicas",
        "summary": (
            "Plataforma web interna para recepción y fisioterapeutas con agenda por sede, "
            "fichas de pacientes, recordatorios por email y SMS y un panel de ocupación."
        ),
        "confidence_pct": 80,
        # Email and SMS are named, but no product: teaches leaving the list empty.
        "technologies": [],
        "rows": [
            _row(
                "Descubrimiento y diseño UI/UX (flujos, wireframes y prototipo)",
                40,
                "design",
                "discovery",
            ),
            _row("Arquitectura, repositorio, CI/CD y entornos", 15, "dev", "setup"),
            _row(
                "Autenticación y roles (administración, recepción, fisioterapeuta)",
                25,
                "dev",
                "development",
            ),
            _row(
                "Agenda y reservas por sede y profesional (calendario, huecos, cambios)",
                80,
                "dev",
                "development",
            ),
            _row("Fichas de pacientes e historial de sesiones", 50, "dev", "development"),
            _row(
                "Recordatorios de cita por email y SMS (proveedor externo)",
                25,
                "dev",
                "development",
            ),
            _row(
                "Panel de métricas (ocupación y cancelaciones por sede)", 30, "dev", "development"
            ),
            _row("Testing y QA", 40, "dev", "qa"),
            _row("Despliegue en producción y formación a usuarios", 15, "dev", "deployment"),
        ],
        # Development carries the external SMS provider and the GDPR requirements.
        "phase_confidence": {
            "discovery": 85,
            "setup": 90,
            "development": 75,
            "qa": 80,
            "deployment": 90,
        },
        "phase_assumptions": {
            "discovery": [
                "Recepción y fisioterapeutas validan los flujos en dos sesiones de trabajo."
            ],
            "setup": ["Se despliega en un proveedor cloud gestionado con entorno de pruebas."],
            "development": [
                "El proveedor de SMS ofrece una API documentada con entorno de pruebas.",
                "Los permisos se limitan a los tres perfiles acordados.",
            ],
            "qa": ["Las pruebas usan datos ficticios, nunca datos reales de pacientes."],
            "deployment": ["La formación se imparte en remoto, una sesión por perfil."],
        },
        "team": [
            _member("desarrolladores full-stack", 2),
            _member("diseñador UX/UI", 1, part_time=True),
        ],
        "duration_weeks": (6, 7),
        "assumptions_and_risks": [
            "Se usa un proveedor de SMS de pago por uso; su coste no está incluido.",
            "No se migran datos históricos de las hojas de cálculo; si se requiere, añadir "
            "20-30 horas.",
            "Los datos de salud exigen cumplir el RGPD (cifrado y registro de accesos).",
        ],
        "narrative": [
            "La propuesta es una plataforma web interna que sustituye el teléfono y las hojas "
            "de cálculo por una agenda compartida entre sedes. Recepción gestiona las citas "
            "por sede y profesional, los fisioterapeutas consultan la ficha y el historial de "
            "cada paciente, y administración sigue la ocupación y las cancelaciones en un "
            "panel. Los recordatorios automáticos por email y SMS atacan directamente las "
            "ausencias.",
            "El trabajo empieza por el diseño de los flujos de recepción, ya que no hay diseño "
            "previo, y se concentra en la agenda, que es la pieza más compleja. El mayor "
            "riesgo está en la integración con el proveedor de SMS y en el cumplimiento del "
            "RGPD para los datos de salud, que exige cifrado y registro de accesos desde el "
            "principio.",
        ],
    },
    {
        "meeting_summary": (
            "Una cadena de 30 cafeterías quiere una app móvil de fidelización para iOS y "
            "Android. Los clientes deben poder registrarse (email, Apple o Google), acumular "
            "puntos mostrando un código QR en caja, ver el catálogo y las promociones, y hacer "
            "pedidos anticipados con pago en la app para recogerlos en tienda. El TPV actual "
            "tiene una API documentada para registrar compras y canjes. Marketing necesita un "
            "backoffice web para crear promociones, enviar notificaciones push segmentadas y "
            "ver informes de uso. Tienen la identidad de marca, pero no diseños de la app. "
            "Quieren lanzarla antes de la campaña de Navidad, en unos tres meses."
        ),
        "project_type": "mobile_app",
        "title": "App móvil de fidelización y pedido anticipado",
        "summary": (
            "App de fidelización para iOS y Android con puntos por QR, pedido anticipado con "
            "pago integrado e integración con el TPV, más un backoffice web para marketing."
        ),
        "confidence_pct": 65,
        "technologies": ["iOS", "Android", "Sign in with Apple", "Google Sign-In"],
        "rows": [
            _row("Descubrimiento y diseño UI/UX (app y backoffice)", 70, "design", "discovery"),
            _row("Setup multiplataforma (React Native), CI/CD y entornos", 25, "dev", "setup"),
            _row(
                "Backend API (usuarios, puntos, catálogo, promociones y pedidos)",
                90,
                "dev",
                "development",
            ),
            _row("Registro, login social (Apple y Google) y perfil", 35, "dev", "development"),
            _row(
                "Tarjeta de fidelización con QR y acumulación de puntos", 45, "dev", "development"
            ),
            _row(
                "Catálogo, pedido anticipado y pago integrado (Stripe)",
                100,
                "dev",
                "development",
            ),
            _row("Integración con la API del TPV (compras y canjes)", 60, "dev", "development"),
            _row("Notificaciones push segmentadas", 30, "dev", "development"),
            _row("Backoffice web (promociones, usuarios e informes)", 70, "dev", "development"),
            _row("Testing y QA en dispositivos iOS y Android", 70, "dev", "qa"),
            _row("Publicación en App Store y Google Play", 20, "dev", "deployment"),
        ],
        # Development holds the POS integration risk; deployment, the app store review.
        "phase_confidence": {
            "discovery": 85,
            "setup": 90,
            "development": 60,
            "qa": 75,
            "deployment": 80,
        },
        "phase_assumptions": {
            "discovery": ["La identidad de marca existente se aplica sin rediseñarla."],
            "setup": ["Una sola base de código multiplataforma para iOS y Android."],
            "development": [
                "La API del TPV tiene entorno de pruebas y documenta compras y canjes.",
                "El pago integrado usa una pasarela con SDK móvil.",
            ],
            "qa": ["Se prueba en un conjunto acotado de dispositivos iOS y Android."],
            "deployment": [
                "El cliente dispone de cuentas de desarrollador en App Store y Google Play."
            ],
        },
        "team": [
            _member("desarrolladores móviles (React Native)", 2),
            _member("desarrollador backend", 1),
            _member("diseñador UX/UI", 1, part_time=True),
            _member("QA", 1, part_time=True),
        ],
        "duration_weeks": (9, 10),
        "assumptions_and_risks": [
            "La API del TPV es estable y tiene entorno de pruebas; si no, la integración puede "
            "duplicarse.",
            "La revisión de las tiendas de apps puede tardar varios días; conviene reservar "
            "margen.",
            "No incluye pedidos a domicilio ni integración con plataformas de reparto.",
        ],
        "narrative": [
            "La propuesta es una app multiplataforma en React Native que convierte la tarjeta "
            "de fidelización en un código QR, permite pedir y pagar por adelantado para "
            "recoger en tienda y recibe promociones segmentadas. Un backend propio concentra "
            "usuarios, puntos, catálogo y pedidos, y un backoffice web da a marketing el "
            "control de las promociones, las notificaciones y los informes.",
            "El desarrollo es la parte más incierta porque depende de la API del TPV para "
            "registrar compras y canjes, y del pago integrado. La fecha de lanzamiento antes "
            "de Navidad obliga a reservar margen para la revisión de las tiendas de apps, y "
            "quedan fuera los pedidos a domicilio y las plataformas de reparto.",
        ],
    },
    {
        "meeting_summary": (
            "Una tienda online en Shopify con unos 300 pedidos diarios introduce los pedidos "
            "a mano en su ERP (Odoo) y actualiza el stock en Shopify una vez al día, lo que "
            "provoca ventas sin stock. Quieren sincronizar automáticamente pedidos y clientes "
            "de Shopify a Odoo, actualizar el stock de Odoo a Shopify cada pocos minutos, "
            "recibir una alerta cuando falle una sincronización y tener un informe diario de "
            "ventas en Looker Studio. No necesitan interfaz propia. Prioridad: estar en "
            "marcha en un mes."
        ),
        "project_type": "data_pipeline",
        "title": "Integración Shopify-Odoo y reporting de ventas",
        "summary": (
            "Sincronización automática de pedidos, clientes y stock entre Shopify y Odoo, con "
            "alertas de fallo y un informe diario de ventas en Looker Studio."
        ),
        "confidence_pct": 70,
        "technologies": ["Shopify", "Odoo", "Looker Studio"],
        "rows": [
            _row(
                "Análisis de las APIs y mapeo de datos (pedidos, clientes, productos)",
                15,
                "dev",
                "discovery",
            ),
            _row(
                "Infraestructura serverless, cola de mensajes y gestión de secretos",
                10,
                "dev",
                "setup",
            ),
            _row(
                "Sincronización de pedidos y clientes Shopify → Odoo (webhooks)",
                30,
                "dev",
                "development",
            ),
            _row(
                "Sincronización de stock Odoo → Shopify (cada 15 minutos)",
                20,
                "dev",
                "development",
            ),
            _row("Gestión de errores, reintentos y alertas por email", 15, "dev", "development"),
            _row("Informe diario de ventas en Looker Studio", 15, "dev", "development"),
            _row("Pruebas con datos reales y QA", 15, "dev", "qa"),
            _row("Documentación y traspaso al equipo del cliente", 5, "dev", "deployment"),
        ],
        # Development holds the Shopify API rate limits and the SKU normalization risk.
        "phase_confidence": {
            "discovery": 85,
            "setup": 90,
            "development": 65,
            "qa": 75,
            "deployment": 95,
        },
        "phase_assumptions": {
            "discovery": ["Los productos comparten SKU en Shopify y en Odoo."],
            "setup": ["El cliente tiene cuenta en un proveedor cloud con servicios serverless."],
            "development": [
                "Odoo expone pedidos, clientes y stock a través de su API.",
                "Los límites de peticiones de Shopify permiten sincronizar el stock cada "
                "15 minutos.",
            ],
            "qa": ["Hay un entorno de pruebas de Odoo con datos representativos."],
            "deployment": ["El equipo del cliente asume la operación tras el traspaso."],
        },
        "team": [_member("desarrollador backend con experiencia en integraciones", 1)],
        "duration_weeks": (4, 5),
        "assumptions_and_risks": [
            "El cliente facilita acceso de API a Shopify y a Odoo, con un entorno de pruebas "
            "de Odoo.",
            "Los productos comparten SKU en ambos sistemas; si no, añadir 10-15 horas de "
            "normalización.",
            "Los límites de peticiones de la API de Shopify pueden obligar a procesar por lotes.",
        ],
        "narrative": [
            "La propuesta elimina la introducción manual de pedidos con una integración sin "
            "interfaz propia: los webhooks de Shopify llevan pedidos y clientes a Odoo, y una "
            "tarea programada devuelve el stock de Odoo a Shopify con la frecuencia suficiente "
            "para evitar ventas sin stock. Cada fallo de sincronización genera una alerta, y "
            "un informe diario en Looker Studio resume las ventas.",
            "El trabajo arranca con el mapeo de datos entre las dos APIs y se apoya en una "
            "infraestructura serverless con cola de mensajes para absorber los picos. Los "
            "riesgos principales son los límites de peticiones de Shopify, que pueden obligar "
            "a procesar por lotes, y la normalización de SKU si no coinciden en ambos sistemas.",
        ],
    },
]
