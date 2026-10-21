## ADDED Requirements

### Requirement: Interfaz mínima de proveedor
El sistema SHALL exponer una interfaz común de proveedor LLM que reciba el system prompt y el mensaje de usuario y devuelva un resultado con: `text`, `model`, `provider`, `input_tokens` y `output_tokens`. Habrá dos implementaciones: OpenAI (Chat Completions) y Anthropic (Messages, con el system prompt en el parámetro `system`).

#### Scenario: Llamada con OpenAI
- **GIVEN** `LLM_PROVIDER=openai`
- **WHEN** se solicita una generación
- **THEN** se llama al SDK de OpenAI con los mensajes `system` y `user`, y el resultado incluye el texto y los tokens que informa la API

#### Scenario: Llamada con Anthropic
- **GIVEN** `LLM_PROVIDER=anthropic`
- **WHEN** se solicita una generación
- **THEN** se llama al SDK de Anthropic con `system=<prompt>` y un mensaje `user`, y el resultado incluye el texto y los tokens que informa la API

### Requirement: Selección de proveedor por configuración
El sistema SHALL seleccionar la implementación del proveedor exclusivamente a partir de `LLM_PROVIDER`, sin condicionales de proveedor en los routers.

#### Scenario: Cambio de proveedor sin tocar código
- **WHEN** se cambia `LLM_PROVIDER` de `openai` a `anthropic` y se reinicia el servicio
- **THEN** las estimaciones se generan con Anthropic sin ningún cambio de código

### Requirement: Coste estimado
El sistema SHALL calcular `cost_usd` a partir de los tokens de entrada y salida y de una tabla de precios en código (USD por millón de tokens, input/output), verificada contra la documentación oficial. Si el modelo no está en la tabla, `cost_usd` MUST ser `null`.

#### Scenario: Modelo con precio conocido
- **GIVEN** que el modelo tiene precios `p_in` y `p_out` por millón de tokens
- **WHEN** una llamada consume `i` tokens de entrada y `o` de salida
- **THEN** `cost_usd = (i * p_in + o * p_out) / 1_000_000`, redondeado a 6 decimales

#### Scenario: Modelo desconocido
- **WHEN** el modelo efectivo no está en la tabla de precios
- **THEN** `cost_usd` es `null` y la estimación se devuelve con normalidad

### Requirement: Errores del proveedor
Los errores del SDK del proveedor (autenticación, rate limit, timeout, error de servidor o conexión) MUST traducirse a un error de dominio propio, sin propagar trazas ni secrets al cliente.

#### Scenario: Proveedor no disponible
- **WHEN** el SDK lanza un error de conexión o de servidor
- **THEN** el servicio produce un error de dominio que el router transforma en una respuesta HTTP 502
