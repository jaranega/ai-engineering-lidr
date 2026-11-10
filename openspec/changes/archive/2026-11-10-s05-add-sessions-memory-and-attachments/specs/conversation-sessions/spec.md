## ADDED Requirements

### Requirement: Almacén de sesiones en memoria
El sistema SHALL guardar las sesiones de conversación en la memoria del proceso, en `app/sessions/store.py` (`SessionStore`), en un diccionario ordenado por `session_id`, sin base de datos ni Redis. Cada `session_id` MUST ser un UUID v4 en minúsculas y con guiones (`str(uuid.uuid4())`). Una `Session` MUST contener su historial (`ConversationHistory`), su `project_metadata` (vacío al crearla), el idioma efectivo del último turno con éxito (`null` al crearla) y `turn_count` (turnos con éxito desde la creación, también los que ya no están en la ventana). El almacén MUST tener un tope de `MAX_SESSIONS` sesiones: al crear una sesión con el almacén lleno, MUST desalojar la usada hace más tiempo (LRU por último acceso; consultar o usar una sesión refresca su uso). Un `session_id` desconocido MUST producir `SessionNotFoundError`. Cada sesión MUST tener un lock no bloqueante para los turnos: si ya hay un turno en curso sobre la misma sesión, otro intento MUST producir `SessionBusyError` sin esperar, y el lock MUST liberarse al terminar el turno, también si falla. Las sesiones son volátiles: se pierden al reiniciar el proceso y no se comparten entre workers. `app/sessions/` MUST NOT importar LiteLLM, Instructor, `app.services` ni `app.prompts`.

#### Scenario: Crear y recuperar
- **WHEN** se crea una sesión y se recupera por su `session_id`
- **THEN** el id es un UUID v4 en minúsculas, la sesión tiene el historial vacío, el `project_metadata` sin datos y `turn_count = 0`

#### Scenario: Sesión desconocida
- **WHEN** se recupera un UUID v4 válido que no se ha creado
- **THEN** se produce `SessionNotFoundError`

#### Scenario: Desalojo LRU
- **GIVEN** un almacén con `max_sessions = 2` y las sesiones A y B, creadas en ese orden
- **WHEN** se consulta A y después se crea C
- **THEN** el almacén contiene A y C, y B produce `SessionNotFoundError`

#### Scenario: Turno concurrente
- **GIVEN** una sesión con un turno en curso
- **WHEN** se intenta empezar otro turno sobre la misma sesión
- **THEN** se produce `SessionBusyError` de inmediato

#### Scenario: Lock liberado tras un error
- **GIVEN** un turno que lanza una excepción
- **WHEN** se empieza otro turno sobre la misma sesión
- **THEN** el segundo turno empieza sin `SessionBusyError`

#### Scenario: Creación concurrente
- **GIVEN** un almacén con `max_sessions = 10`
- **WHEN** varios hilos crean 50 sesiones a la vez
- **THEN** el almacén nunca contiene más de 10 sesiones

### Requirement: Historial con ventana deslizante
`ConversationHistory` SHALL guardar turnos completos (`Turn(number, user, assistant)`, un par de mensajes `user` + `assistant`), nunca mensajes sueltos, con un máximo de `max_turns` turnos (`MAX_CONVERSATION_TURNS`, por defecto 6). Tras cada `append`, MUST descartar los turnos más antiguos mientras haya más de `max_turns`. `Turn.number` MUST ser el ordinal del turno en la sesión (desde 1) y conservarse tras el recorte. `to_messages()` MUST devolver los mensajes de los turnos de la ventana, del más antiguo al más reciente, alternando `user` y `assistant` y empezando por `user`, y MUST NOT incluir el system prompt. `max_turns < 1` o un `user` o `assistant` vacío MUST producir `ValueError`.

#### Scenario: Ventana tras cada turno
- **GIVEN** un historial con `max_turns = 3`
- **WHEN** se añaden 5 turnos
- **THEN** tras cada `append` hay como máximo 3 turnos, y al final quedan los turnos 3, 4 y 5 con sus `number` originales

