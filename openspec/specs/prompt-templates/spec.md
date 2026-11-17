# prompt-templates Specification

## Purpose
Sacar el prompt de estimación del código a templates Jinja2 versionados (`app/prompts/estimation/<versión>/`), con un loader que renderiza `(system, user)` de forma determinista, falla ante variables no definidas o versiones inexistentes, detecta el idioma de la descripción y registra cada render sin su contenido.
## Requirements
### Requirement: Templates versionados de estimación
El sistema SHALL guardar el prompt de estimación fuera del código, en templates Jinja2 dentro de `app/prompts/estimation/<versión>/`, con tres ficheros por versión como mínimo: `system.j2` (instrucciones), `user.j2` (envoltorio de la entrada del usuario) y `examples.j2` (ejemplos few-shot, incluido desde `system.j2` con `{% include %}`). Las versiones disponibles MUST ser `v3`, la de `POST /api/v1/estimate`; `v4`, de los turnos de sesión, que añade el parcial `project_metadata.j2` incluido desde su `system.j2`; y `v5`, la versión por defecto de los turnos de sesión, que añade los parciales `conversation_summary.j2` y `audience.j2`. Cada versión MUST ser una copia completa e independiente: ningún template MUST incluir, extender ni importar templates de otra versión. Las listas de versiones admitidas (`PROMPT_VERSIONS = ("v3",)` para la estimación y `SESSION_PROMPT_VERSIONS = ("v4", "v5")` para las sesiones, con `DEFAULT_SESSION_PROMPT_VERSION = "v5"`) MUST definirse en un único sitio del código y su unión (`PUBLISHED_PROMPT_VERSIONS`) MUST coincidir con las carpetas existentes.

#### Scenario: Estructura de las versiones
- **WHEN** se inspecciona `app/prompts/estimation/`
- **THEN** existen las carpetas `v3` (con `system.j2`, `user.j2` y `examples.j2`), `v4` (con `system.j2`, `user.j2`, `examples.j2` y `project_metadata.j2`) y `v5` (con los de `v4` más `conversation_summary.j2` y `audience.j2`), y no hay otras carpetas de versión

#### Scenario: Versiones aisladas
- **WHEN** se renderiza `v3`, `v4` y `v5`
- **THEN** todos los templates usados en cada render son los de su propia carpeta

#### Scenario: Versiones publicadas
- **WHEN** se comparan `PUBLISHED_PROMPT_VERSIONS` con las carpetas de `app/prompts/estimation/`
- **THEN** coinciden exactamente

### Requirement: Loader de prompts
`app/prompts/loader.py` SHALL exponer `render_estimation_prompt(request, version="v3", *, num_examples=3) -> tuple[str, str]`, que devuelve `(system, user)` listos para enviar al modelo, sin espacios al principio ni al final. El loader MUST usar un `Environment` de Jinja2 con `undefined=StrictUndefined`, `trim_blocks=True`, `lstrip_blocks=True` y `autoescape=False`. Una variable no definida en el contexto MUST producir un error, nunca un texto vacío. Una versión que no esté en la lista de versiones admitidas MUST producir un error de dominio antes de acceder al sistema de ficheros. El render MUST ser determinista y no hacer llamadas de red.

#### Scenario: Render por defecto
- **GIVEN** una entrada válida
- **WHEN** se llama a `render_estimation_prompt(request)`
- **THEN** devuelve el system y el user de `v3`

#### Scenario: Variable no definida
- **GIVEN** un template que usa una variable que el contexto no proporciona
- **WHEN** se renderiza
- **THEN** se produce un error de Jinja2 (`UndefinedError`)

#### Scenario: Versión desconocida o retirada
- **WHEN** se llama con `version = "v1"`, `"v9"` o `"../v3"`
- **THEN** se produce un error de dominio de versión desconocida

#### Scenario: Determinismo
- **WHEN** se renderiza dos veces con la misma entrada, versión y número de ejemplos
- **THEN** los dos resultados son idénticos

#### Scenario: Todas las combinaciones renderizan
- **WHEN** se renderiza cada combinación de versión, `project_type`, `detail_level`, `output_format`, 0 o 3 ejemplos y con o sin proyectos de referencia
- **THEN** todas renderizan sin error y ningún prompt contiene la representación Python de un enum (`ProjectType.`, `DetailLevel.`, `OutputFormat.`)

### Requirement: Mensaje del usuario
`user.j2` SHALL contener una línea con el tipo de proyecto, el bloque `<reference_projects>` con un `<reference_project>` por proyecto de referencia (nombre, resumen y, si están, horas reales y duración en semanas) recorrido con `{% for %}` y omitido entero si no hay proyectos, y al final el bloque `<project_description>` con la descripción literal, sin escapar ni modificar. Ni la descripción ni los proyectos de referencia MUST aparecer en el system prompt.

#### Scenario: Descripción literal
- **GIVEN** una descripción con el texto `App para reservar pistas de pádel con pagos <online> & avisos`
- **WHEN** se renderiza
- **THEN** el user contiene ese texto literal dentro de `<project_description>…</project_description>` y el system no lo contiene

