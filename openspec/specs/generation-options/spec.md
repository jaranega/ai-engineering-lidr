# generation-options Specification

## Purpose
Permitir ajustar por petición cómo se genera la estimación (número de ejemplos, uso de ejemplos, modelo, límite de salida y evaluación) con valores por defecto que reproducen el comportamiento original y validación que devuelve 422 ante opciones inválidas.
## Requirements
### Requirement: Parámetros de generación por petición
`POST /api/v1/estimate` SHALL aceptar, además de los campos obligatorios del formulario (`description`, `project_type`, `detail_level` y `output_format`), estos campos opcionales. Sus valores por defecto MUST reproducir el comportamiento sin parámetros (mismo modelo con fallback, `max_tokens` 2048, todos los ejemplos, con evaluación).

| Campo | Tipo | Por defecto | Reglas (422 si no se cumplen) |
|---|---|---|---|
| `num_examples` | `integer` | número de ejemplos (3) | `0..3` |
| `use_examples` | `boolean` | `true` | `false` equivale a `num_examples = 0` |
| `model` | `string \| null` | `null` (modelo primario con fallback) | 1..100 caracteres; modelo de OpenAI o Anthropic con API key configurada |
| `max_tokens` | `integer` | `2048` | `1..16384` |
| `evaluate` | `boolean` | `true` | — |

Toda la validación MUST ocurrir antes de cualquier llamada al LLM. Los campos retirados `preprocessing`, `example_format` y `thinking_budget` MUST NOT aceptarse con efecto: un body que los incluya se procesa como si no estuvieran (Pydantic ignora los campos desconocidos). La app Streamlit MUST NOT exponer estos parámetros y usa siempre los valores por defecto.

#### Scenario: Petición sin parámetros
- **GIVEN** un body solo con `description`, `project_type`, `detail_level` y `output_format`
- **WHEN** se hace POST a `/api/v1/estimate`
- **THEN** el proveedor recibe el modelo primario con fallback, `max_tokens = 2048` y el system prompt con los 3 ejemplos

#### Scenario: Valor fuera de rango
- **WHEN** el body incluye `num_examples: 4` o `max_tokens: 0`
- **THEN** la respuesta es 422 indicando el campo y no se llama al LLM

#### Scenario: Sin ejemplos
- **WHEN** el body incluye `use_examples: false` (con cualquier `num_examples`) o `num_examples: 0`
- **THEN** el system prompt no contiene ningún ejemplo ni el bloque `<examples>`

#### Scenario: Número de ejemplos
- **WHEN** el body incluye `num_examples: 1`
- **THEN** el system prompt contiene solo el primer ejemplo de `ESTIMATION_EXAMPLES`

#### Scenario: Campos retirados
- **WHEN** el body incluye además `preprocessing: "two_phase"`, `example_format: "json"` o `thinking_budget: 2048`
- **THEN** la respuesta es 200, la llamada estructurada no incluye `thinking` y el system prompt es el mismo que sin esos campos

### Requirement: Sobrescritura del modelo por petición
Cuando la petición incluye `model`, el sistema SHALL resolver su proveedor sin llamar a la red y usar ese modelo **sin fallback**. Si el modelo no se reconoce, su proveedor no es OpenAI ni Anthropic o no hay API key configurada para su proveedor, la respuesta MUST ser 422 con un mensaje que nombre el campo `model` y no contenga valores de keys, sin llamar al LLM. El modelo y proveedor efectivos de la respuesta MUST ser los del modelo pedido, con `fallback_used = false`.

#### Scenario: Modelo de otro proveedor
- **GIVEN** `PRIMARY_MODEL=gpt-4o-mini` y `ANTHROPIC_API_KEY` configurada
- **WHEN** el body incluye `model: "claude-haiku-4-5"`
- **THEN** la estimación la genera `claude-haiku-4-5`, la respuesta tiene `model = "claude-haiku-4-5"`, `provider = "anthropic"` y `fallback_used = false`

#### Scenario: Modelo sin key
- **GIVEN** `ANTHROPIC_API_KEY` sin definir
- **WHEN** el body incluye `model: "claude-haiku-4-5"`
- **THEN** la respuesta es 422 con un error en `model` que menciona `ANTHROPIC_API_KEY` y no se llama al LLM

#### Scenario: Modelo no soportado
- **WHEN** el body incluye `model: "gemini-2.0-flash"`
- **THEN** la respuesta es 422 con un error en `model` que indica los proveedores admitidos

#### Scenario: Override sin fallback
- **GIVEN** un body con `model` válido y un modelo que falla en todos los intentos
- **WHEN** se hace POST a `/api/v1/estimate`
- **THEN** la respuesta es 502 genérico y no se llama al modelo de respaldo

### Requirement: Límite de salida por petición
`max_tokens` SHALL fijar el límite de tokens de salida de la llamada de estimación. `truncated` y el aviso de truncado MUST evaluarse respecto al límite efectivo de la llamada.

#### Scenario: Límite pequeño
- **GIVEN** un body con `max_tokens: 50` y un proveedor que corta por límite
- **WHEN** se hace POST a `/api/v1/estimate`
- **THEN** el proveedor recibe `max_tokens = 50` y la respuesta tiene `truncated = true`

