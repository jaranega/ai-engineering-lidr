# conversation-sessions Specification

## Purpose
Mantener conversaciones de estimación en la memoria del proceso: sesiones con un historial de ventana deslizante (pares user/assistant), un `project_metadata` separado del historial y derivado de la salida estructurada de cada turno, y los endpoints para crear, consultar y conversar en una sesión.
## Requirements
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
`ConversationHistory(max_turns, max_anchors=0)` SHALL guardar turnos completos (`Turn(number, user, assistant)`, un par de mensajes `user` + `assistant`), nunca mensajes sueltos, con un máximo de `max_turns` turnos en la ventana (`MAX_CONVERSATION_TURNS`, por defecto 6). Tras cada `append`, cuya firma no cambia, MUST sacar de la ventana los turnos más antiguos mientras haya más de `max_turns` y guardarlos en una lista interna de expulsados, que `take_evicted()` MUST devolver (del más antiguo al más reciente) y vaciar. `Turn.number` MUST ser el ordinal del turno en la sesión (desde 1) y conservarse tras el recorte. Además de la ventana, el historial MUST guardar las anclas (`Anchor(turn, rules)`, como mucho `max_anchors`), el resumen acumulativo (`summary`, `None` o un texto no vacío de como mucho 2000 caracteres), `summarized_turns`, el backlog de turnos pendientes de resumir y `dropped_turns`, escritos solo por la política de compresión (capability `memory-compression`) con `set_anchors`, `set_summary(text, covered_turns=…)` y `set_backlog`, que MUST sustituir cada valor entero (tuplas y `str`) para que una lectura sin lock vea el estado anterior o el nuevo. `to_messages()` MUST devolver los pares de las anclas en orden cronológico seguidos de los pares de la ventana, del más antiguo al más reciente, alternando `user` y `assistant` y empezando por `user`, y MUST NOT incluir el system prompt ni el resumen. `len()` MUST seguir contando solo los turnos de la ventana. `max_turns < 1`, `max_anchors < 0` o un `user` o `assistant` vacío MUST producir `ValueError`.

#### Scenario: Ventana tras cada turno
- **GIVEN** un historial con `max_turns = 3`
- **WHEN** se añaden 5 turnos
- **THEN** tras cada `append` hay como máximo 3 turnos, y al final quedan los turnos 3, 4 y 5 con sus `number` originales

#### Scenario: Expulsados entregados una vez
- **GIVEN** un historial con `max_turns = 3` y 5 turnos añadidos
- **WHEN** se llama dos veces a `take_evicted()`
- **THEN** la primera devuelve los turnos 1 y 2 en orden y la segunda una lista vacía

#### Scenario: Mensajes alternos sin system
- **GIVEN** un historial con 2 turnos
- **WHEN** se llama a `to_messages()`
- **THEN** devuelve 4 mensajes con roles `user`, `assistant`, `user`, `assistant`, en orden, y ninguno con rol `system`

#### Scenario: Anclas antes de la ventana
- **GIVEN** un historial con un ancla (turno 2) y los turnos 5 y 6 en la ventana
- **WHEN** se llama a `to_messages()`
- **THEN** devuelve 6 mensajes alternos que empiezan por `user`: el par del turno 2 y después los de los turnos 5 y 6

#### Scenario: Ventana inválida
- **WHEN** se crea un historial con `max_turns = 0` o con `max_anchors = -1`
- **THEN** se produce `ValueError`

