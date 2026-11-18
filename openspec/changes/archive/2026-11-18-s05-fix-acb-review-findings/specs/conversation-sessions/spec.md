## ADDED Requirements

### Requirement: Coste de las llamadas fallidas de la revisión
Con `review=true`, `input_tokens`, `output_tokens` y `cost_usd` del turno SHALL incluir también los intentos que fallaron y consumieron tokens: una llamada del Critic que agota sus reintentos de validación y un borrador del Actor que se corta o no valida en un reintento. Cuando el proveedor no informa del consumo de un intento fallido, sus tokens MUST contarse como desconocidos y `cost_usd` MUST ser `null`. `acb_completed` MUST registrar los totales con esos intentos.

#### Scenario: Critic que agota los reintentos
- **GIVEN** un borrador de 1000/200 tokens y un Critic que falla con `StructuredOutputError` tras intentos que consumieron 500/80 tokens informados por el proveedor
- **WHEN** termina el turno con `review=true`
- **THEN** la respuesta es 200 con `acb.stop_reason = "critic_failed"`, `input_tokens = 1500` y `output_tokens = 280`

#### Scenario: Consumo desconocido
- **GIVEN** un Critic que falla sin que el proveedor informe del consumo
- **WHEN** termina el turno con `review=true`
- **THEN** `cost_usd` es `null`

### Requirement: Descripción de `tier` en OpenAPI
La descripción del campo `tier` del turno SHALL indicar que se admite con `prompt_version` `v5` y `v6` y que con `v4` responde 422.

#### Scenario: Descripción del tier
- **WHEN** se consulta `/openapi.json`
- **THEN** la descripción de `tier` menciona `v5` y `v6`