#### Scenario: Mensajes alternos sin system
- **GIVEN** un historial con 2 turnos
- **WHEN** se llama a `to_messages()`
- **THEN** devuelve 4 mensajes con roles `user`, `assistant`, `user`, `assistant`, en orden, y ninguno con rol `system`

#### Scenario: Ventana inválida
- **WHEN** se crea un historial con `max_turns = 0`
- **THEN** se produce `ValueError`

### Requirement: Contenido del turno guardado
Tras un turno con éxito, el sistema SHALL añadir al historial un par cuyo `user` es el mensaje `user` de `v4` renderizado para ese turno con el texto de cada adjunto sustituido por el marcador `[…]` (el separador `--- attachment: <nombre> ---` se conserva) y cuyo `assistant` es el `text` en Markdown que recibió el usuario. Un turno que termina en error (400, 404, 409, 413, 415, 422 o 502) MUST NOT añadir ningún mensaje al historial ni modificar el `project_metadata`, el idioma de la sesión ni `turn_count`. Un turno con éxito fuera de alcance (`result.out_of_scope = true`) MUST añadirse al historial y contar en `turn_count`, pero MUST NOT modificar el `project_metadata`.

#### Scenario: Adjunto sustituido en el historial
- **GIVEN** un turno con un PDF cuyo texto contiene `MARCADOR-PDF`
- **WHEN** el turno termina con éxito y se envía el siguiente turno
- **THEN** el mensaje `user` del turno anterior que recibe el LLM contiene `--- attachment: spec.pdf ---` y `[…]` y no contiene `MARCADOR-PDF`

#### Scenario: Respuesta guardada
- **WHEN** un turno termina con éxito
- **THEN** el mensaje `assistant` guardado es idéntico al `text` de la respuesta de ese turno

#### Scenario: Turno fallido
- **GIVEN** una sesión con un turno y un proveedor que falla en el segundo
- **WHEN** se envía el segundo turno
- **THEN** la respuesta es 502 y la sesión conserva un turno, el mismo `project_metadata` y `turn_count = 1`

#### Scenario: Turno fuera de alcance
- **GIVEN** una sesión con `project_name` relleno y un LLM que devuelve `confidence_pct = 10`
- **WHEN** se envía el turno
- **THEN** la respuesta es 200, `turn_count` aumenta en 1, el turno está en el historial y el `project_metadata` no cambia

### Requirement: Metadata del proyecto
El sistema SHALL mantener por sesión un `ProjectMetadata` inmutable (Pydantic, `frozen`) con `project_name` (`str | null`), `assumed_team_size` (`int | null`), `mentioned_technologies` (lista de textos, vacía por defecto) y `agreed_scope` (`str | null`), separado del historial y sustituido entero tras cada turno con éxito en alcance. `update_project_metadata(current, estimate)` en `app/sessions/metadata.py` MUST ser una función pura y determinista, sin llamar al LLM, con estas reglas sobre el `ComputedEstimate` del turno: `project_name` = `title` (se sobrescribe); `assumed_team_size` = suma de `count` de `team` (un perfil a tiempo parcial cuenta como sus personas); `agreed_scope` = `summary`; `mentioned_technologies` = las actuales seguidas de las nuevas `technologies`, en orden y sin duplicados sin distinguir mayúsculas (`casefold`, gana la primera grafía) y, si pasan de 30, descartando las más antiguas. Con `out_of_scope = true` MUST devolver el metadata actual sin cambios.

#### Scenario: Primer turno
- **GIVEN** un metadata vacío y una estimación con `title` "Clinic booking platform", equipo de 2 + 1 personas, `technologies` `["Twilio"]` y `summary` `S`
- **WHEN** se actualiza el metadata
- **THEN** `project_name = "Clinic booking platform"`, `assumed_team_size = 3`, `mentioned_technologies = ["Twilio"]` y `agreed_scope = S`

