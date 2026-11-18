## MODIFIED Requirements

### Requirement: Templates versionados de estimación
El sistema SHALL guardar el prompt de estimación fuera del código, en templates Jinja2 dentro de `app/prompts/estimation/<versión>/`. Cada versión MUST tener como mínimo tres ficheros:
- `system.j2`, con las instrucciones;
- `user.j2`, el envoltorio de la entrada del usuario;
- `examples.j2`, con los ejemplos few-shot, incluido desde `system.j2` con `{% include %}`.

Las versiones disponibles MUST ser:
- `v3`, la de `POST /api/v1/estimate`;
- `v4`, de los turnos de sesión, que añade el parcial `project_metadata.j2` incluido desde su `system.j2`;
- `v5`, que añade los parciales `conversation_summary.j2` y `audience.j2`;
- `v6`, la versión por defecto de los turnos de sesión, con los mismos ficheros que `v5` y el bloque opcional `<critic_feedback>` en `user.j2`.

Cada versión MUST ser una copia completa e independiente: ningún template MUST incluir, extender ni importar templates de otra versión.

Las listas de versiones admitidas MUST definirse en un único sitio del código:
- `PROMPT_VERSIONS = ("v3",)` para la estimación;
- `SESSION_PROMPT_VERSIONS = ("v4", "v5", "v6")` para las sesiones, con `DEFAULT_SESSION_PROMPT_VERSION = "v6"`;
- `MEMORY_PROMPT_VERSIONS = {"v5", "v6"}`;
- `REVIEW_PROMPT_VERSIONS = {"v6"}`.

Su unión (`PUBLISHED_PROMPT_VERSIONS`) MUST coincidir con las carpetas existentes.

#### Scenario: Estructura de las versiones
- **WHEN** se inspecciona `app/prompts/estimation/`
- **THEN** existen las carpetas `v3` (con `system.j2`, `user.j2` y `examples.j2`), `v4` (con `system.j2`, `user.j2`, `examples.j2` y `project_metadata.j2`), `v5` (con los de `v4` más `conversation_summary.j2` y `audience.j2`) y `v6` (con los mismos ficheros que `v5`), y no hay otras carpetas de versión

#### Scenario: Versiones aisladas
- **WHEN** se renderiza `v3`, `v4`, `v5` y `v6`
- **THEN** todos los templates usados en cada render son los de su propia carpeta

#### Scenario: Versiones publicadas
- **WHEN** se comparan `PUBLISHED_PROMPT_VERSIONS` con las carpetas de `app/prompts/estimation/`
- **THEN** coinciden exactamente

### Requirement: Render del prompt de sesión
`app/prompts/loader.py` SHALL exponer `render_session_prompt(turn, version="v6", *, attachments=(), project_metadata=None, response_language=None, num_examples=3, conversation_summary=None, tier=Tier.DEFAULT, critic_feedback=None) -> SessionPrompt`, donde:
- `turn` es un `SessionTurnInput`;
- `attachments` son pares `(nombre saneado, texto enmascarado)`;
- `critic_feedback` es un `CriticFeedback` o `None`;
- `SessionPrompt` es un objeto inmutable con tres campos, sin espacios al principio ni al final:
  - `system`;
  - `user`, el mensaje del turno actual con el texto de los adjuntos y, en `v6`, el bloque `<critic_feedback>` si se recibe;
  - `history_user`, el mismo mensaje con el texto de cada adjunto sustituido por `[…]` y siempre sin `<critic_feedback>`.

El render MUST usar el mismo `Environment` de Jinja2 que `render_estimation_prompt`.

**Bloques por versión.**
- `conversation_summary` y `tier` MUST pintarse solo en `v5` y `v6`. `v4` MUST ignorarlos y renderizar exactamente lo mismo que sin ellos.
- `critic_feedback` MUST pintarse solo en `v6`. `v4` y `v5` MUST ignorarlo.
- Sin `critic_feedback`, `v6` MUST renderizar `system`, `user` e `history_user` idénticos byte a byte a los de `v5`.

**Versiones rechazadas.** Una versión que no esté en `SESSION_PROMPT_VERSIONS` (incluida `v3`) MUST producir el error de dominio de versión desconocida antes de acceder al sistema de ficheros. `render_estimation_prompt` MUST seguir rechazando `v4`, `v5` y `v6`.

**Idioma.** El loader MUST NOT detectar el idioma en este render: usa el `response_language` que recibe.

**Evento.** El render MUST ser determinista, sin red, y registrar `prompt_rendered` sin ningún contenido, con:
- `prompt = "session"`, `version`, `output_format`, `detail_level`, `project_type` y `num_examples`;
- `attachments` (número), `metadata_fields` (nombres de los campos con valor) y `response_language`;
- `tier`, `summary_chars` (0 sin resumen) y `critic_issues` (número de problemas pintados, 0 sin feedback);
- el SHA-256 truncado a 12 caracteres y la longitud de `system`, `user` e `history_user`.

#### Scenario: Render por defecto
- **GIVEN** un `SessionTurnInput` válido
- **WHEN** se llama a `render_session_prompt(turn)`
- **THEN** devuelve el system y el user de `v6`, e `history_user` es igual a `user` porque no hay adjuntos ni feedback

#### Scenario: v4 ignora resumen y tier
- **WHEN** se renderiza `v4` con y sin `conversation_summary` y `tier = Tier.EXECUTIVE`
- **THEN** los dos system son idénticos y ninguno contiene `<conversation_summary>` ni `<audience>`