### Requirement: Contenido del turno guardado
Tras un turno con éxito, el sistema SHALL añadir al historial un par cuyo `user` es el mensaje `user` de la versión del prompt del turno (`v4`, `v5` o `v6`, con el mismo `user.j2` salvo el bloque `<critic_feedback>` de `v6`, que nunca se guarda) renderizado para ese turno con el texto de cada adjunto sustituido por el marcador `[…]` (el separador `--- attachment: <nombre> ---` se conserva) y cuyo `assistant` es el `text` en Markdown que recibió el usuario. Con revisión Actor-Critic-Boss, el par MUST guardarse una sola vez con el resultado final: los borradores intermedios y el feedback del Critic MUST NOT llegar al historial. El mismo `user` guardado MUST ser el que se conserva en un ancla y el que recibe el resumidor. Un turno que termina en error (400, 404, 409, 413, 415, 422 o 502) MUST NOT añadir ningún mensaje al historial ni modificar el `project_metadata`, el idioma de la sesión, `last_tier`, `last_review`, `turn_count`, las anclas ni el resumen, y MUST NOT disparar la compresión. Un turno con éxito fuera de alcance (`result.out_of_scope = true`) MUST añadirse al historial y contar en `turn_count`, pero MUST NOT modificar el `project_metadata`.

#### Scenario: Adjunto sustituido en el historial
- **GIVEN** un turno con un PDF cuyo texto contiene `MARCADOR-PDF`
- **WHEN** el turno termina con éxito y se envía el siguiente turno
- **THEN** el mensaje `user` del turno anterior que recibe el LLM contiene `--- attachment: spec.pdf ---` y `[…]` y no contiene `MARCADOR-PDF`

#### Scenario: Respuesta guardada
- **WHEN** un turno termina con éxito
- **THEN** el mensaje `assistant` guardado es idéntico al `text` de la respuesta de ese turno

#### Scenario: Turno revisado guardado una vez
- **GIVEN** un turno con `review=true` en el que el Critic pide un reintento y acepta el segundo borrador
- **WHEN** el turno termina con éxito
- **THEN** el historial gana exactamente un par, su `assistant` es el `text` del segundo borrador y su `user` no contiene `<critic_feedback>`

#### Scenario: Turno fallido
- **GIVEN** una sesión con un turno y un proveedor que falla en el segundo
- **WHEN** se envía el segundo turno
- **THEN** la respuesta es 502, la sesión conserva un turno, el mismo `project_metadata`, el mismo `last_tier`, el mismo `last_review` y `turn_count = 1`, y no se llama a la compresión

#### Scenario: Turno fuera de alcance
- **GIVEN** una sesión con `project_name` relleno y un LLM que devuelve `confidence_pct = 10`
- **WHEN** se envía el turno
- **THEN** la respuesta es 200, `turn_count` aumenta en 1, el turno está en el historial y el `project_metadata` no cambia

### Requirement: Metadata del proyecto
El sistema SHALL mantener por sesión un `ProjectMetadata` inmutable (Pydantic, `frozen`) con `project_name` (`str | null`), `assumed_team_size` (`int | null`), `mentioned_technologies` (lista de textos, vacía por defecto), `agreed_scope` (`str | null`), `explicit_constraints` (lista de textos, vacía por defecto) y `rejected_options` (lista de textos, vacía por defecto), en este orden, separado del historial y sustituido entero tras cada turno con éxito en alcance. `update_project_metadata(current, estimate)` en `app/sessions/metadata.py` MUST ser una función pura y determinista, sin llamar al LLM, que usa `merge_unique(current, new, limit)` (las actuales seguidas de las nuevas, en orden, sin duplicados sin distinguir mayúsculas tras recortar, colapsar los espacios y quitar la puntuación final (`.`, `;`, `:`), ganando la primera grafía, y, si pasan de `limit`, descartando las más antiguas; `merge_technologies` queda como alias) y aplica estas reglas sobre el `ComputedEstimate` del turno, en este orden: `project_name` = `title` (se sobrescribe); `assumed_team_size` = suma de `count` de `team` (un perfil a tiempo parcial cuenta como sus personas); `agreed_scope` = `summary`; `explicit_constraints` = `merge_unique(actuales, estimate.explicit_constraints, 15)`; `rejected_options` = `merge_unique(actuales, estimate.rejected_options, 20)` y después, como readopción, sin los nombres que están en `estimate.technologies` y no en `estimate.rejected_options` del mismo turno; y `mentioned_technologies` = `merge_unique(actuales, estimate.technologies, 30)` sin ningún nombre que esté en `rejected_options` (política de olvido 1). Las comparaciones entre listas MUST ser exactas por `casefold` tras esa misma normalización, nunca por subcadena. Las restricciones MUST acumularse y no retirarse en este change. Con `out_of_scope = true` MUST devolver el metadata actual sin cambios.