#### Scenario: Acumulación sin duplicados
- **GIVEN** un metadata con `mentioned_technologies = ["Twilio"]`
- **WHEN** se actualiza con una estimación con `technologies = ["Stripe", "twilio"]`
- **THEN** `mentioned_technologies = ["Twilio", "Stripe"]`

#### Scenario: Tope de tecnologías
- **GIVEN** un metadata con 30 tecnologías `T1`…`T30`
- **WHEN** se actualiza con una estimación con `technologies = ["Nueva"]`
- **THEN** hay 30 tecnologías, `T1` ya no está y `Nueva` es la última

#### Scenario: Fuera de alcance no actualiza
- **WHEN** se actualiza un metadata con una estimación con `out_of_scope = true`
- **THEN** el resultado es igual al metadata actual

### Requirement: Endpoints de sesión
El sistema SHALL exponer, con el prefijo `/api/v1` y la etiqueta `sessions` en OpenAPI: `POST /api/v1/sessions`, sin cuerpo, que crea una sesión vacía y responde **201** con `{"session_id": <uuid>}`; y `GET /api/v1/sessions/{session_id}`, que responde **200** con `SessionInfo` (`session_id`, `turn_count` con los turnos con éxito desde la creación, `history_turns` con los turnos que hay en la ventana, `max_turns` y `project_metadata`). `session_id` MUST declararse como UUID en la ruta: un formato inválido MUST dar **422** sin consultar el almacén, y un UUID válido que no existe MUST dar **404** con `{"detail": {"reason": "session_not_found", "message": <frase fija>}}`. El router MUST NOT contener lógica de negocio y MUST depender del servicio de sesiones vía `Depends`.

#### Scenario: Crear una sesión
- **WHEN** se hace POST a `/api/v1/sessions`
- **THEN** la respuesta es 201 con un `session_id` que es un UUID v4

#### Scenario: Consultar una sesión nueva
- **GIVEN** una sesión recién creada
- **WHEN** se hace GET a `/api/v1/sessions/{session_id}`
- **THEN** la respuesta es 200 con `turn_count = 0`, `history_turns = 0`, `max_turns = 6` y un `project_metadata` sin datos

#### Scenario: Sesión inexistente
- **WHEN** se hace GET a `/api/v1/sessions/{uuid}` con un UUID v4 válido que no existe
- **THEN** la respuesta es 404 con `detail.reason = "session_not_found"`

#### Scenario: Identificador mal formado
- **WHEN** se hace GET a `/api/v1/sessions/no-es-un-uuid`
- **THEN** la respuesta es 422

### Requirement: Turno de estimación en una sesión
El sistema SHALL exponer `POST /api/v1/sessions/{session_id}/estimate`, que recibe `multipart/form-data` con `transcript` (obligatorio, de 1 a 20 000 caracteres y no vacío tras recortar los espacios), `project_type`, `detail_level` y `output_format` (obligatorios, con los valores de los enums de `app.domain`) y una lista opcional `attachments` de ficheros (capability `attachments`), y el query param `prompt_version` (solo `"v4"`, por defecto `"v4"`). Con éxito MUST responder **200** con el contrato de `POST /api/v1/estimate` (`text`, `result`, `prompt_version`, métricas y `validation`) más `session_id`, `turn` (ordinal del turno, igual a `turn_count` tras él) y `project_metadata` (ya actualizado). Los errores MUST ser: **404** `session_not_found`; **409** `{"detail": {"reason": "session_busy", "message"}}` si hay otro turno en curso en la misma sesión; **400** de los guardrails (capability `input-guardrails`); **413**, **415** y **422** de los adjuntos (capability `attachments`); **422** de validación de FastAPI (un `transcript` solo con espacios da un error en `["body", "transcript"]` sin el valor enviado); y **502** con el `detail` genérico del proveedor, como `/api/v1/estimate`. OpenAPI MUST documentar el cuerpo multipart y todos esos códigos.

