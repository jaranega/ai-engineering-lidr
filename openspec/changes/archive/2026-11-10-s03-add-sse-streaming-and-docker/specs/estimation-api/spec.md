## ADDED Requirements

### Requirement: Contexto CAG de solo lectura
El sistema SHALL exponer `GET /api/v1/context`, que devuelve el contexto que usará una petición de estimación en streaming, sin llamar al LLM.

Query:
| Parámetro | Tipo | Reglas |
|---|---|---|
| `message_count` | `integer` | opcional, por defecto `1`, mínimo `1` |

Response `200` (`application/json`, schema `ContextResponse`):
| Campo | Tipo |
|---|---|
| `system_prompt` | `string` (el system prompt de una petición con `message_count` mensajes: el del endpoint con 1; el que incluye la sección de conversación con más) |
| `examples` | `array` de `{meeting_summary: string, estimation: string}` (los ejemplos CAG, en orden) |
| `max_message_chars` | `integer` (límite de caracteres por mensaje, el mismo que valida la API: 20 000) |

#### Scenario: Primer turno
- **WHEN** se hace GET a `/api/v1/context`
- **THEN** la respuesta es 200, `system_prompt` es igual al de `POST /api/v1/estimate`, `examples` contiene todos los ejemplos CAG y `max_message_chars` es 20000

#### Scenario: Turno de ajuste
- **WHEN** se hace GET a `/api/v1/context?message_count=3`
- **THEN** `system_prompt` es el que incluye la sección de conversación

#### Scenario: Parámetro inválido
- **WHEN** se hace GET a `/api/v1/context?message_count=0`
- **THEN** la respuesta es 422

#### Scenario: Sin llamada al LLM
- **WHEN** se hace GET a `/api/v1/context`
- **THEN** no se llama al proveedor LLM

#### Scenario: Documentado en OpenAPI
- **WHEN** se consulta `/openapi.json`
- **THEN** incluye la operación `GET /api/v1/context` con el schema `ContextResponse`
