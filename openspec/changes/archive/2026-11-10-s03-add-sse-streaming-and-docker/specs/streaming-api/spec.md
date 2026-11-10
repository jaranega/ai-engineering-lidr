## ADDED Requirements

### Requirement: Endpoint de estimación en streaming
El sistema SHALL exponer `POST /api/v1/estimate/stream`, que recibe la conversación completa y devuelve la respuesta del LLM como Server-Sent Events (`Content-Type: text/event-stream`) a medida que se genera. El system prompt MUST ser el mismo que usa el chat para ese número de mensajes (`system_prompt_for`: el del endpoint con un único mensaje, el que incluye la sección de conversación a partir del segundo turno). El endpoint MUST NOT aceptar parámetros de generación por petición.

Request (`application/json`, schema `StreamEstimationRequest`):
| Campo | Tipo | Reglas |
|---|---|---|
| `messages` | `array` de `{role: "user" \| "assistant", content: string}` | obligatorio y no vacío; el primer y el último mensaje son `user`; los roles alternan estrictamente; cada `content`, tras eliminar espacios iniciales y finales, tiene entre 1 y 20 000 caracteres (el mismo límite que `transcription`) |

#### Scenario: Primer turno
- **GIVEN** un proveedor que entrega la respuesta en tres fragmentos
- **WHEN** se hace POST a `/api/v1/estimate/stream` con `{"messages": [{"role": "user", "content": "<transcripción>"}]}`
- **THEN** la respuesta es 200 `text/event-stream`, contiene tres eventos `token` en orden seguidos de un evento `metrics`, y el proveedor recibe un único mensaje `user` con el system prompt del endpoint

#### Scenario: Turno de ajuste
- **GIVEN** una conversación `user`, `assistant`, `user`
- **WHEN** se envía al endpoint
- **THEN** el proveedor recibe los tres mensajes en orden con el system prompt que incluye la sección de conversación

#### Scenario: Espacios alrededor de los mensajes
- **WHEN** un mensaje se envía con contenido `"  T  \n"`
- **THEN** el proveedor recibe `"T"`

#### Scenario: Conversación inválida
- **WHEN** `messages` está vacío, empieza o termina con `assistant`, tiene dos mensajes seguidos con el mismo rol, o algún `content` queda vacío tras recortarlo
- **THEN** la respuesta es 422 (JSON, sin abrir el stream) y no se llama al LLM

#### Scenario: Mensaje demasiado largo
- **WHEN** algún `content` supera los 20 000 caracteres tras recortarlo
- **THEN** la respuesta es 422 y no se llama al LLM

#### Scenario: Sin límite de mensajes
- **GIVEN** una conversación válida de 21 mensajes
- **WHEN** se envía al endpoint
- **THEN** se procesa con normalidad

#### Scenario: Documentado en OpenAPI
- **WHEN** se consulta `/openapi.json`
- **THEN** incluye la operación `POST /api/v1/estimate/stream` con el schema `StreamEstimationRequest`

### Requirement: Formato de los eventos
El stream SHALL emitir únicamente estos eventos, cada uno con `data` en JSON en una sola línea:

| Evento | `data` | Cuándo |
|---|---|---|
| `token` | string JSON con el fragmento de texto | por cada fragmento no vacío, en orden |
| `metrics` | objeto `EstimationMetrics` | una vez, al terminar correctamente |
| `error` | `{"detail": "LLM provider error"}` | una vez, si la generación falla |

`EstimationMetrics` MUST tener exactamente los campos de `EstimationResponse` salvo `estimation`: `model`, `provider`, `input_tokens`, `output_tokens`, `cost_usd`, `truncated`, `fallback_used`, `cache_hit` y `latency_ms`, con los mismos tipos. `metrics` y `error` MUST ser terminales y excluyentes; no hay evento `done`. El servidor MAY enviar comentarios SSE de keep-alive (`: ping`), que el cliente MUST ignorar.

#### Scenario: Fragmento con saltos de línea
- **GIVEN** un proveedor que entrega el fragmento `"## Título\n\n| A | B |"`
- **WHEN** se consume el stream
- **THEN** el evento `token` lleva ese fragmento como string JSON en una sola línea `data:` y, al decodificarlo, se obtiene el texto original exacto

#### Scenario: Payload de métricas
- **GIVEN** un resultado con 1200 tokens de entrada, 300 de salida, servido por el respaldo y desde la caché
- **WHEN** termina el stream
- **THEN** el evento `metrics` contiene esos tokens, el modelo y proveedor del respaldo, `fallback_used = true`, `cache_hit = true`, `cost_usd`, `truncated` y `latency_ms`, y ningún campo `estimation`

#### Scenario: Respuesta truncada
- **GIVEN** un proveedor que corta la generación por el límite de tokens
- **WHEN** termina el stream
- **THEN** el evento `metrics` tiene `truncated = true`

#### Scenario: Acierto de caché
- **GIVEN** una respuesta guardada para la conversación
- **WHEN** se consume el stream
- **THEN** llega un único evento `token` con el texto guardado seguido de `metrics` con `cache_hit = true`

### Requirement: Errores durante el streaming
Cualquier fallo de la generación, antes del primer fragmento o a mitad del stream, SHALL comunicarse con código `200` y un evento `error` cuyo `data` es exactamente `{"detail": "LLM provider error"}`, sin trazas, mensajes del SDK ni datos sensibles, tras el cual el stream termina. Una excepción inesperada que no sea del proveedor MUST tratarse igual y registrarse solo con su tipo. Una respuesta sin texto MUST transmitirse tal cual (evento `metrics` sin eventos `token`); es el cliente quien la trata como error.

#### Scenario: Proveedor caído antes del primer fragmento
- **GIVEN** un proveedor que lanza `LLMProviderError` al empezar
- **WHEN** se hace POST al endpoint
- **THEN** la respuesta es 200 con un único evento `error` y ningún evento `token` ni `metrics`

#### Scenario: Fallo a mitad del stream
- **GIVEN** un proveedor que entrega dos fragmentos y después falla
- **WHEN** se consume el stream
- **THEN** llegan dos eventos `token` seguidos de un evento `error` genérico, y ningún `metrics`

#### Scenario: Error sin detalles
- **GIVEN** un proveedor que falla con un error cuyo mensaje contiene `sk-test-visible-value`
- **WHEN** se consume el stream
- **THEN** el cuerpo de la respuesta no contiene `sk-test-visible-value`

#### Scenario: Respuesta vacía
- **GIVEN** un proveedor cuyo stream termina sin ningún fragmento de texto
- **WHEN** se consume el stream
- **THEN** llega un evento `metrics` sin ningún evento `token`

### Requirement: Desconexión del cliente
Si el cliente cierra la conexión antes del final, el sistema SHALL dejar de consumir la respuesta del LLM, cerrar su stream y MUST NOT guardar nada en la caché.

#### Scenario: Cliente que se desconecta
- **GIVEN** un stream que ya ha entregado un fragmento
- **WHEN** el cliente cierra la conexión
- **THEN** el stream del proveedor se cierra, la caché no guarda ninguna entrada y se registra el evento `estimate_stream_cancelled`
