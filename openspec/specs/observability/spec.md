# observability Specification

## Purpose
Logging estructurado del servicio y de la interfaz con structlog (consola en desarrollo, JSON en producción): eventos de cada llamada al LLM y de la caché, y garantía de que nunca se registran descripciones de proyectos, proyectos de referencia, mensajes, prompts, respuestas ni API keys.
## Requirements
### Requirement: Logging estructurado
El sistema SHALL emitir sus logs con structlog: en formato de consola legible cuando `APP_ENV` no es `production` y en JSON (un objeto por línea, con `timestamp` ISO 8601, `level` y `event`) cuando `APP_ENV=production`. El nivel mínimo MUST ser `LOG_LEVEL`. Los logs de la librería estándar (`logging`), incluidos los de la aplicación y los de terceros, MUST salir con el mismo formato.

#### Scenario: Producción en JSON
- **GIVEN** `APP_ENV=production`
- **WHEN** el servicio registra un evento
- **THEN** la línea de log es un JSON válido con los campos `timestamp`, `level` y `event`

#### Scenario: Desarrollo en consola
- **GIVEN** `APP_ENV=development`
- **WHEN** el servicio registra un evento
- **THEN** la línea de log es texto legible y no un JSON

### Requirement: Eventos de la llamada al LLM
Cada llamada al LLM, normal o en streaming, SHALL registrar:
- `llm_call_started`: modo (`generate` o `stream`) y modelo primario;
- al terminar, `llm_call_completed`: modelo y proveedor efectivos, `fallback_used`, `cache_hit`, tokens de entrada y salida, `cost_usd`, `latency_ms`, `truncated`, y `finish_reason` si el proveedor lo informa;
- si falla, `llm_call_failed`: tipo de error, status HTTP si existe y `latency_ms`.

Un acierto de caché MUST registrar `llm_call_completed` con `cache_hit = true`.

#### Scenario: Llamada correcta
- **GIVEN** un proveedor que responde con 1200 tokens de entrada y 300 de salida
- **WHEN** se genera una estimación
- **THEN** se registran `llm_call_started` y `llm_call_completed`, este último con el modelo efectivo, los tokens, el coste y la latencia

#### Scenario: Llamada fallida
- **WHEN** el primario y el respaldo fallan
- **THEN** se registra `llm_call_failed` con el tipo de error y la latencia

#### Scenario: Fallback registrado
- **GIVEN** un primario que falla y un respaldo que responde
- **WHEN** se genera una estimación
- **THEN** `llm_call_completed` indica el modelo del respaldo y `fallback_used = true`

### Requirement: Eventos de caché
El sistema SHALL registrar `cache_hit`, `cache_miss` y `cache_stored` (con un prefijo de la clave, nunca su contenido) y un warning `cache_unavailable` cuando Redis falla.

#### Scenario: Fallo y acierto
- **WHEN** se envía dos veces la misma petición con la caché disponible
- **THEN** se registran `cache_miss` y `cache_stored` en la primera, y `cache_hit` en la segunda

### Requirement: Datos que nunca se registran
Ningún log del sistema, de la aplicación o de las librerías de terceros con el nivel configurado, MUST contener el contenido de las descripciones de proyecto, de los proyectos de referencia, de los system prompts o mensajes renderizados, las respuestas del LLM ni las API keys. Los mensajes de error de los SDK MUST NOT registrarse literalmente (solo el tipo de error y el status). Los loggers de terceros que vuelcan peticiones a nivel DEBUG MUST quedar como mínimo en INFO, y los loggers propios de LiteLLM MUST quedar silenciados (sus mensajes de error incluyen el cuerpo de la respuesta del proveedor) y su modo de depuración desactivado.

#### Scenario: Log sin contenido de la petición
- **GIVEN** `LOG_LEVEL=DEBUG` y una descripción con el texto `DESCRIPCION-SECRETA-123`
- **WHEN** se genera una estimación, con acierto o fallo de caché
- **THEN** ningún log contiene `DESCRIPCION-SECRETA-123`

#### Scenario: Log sin proyectos de referencia
- **GIVEN** `LOG_LEVEL=DEBUG` y un proyecto de referencia cuyo nombre es `REFERENCIA-SECRETA-456`
- **WHEN** se genera una estimación
- **THEN** ningún log contiene `REFERENCIA-SECRETA-456`

#### Scenario: Log sin API keys
- **GIVEN** `OPENAI_API_KEY=sk-test-visible-value` y un proveedor que falla con un error cuyo mensaje contiene la key
- **WHEN** se registra el fallo
- **THEN** ningún log contiene `sk-test-visible-value`