#### Scenario: Proyectos de referencia
- **GIVEN** dos proyectos de referencia
- **WHEN** se renderiza
- **THEN** el user contiene un bloque `<reference_projects>` con dos `<reference_project>`, antes de `<project_description>`, y el system no contiene sus nombres

#### Scenario: Sin proyectos de referencia
- **WHEN** se renderiza con `reference_projects` `null` o `[]`
- **THEN** el user no contiene `<reference_projects>`

### Requirement: Evento de render
Cada render SHALL registrar con structlog el evento `prompt_rendered` con `prompt = "estimation"`, `version`, `output_format`, `detail_level`, `project_type`, `num_examples`, `reference_projects` (número de elementos), `response_language` (`es`, `en` o `null`, el idioma detectado de la descripción), `system_sha256` y `user_sha256` (los 12 primeros caracteres hexadecimales del SHA-256 de cada texto) y `system_chars` y `user_chars`. El evento MUST NOT contener la descripción, los proyectos de referencia ni el texto de los prompts.

#### Scenario: Evento registrado
- **WHEN** se renderiza una petición con `v3` y `phases_table`
- **THEN** se registra `prompt_rendered` con `version = "v3"`, `output_format = "phases_table"` y dos hashes de 12 caracteres hexadecimales

#### Scenario: Hash estable
- **WHEN** se renderiza dos veces la misma entrada
- **THEN** los dos eventos tienen los mismos `system_sha256` y `user_sha256`

#### Scenario: Sin contenido
- **GIVEN** una descripción con el texto `DESCRIPCION-SECRETA-123`
- **WHEN** se renderiza
- **THEN** el evento `prompt_rendered` no contiene `DESCRIPCION-SECRETA-123`

### Requirement: Versión v3 inmutable
El texto renderizado de `v3` SHALL quedar idéntico, byte a byte, al de antes de este change. Un test dorado MUST fijar el SHA-256 de los system y user de `v3` para al menos 4 combinaciones de `project_type`, `detail_level`, `output_format`, número de ejemplos y proyectos de referencia, con los valores calculados antes de modificar el código. Los JSON de los ejemplos de `v3` MUST NOT incluir `technologies`.

#### Scenario: Hash dorado
- **WHEN** se renderiza `v3` con cada combinación fijada en el test dorado
- **THEN** el SHA-256 del system y del user coincide con el valor guardado

#### Scenario: Ejemplos de v3 sin tecnologías
- **WHEN** se renderiza el system de `v3` con los ejemplos por defecto
- **THEN** ningún `<example_output>` contiene la clave `technologies`

### Requirement: Render del prompt de sesión
`app/prompts/loader.py` SHALL exponer `render_session_prompt(turn, version="v5", *, attachments=(), project_metadata=None, response_language=None, num_examples=3, conversation_summary=None, tier=Tier.DEFAULT) -> SessionPrompt`, donde `turn` es un `SessionTurnInput`, `attachments` son pares `(nombre saneado, texto enmascarado)` y `SessionPrompt` es un objeto inmutable con `system`, `user` (el mensaje del turno actual con el texto de los adjuntos) e `history_user` (el mismo mensaje con el texto de cada adjunto sustituido por `[…]`), sin espacios al principio ni al final. MUST usar el mismo `Environment` de Jinja2 que `render_estimation_prompt`. `conversation_summary` y `tier` MUST pintarse solo en `v5`; `v4` MUST ignorarlos y renderizar exactamente lo mismo que sin ellos. Una versión que no esté en `SESSION_PROMPT_VERSIONS` (incluida `v3`) MUST producir el error de dominio de versión desconocida antes de acceder al sistema de ficheros, y `render_estimation_prompt` MUST seguir rechazando `v4` y `v5`. El loader MUST NOT detectar el idioma en este render: usa el `response_language` que recibe. El render MUST ser determinista, sin red, y registrar `prompt_rendered` con `prompt = "session"`, `version`, `output_format`, `detail_level`, `project_type`, `num_examples`, `attachments` (número), `metadata_fields` (nombres de los campos con valor), `response_language`, `tier`, `summary_chars` (0 sin resumen) y el SHA-256 truncado a 12 caracteres y la longitud de `system`, `user` e `history_user`, sin ningún contenido.

#### Scenario: Render por defecto
- **GIVEN** un `SessionTurnInput` válido
- **WHEN** se llama a `render_session_prompt(turn)`
- **THEN** devuelve el system y el user de `v5`, e `history_user` es igual a `user` porque no hay adjuntos

#### Scenario: v4 ignora resumen y tier
- **WHEN** se renderiza `v4` con y sin `conversation_summary` y `tier = Tier.EXECUTIVE`
- **THEN** los dos system son idénticos y ninguno contiene `<conversation_summary>` ni `<audience>`

#### Scenario: Versión no admitida
- **WHEN** se llama a `render_session_prompt` con `version = "v3"` y a `render_estimation_prompt` con `version = "v4"` y `"v5"`
- **THEN** las tres llamadas producen el error de dominio de versión desconocida

