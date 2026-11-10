## MODIFIED Requirements

### Requirement: Parámetros de generación por petición
`POST /api/v1/estimate` SHALL aceptar, además de los campos obligatorios del formulario (`description`, `project_type`, `detail_level` y `output_format`), estos campos opcionales. Sus valores por defecto MUST reproducir el comportamiento sin parámetros (mismo modelo con fallback, `max_tokens` 2048, sin thinking, todos los ejemplos, con evaluación).

| Campo | Tipo | Por defecto | Reglas (422 si no se cumplen) |
|---|---|---|---|
| `num_examples` | `integer` | número de ejemplos (3) | `0..3` |
| `use_examples` | `boolean` | `true` | `false` equivale a `num_examples = 0` |
| `model` | `string \| null` | `null` (modelo primario con fallback) | 1..100 caracteres; modelo de OpenAI o Anthropic con API key configurada |
| `max_tokens` | `integer` | `2048` | `1..16384` |
| `thinking_budget` | `integer \| null` | `null` | `1024..16384` |
| `evaluate` | `boolean` | `true` | — |

Toda la validación MUST ocurrir antes de cualquier llamada al LLM. Los campos retirados `preprocessing` y `example_format` MUST NOT aceptarse con efecto: un body que los incluya se procesa como si no estuvieran (Pydantic ignora los campos desconocidos). La app Streamlit MUST NOT exponer estos parámetros y usa siempre los valores por defecto.

#### Scenario: Petición sin parámetros
- **GIVEN** un body solo con `description`, `project_type`, `detail_level` y `output_format`
- **WHEN** se hace POST a `/api/v1/estimate`
- **THEN** el proveedor recibe el modelo primario con fallback, `max_tokens = 2048`, sin thinking, y el system prompt con los 3 ejemplos

#### Scenario: Valor fuera de rango
- **WHEN** el body incluye `num_examples: 4`, `max_tokens: 0` o `thinking_budget: 500`
- **THEN** la respuesta es 422 indicando el campo y no se llama al LLM

#### Scenario: Sin ejemplos
- **WHEN** el body incluye `use_examples: false` (con cualquier `num_examples`) o `num_examples: 0`
- **THEN** el system prompt no contiene ningún ejemplo ni el bloque `<examples>`

#### Scenario: Número de ejemplos
- **WHEN** el body incluye `num_examples: 1`
- **THEN** el system prompt contiene solo el primer ejemplo de `ESTIMATION_EXAMPLES`

#### Scenario: Campos retirados
- **WHEN** el body incluye además `preprocessing: "two_phase"` o `example_format: "json"`
- **THEN** la respuesta es 200, se hace una única llamada al LLM y el system prompt es el mismo que sin esos campos

## REMOVED Requirements

### Requirement: Preprocesado de la transcripción
**Reason**: Las estrategias `inline_cleaning` y `two_phase` se diseñaron para transcripciones de reunión desordenadas de hasta 20 000 caracteres; con una descripción de 20 a 2000 caracteres escrita en un formulario tipado no aportan (decisión del usuario en S04).
**Migration**: No hay sustituto. La descripción llega al LLM tal cual dentro de `user.j2`; los requisitos se estructuran con los campos del formulario.
