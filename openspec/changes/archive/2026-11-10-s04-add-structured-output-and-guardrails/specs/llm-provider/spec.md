## MODIFIED Requirements

### Requirement: Límites de la llamada al LLM
Cada llamada al LLM, en modo normal, en streaming o estructurado, MUST usar como límite de tokens de salida el valor de la petición o, si no lo indica, `max_tokens = 2048` (constante en el código, valor por defecto). Cada llamada usa un timeout de `LLM_TIMEOUT` segundos (por defecto 60) y como máximo `LLM_RETRIES` reintentos por modelo (por defecto 2): el primario agota sus reintentos antes de pasar al respaldo, y el respaldo tiene los mismos reintentos. Si la salida de texto se corta por alcanzar el límite, el sistema MUST registrar un warning en el log, devolver el texto recibido y marcar el resultado con `truncated = true`; en la generación estructurada, una salida cortada MUST producir un error de dominio.

#### Scenario: Salida truncada
- **WHEN** el proveedor indica que la generación de texto terminó por límite de tokens (`finish_reason=length`, o `stop_reason=max_tokens` en Anthropic)
- **THEN** se registra un warning y el resultado contiene el texto recibido con `truncated = true`

#### Scenario: Salida completa
- **WHEN** el proveedor indica que la generación terminó normalmente
- **THEN** el resultado tiene `truncated = false`

#### Scenario: Salida truncada en streaming
- **WHEN** una respuesta en streaming termina por límite de tokens
- **THEN** se registra el mismo warning, el texto entregado se conserva y el resultado tiene `truncated = true`

#### Scenario: Límite por defecto
- **WHEN** se solicita una generación sin opciones de llamada (por ejemplo, `POST /api/v1/estimate` sin `max_tokens`)
- **THEN** la llamada usa `max_tokens = 2048`

#### Scenario: Límite de la petición
- **WHEN** se solicita una generación con `max_tokens = 4000`
- **THEN** la llamada usa `max_tokens = 4000`

#### Scenario: Límites configurados
- **GIVEN** `LLM_TIMEOUT=30` y `LLM_RETRIES=1`
- **WHEN** se solicita una generación
- **THEN** la llamada usa un timeout de 30 segundos y como máximo 1 reintento por modelo

#### Scenario: Errores sin datos sensibles en el log
- **WHEN** la llamada al LLM lanza un error
- **THEN** el log registra el tipo de error y el status HTTP, sin el mensaje completo del error ni ninguna API key

### Requirement: Opciones por llamada
La interfaz de proveedor SHALL aceptar, en `generate`, `stream` y `generate_structured`, unas opciones de llamada opcionales: modelo destino (sustituye al primario y desactiva el fallback) y `max_tokens`. Sin opciones, el comportamiento MUST ser el de los valores por defecto, de modo que los llamadores sin opciones y los dobles de prueba existentes no cambien. El razonamiento extendido (`thinking`) se retira. El streaming (`stream`) se mantiene en la interfaz aunque ningún endpoint lo exponga desde S04.

#### Scenario: Sin opciones
- **WHEN** se llama a `generate(system, messages)` sin opciones
- **THEN** se usa el modelo primario con fallback y `max_tokens = 2048`

#### Scenario: Modelo destino
- **GIVEN** opciones con modelo destino `claude-haiku-4-5`
- **WHEN** se llama a `generate` o a `generate_structured` y el modelo falla
- **THEN** se produce el error de dominio sin llamar al respaldo

## ADDED Requirements

### Requirement: Generación estructurada
La interfaz de proveedor SHALL ofrecer `generate_structured(system, messages, response_model, options=None)`, que devuelve un resultado con `value` (instancia validada de `response_model`), `model` y `provider` efectivos, `input_tokens` y `output_tokens` **sumados de todos los intentos**, `attempts`, `fallback_used`, `cache_hit` y `latency_ms`. La implementación MUST usar Instructor (`instructor==1.17.0`) en `Mode.TOOLS` sobre el `completion` del Router de LiteLLM de la llamada, de modo que el fallback y los reintentos de transporte sigan en el Router. Instructor MUST reintentar solo los errores de validación o de parseo, como máximo `STRUCTURED_MAX_RETRIES` veces, devolviendo al modelo el error de validación. MUST NOT pasarse `context` ni `validation_context` a Instructor, para que el contenido de los mensajes nunca se interprete como plantilla. Una salida cortada por el límite de tokens o un objeto que sigue siendo inválido tras agotar los reintentos MUST producir `StructuredOutputError` (subtipo del error de dominio del proveedor, con `reason` `truncated` o `invalid` y `attempts`). El coste MUST calcularse con los tokens sumados y el modelo efectivo de la última respuesta.

#### Scenario: Reintento por validación
- **GIVEN** un proveedor que devuelve primero un objeto inválido y después uno válido
- **WHEN** se llama a `generate_structured`
- **THEN** el resultado tiene el objeto válido, `attempts = 2` y los tokens de las dos llamadas sumados

#### Scenario: Reintentos agotados
- **GIVEN** un proveedor que devuelve siempre un objeto inválido y `STRUCTURED_MAX_RETRIES = 2`
- **WHEN** se llama a `generate_structured`
- **THEN** se hacen 3 llamadas y se produce `StructuredOutputError` con `reason = "invalid"`

#### Scenario: Salida cortada
- **GIVEN** un proveedor que termina con `finish_reason = "length"`
- **WHEN** se llama a `generate_structured`
- **THEN** se produce `StructuredOutputError` con `reason = "truncated"` tras una sola llamada

#### Scenario: Error de transporte sin reintento de Instructor
- **GIVEN** un Router que lanza un error de conexión
- **WHEN** se llama a `generate_structured`
- **THEN** se produce el error de dominio del proveedor tras una sola llamada al Router

#### Scenario: Llaves en la descripción
- **GIVEN** un mensaje `user` que contiene `{{ x }}`
- **WHEN** se llama a `generate_structured`
- **THEN** el mensaje llega al Router sin modificar

#### Scenario: Herramienta forzada
- **WHEN** se llama a `generate_structured`
- **THEN** la llamada al Router incluye `tools` con el JSON Schema de `response_model` y `tool_choice` forzado
