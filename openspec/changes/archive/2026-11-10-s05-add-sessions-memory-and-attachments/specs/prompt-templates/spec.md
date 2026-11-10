## MODIFIED Requirements

### Requirement: Templates versionados de estimación
El sistema SHALL guardar el prompt de estimación fuera del código, en templates Jinja2 dentro de `app/prompts/estimation/<versión>/`, con tres ficheros por versión como mínimo: `system.j2` (instrucciones), `user.j2` (envoltorio de la entrada del usuario) y `examples.j2` (ejemplos few-shot, incluido desde `system.j2` con `{% include %}`). Las versiones disponibles MUST ser `v3`, la de `POST /api/v1/estimate`, y `v4`, la de los turnos de sesión, que añade el parcial `project_metadata.j2` incluido desde su `system.j2`. Cada versión MUST ser una copia completa e independiente: ningún template MUST incluir, extender ni importar templates de otra versión. Las listas de versiones admitidas (`PROMPT_VERSIONS = ("v3",)` para la estimación y `SESSION_PROMPT_VERSIONS = ("v4",)` para las sesiones) MUST definirse en un único sitio del código y su unión (`PUBLISHED_PROMPT_VERSIONS`) MUST coincidir con las carpetas existentes.

#### Scenario: Estructura de las versiones
- **WHEN** se inspecciona `app/prompts/estimation/`
- **THEN** existen las carpetas `v3` (con `system.j2`, `user.j2` y `examples.j2`) y `v4` (con `system.j2`, `user.j2`, `examples.j2` y `project_metadata.j2`), y no hay otras carpetas de versión

#### Scenario: Versiones aisladas
- **WHEN** se renderiza `v3` y se renderiza `v4`
- **THEN** todos los templates usados en cada render son los de su propia carpeta

#### Scenario: Versiones publicadas
- **WHEN** se comparan `PUBLISHED_PROMPT_VERSIONS` con las carpetas de `app/prompts/estimation/`
- **THEN** coinciden exactamente

## ADDED Requirements

### Requirement: Versión v3 inmutable
El texto renderizado de `v3` SHALL quedar idéntico, byte a byte, al de antes de este change. Un test dorado MUST fijar el SHA-256 de los system y user de `v3` para al menos 4 combinaciones de `project_type`, `detail_level`, `output_format`, número de ejemplos y proyectos de referencia, con los valores calculados antes de modificar el código. Los JSON de los ejemplos de `v3` MUST NOT incluir `technologies`.

#### Scenario: Hash dorado
- **WHEN** se renderiza `v3` con cada combinación fijada en el test dorado
- **THEN** el SHA-256 del system y del user coincide con el valor guardado

#### Scenario: Ejemplos de v3 sin tecnologías
- **WHEN** se renderiza el system de `v3` con los ejemplos por defecto
- **THEN** ningún `<example_output>` contiene la clave `technologies`

### Requirement: Render del prompt de sesión
`app/prompts/loader.py` SHALL exponer `render_session_prompt(turn, version="v4", *, attachments=(), project_metadata=None, response_language=None, num_examples=3) -> SessionPrompt`, donde `turn` es un `SessionTurnInput`, `attachments` son pares `(nombre saneado, texto enmascarado)` y `SessionPrompt` es un objeto inmutable con `system`, `user` (el mensaje del turno actual con el texto de los adjuntos) e `history_user` (el mismo mensaje con el texto de cada adjunto sustituido por `[…]`), sin espacios al principio ni al final. MUST usar el mismo `Environment` de Jinja2 que `render_estimation_prompt`. Una versión que no esté en `SESSION_PROMPT_VERSIONS` (incluida `v3`) MUST producir el error de dominio de versión desconocida antes de acceder al sistema de ficheros, y `render_estimation_prompt` MUST seguir rechazando `v4`. El loader MUST NOT detectar el idioma en este render: usa el `response_language` que recibe. El render MUST ser determinista, sin red, y registrar `prompt_rendered` con `prompt = "session"`, `version`, `output_format`, `detail_level`, `project_type`, `num_examples`, `attachments` (número), `metadata_fields` (nombres de los campos con valor), `response_language` y el SHA-256 truncado a 12 caracteres y la longitud de `system`, `user` e `history_user`, sin ningún contenido.

#### Scenario: Render por defecto
- **GIVEN** un `SessionTurnInput` válido
- **WHEN** se llama a `render_session_prompt(turn)`
- **THEN** devuelve el system y el user de `v4`, e `history_user` es igual a `user` porque no hay adjuntos

#### Scenario: Versión no admitida
- **WHEN** se llama a `render_session_prompt` con `version = "v3"` y a `render_estimation_prompt` con `version = "v4"`
- **THEN** las dos llamadas producen el error de dominio de versión desconocida

#### Scenario: Evento sin contenido
- **GIVEN** una transcripción con `TRANSCRIPCION-SECRETA-123` y un adjunto con `ADJUNTO-SECRETO-456`
- **WHEN** se renderiza el prompt de sesión
- **THEN** se registra `prompt_rendered` con `prompt = "session"` y `attachments = 1`, y el evento no contiene ninguno de los dos marcadores

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