### Requirement: Eventos de los guardrails y de la salida estructurada
El sistema SHALL registrar, sin contenido de la petición ni de la respuesta: `input_guardrail_blocked` (`reason`, `field`, `rule`), `moderation_checked` (`flagged`, número de textos, `latency_ms` y, si se marca, solo los nombres de las categorías), `moderation_failed` (`error_type`, `status_code`), `moderation_disabled` una vez al construir el moderador (`reason`: `config` o `no_openai_key`), `structured_output_retry` (`attempt`, número de errores, `error_locs` con las rutas de campo y el tipo de error, y `coherence_errors` con los mensajes fijos de los validadores de coherencia del schema; nunca el mensaje de Pydantic de un campo, que incluye valores), `structured_output_failed` (`reason`, `attempts`), `llm_call_started`/`llm_call_completed` con `mode = "structured"` y `attempts`, `cache_entry_invalid`, `estimation_out_of_scope` (`confidence_pct`) y `estimation_rendered` (`output_format`, `language`, `language_source` `detected` o `llm`).

#### Scenario: Reintento sin valores
- **GIVEN** una descripción con `DESCRIPCION-SECRETA-123` y un LLM que devuelve primero un objeto inválido que la repite
- **WHEN** se genera la estimación
- **THEN** se registra `structured_output_retry` y ningún log contiene `DESCRIPCION-SECRETA-123`

#### Scenario: Moderación sin contenido
- **GIVEN** una moderación que marca la descripción `TEXTO-MARCADO-789`
- **WHEN** se procesa la petición
- **THEN** se registran `moderation_checked` con `flagged = true` e `input_guardrail_blocked`, y ningún log contiene `TEXTO-MARCADO-789`

### Requirement: Eventos de sesión y de adjuntos
El sistema SHALL registrar, sin contenido: `session_created` (`session`, `sessions` con el total en el almacén), `session_evicted` (`session`, `turn_count`, `idle_s`), `session_not_found` (`session`), `session_busy` (`session`), `session_turn_completed` (`session`, `turn`, `history_messages` con los mensajes del historial enviados al LLM antes del nuevo `user`, incluidos los de las anclas, `history_turns`, `attachments` con el número de adjuntos, `out_of_scope`, `language`, `metadata_updated`, `metadata_fields` con los nombres de los campos con valor, `technologies` con su número, `anchors`, `summary_chars`, `tier`, `tier_rule`, y `constraints` y `rejected` con el número de elementos de `explicit_constraints` y `rejected_options` del metadata), `attachment_extracted` (`index`, `kind`, `size_bytes`, `chars`, `pages`), `attachment_rejected` (`index`, `reason` y, si se conocen, `kind`, `size_bytes` y `error_type`), `attachment_pii_masked` (`index` y los recuentos `email`, `phone` e `iban`, solo si hay alguno) y `estimation_requested` con `mode = "session"`, `attachments` e `history_messages`. El campo `session` MUST ser solo los 8 primeros caracteres del `session_id`, nunca el id completo. Durante un turno, todos los eventos (incluidos `llm_call_*`, `prompt_rendered`, `input_guardrail_blocked` y los de la memoria y el tier) MUST llevar el campo `session`. Ningún evento MUST contener la transcripción, el texto ni el nombre de los adjuntos, los valores del `project_metadata`, los mensajes del historial, el texto del resumen ni el `session_id` completo.

#### Scenario: Turno registrado
- **GIVEN** una sesión con un turno previo
- **WHEN** se completa un segundo turno con un adjunto
- **THEN** se registran `attachment_extracted` con `index = 0` y `session_turn_completed` con `turn = 2`, `history_messages = 2`, `attachments = 1`, `anchors = 0`, `summary_chars = 0`, `tier` y `tier_rule`, y `llm_call_completed` lleva el campo `session`

#### Scenario: Sin contenido en los logs
- **GIVEN** `LOG_LEVEL=DEBUG`, una transcripción con `TRANSCRIPCION-SECRETA-123` y un PDF llamado `NOMBRE-SECRETO.pdf` cuyo texto contiene `PDF-SECRETO-456`
- **WHEN** se completa el turno y se envía un segundo turno a la misma sesión
- **THEN** ningún log contiene `TRANSCRIPCION-SECRETA-123`, `NOMBRE-SECRETO`, `PDF-SECRETO-456` ni el `session_id` completo

#### Scenario: Adjunto rechazado
- **WHEN** se envía un PDF cifrado como segundo adjunto
- **THEN** se registra `attachment_rejected` con `index = 1` y `reason = "encrypted"`, sin el nombre del fichero

#### Scenario: Enmascarado registrado
- **WHEN** se envía un adjunto con dos emails
- **THEN** se registra `attachment_pii_masked` con `email = 2` y sin los emails