#### Scenario: Evento sin contenido
- **GIVEN** una transcripción con `TRANSCRIPCION-SECRETA-123`, un adjunto con `ADJUNTO-SECRETO-456` y un resumen con `RESUMEN-SECRETO-789`
- **WHEN** se renderiza el prompt de sesión con `v5`
- **THEN** se registra `prompt_rendered` con `prompt = "session"`, `attachments = 1`, `tier` y `summary_chars` mayor que 0, y el evento no contiene ninguno de los tres marcadores

### Requirement: Mensaje del usuario de v4
`user.j2` de `v4` SHALL contener, en este orden: `<project_type>` con el tipo de proyecto; si hay adjuntos, el bloque `<attachments>` con, por cada adjunto, la línea separadora `--- attachment: <nombre saneado> ---` seguida de su texto (o de `[…]` en `history_user`); y al final `<transcript>` con la transcripción literal, sin escapar ni modificar. Sin adjuntos, el bloque `<attachments>` MUST omitirse. Ni la transcripción ni el texto de los adjuntos MUST aparecer en el system prompt.

#### Scenario: Adjuntos antes de la transcripción
- **GIVEN** una transcripción `T` y un adjunto `spec.pdf` con el texto `A`
- **WHEN** se renderiza el prompt de sesión
- **THEN** `user` contiene `--- attachment: spec.pdf ---` seguido de `A` dentro de `<attachments>…</attachments>`, antes de `<transcript>`, y termina con `T` dentro de `<transcript>…</transcript>`

#### Scenario: Texto del adjunto sustituido
- **GIVEN** el mismo turno con un adjunto
- **WHEN** se renderiza el prompt de sesión
- **THEN** `history_user` contiene el separador del adjunto seguido de `[…]` y no contiene `A`, y el resto es igual a `user`

#### Scenario: Sin adjuntos
- **WHEN** se renderiza el prompt de sesión sin adjuntos
- **THEN** `user` no contiene `<attachments>`

### Requirement: Versión v4 inmutable
El texto renderizado de `v4` SHALL quedar idéntico, byte a byte, al de antes de este change. Un test dorado MUST fijar el SHA-256 del system y del user de `v4` para al menos 4 combinaciones (con y sin `project_metadata` con datos, con y sin adjuntos), con los valores calculados antes de modificar el código. El test dorado de `v3` MUST seguir en verde. Los JSON de los ejemplos de `v3` y `v4` MUST NOT incluir `explicit_constraints` ni `rejected_options`. El `user.j2` y el `examples.j2` de `v5` MUST ser copias literales de los de `v4`.

#### Scenario: Hash dorado de v4
- **WHEN** se renderiza `v4` con cada combinación fijada en el test dorado
- **THEN** el SHA-256 del system y del user coincide con el valor guardado

#### Scenario: Ejemplos de v3 y v4 sin campos de memoria
- **WHEN** se renderiza el system de `v3` y el de `v4` con los ejemplos por defecto
- **THEN** ningún `<example_output>` contiene las claves `explicit_constraints` ni `rejected_options`

#### Scenario: User de v5 igual al de v4
- **WHEN** se renderiza el mismo turno con adjuntos en `v4` y en `v5`
- **THEN** `user` e `history_user` son idénticos en las dos versiones

### Requirement: Templates de los prompts de memoria
El sistema SHALL guardar los prompts auxiliares de la memoria como templates Jinja2 versionados e inmutables, en `app/prompts/conversation_summary/v1/{system,user}.j2` (resumidor) y `app/prompts/anchor_classifier/v1/{system,user}.j2` (clasificador de anclas del modo LLM), cada uno con su propio `Environment` (mismas opciones que el de estimación) y raíz en su carpeta. La versión de cada uno MUST ser una constante del código (`SUMMARY_PROMPT_VERSION = "v1"`, `ANCHOR_PROMPT_VERSION = "v1"`), no un parámetro de la API. El loader MUST exponer `render_summary_prompt(previous, turns, language) -> (system, user)` y `render_anchor_prompt(turn) -> (system, user)`, deterministas y sin red, que registran `prompt_rendered` con `prompt = "conversation_summary"` o `"anchor_classifier"`, la versión, los hashes y las longitudes, sin contenido. `PUBLISHED_PROMPT_VERSIONS` MUST seguir refiriéndose solo a `estimation/`.

#### Scenario: Carpetas de los prompts de memoria
- **WHEN** se inspeccionan `app/prompts/conversation_summary/` y `app/prompts/anchor_classifier/`
- **THEN** cada una contiene solo la carpeta `v1`, con `system.j2` y `user.j2`

#### Scenario: Determinismo del resumen
- **WHEN** se renderiza dos veces el prompt del resumen con el mismo resumen previo, turnos e idioma
- **THEN** los dos resultados son idénticos

#### Scenario: Evento del resumen sin contenido
- **GIVEN** un turno cuyo `user` contiene `TURNO-SECRETO-123`
- **WHEN** se renderiza el prompt del resumen
- **THEN** se registra `prompt_rendered` con `prompt = "conversation_summary"` y el evento no contiene `TURNO-SECRETO-123`

