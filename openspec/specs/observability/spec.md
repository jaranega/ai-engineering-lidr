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
El sistema SHALL registrar, sin contenido: `session_created` (`session`, `sessions` con el total en el almacén), `session_evicted` (`session`, `turn_count`, `idle_s`), `session_not_found` (`session`), `session_busy` (`session`), `session_turn_completed` (`session`, `turn`, `history_messages` con los mensajes del historial enviados al LLM antes del nuevo `user`, `history_turns`, `attachments` con el número de adjuntos, `out_of_scope`, `language`, `metadata_updated`, `metadata_fields` con los nombres de los campos con valor y `technologies` con su número), `attachment_extracted` (`index`, `kind`, `size_bytes`, `chars`, `pages`), `attachment_rejected` (`index`, `reason` y, si se conocen, `kind`, `size_bytes` y `error_type`), `attachment_pii_masked` (`index` y los recuentos `email`, `phone` e `iban`, solo si hay alguno) y `estimation_requested` con `mode = "session"`, `attachments` e `history_messages`. El campo `session` MUST ser solo los 8 primeros caracteres del `session_id`, nunca el id completo. Durante un turno, todos los eventos (incluidos `llm_call_*`, `prompt_rendered` e `input_guardrail_blocked`) MUST llevar el campo `session`. Ningún evento MUST contener la transcripción, el texto ni el nombre de los adjuntos, los valores del `project_metadata`, los mensajes del historial ni el `session_id` completo.

#### Scenario: Turno registrado
- **GIVEN** una sesión con un turno previo
- **WHEN** se completa un segundo turno con un adjunto
- **THEN** se registran `attachment_extracted` con `index = 0` y `session_turn_completed` con `turn = 2`, `history_messages = 2` y `attachments = 1`, y `llm_call_completed` lleva el campo `session`

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

