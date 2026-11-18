"""Load scenarios of the CAG stress test, with their fact-tracker (design s06 D3).

Every text is fixed in the code, so a run is reproducible. Each `ScenarioTurn` declares
the `facts` the client states in that turn; they must be remembered in that turn and in
every later one, until a later turn lists them in `retracts` (the client replaced them).
`facts_at(scenario, n)` returns the facts in force after turn `n`.

- `growing` (Spanish, `web_saas`): project "Nimbus" on React and Postgres; every turn
  adds a coherent requirement.
- `pivot` (English, `mobile_app`): "Lumora" starts on React Native (a fact in turns
  1-4); turn 5 switches to Flutter and retracts React Native.
- `contradiction` (Spanish, `web_saas`): "Quasar"; turn 3 sets a budget of 30.000 €,
  turn 8 replaces it with 80.000 €.
- `attachments` (Spanish, `web_saas`): one short fixed turn; the stress is the PDF built
  by `evals.stress.fixtures.build_pdfs`, which carries `ATTACHMENT_MARKERS`.

No LLM and no network; only `app.domain` enums are imported.
"""

from dataclasses import dataclass

from app.domain import Language, ProjectType


@dataclass(frozen=True)
class ScenarioTurn:
    index: int  # 1-based
    transcript: str
    facts: tuple[str, ...] = ()  # facts declared in this turn
    retracts: tuple[str, ...] = ()  # earlier facts this turn replaces (no longer required)


@dataclass(frozen=True)
class Scenario:
    name: str
    language: Language
    project_type: ProjectType
    turns: tuple[ScenarioTurn, ...]


def _turns(*items: tuple[str, tuple[str, ...]] | tuple[str, tuple[str, ...], tuple[str, ...]]):
    """Number the turns from 1: each item is `(transcript, facts[, retracts])`."""
    turns: list[ScenarioTurn] = []
    for position, item in enumerate(items, start=1):
        transcript, facts, *rest = item
        retracts = rest[0] if rest else ()
        turns.append(ScenarioTurn(position, transcript, tuple(facts), tuple(retracts)))
    return tuple(turns)


GROWING = Scenario(
    name="growing",
    language="es",
    project_type=ProjectType.WEB_SAAS,
    turns=_turns(
        (
            "Somos una empresa de logística de última milla y queremos construir Nimbus, una "
            "plataforma SaaS web para que nuestros clientes gestionen sus flotas de reparto: "
            "alta de vehículos y conductores, planificación de rutas diarias y seguimiento de "
            "entregas. El equipo interno ya trabaja con React en el frontend y Postgres como "
            "base de datos, así que queremos mantener ese stack.",
            ("Nimbus", "React", "Postgres"),
        ),
        (
            "Los clientes corporativos nos piden inicio de sesión único: Nimbus tiene que "
            "soportar SSO con Google Workspace y Microsoft Entra ID, además del login con "
            "email y contraseña para las empresas pequeñas.",
            ("SSO",),
        ),
        (
            "Otro punto clave: Nimbus debe ser multi-tenant. Cada empresa cliente tiene sus "
            "datos aislados, su propio subdominio y su configuración de marca (logo y colores).",
            ("multi-tenant",),
        ),
        (
            "Por temas de cumplimiento con nuestros clientes grandes necesitamos un audit log: "
            "quién cambió qué y cuándo, sobre rutas, conductores y permisos, consultable desde "
            "el panel de administración y conservado al menos dos años.",
            ("audit log",),
        ),
        (
            "Los jefes de tráfico quieren exportar a CSV el histórico de entregas y los "
            "kilómetros por vehículo, filtrando por rango de fechas, para cuadrarlo con su "
            "contabilidad.",
            ("CSV",),
        ),
        (
            "Sobre permisos: habrá tres roles, administrador de la empresa, despachador y "
            "conductor. El despachador asigna rutas; el conductor solo ve las suyas.",
            (),
        ),
        (
            "Queremos notificaciones: email y push en el navegador cuando una entrega se "
            "retrasa más de 30 minutos o cuando un conductor marca una incidencia.",
            (),
        ),
        (
            "Algunos clientes quieren integrar Nimbus con sus sistemas, así que necesitamos una "
            "API pública REST documentada, con claves por empresa y límites de uso.",
            (),
        ),
        (
            "La facturación será por suscripción mensual según el número de vehículos. "
            "Queremos cobrar con Stripe, con prueba gratuita de 14 días.",
            ("Stripe",),
        ),
        (
            "Para el seguimiento en vivo, el despachador necesita un mapa en tiempo real con la "
            "posición de cada vehículo. Hemos pensado en Mapbox para los mapas.",
            ("Mapbox",),
        ),
        (
            "La dirección de cada cliente quiere un panel de analítica con KPIs: entregas a "
            "tiempo, coste por kilómetro e incidencias por conductor, con comparativa mensual.",
            (),
        ),
        (
            "Además de la API, necesitamos webhooks para avisar a los sistemas del cliente "
            "cuando cambia el estado de una entrega.",
            (),
        ),
        (
            "Muchos clientes vienen de hojas de cálculo: hace falta una importación masiva de "
            "vehículos, conductores y direcciones desde Excel, con validación y vista previa.",
            (),
        ),
        (
            "Vamos a vender también en Portugal y Reino Unido: la interfaz tiene que estar en "
            "español, inglés y portugués desde el lanzamiento.",
            (),
        ),
        (
            "Tratamos datos de geolocalización de conductores, así que hay que cumplir el RGPD: "
            "consentimiento, derecho de supresión y borrado automático de posiciones a los "
            "90 días.",
            (),
        ),
        (
            "Dos clientes grandes nos exigen sincronizar los pedidos con su ERP mediante "
            "ficheros por SFTP cada noche, además de la API.",
            (),
        ),
        (
            "En cuanto a volumen, esperamos llegar a 500 empresas y unos 20.000 vehículos en el "
            "primer año, con picos de posiciones cada 10 segundos.",
            (),
        ),
        (
            "Algunos clientes son administraciones públicas y nos piden accesibilidad WCAG 2.1 "
            "nivel AA en toda la aplicación web.",
            (),
        ),
        (
            "Infraestructura: todo en AWS, con entornos de staging y producción y despliegue "
            "continuo desde el repositorio.",
            ("AWS",),
        ),
        (
            "Para cerrar: queremos lanzar Nimbus en unos nueve meses. ¿Podéis darnos la "
            "estimación completa con todo lo que hemos hablado hasta ahora?",
            (),
        ),
    ),
)

