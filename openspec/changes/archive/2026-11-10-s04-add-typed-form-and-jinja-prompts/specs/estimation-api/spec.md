## MODIFIED Requirements

### Requirement: Endpoint de estimación
El sistema SHALL exponer `POST /api/v1/estimate`, que recibe la descripción tipada de un proyecto y, opcionalmente, parámetros de generación, renderiza el prompt con la versión pedida (capability `prompt-templates`) y devuelve la estimación generada por el LLM.

Query:
| Parámetro | Tipo | Reglas |
|---|---|---|
| `prompt_version` | `"v1" \| "v2"` | opcional, por defecto `"v1"`; otro valor da 422 con `loc = ["query", "prompt_version"]` |

Request (`application/json`, schema `EstimationRequest`):
| Campo | Tipo | Reglas |
|---|---|---|
| `description` | `string` | obligatorio; entre 20 y 2000 caracteres tras eliminar los espacios del principio y del final |
| `project_type` | `"mobile_app" \| "web_saas" \| "internal_tool" \| "data_pipeline"` | obligatorio |
| `detail_level` | `"summary" \| "medium" \| "detailed"` | obligatorio |
| `output_format` | `"phases_table" \| "line_items" \| "narrative"` | obligatorio |
| `reference_projects` | `array` de `ReferenceProject` \| `null` | opcional, como máximo 5 elementos; `[]` equivale a `null` |
| `num_examples`, `use_examples`, `model`, `max_tokens`, `thinking_budget`, `evaluate` | ver capability `generation-options` | opcionales |

`ReferenceProject` (no admite campos adicionales):
| Campo | Tipo | Reglas |
|---|---|---|
| `name` | `string` | obligatorio, 1–100 caracteres tras recortar espacios |
| `summary` | `string` | obligatorio, 1–500 caracteres tras recortar espacios |
| `actual_hours` | `integer \| null` | opcional, `1..20000` |
| `duration_weeks` | `integer \| null` | opcional, `1..104` |

Response `200` (`application/json`, schema `EstimationResponse`):
| Campo | Tipo |
|---|---|
| `text` | `string` (Markdown) |
| `prompt_version` | `string` (versión del prompt efectivamente renderizada) |
| `model` | `string` (modelo efectivo: el que respondió, primario, respaldo o el pedido en `model`) |
| `provider` | `string` (proveedor del modelo efectivo: `openai` o `anthropic`) |
| `input_tokens` | `integer` |
| `output_tokens` | `integer` |
| `cost_usd` | `number \| null` |
| `truncated` | `boolean` (`true` si la estimación se cortó por el límite de tokens) |
| `fallback_used` | `boolean` (`true` si respondió el modelo de respaldo) |
| `cache_hit` | `boolean` (`true` si la estimación vino de la caché) |
| `latency_ms` | `integer` (tiempo total en el servidor desde que se recibe la petición hasta que se tiene la respuesta, incluida la evaluación, en milisegundos) |
| `validation` | objeto de la capability `estimation-evaluation` \| `null` (si `evaluate = false`) |

El servicio MUST enviar al LLM el system prompt y el mensaje `user` renderizados como dos mensajes separados (`system` y `user`), sin concatenarlos.

#### Scenario: Estimación correcta
- **GIVEN** un modelo primario disponible y la caché vacía
- **WHEN** se hace POST a `/api/v1/estimate` con `{"description": "<descripción de 20 a 2000 caracteres>", "project_type": "web_saas", "detail_level": "medium", "output_format": "line_items"}`
- **THEN** la respuesta es 200 con todos los campos del contrato, `prompt_version = "v1"`, `model`/`provider` son los del modelo primario, `truncated`, `fallback_used` y `cache_hit` son `false`, `latency_ms` es mayor o igual que 0 y `validation` no es `null`

#### Scenario: Mensajes separados
- **WHEN** se hace POST a `/api/v1/estimate` con un body válido
- **THEN** el proveedor recibe el system prompt renderizado como `system` y una conversación con un único mensaje `user` igual al `user` renderizado

#### Scenario: Versión del prompt
- **WHEN** se hace POST a `/api/v1/estimate?prompt_version=v2` con un body válido
- **THEN** la respuesta es 200 con `prompt_version = "v2"` y el system prompt enviado es el de `v2`

#### Scenario: Versión desconocida
- **WHEN** se hace POST a `/api/v1/estimate?prompt_version=v9`
- **THEN** la respuesta es 422 con `loc = ["query", "prompt_version"]` y no se llama al LLM

#### Scenario: Estimación servida por el respaldo
- **GIVEN** un modelo primario que falla y un respaldo disponible
- **WHEN** se hace POST a `/api/v1/estimate` con un body válido
- **THEN** la respuesta es 200 con `fallback_used = true` y el `model`/`provider` del respaldo

#### Scenario: Estimación desde la caché
- **GIVEN** que el mismo body con la misma versión ya se estimó y la caché está disponible
- **WHEN** se hace POST a `/api/v1/estimate` con ese body
- **THEN** la respuesta es 200 con el mismo `text`, `cache_hit = true`, el `prompt_version` de la petición y `validation` calculada sobre ese texto, sin llamar al LLM

#### Scenario: Estimación truncada
- **GIVEN** un proveedor que corta la generación por el límite de tokens
- **WHEN** se hace POST a `/api/v1/estimate` con un body válido
- **THEN** la respuesta es 200 con el texto recibido, `truncated = true` y `validation.finish_ok = false`

#### Scenario: Descripción fuera de límites
- **WHEN** el body no incluye `description`, o esta tiene menos de 20 caracteres (o solo espacios) o más de 2000 tras recortar
- **THEN** la respuesta es 422 y no se llama al LLM

#### Scenario: Enum inválido o ausente
- **WHEN** falta `project_type`, `detail_level` u `output_format`, o su valor no es uno de los admitidos (por ejemplo `"MOBILE_APP"`)
- **THEN** la respuesta es 422 con el campo en `loc` y no se llama al LLM

#### Scenario: Proyectos de referencia inválidos
- **WHEN** `reference_projects` tiene más de 5 elementos, o un elemento sin `name`, con un `summary` de más de 500 caracteres o con un campo no definido
- **THEN** la respuesta es 422 y no se llama al LLM

#### Scenario: Parámetro inválido
- **WHEN** un parámetro opcional no cumple sus reglas (incluido un `model` sin key configurada)
- **THEN** la respuesta es 422 con el campo en `loc` y no se llama al LLM

#### Scenario: Fallo del proveedor
- **WHEN** el primario y el respaldo fallan
- **THEN** la respuesta es 502 con un `detail` genérico, sin traza ni datos sensibles

#### Scenario: Errores documentados
- **WHEN** se consulta `/openapi.json`
- **THEN** la operación `POST /api/v1/estimate` documenta las respuestas 200, 422 y 502, el query param `prompt_version` con sus valores, los enums con sus valores y los campos opcionales del request con sus límites

## REMOVED Requirements

### Requirement: Contexto CAG de solo lectura
**Reason**: Su único consumidor era el panel de contexto del chat, que se retira en S04; además el system prompt ya no depende del número de mensajes sino de los campos tipados de cada petición.
**Migration**: El render de cada petición queda identificado en el log `prompt_rendered` (versión y hash, capability `prompt-templates`); los templates se consultan en `app/prompts/estimation/<versión>/`.
