## MODIFIED Requirements

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

## ADDED Requirements

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
