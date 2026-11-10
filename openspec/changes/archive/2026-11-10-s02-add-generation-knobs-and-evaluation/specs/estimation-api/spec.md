## MODIFIED Requirements

### Requirement: Endpoint de estimación
El sistema SHALL exponer `POST /api/v1/estimate`, que recibe una transcripción y, opcionalmente, parámetros de generación, y devuelve la estimación generada por el LLM.

Request (`application/json`, schema `EstimationRequest`):
| Campo | Tipo | Reglas |
|---|---|---|
| `transcription` | `string` | obligatorio, no vacío tras eliminar espacios, máximo 20 000 caracteres |
| `preprocessing`, `example_format`, `num_examples`, `use_examples`, `model`, `max_tokens`, `thinking_budget`, `evaluate` | ver capability `generation-options` | opcionales |

Response `200` (`application/json`, schema `EstimationResponse`):
| Campo | Tipo |
|---|---|
| `estimation` | `string` (Markdown) |
| `model` | `string` (modelo efectivo de la llamada de estimación: el que respondió, primario, respaldo o el pedido en `model`) |
| `provider` | `string` (proveedor del modelo efectivo: `openai` o `anthropic`) |
| `input_tokens` | `integer` (llamada de estimación) |
| `output_tokens` | `integer` (llamada de estimación) |
| `cost_usd` | `number \| null` (llamada de estimación) |
| `truncated` | `boolean` (`true` si la estimación se cortó por el límite de tokens) |
| `fallback_used` | `boolean` (`true` si respondió el modelo de respaldo) |
| `cache_hit` | `boolean` (`true` si la estimación vino de la caché) |
| `latency_ms` | `integer` (tiempo total en el servidor desde que se recibe la petición hasta que se tiene la respuesta, incluidas la fase previa y la evaluación, en milisegundos) |
| `preprocessing` | `"none" \| "inline_cleaning" \| "two_phase"` (estrategia aplicada) |
| `extracted_requirements` | `string \| null` (requisitos extraídos; solo con `two_phase`) |
| `preprocessing_input_tokens` | `integer` (fase previa; `0` sin `two_phase`) |
| `preprocessing_output_tokens` | `integer` (fase previa; `0` sin `two_phase`) |
| `preprocessing_cost_usd` | `number \| null` (fase previa; `null` sin `two_phase` o con modelo sin precio) |
| `validation` | objeto de la capability `estimation-evaluation` \| `null` (si `evaluate = false`) |

El coste de la fase previa MUST NOT sumarse a `cost_usd`.

#### Scenario: Estimación correcta
- **GIVEN** un modelo primario disponible y la caché vacía
- **WHEN** se hace POST a `/api/v1/estimate` con `{"transcription": "<texto de reunión>"}`
- **THEN** la respuesta es 200 con todos los campos del contrato, `model`/`provider` son los del modelo primario, `truncated`, `fallback_used` y `cache_hit` son `false`, `latency_ms` es mayor o igual que 0, `preprocessing = "none"`, `extracted_requirements` es `null`, los tokens de la fase previa son 0 y `validation` no es `null`

#### Scenario: Estimación servida por el respaldo
- **GIVEN** un modelo primario que falla y un respaldo disponible
- **WHEN** se hace POST a `/api/v1/estimate` con una transcripción válida
- **THEN** la respuesta es 200 con `fallback_used = true` y el `model`/`provider` del respaldo

#### Scenario: Estimación desde la caché
- **GIVEN** que la misma transcripción ya se estimó con las mismas opciones y la caché está disponible
- **WHEN** se hace POST a `/api/v1/estimate` con esa transcripción
- **THEN** la respuesta es 200 con el mismo `estimation`, `cache_hit = true` y `validation` calculada sobre ese texto, sin llamar al LLM

#### Scenario: Estimación en dos fases
- **GIVEN** un proveedor que informa 800 tokens de entrada y 200 de salida en la extracción
- **WHEN** se hace POST con `preprocessing: "two_phase"`
- **THEN** la respuesta tiene `extracted_requirements` con el texto extraído, `preprocessing_input_tokens = 800`, `preprocessing_output_tokens = 200`, `preprocessing_cost_usd` calculado con la tabla de precios, y `cost_usd` solo de la llamada de estimación

#### Scenario: Estimación truncada
- **GIVEN** un proveedor que corta la generación por el límite de tokens
- **WHEN** se hace POST a `/api/v1/estimate` con una transcripción válida
- **THEN** la respuesta es 200 con el texto recibido, `truncated = true` y `validation.finish_ok = false`

#### Scenario: Transcripción ausente o vacía
- **WHEN** el body no incluye `transcription` o esta solo contiene espacios
- **THEN** la respuesta es 422 y no se llama al LLM

#### Scenario: Transcripción demasiado larga
- **WHEN** `transcription` supera los 20 000 caracteres
- **THEN** la respuesta es 422 y no se llama al LLM

#### Scenario: Parámetro inválido
- **WHEN** un parámetro opcional no cumple sus reglas (incluido un `model` sin key configurada)
- **THEN** la respuesta es 422 con el campo en `loc` y no se llama al LLM

#### Scenario: Fallo del proveedor
- **WHEN** el primario y el respaldo fallan, o falla la fase previa de `two_phase`
- **THEN** la respuesta es 502 con un `detail` genérico, sin traza ni datos sensibles

#### Scenario: Error 502 documentado
- **WHEN** se consulta `/openapi.json`
- **THEN** la operación `POST /api/v1/estimate` documenta las respuestas 200, 422 y 502 y los campos opcionales del request con sus límites