#### Scenario: v6 sin feedback igual a v5
- **WHEN** se renderiza el mismo turno, con adjuntos, metadata, resumen y `tier = Tier.EXECUTIVE`, en `v5` y en `v6` sin `critic_feedback`
- **THEN** `system`, `user` e `history_user` son idénticos byte a byte

#### Scenario: Feedback en v6
- **GIVEN** un `CriticFeedback` con un problema `major` `scope_mismatch` en `rows[2].hours` y otro `minor`
- **WHEN** se renderiza `v6` con ese feedback y un adjunto
- **THEN** `user` termina con el bloque `<critic_feedback>` después de `</transcript>`, contiene el problema `major` y no el `minor`, e `history_user` no contiene `<critic_feedback>`

#### Scenario: v5 ignora el feedback
- **WHEN** se renderiza `v5` con y sin `critic_feedback`
- **THEN** los dos resultados son idénticos

#### Scenario: Versión no admitida
- **WHEN** se llama a `render_session_prompt` con `version = "v3"` y a `render_estimation_prompt` con `version = "v4"`, `"v5"` y `"v6"`
- **THEN** las cuatro llamadas producen el error de dominio de versión desconocida

#### Scenario: Evento sin contenido
- **GIVEN** una transcripción con `TRANSCRIPCION-SECRETA-123`, un adjunto con `ADJUNTO-SECRETO-456`, un resumen con `RESUMEN-SECRETO-789` y un feedback cuya descripción contiene `FEEDBACK-SECRETO-000`
- **WHEN** se renderiza el prompt de sesión con `v6`
- **THEN** se registra `prompt_rendered` con `prompt = "session"`, `attachments = 1`, `tier`, `summary_chars` mayor que 0 y `critic_issues = 1`, y el evento no contiene ninguno de los cuatro marcadores

## ADDED Requirements

### Requirement: Versión v5 inmutable
El texto renderizado de `v5` SHALL quedar idéntico, byte a byte, al de antes de este change. Un test dorado MUST fijar el SHA-256 del system y del user de `v5` para al menos 4 combinaciones (con y sin `project_metadata` con datos, con y sin resumen, con y sin adjuntos, y dos tiers), con los valores calculados antes de modificar el código. Los tests dorados de `v3` y `v4` MUST seguir en verde. Los ficheros de `v6` distintos de `user.j2` MUST ser copias literales de los de `v5`.

#### Scenario: Golden de v5
- **WHEN** se renderizan las combinaciones del test dorado de `v5`
- **THEN** los SHA-256 coinciden con los fijados antes del cambio

#### Scenario: Copias de v6
- **WHEN** se comparan `system.j2`, `examples.j2`, `project_metadata.j2`, `conversation_summary.j2` y `audience.j2` de `v5` y `v6`
- **THEN** son idénticos byte a byte

### Requirement: Templates del Critic
El sistema SHALL guardar el prompt del Critic en `app/prompts/critic/v1/{system,user}.j2`, versionado e inmutable, con su propio `Environment` (mismas opciones que el de estimación) y raíz en su carpeta. La versión MUST ser una constante del código (`CRITIC_PROMPT_VERSION = "v1"`).

**System.** Está en inglés y MUST incluir:
- el papel de auditor independiente que no produjo la estimación;
- las siete categorías con sus umbrales en horas;
- las reglas del veredicto: ante la duda, `accept` y `minor`;
- que `needs_iteration` exige un problema `critical` o `major`;
- el formato de `field_path` sobre el esquema (`summary`, `rows[i].hours`, `phases[i]`, `team`, `duration_weeks`, `assumptions_and_risks`, `technologies`);
- que `description` y `suggested_fix` se escriben en el idioma indicado;
- que puede haber hechos de turnos anteriores o de adjuntos que no ve, y que solo marque `hallucination` cuando un dato contradiga lo que sí ve.

**User.** MUST contener los bloques `<transcript>`, `<project_metadata>`, `<conversation_summary>` (solo si hay resumen), `<resolved_tier>`, `<response_language>` y `<estimation_under_review>`. Este último contiene el título, el resumen, la confianza, las filas con su índice, fase, perfil y horas, los totales de horas y coste, las fases, el equipo, la duración, los supuestos y las tecnologías.

**Render.** `render_critic_prompt(*, transcript, project_metadata, conversation_summary, tier, language, estimate) -> (system, user)` MUST ser determinista, sin red, y registrar `prompt_rendered` con `prompt = "critic"`, la versión, los hashes y las longitudes, sin contenido. `PUBLISHED_PROMPT_VERSIONS` MUST seguir refiriéndose solo a `estimation/`.

#### Scenario: Carpeta del Critic
- **WHEN** se inspecciona `app/prompts/critic/`
- **THEN** contiene solo la carpeta `v1`, con `system.j2` y `user.j2`

#### Scenario: Estimación bajo revisión
- **GIVEN** un `ComputedEstimate` con 3 filas y `total_hours = 120`
- **WHEN** se renderiza el prompt del Critic
- **THEN** el user contiene `<estimation_under_review>` con las filas con índices 0, 1 y 2 y el total de 120 horas

#### Scenario: Evento del Critic sin contenido
- **GIVEN** una transcripción con `TRANSCRIPCION-SECRETA-123`
- **WHEN** se renderiza el prompt del Critic
- **THEN** se registra `prompt_rendered` con `prompt = "critic"` y el evento no contiene `TRANSCRIPCION-SECRETA-123`
