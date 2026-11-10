# observability Specification

## Purpose
Logging estructurado del servicio y de la interfaz con structlog (consola en desarrollo, JSON en producción): eventos de cada llamada al LLM y de la caché, y garantía de que nunca se registran transcripciones, mensajes, prompts, respuestas ni API keys.
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
Ningún log del sistema, de la aplicación o de las librerías de terceros con el nivel configurado, MUST contener el contenido de las transcripciones, los mensajes de la conversación, los system prompts, las respuestas del LLM ni las API keys. Los mensajes de error de los SDK MUST NOT registrarse literalmente (solo el tipo de error y el status). Los loggers de terceros que vuelcan peticiones a nivel DEBUG MUST quedar como mínimo en INFO, y los loggers propios de LiteLLM MUST quedar silenciados (sus mensajes de error incluyen el cuerpo de la respuesta del proveedor) y su modo de depuración desactivado.

#### Scenario: Log sin contenido de la petición
- **GIVEN** `LOG_LEVEL=DEBUG` y una transcripción con el texto `TRANSCRIPCION-SECRETA-123`
- **WHEN** se genera una estimación, con acierto o fallo de caché
- **THEN** ningún log contiene `TRANSCRIPCION-SECRETA-123`

#### Scenario: Log sin API keys
- **GIVEN** `OPENAI_API_KEY=sk-test-visible-value` y un proveedor que falla con un error cuyo mensaje contiene la key
- **WHEN** se registra el fallo
- **THEN** ningún log contiene `sk-test-visible-value`

### Requirement: Eventos del streaming por HTTP
El endpoint SSE SHALL registrar `estimate_stream_cancelled` (con el número de fragmentos enviados) cuando el cliente se desconecta antes del final y `estimate_stream_failed` (solo con el tipo de error) cuando una excepción inesperada que no es del proveedor corta la generación. La app Streamlit SHALL registrar `api_unreachable` (tipo de error) cuando no puede conectar con la API y `api_stream_failed` (motivo: estado HTTP distinto de 200, evento `error`, stream sin evento terminal o error de transporte a mitad). Ninguno de estos eventos MUST incluir el contenido de los mensajes, de la respuesta ni mensajes de error literales.

#### Scenario: Cancelación registrada
- **GIVEN** un stream SSE que ha entregado dos fragmentos
- **WHEN** el cliente se desconecta
- **THEN** se registra `estimate_stream_cancelled` con `fragments = 2`

#### Scenario: API inaccesible desde la UI
- **GIVEN** que la API no acepta conexiones
- **WHEN** la app Streamlit intenta enviar un mensaje
- **THEN** se registra `api_unreachable` con el tipo de error y sin el texto del mensaje