PIVOT = Scenario(
    name="pivot",
    language="en",
    project_type=ProjectType.MOBILE_APP,
    turns=_turns(
        (
            "We are a small wellness startup and we want to build Lumora, a habit-tracking "
            "mobile app for iOS and Android. Users create daily habits, check them off and see "
            "their streaks. Our two developers know React Native, so we plan to build it with "
            "React Native and a Firebase backend.",
            ("Lumora", "React Native"),
        ),
        (
            "Reminders are essential: each habit can have one or more local notifications at "
            "custom times, and a weekly summary notification every Sunday.",
            (),
        ),
        (
            "We would like to sync steps and sleep from Apple Health and Google Fit so that "
            "some habits are checked off automatically.",
            (),
        ),
        (
            "Users should be able to group habits into routines (morning, evening) and reorder "
            "them with drag and drop.",
            (),
        ),
        (
            "Important change of plans: after a technical review we have decided to switch to "
            "Flutter instead of React Native. Please discard React Native completely and plan "
            "the whole app in Flutter, keeping the Firebase backend.",
            ("Flutter",),
            ("React Native",),
        ),
        (
            "Lumora must work offline: users check habits without connection and everything "
            "syncs when they are back online, resolving conflicts by latest change.",
            (),
        ),
        (
            "Monetization: a free tier with up to five habits and a premium subscription, "
            "monthly or yearly, handled through RevenueCat.",
            (),
        ),
        (
            "We want social challenges: invite friends, join a 30-day challenge together and "
            "see a shared leaderboard.",
            (),
        ),
        (
            "Home screen widgets on both platforms showing today's habits and the current streak.",
            (),
        ),
        (
            "Dark mode and a couple of colour themes for premium users.",
            (),
        ),
        (
            "The app should launch in English, Spanish and German.",
            (),
        ),
        (
            "Product analytics with Amplitude: onboarding funnel, retention by cohort and "
            "conversion to premium.",
            (),
        ),
        (
            "Support for wearables: a simple Apple Watch and Wear OS companion to check off "
            "habits from the wrist.",
            (),
        ),
        (
            "Premium users get a chat with a human wellness coach, with text messages and "
            "photos, answered within 24 hours.",
            (),
        ),
        (
            "A short onboarding quiz that suggests a starter set of habits based on the "
            "user's goals.",
            (),
        ),
        (
            "We need remote configuration and A/B testing of the paywall and the onboarding "
            "screens.",
            (),
        ),
        (
            "Accessibility matters to us: dynamic font sizes, screen reader labels and "
            "sufficient contrast everywhere.",
            (),
        ),
        (
            "Before launch we want a closed beta with about 200 testers through TestFlight and "
            "the Google Play internal testing track.",
            (),
        ),
        (
            "An admin web panel for our team to manage challenges, coaches and push campaigns.",
            (),
        ),
        (
            "That's everything. We would like to launch Lumora in about six months. Can you "
            "give us the full estimate with everything we have discussed?",
            (),
        ),
    ),
)