#### Scenario: Primer turno
- **GIVEN** un metadata vacío y una estimación con `title` "Clinic booking platform", equipo de 2 + 1 personas, `technologies` `["Twilio"]` y `summary` `S`
- **WHEN** se actualiza el metadata
- **THEN** `project_name = "Clinic booking platform"`, `assumed_team_size = 3`, `mentioned_technologies = ["Twilio"]`, `agreed_scope = S`, `explicit_constraints = []` y `rejected_options = []`

#### Scenario: Acumulación sin duplicados
- **GIVEN** un metadata con `mentioned_technologies = ["Twilio"]`
- **WHEN** se actualiza con una estimación con `technologies = ["Stripe", "twilio"]`
- **THEN** `mentioned_technologies = ["Twilio", "Stripe"]`

#### Scenario: Tope de tecnologías
- **GIVEN** un metadata con 30 tecnologías `T1`…`T30`
- **WHEN** se actualiza con una estimación con `technologies = ["Nueva"]`
- **THEN** hay 30 tecnologías, `T1` ya no está y `Nueva` es la última

#### Scenario: Restricciones acumuladas
- **GIVEN** un metadata con `explicit_constraints = ["NDA firmado"]`
- **WHEN** se actualiza con una estimación con `explicit_constraints = ["Presupuesto máximo de 40 000 €", "nda firmado"]`
- **THEN** `explicit_constraints = ["NDA firmado", "Presupuesto máximo de 40 000 €"]`

#### Scenario: Duplicados con puntuación final
- **GIVEN** un metadata con `explicit_constraints = ["Fixed budget of $45k"]`
- **WHEN** se actualiza con una estimación con `explicit_constraints = ["Fixed budget of $45k."]`
- **THEN** `explicit_constraints = ["Fixed budget of $45k"]`

#### Scenario: Tope de restricciones
- **GIVEN** un metadata con 15 restricciones `R1`…`R15`
- **WHEN** se actualiza con una estimación con `explicit_constraints = ["Nueva"]`
- **THEN** hay 15 restricciones, `R1` ya no está y `Nueva` es la última

#### Scenario: Política de olvido
- **GIVEN** un metadata con `mentioned_technologies = ["Firebase", "Stripe"]`
- **WHEN** se actualiza con una estimación con `technologies = ["Supabase", "Stripe"]` y `rejected_options = ["firebase"]`
- **THEN** `rejected_options = ["firebase"]` y `mentioned_technologies = ["Stripe", "Supabase"]`

#### Scenario: Sin retirar por subcadena
- **GIVEN** un metadata con `mentioned_technologies = ["Android"]`
- **WHEN** se actualiza con una estimación con `rejected_options = ["app Android"]`
- **THEN** `mentioned_technologies` sigue conteniendo `Android`

#### Scenario: Readopción
- **GIVEN** un metadata con `rejected_options = ["Firebase"]`
- **WHEN** se actualiza con una estimación con `technologies = ["Firebase"]` y `rejected_options = []`
- **THEN** `rejected_options = []` y `mentioned_technologies` contiene `Firebase`

#### Scenario: Descarte y adopción en el mismo turno
- **WHEN** se actualiza un metadata vacío con una estimación con `technologies = ["Firebase"]` y `rejected_options = ["Firebase"]`
- **THEN** `rejected_options = ["Firebase"]` y `mentioned_technologies` no contiene `Firebase`

#### Scenario: Fuera de alcance no actualiza
- **WHEN** se actualiza un metadata con una estimación con `out_of_scope = true`
- **THEN** el resultado es igual al metadata actual

