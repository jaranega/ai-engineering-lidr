## ADDED Requirements

### Requirement: Parámetros de generación por petición
`POST /api/v1/estimate` SHALL aceptar, además de `transcription`, estos campos opcionales. Sus valores por defecto MUST reproducir el comportamiento sin parámetros (mismo modelo con fallback, `max_tokens` 2048, sin thinking, todos los ejemplos en Markdown, sin preprocesado, con evaluación).

| Campo | Tipo | Por defecto | Reglas (422 si no se cumplen) |
|---|---|---|---|
| `preprocessing` | `"none" \| "inline_cleaning" \| "two_phase"` | `"none"` | uno de los valores |
| `example_format` | `"markdown" \| "json" \| "narrative"` | `"markdown"` | uno de los valores |
| `num_examples` | `integer` | número de ejemplos (3) | `0..3` |
| `use_examples` | `boolean` | `true` | `false` equivale a `num_examples = 0` |
| `model` | `string \| null` | `null` (modelo primario con fallback) | 1..100 caracteres; modelo de OpenAI o Anthropic con API key configurada |
| `max_tokens` | `integer` | `2048` | `1..16384` |
| `thinking_budget` | `integer \| null` | `null` | `1024..16384` |
| `evaluate` | `boolean` | `true` | — |

Toda la validación MUST ocurrir antes de cualquier llamada al LLM. El endpoint SSE y la app Streamlit MUST NOT exponer estos parámetros y usan siempre los valores por defecto.

#### Scenario: Petición sin parámetros
- **GIVEN** un body solo con `transcription`
- **WHEN** se hace POST a `/api/v1/estimate`
- **THEN** el proveedor recibe el modelo primario con fallback, `max_tokens = 2048`, sin thinking, y el system prompt con los 3 ejemplos en Markdown

#### Scenario: Valor fuera de rango
- **WHEN** el body incluye `num_examples: 4`, `max_tokens: 0`, `thinking_budget: 500` o `preprocessing: "otro"`
- **THEN** la respuesta es 422 indicando el campo y no se llama al LLM

#### Scenario: Sin ejemplos
- **WHEN** el body incluye `use_examples: false` (con cualquier `num_examples`) o `num_examples: 0`
- **THEN** el system prompt no contiene ningún ejemplo ni el bloque `<examples>`

#### Scenario: Número de ejemplos
- **WHEN** el body incluye `num_examples: 1`
- **THEN** el system prompt contiene solo el primer ejemplo de `ESTIMATION_EXAMPLES`

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

### Requirement: Razonamiento extendido
Con `thinking_budget`, el sistema SHALL activar el razonamiento extendido solo si el modelo destino (el pedido en `model` o, si no hay, el primario) es de Anthropic: MUST enviar `thinking` con ese presupuesto y un límite de salida efectivo de `max_tokens + thinking_budget`. Si el modelo destino es de OpenAI, MUST ignorar el presupuesto, no enviar `thinking` y registrar el warning `thinking_budget_ignored` (proveedor y modelo), sin cambiar la respuesta HTTP. Si el primario es de Anthropic y el respaldo de OpenAI, un fallback MUST responder sin thinking en lugar de fallar por el parámetro.

#### Scenario: Thinking con Anthropic
- **GIVEN** `PRIMARY_MODEL=claude-haiku-4-5`
- **WHEN** el body incluye `thinking_budget: 2048` y `max_tokens: 2048`
- **THEN** la llamada se hace con `thinking = {"type": "enabled", "budget_tokens": 2048}` y un límite de salida de 4096

#### Scenario: Thinking ignorado en OpenAI
- **GIVEN** `PRIMARY_MODEL=gpt-4o-mini`
- **WHEN** el body incluye `thinking_budget: 2048`
- **THEN** la llamada no incluye `thinking`, la respuesta es 200 y el log contiene `thinking_budget_ignored`

#### Scenario: Fallback a OpenAI con thinking
- **GIVEN** `PRIMARY_MODEL=claude-haiku-4-5`, `FALLBACK_MODEL=gpt-4o-mini`, un primario que falla y `thinking_budget: 2048`
- **WHEN** se hace POST a `/api/v1/estimate`
- **THEN** responde el respaldo sin thinking y la respuesta tiene `fallback_used = true`

### Requirement: Preprocesado de la transcripción
`preprocessing` SHALL elegir la estrategia de entrada:
- `none`: comportamiento por defecto;
- `inline_cleaning`: el system prompt incluye, justo después del rol, un bloque que indica ignorar la charla irrelevante, explicitar los requisitos implícitos, quedarse con lo último ante contradicciones e interpretar la jerga;
- `two_phase`: una primera llamada extrae los requisitos de la transcripción (límite de 1500 tokens de salida, en el idioma de la transcripción, sin estimar) y la estimación se genera enviando los requisitos extraídos como mensaje `user`.

Con `two_phase`, el prompt de extracción MUST estar en inglés, abrir y cerrar con la regla de idioma y usar los títulos de sección del idioma de la transcripción; y en la llamada de estimación la regla de idioma que abre el system prompt y la que cierra las reglas MUST referirse a la lista de requisitos del mensaje `user` en lugar de a la transcripción. El system prompt por defecto (sin `two_phase`) no cambia. (Medido con gpt-4o-mini: con el prompt de extracción en español y la regla referida a la transcripción, las estimaciones de transcripciones en inglés salían en español.)

Con `two_phase`, un fallo o una extracción vacía en la primera llamada MUST producir 502 genérico; una extracción truncada MUST continuar con el texto recibido y registrar un warning.

#### Scenario: Limpieza en línea
- **WHEN** el body incluye `preprocessing: "inline_cleaning"`
- **THEN** el system prompt contiene el bloque de limpieza después del rol y antes de las instrucciones de formato, y la conversación es la transcripción sin modificar

#### Scenario: Dos fases
- **GIVEN** un proveedor que devuelve `R` en la extracción
- **WHEN** el body incluye `preprocessing: "two_phase"`
- **THEN** se hacen dos llamadas; la segunda recibe `R` como único mensaje `user`; la respuesta incluye `extracted_requirements = R`

#### Scenario: Idioma en dos fases
- **WHEN** se construyen los prompts de `two_phase`
- **THEN** el prompt de extracción está en inglés y empieza y termina por la regla de idioma, el system prompt de la estimación difiere del de una sola fase solo en las dos reglas de idioma (que hablan de la lista de requisitos) y el system prompt de una sola fase no cambia

#### Scenario: Fallo en la extracción
- **GIVEN** un proveedor que falla en la primera llamada
- **WHEN** el body incluye `preprocessing: "two_phase"`
- **THEN** la respuesta es 502 genérico y no se hace la llamada de estimación

#### Scenario: Extracción vacía
- **GIVEN** un proveedor que devuelve texto en blanco en la extracción
- **WHEN** el body incluye `preprocessing: "two_phase"`
- **THEN** la respuesta es 502 genérico