### Requirement: Eventos de la memoria y del tier
El sistema SHALL registrar, sin contenido: `anchor_detected` (`turn`, `rules` con los identificadores de las reglas, `source`), `anchor_demoted` (`turn`, `anchors`), `anchor_classifier_failed` (`turn`, `error_type`), `history_compressed` (`evicted`, `anchored`, `demoted`, `summarized`, `backlog`, `dropped`, `anchors`, `summary_updated`, `summary_chars`, `summary_sha256` con los 12 primeros caracteres hexadecimales y `latency_ms`), `summary_failed` (`error_type`, `backlog`), `summary_backlog_dropped` (`dropped`), `history_compression_failed` (`error_type`) y `tier_resolved` (`tier`, `rule`, `explicit` como booleano, `prompt_version`). Las llamadas al LLM de la memoria MUST registrar sus `llm_call_*` con `llm_purpose` (`conversation_summary` o `anchor_classifier`) mediante un contextvar, sin cambios en el proveedor. Ningún evento MUST contener el fragmento que casó una regla, el texto del resumen, el texto de las restricciones u opciones descartadas ni la transcripción.

#### Scenario: Compresión registrada
- **GIVEN** un compresor determinista y `MAX_CONVERSATION_TURNS = 2`
- **WHEN** se envía el tercer turno de una sesión
- **THEN** se registra `history_compressed` con `evicted = 1`, `summary_updated = true` y `summary_chars` mayor que 0

#### Scenario: Llamada de la memoria atribuida
- **WHEN** el resumidor llama al proveedor simulado
- **THEN** su `llm_call_completed` lleva `llm_purpose = "conversation_summary"`

#### Scenario: Memoria sin contenido en los logs
- **GIVEN** `LOG_LEVEL=DEBUG`, un resumidor falso que copia los turnos al resumen, una transcripción con `RESUMIDO-SECRETO-123` que sale de la ventana y una estimación con `explicit_constraints = ["RESTRICCION-SECRETA-456"]`
- **WHEN** se completan los turnos
- **THEN** ningún log contiene `RESUMIDO-SECRETO-123` ni `RESTRICCION-SECRETA-456`

#### Scenario: Tier registrado
- **WHEN** se resuelve el tier de un turno con un NDA
- **THEN** se registra `tier_resolved` con `tier = "executive"`, `rule = "nda_detected"` y `explicit = false`, sin el texto del turno

### Requirement: Eventos de la revisión Actor-Critic-Boss
Con `review=true`, el sistema SHALL registrar, con el identificador corto de la sesión en el contexto, estos eventos:
- **`critic_completed`**, por cada revisión con éxito, con `iteration`, `verdict`, `issues`, `critical`, `major`, `minor`, `categories` (lista ordenada sin repetidos), `confidence` y `latency_ms`.
- **`critic_failed`**, por cada fallo del Critic, con `iteration` y `error_type`.
- **`boss_iteration`**, por cada iteración, con `iteration`, `decision`, `verdict` (o `None`), `issues` y `draft_out_of_scope`.
- **`acb_completed`**, una vez por turno, con `final_decision`, `stop_reason`, `iterations`, `llm_calls`, `latency_ms`, `input_tokens` y `output_tokens` acumulados.

Además:
- Las llamadas del Critic MUST emitir sus eventos de proveedor (`llm_call_started` y `llm_call_completed`) con `llm_purpose = "critic"`.
- Los borradores del Actor MUST llevar `review_iteration` en el contexto.
- `session_turn_completed` MUST añadir `review` (bool) y `review_decision` (`final_decision` o `None`).

Ningún evento MUST contener la transcripción, el texto de los problemas (`description`, `suggested_fix`), el `field_path` ni la estimación.

#### Scenario: Eventos de una revisión con reintento
- **GIVEN** un Critic simulado que pide un reintento y después acepta
- **WHEN** se envía un turno con `review=true`
- **THEN** se registran dos `critic_completed`, dos `boss_iteration` (`iterate` y `accept`) y un `acb_completed` con `iterations = 2`, `llm_calls = 4` y `stop_reason = "accepted"`

#### Scenario: Sin texto del Critic en los logs
- **GIVEN** un Critic simulado cuya descripción de problema contiene `DESCRIPCION-SECRETA-123` y su `field_path` es `rows[7].hours`
- **WHEN** se completa un turno con `review=true`
- **THEN** ningún evento registrado contiene `DESCRIPCION-SECRETA-123` ni `rows[7]`

#### Scenario: Turno sin revisión
- **WHEN** se completa un turno con `review=false`
- **THEN** no se registra ningún evento `critic_*`, `boss_iteration` ni `acb_completed`, y `session_turn_completed` tiene `review = false`