### Requirement: Endpoints de sesión
El sistema SHALL exponer, con el prefijo `/api/v1` y la etiqueta `sessions` en OpenAPI: `POST /api/v1/sessions`, sin cuerpo, que crea una sesión vacía y responde **201** con `{"session_id": <uuid>}`; y `GET /api/v1/sessions/{session_id}`, que responde **200** con `SessionInfo` (`session_id`, `turn_count` con los turnos con éxito desde la creación, `history_turns` con los turnos que hay en la ventana, `max_turns`, `project_metadata`, `anchors_count` con el número de anclas, `summary_chars` con la longitud del resumen o 0 si no hay, `last_tier` y `last_tier_rule` con el tier y la regla del último turno con éxito o `null` si no hay ninguno, y `last_review` con `{final_decision, stop_reason, iterations_run}` de la revisión del último turno con éxito o `null` si ese turno no pidió revisión o no hay ninguno). La respuesta MUST NOT incluir el texto del resumen, el de las anclas ni el de los problemas del Critic. `session_id` MUST declararse como UUID en la ruta: un formato inválido MUST dar **422** sin consultar el almacén, y un UUID válido que no existe MUST dar **404** con `{"detail": {"reason": "session_not_found", "message": <frase fija>}}`. El router MUST NOT contener lógica de negocio y MUST depender del servicio de sesiones vía `Depends`; `Tier` y `TierRule` MUST importarse desde `app.schemas`.

#### Scenario: Crear una sesión
- **WHEN** se hace POST a `/api/v1/sessions`
- **THEN** la respuesta es 201 con un `session_id` que es un UUID v4

#### Scenario: Consultar una sesión nueva
- **GIVEN** una sesión recién creada
- **WHEN** se hace GET a `/api/v1/sessions/{session_id}`
- **THEN** la respuesta es 200 con `turn_count = 0`, `history_turns = 0`, `max_turns = 6`, un `project_metadata` sin datos, `anchors_count = 0`, `summary_chars = 0`, `last_tier = null`, `last_tier_rule = null` y `last_review = null`

#### Scenario: Memoria tras varios turnos
- **GIVEN** una sesión con un ancla, un resumen de 120 caracteres y un último turno resuelto como `executive` por `nda_detected`
- **WHEN** se hace GET a `/api/v1/sessions/{session_id}`
- **THEN** la respuesta tiene `anchors_count = 1`, `summary_chars = 120`, `last_tier = "executive"` y `last_tier_rule = "nda_detected"`, y no contiene el texto del resumen

#### Scenario: Última revisión
- **GIVEN** una sesión cuyo último turno usó `review=true` y terminó en `synthesize` por `budget_exhausted` tras 3 iteraciones
- **WHEN** se hace GET a `/api/v1/sessions/{session_id}`
- **THEN** `last_review = {"final_decision": "synthesize", "stop_reason": "budget_exhausted", "iterations_run": 3}`

#### Scenario: Turno sin revisión tras uno revisado
- **GIVEN** una sesión con un turno revisado seguido de un turno con `review=false`
- **WHEN** se hace GET a `/api/v1/sessions/{session_id}`
- **THEN** `last_review = null`

#### Scenario: Sesión inexistente
- **WHEN** se hace GET a `/api/v1/sessions/{uuid}` con un UUID v4 válido que no existe
- **THEN** la respuesta es 404 con `detail.reason = "session_not_found"`

#### Scenario: Identificador mal formado
- **WHEN** se hace GET a `/api/v1/sessions/no-es-un-uuid`
- **THEN** la respuesta es 422