CONTRADICTION = Scenario(
    name="contradiction",
    language="es",
    project_type=ProjectType.WEB_SAAS,
    turns=_turns(
        (
            "Somos una red de 40 clínicas veterinarias y queremos construir Quasar, una "
            "plataforma web de citas online: los dueños de mascotas reservan cita, eligen "
            "clínica y veterinario, y reciben recordatorios.",
            ("Quasar",),
        ),
        (
            "Cada clínica necesita gestionar sus agendas, horarios de veterinarios y tipos de "
            "consulta, y bloquear huecos para urgencias.",
            (),
        ),
        (
            "Sobre el dinero: el presupuesto máximo que tenemos aprobado para Quasar es de "
            "30.000 €, y nos gustaría que la estimación se ajuste a esa cifra.",
            ("30.000",),
        ),
        (
            "Queremos una ficha de cada mascota con su historial de vacunas y visitas, "
            "visible para el dueño.",
            (),
        ),
        (
            "Recordatorios automáticos por email y SMS 24 horas antes de la cita, y aviso de "
            "vacunas pendientes.",
            (),
        ),
        (
            "Pago por adelantado opcional de la consulta con tarjeta al reservar.",
            (),
        ),
        (
            "Un panel para la central con la ocupación de todas las clínicas y las "
            "cancelaciones por semana.",
            (),
        ),
        (
            "Cambio importante: tras la reunión con la dirección, el presupuesto de Quasar pasa "
            "a ser de 80.000 €. La cifra de 30.000 € ya no aplica; tomad 80.000 € como el "
            "presupuesto vigente.",
            ("80.000",),
            ("30.000",),
        ),
        (
            "Con el nuevo presupuesto queremos añadir videoconsulta para revisiones sencillas.",
            (),
        ),
        (
            "Integración con el software de gestión que ya usan las clínicas, que expone una "
            "API para sincronizar citas y fichas.",
            (),
        ),
        (
            "Valoraciones de los clientes tras cada cita, visibles en la ficha de cada clínica.",
            (),
        ),
        (
            "Una lista de espera: si alguien cancela, se avisa automáticamente al siguiente.",
            (),
        ),
        (
            "Programa de fidelización con puntos por cita que se canjean en la tienda de la "
            "clínica.",
            (),
        ),
        (
            "La web tiene que estar en español y en catalán.",
            (),
        ),
        (
            "Roles: administrador de la red, gerente de clínica, veterinario y recepción.",
            (),
        ),
        (
            "Informes mensuales en PDF por clínica que se envían a cada gerente.",
            (),
        ),
        (
            "Accesibilidad básica y que funcione bien en móvil, porque la mayoría reserva "
            "desde el teléfono.",
            (),
        ),
        (
            "Hosting en la nube europea por protección de datos, con copias de seguridad diarias.",
            (),
        ),
        (
            "Querríamos un piloto en cinco clínicas antes de desplegar en toda la red.",
            (),
        ),
        (
            "Eso es todo. ¿Nos dais la estimación final de Quasar con todo lo hablado y "
            "ajustada al presupuesto vigente?",
            (),
        ),
    ),
)

ATTACHMENTS = Scenario(
    name="attachments",
    language="es",
    project_type=ProjectType.WEB_SAAS,
    turns=_turns(
        (
            "Queremos modernizar el portal de clientes de nuestra aseguradora: consulta de "
            "pólizas, partes de siniestro online y chat con un gestor. Os adjuntamos el pliego "
            "técnico si lo tenemos; estimad el proyecto a partir de lo que contenga.",
            (),
        ),
    ),
)

# Markers of the attachment PDFs (start, middle and end of the document), each with the
# spellings that count as recalled; searched in the response (title, summary, tasks and
# assumptions) without case nor accents.
ATTACHMENT_MARKERS: tuple[tuple[str, ...], ...] = (
    ("ATLAS-7",),
    ("99,9", "99.9"),
    ("Kestrel",),
)

SCENARIOS: dict[str, Scenario] = {
    scenario.name: scenario for scenario in (GROWING, PIVOT, CONTRADICTION, ATTACHMENTS)
}
# The multi-turn profiles, in the order the runner uses by default.
PROFILE_NAMES: tuple[str, ...] = ("growing", "pivot", "contradiction")
# N of the memory drift curve (turn N of the 20-turn conversation).
DRIFT_CHECKPOINTS: tuple[int, ...] = (1, 3, 6, 10, 20)


def get_scenario(name: str) -> Scenario:
    """The scenario called `name`; `KeyError` with the known names otherwise."""
    try:
        return SCENARIOS[name]
    except KeyError:
        raise KeyError(f"unknown scenario {name!r}; expected one of {sorted(SCENARIOS)}") from None


def select_turns(scenario: Scenario, n: int) -> tuple[ScenarioTurn, ...]:
    """The first `n` turns of the scenario (all of them when `n` is larger)."""
    if n < 1:
        raise ValueError("n must be at least 1")
    return scenario.turns[:n]


def facts_at(scenario: Scenario, turn_index: int) -> tuple[str, ...]:
    """Facts in force after turn `turn_index`: declared in it or before, minus those a
    turn up to it retracted, in declaration order.
    """
    facts: list[str] = []
    for turn in scenario.turns[:turn_index]:
        facts = [fact for fact in facts if fact not in turn.retracts]
        facts.extend(fact for fact in turn.facts if fact not in facts)
    return tuple(facts)