#### Scenario: Turno correcto
- **GIVEN** una sesión vacía y un proveedor simulado
- **WHEN** se hace POST multipart con `transcript`, `project_type`, `detail_level` y `output_format`
- **THEN** la respuesta es 200 con `text`, `result`, `prompt_version = "v4"`, `session_id` igual al de la ruta, `turn = 1` y el `project_metadata` actualizado

#### Scenario: Sesión inexistente
- **WHEN** se envía un turno a un UUID v4 válido que no existe
- **THEN** la respuesta es 404 con `detail.reason = "session_not_found"` y no se llama al LLM

#### Scenario: Turno simultáneo
- **GIVEN** una sesión con el lock de turno tomado
- **WHEN** se envía un turno a esa sesión
- **THEN** la respuesta es 409 con `detail.reason = "session_busy"` y no se llama al LLM

#### Scenario: Transcripción vacía
- **WHEN** se envía un turno con `transcript` de solo espacios
- **THEN** la respuesta es 422 con un error en `["body", "transcript"]` y la sesión no cambia

#### Scenario: Versión no admitida
- **WHEN** se envía un turno con `prompt_version=v3`
- **THEN** la respuesta es 422

#### Scenario: OpenAPI
- **WHEN** se consulta `/openapi.json`
- **THEN** contiene las tres operaciones de sesión, el cuerpo `multipart/form-data` del turno y las respuestas 200, 400, 404, 409, 413, 415, 422 y 502 del turno

### Requirement: Mensajes enviados al LLM en un turno
En cada turno, el sistema SHALL enviar al LLM el system prompt de `v4` regenerado con el `project_metadata` actual de la sesión (capability `cag-prompt`) y, como mensajes, los de `to_messages()` del historial seguidos del mensaje `user` del turno actual con el texto completo de sus adjuntos. El número de mensajes MUST ser `2 × min(turnos previos, MAX_CONVERSATION_TURNS) + 1`. El pipeline del turno MUST ser, en este orden: tomar el lock de la sesión, extraer los adjuntos, enmascarar los datos personales de los adjuntos, comprobar la transcripción y los adjuntos con los guardrails de regex, la moderación, renderizar el prompt, la llamada estructurada, el cálculo, el render, la evaluación y, solo con éxito, actualizar la sesión. El turno MUST reutilizar el mismo pipeline de estimación que `POST /api/v1/estimate` (salida estructurada, importes en el código, render y evaluación) sin duplicarlo.

#### Scenario: Segundo turno
- **GIVEN** una sesión con un turno con éxito
- **WHEN** se envía el segundo turno
- **THEN** el proveedor recibe 3 mensajes (`user`, `assistant` con el Markdown del turno 1, `user` actual) y un system que contiene el `project_name` del turno 1 dentro de `<project_metadata>`

#### Scenario: Ventana respetada
- **GIVEN** `MAX_CONVERSATION_TURNS = 6`
- **WHEN** se envían 8 turnos a la misma sesión
- **THEN** la llamada `k` recibe `2 × min(k − 1, 6) + 1` mensajes, nunca más de 13

### Requirement: Idioma de la respuesta en una sesión
El idioma de la respuesta de un turno SHALL ser el detectado en la transcripción del turno y, si la detección es ambigua, el idioma efectivo del último turno con éxito de la sesión. El idioma MUST NOT detectarse sobre el texto de los adjuntos. Tras cada turno con éxito, el idioma de la sesión MUST pasar a ser el `language` del resultado.

#### Scenario: Seguimiento breve
- **GIVEN** una sesión cuyo último turno fue en español y una transcripción sin idioma detectable ("OK, Stripe")
- **WHEN** se envía el turno
- **THEN** el system prompt termina con la línea que nombra el español

#### Scenario: Adjunto en otro idioma
- **GIVEN** una transcripción en español y un PDF en inglés
- **WHEN** se envía el turno
- **THEN** el idioma pedido en el system prompt es el español