### Requirement: Turno de estimación en una sesión
El sistema SHALL exponer `POST /api/v1/sessions/{session_id}/estimate`, que recibe `multipart/form-data` con `transcript` (obligatorio, de 1 a 20 000 caracteres y no vacío tras recortar los espacios), `project_type`, `detail_level` y `output_format` (obligatorios, con los valores de los enums de `app.domain`), `tier` (opcional, un valor de `Tier`; sin él, el tier se resuelve automáticamente, capability `audience-tier`), `review` (opcional, booleano, por defecto `false`; con `true` el turno pasa por la revisión Actor-Critic-Boss, capability `actor-critic-boss`) y una lista opcional `attachments` de ficheros (capability `attachments`), y el query param `prompt_version` (`"v4"`, `"v5"` o `"v6"`, por defecto `"v6"`). Con éxito MUST responder **200** con el contrato de `POST /api/v1/estimate` (`text`, `result`, `prompt_version`, métricas y `validation`) más `session_id`, `turn` (ordinal del turno, igual a `turn_count` tras él), `project_metadata` (ya actualizado), `tier` y `tier_rule` (los aplicados en el turno) y `acb` (la traza de la revisión, o `null` sin `review=true`). Los errores MUST ser: **404** `session_not_found`; **409** `{"detail": {"reason": "session_busy", "message"}}` si hay otro turno en curso en la misma sesión; **400** de los guardrails (capability `input-guardrails`); **413**, **415** y **422** de los adjuntos (capability `attachments`); **422** de validación de FastAPI (un `transcript` solo con espacios da un error en `["body", "transcript"]` sin el valor enviado, un `tier` fuera del enum da un error en `["body", "tier"]` y un `review` que no es booleano da un error en `["body", "review"]`); **422** en `["body", "tier"]` si se envía un `tier` con `prompt_version=v4`; **422** en `["body", "review"]` con el mensaje `"requires prompt_version v6"` si se envía `review=true` con `prompt_version` `v4` o `v5`, sin llamar al LLM; y **502** con el `detail` genérico del proveedor, como `/api/v1/estimate`. Un fallo de la compresión del historial o del Critic MUST NOT cambiar la respuesta. OpenAPI MUST documentar el cuerpo multipart con `tier` y `review`, los campos nuevos de la respuesta y todos esos códigos.

#### Scenario: Turno correcto
- **GIVEN** una sesión vacía y un proveedor simulado
- **WHEN** se hace POST multipart con `transcript`, `project_type`, `detail_level` y `output_format`
- **THEN** la respuesta es 200 con `text`, `result`, `prompt_version = "v6"`, `session_id` igual al de la ruta, `turn = 1`, el `project_metadata` actualizado, `tier`, `tier_rule` y `acb = null`, y el proveedor recibe una sola llamada estructurada

#### Scenario: Turno con revisión
- **GIVEN** un proveedor simulado cuyo Critic devuelve `accept`
- **WHEN** se envía un turno con `review=true`
- **THEN** la respuesta es 200 con `acb.final_decision = "accept"`, `acb.stop_reason = "accepted"` y `acb.iterations_run = 1`, y el proveedor recibe dos llamadas estructuradas (estimación y Critic)

#### Scenario: Revisión con una versión sin feedback
- **WHEN** se envía un turno con `review=true` y `prompt_version=v5`
- **THEN** la respuesta es 422 con un error en `["body", "review"]` y el mensaje `requires prompt_version v6`, no se llama al LLM y la sesión no cambia

#### Scenario: Tier explícito
- **WHEN** se envía un turno con `tier=developer`
- **THEN** la respuesta es 200 con `tier = "developer"` y `tier_rule = "explicit"`

#### Scenario: Tier con v4
- **WHEN** se envía un turno con `tier=executive` y `prompt_version=v4`
- **THEN** la respuesta es 422 con un error en `["body", "tier"]`, no se llama al LLM y la sesión no cambia

#### Scenario: Tier inválido
- **WHEN** se envía un turno con `tier=ceo`
- **THEN** la respuesta es 422 con un error en `["body", "tier"]`

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
- **THEN** contiene las tres operaciones de sesión, el cuerpo `multipart/form-data` del turno con `tier` y `review`, `prompt_version` con `v4`, `v5` y `v6`, `tier`, `tier_rule` y `acb` en la respuesta, `last_review` en `SessionInfo` y las respuestas 200, 400, 404, 409, 413, 415, 422 y 502 del turno

### Requirement: Mensajes enviados al LLM en un turno
En cada turno, el sistema SHALL enviar al LLM el system prompt de la versión pedida regenerado con el `project_metadata` actual de la sesión y, con `v5`, con el resumen de la conversación (si lo hay) y el tier del turno (capability `cag-prompt`); y, como mensajes, los de `to_messages()` del historial (anclas y después la ventana) seguidos del mensaje `user` del turno actual con el texto completo de sus adjuntos. El resumen MUST ir solo en el system, dentro de `<conversation_summary>`, nunca como mensaje. El número de mensajes MUST ser `2 × (anclas + min(turnos previos, MAX_CONVERSATION_TURNS)) + 1`. El pipeline del turno MUST ser, en este orden: tomar el lock de la sesión, resolver el tier (y rechazar un `tier` explícito con `v4`), extraer los adjuntos, enmascarar los datos personales de los adjuntos, comprobar la transcripción y los adjuntos con los guardrails de regex, la moderación, renderizar el prompt, la llamada estructurada, el cálculo, el render, la evaluación y, solo con éxito, actualizar la sesión y comprimir el historial. El turno MUST reutilizar el mismo pipeline de estimación que `POST /api/v1/estimate` (salida estructurada, importes en el código, render y evaluación) sin duplicarlo; `estimate_turn` MUST seguir sin estado y recibir el tier y el resumen como datos.

#### Scenario: Segundo turno
- **GIVEN** una sesión con un turno con éxito
- **WHEN** se envía el segundo turno
- **THEN** el proveedor recibe 3 mensajes (`user`, `assistant` con el Markdown del turno 1, `user` actual) y un system que contiene el `project_name` del turno 1 dentro de `<project_metadata>`

#### Scenario: Ventana respetada sin anclas
- **GIVEN** `MAX_CONVERSATION_TURNS = 6` y un compresor que no ancla ningún turno
- **WHEN** se envían 8 turnos a la misma sesión
- **THEN** la llamada `k` recibe `2 × min(k − 1, 6) + 1` mensajes, nunca más de 13, y el system de la llamada 8 contiene `<conversation_summary>`

#### Scenario: Ancla fuera de la ventana
- **GIVEN** `MAX_CONVERSATION_TURNS = 6` y un turno 2 con "Hemos firmado un NDA y el presupuesto está cerrado en 40 000 €"
- **WHEN** se envían 9 turnos a la misma sesión
- **THEN** la llamada 9 recibe `2 × (1 + 6) + 1 = 15` mensajes, el primer par es el turno 2 literal y el system contiene `<conversation_summary>` con el turno 1 (el único expulsado que no es ancla)

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

### Requirement: Compresión del historial tras el turno
Tras un turno con éxito, el servicio de sesiones SHALL, dentro del lock de la sesión y después de actualizar el historial, el metadata, el idioma, `last_tier` y `turn_count`, ejecutar la compresión del historial (capability `memory-compression`) con el detector, el resumidor y `max_anchors` de `get_memory_compression`, y el idioma de la sesión ya actualizado. La compresión es síncrona y MUST terminar antes de responder, de modo que `GET /api/v1/sessions/{id}` vea siempre el estado comprimido. Cualquier excepción de la compresión MUST capturarse, registrarse como `history_compression_failed` con `error_type` y no cambiar la respuesta del turno (200) ni el turno ya guardado. Una sesión con `prompt_version=v4` también se comprime, aunque `v4` no pinte el resumen.

#### Scenario: Resumidor que falla
- **GIVEN** un compresor cuyo `summarize` lanza una excepción
- **WHEN** se envía el turno 7 de una sesión con `MAX_CONVERSATION_TURNS = 6`
- **THEN** la respuesta es 200, `turn_count = 7` y el turno 1 queda en el backlog

#### Scenario: Detector que falla
- **GIVEN** un compresor cuyo `detect` lanza una excepción
- **WHEN** se envía un turno que expulsa otro de la ventana
- **THEN** la respuesta es 200 y se registra `history_compression_failed`

#### Scenario: Compresión dentro del lock
- **GIVEN** un compresor que registra si el lock de la sesión está tomado al ejecutarse
- **WHEN** se envía un turno que expulsa otro
- **THEN** el lock estaba tomado durante la compresión

### Requirement: Tier del turno en la sesión
El servicio de sesiones SHALL resolver el tier del turno dentro del lock y antes de renderizar el prompt, con `resolve_tier(transcripción, project_metadata actual, tier explícito)` (capability `audience-tier`), y pasarlo a `estimate_turn`. `Session` MUST guardar `last_tier: TierDecision | None` (`None` al crearla), que MUST actualizarse solo tras un turno con éxito.

#### Scenario: Tier en el render
- **GIVEN** una transcripción con "We signed an NDA"
- **WHEN** se envía el turno con `prompt_version=v5`
- **THEN** `render_session_prompt` recibe `tier = executive` y el system contiene `<audience>`

#### Scenario: Tier persistente por el metadata
- **GIVEN** un turno 2 cuya estimación devuelve `explicit_constraints = ["NDA firmado"]`
- **WHEN** se envían los turnos 3 a 9 sin mencionar el NDA
- **THEN** todos responden `tier = "executive"` y `tier_rule = "nda_detected"`

### Requirement: Pipeline del turno con revisión
Con `review=true`, el sistema SHALL ejecutar el turno con el mismo orden que sin revisión (lock, tier, adjuntos, enmascarado, guardrails de regex, moderación) y, en lugar de una única llamada estructurada, el bucle del Boss (capability `actor-critic-boss`): cada borrador del Actor usa el system y los mensajes del historial del turno, y el último mensaje `user` con el bloque `<critic_feedback>` a partir del segundo borrador. Los guardrails y la moderación MUST ejecutarse una sola vez por turno. El cálculo de importes, el render y la evaluación estructural MUST aplicarse al resultado final una sola vez para la respuesta (el Critic recibe cada borrador ya calculado). La actualización de la sesión (historial, `project_metadata`, idioma, `last_tier`, `last_review`, `turn_count`) y la compresión MUST hacerse una sola vez, tras el bucle, con el resultado final. `input_tokens`, `output_tokens` y `cost_usd` de la respuesta MUST sumar todas las llamadas estructuradas del turno (borradores y Critic), `cost_usd` MUST ser `null` si alguna no tiene precio conocido, `cache_hit` MUST ser `true` solo si todas fueron aciertos de caché, `model` y `provider` MUST ser los del borrador final y `latency_ms` MUST cubrir el turno completo. `estimate_turn` MUST seguir sin estado y recibir la configuración de la revisión como dato.

#### Scenario: Guardrails una vez
- **GIVEN** un Critic simulado que pide un reintento y después acepta
- **WHEN** se envía un turno con `review=true`
- **THEN** el moderador recibe una sola llamada y el proveedor recibe dos borradores y dos llamadas al Critic

#### Scenario: Métricas sumadas
- **GIVEN** un turno revisado con un borrador de 1000/200 tokens y un Critic de 400/100 tokens
- **WHEN** termina el turno
- **THEN** la respuesta tiene `input_tokens = 1400` y `output_tokens = 300`

#### Scenario: Feedback solo en el reintento
- **GIVEN** un Critic simulado que devuelve `needs_iteration` con un problema `major` y después `accept`
- **WHEN** se envía un turno con `review=true`
- **THEN** el último mensaje `user` del primer borrador no contiene `<critic_feedback>` y el del segundo sí, con la categoría y el `field_path` del problema

#### Scenario: Error del proveedor en el primer borrador
- **GIVEN** un proveedor que falla en la primera llamada
- **WHEN** se envía un turno con `review=true`
- **THEN** la respuesta es 502 y la sesión no cambia

