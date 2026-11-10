# llm-provider Specification

## Purpose
Abstraer la llamada a los proveedores LLM (OpenAI y Anthropic) tras una interfaz mínima: llamadas vía LiteLLM con modelo primario y de respaldo, uso de tokens, coste estimado, límites de la llamada y tratamiento de errores sin filtrar datos sensibles.
## Requirements
### Requirement: Interfaz mínima de proveedor
El sistema SHALL exponer una interfaz común de proveedor LLM que reciba el system prompt y una lista ordenada de mensajes de conversación (cada uno con `role` `user` o `assistant` y `content`) y devuelva un resultado con: `text`, `model` y `provider` **efectivos** (el modelo configurado, sin prefijo, del despliegue que respondió, y su proveedor), `input_tokens`, `output_tokens`, `truncated` (`true` si la generación terminó por alcanzar el límite de tokens), `fallback_used` (`true` si respondió el modelo de respaldo), `cache_hit` (`true` si la respuesta vino de la caché) y `latency_ms`. La implementación MUST hacer las llamadas mediante LiteLLM, que traduce al formato de cada proveedor: en OpenAI el system prompt va como primer mensaje `system` seguido de la conversación; en Anthropic va en el parámetro `system` y la conversación en `messages`. Ningún módulo fuera de `app/services` MUST importar LiteLLM ni los SDK de los proveedores.

#### Scenario: Llamada con un modelo de OpenAI
- **GIVEN** `PRIMARY_MODEL=gpt-4o-mini`
- **WHEN** se solicita una generación
- **THEN** el despliegue primario usa el modelo `gpt-4o-mini` de OpenAI y recibe el mensaje `system` seguido de los mensajes de la conversación en el mismo orden, y el resultado incluye el texto y los tokens que informa la API

#### Scenario: Llamada con un modelo de Anthropic
- **GIVEN** `PRIMARY_MODEL=claude-haiku-4-5`
- **WHEN** se solicita una generación
- **THEN** la llamada se dirige a Anthropic con el system prompt y los mensajes de la conversación en el mismo orden, y el resultado tiene `provider = "anthropic"` y los tokens que informa la API

#### Scenario: Conversación de varios turnos
- **GIVEN** los mensajes `user`, `assistant`, `user`
- **WHEN** se solicita una generación
- **THEN** LiteLLM recibe los tres mensajes con sus roles y contenidos sin modificar, y ningún mensaje de la conversación aparece dentro del system prompt

#### Scenario: Mismos parámetros en ambos modos
- **WHEN** se solicita una generación normal y una en streaming con las mismas entradas
- **THEN** LiteLLM recibe en ambas llamadas el mismo modelo, los mismos mensajes y el mismo límite de tokens, y la de streaming añade solo los parámetros propios del streaming

### Requirement: Coste estimado
El sistema SHALL calcular `cost_usd` a partir de los tokens de entrada y salida y de una tabla de precios en código (USD por millón de tokens, input/output), verificada contra la documentación oficial. El coste MUST calcularse con el **modelo efectivo**, que es el modelo **configurado** del despliegue que respondió (primario o respaldo, sin prefijo de proveedor), no el snapshot que devuelva la API; la tabla MUST incluir tanto los alias como los snapshots conocidos de cada modelo soportado. Si el modelo no está en la tabla, `cost_usd` MUST ser `null`.

#### Scenario: Modelo con precio conocido
- **GIVEN** que el modelo tiene precios `p_in` y `p_out` por millón de tokens
- **WHEN** una llamada consume `i` tokens de entrada y `o` de salida
- **THEN** `cost_usd = (i * p_in + o * p_out) / 1_000_000`, redondeado a 6 decimales

#### Scenario: Modelo configurado con prefijo de proveedor
- **WHEN** `PRIMARY_MODEL=anthropic/claude-haiku-4-5` y responde el primario
- **THEN** el resultado tiene `model = "claude-haiku-4-5"` y `cost_usd` se calcula con los precios de `claude-haiku-4-5`

#### Scenario: Snapshot configurado
- **WHEN** `PRIMARY_MODEL=claude-haiku-4-5-20251001`
- **THEN** `cost_usd` se calcula con los mismos precios que `claude-haiku-4-5`

#### Scenario: Coste del respaldo
- **GIVEN** `PRIMARY_MODEL=gpt-4o-mini` y `FALLBACK_MODEL=claude-haiku-4-5`
- **WHEN** responde el modelo de respaldo
- **THEN** `cost_usd` se calcula con los precios de `claude-haiku-4-5`

#### Scenario: Modelo desconocido
- **WHEN** el modelo efectivo no está en la tabla de precios
- **THEN** `cost_usd` es `null` y la estimación se devuelve con normalidad

### Requirement: Errores del proveedor
Los errores de la llamada al LLM (autenticación, rate limit, timeout, error de servidor o conexión), una vez agotados los reintentos y el fallback, MUST traducirse a un error de dominio propio, sin propagar trazas ni secrets al cliente. Durante la iteración de una respuesta en streaming, **cualquier** excepción (también los errores de transporte o de parseo que LiteLLM no envuelve) MUST traducirse al mismo error de dominio.

#### Scenario: Proveedores no disponibles
- **WHEN** el primario y el respaldo fallan con un error de conexión o de servidor
- **THEN** el servicio produce un error de dominio que el router transforma en una respuesta HTTP 502

#### Scenario: Error durante el streaming
- **GIVEN** una respuesta en streaming que ya ha entregado algunos fragmentos
- **WHEN** se produce un error al obtener el siguiente fragmento
- **THEN** la iteración termina con el mismo error de dominio, que conserva la excepción original como causa

#### Scenario: Error de transporte no envuelto
- **GIVEN** una respuesta en streaming que ya ha entregado algunos fragmentos
- **WHEN** se produce una excepción que no es de LiteLLM (p. ej. un timeout de lectura del cliente HTTP)
- **THEN** la iteración termina con el error de dominio y el log registra el tipo de la excepción original

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

### Requirement: Generación en streaming
La interfaz de proveedor SHALL ofrecer, con las mismas entradas que la generación normal, un modo streaming que entregue el texto de la respuesta como una secuencia de fragmentos a medida que los produce la API. Al agotarse la secuencia, MUST quedar disponible un resultado con los mismos campos que la generación normal (`text` igual a la concatenación de los fragmentos, modelo y proveedor efectivos, tokens, `truncated`, `fallback_used`, `cache_hit` y `latency_ms`), con los tokens que informa la API en el propio stream (solicitando el uso con `stream_options={"include_usage": true}`). Una vez consumido, el resultado MUST seguir disponible aunque la secuencia se vuelva a recorrer.

#### Scenario: Streaming con varios fragmentos
- **GIVEN** una API que devuelve tres fragmentos de texto y un último chunk con el uso de tokens
- **WHEN** se consume la respuesta en streaming
- **THEN** se obtienen los tres fragmentos en orden y, al terminar, el resultado tiene como texto su concatenación y los tokens del chunk de uso

#### Scenario: Resultado antes de terminar
- **WHEN** se consulta el resultado de un stream que aún no se ha consumido por completo
- **THEN** se produce un error de programación explícito en lugar de un resultado incompleto

#### Scenario: Uso de tokens ausente
- **GIVEN** un stream que no incluye el chunk de uso
- **WHEN** se consume por completo
- **THEN** el resultado informa 0 tokens de entrada y de salida, igual que en la generación normal

#### Scenario: Segunda iteración
- **GIVEN** un stream ya consumido por completo
- **WHEN** se vuelve a recorrer
- **THEN** no entrega ningún fragmento, no llama de nuevo al LLM y el resultado sigue siendo el de la primera iteración

#### Scenario: Fallo antes del primer fragmento
- **GIVEN** un primario que falla al iniciar el stream y un respaldo disponible
- **WHEN** se consume la respuesta en streaming
- **THEN** los fragmentos llegan del respaldo y el resultado tiene `fallback_used = true`

### Requirement: Fallback entre modelo primario y de respaldo
El sistema SHALL llamar primero al modelo primario (`PRIMARY_MODEL`) y, solo si este falla tras agotar sus reintentos, al modelo de respaldo (`FALLBACK_MODEL`), aunque sea de otro proveedor. El orden MUST ser siempre primario primero (nunca reparto de carga entre ambos). Si el respaldo está desactivado (`FALLBACK_MODEL` vacío) o no tiene API key configurada, el sistema MUST funcionar sin fallback. Una vez que un stream ha entregado algún fragmento, un fallo posterior MUST NOT pasar al respaldo (se produce el error de dominio).

#### Scenario: Primario disponible
- **WHEN** el primario responde correctamente
- **THEN** el respaldo no se llama y el resultado tiene `fallback_used = false` y el modelo del primario

#### Scenario: Primario caído
- **GIVEN** un primario que falla con error de servidor en todos los intentos y un respaldo disponible
- **WHEN** se solicita una generación
- **THEN** responde el respaldo, el resultado tiene `fallback_used = true` y el modelo y proveedor del respaldo

#### Scenario: Ambos caídos
- **WHEN** el primario y el respaldo fallan
- **THEN** se produce el error de dominio

#### Scenario: Sin key del respaldo
- **GIVEN** `FALLBACK_MODEL=claude-haiku-4-5` sin `ANTHROPIC_API_KEY` y un primario que falla
- **WHEN** se solicita una generación
- **THEN** se produce el error de dominio sin intentar llamar al respaldo

#### Scenario: Fallo a mitad de stream
- **GIVEN** un stream del primario que ya ha entregado fragmentos
- **WHEN** el primario falla
- **THEN** la iteración termina con el error de dominio y el respaldo no se llama

### Requirement: Opciones por llamada
La interfaz de proveedor SHALL aceptar, en `generate`, `stream` y `generate_structured`, unas opciones de llamada opcionales: modelo destino (sustituye al primario y desactiva el fallback) y `max_tokens`. Sin opciones, el comportamiento MUST ser el de los valores por defecto, de modo que los llamadores sin opciones y los dobles de prueba existentes no cambien. El razonamiento extendido (`thinking`) se retira. El streaming (`stream`) se mantiene en la interfaz aunque ningún endpoint lo exponga desde S04.

#### Scenario: Sin opciones
- **WHEN** se llama a `generate(system, messages)` sin opciones
- **THEN** se usa el modelo primario con fallback y `max_tokens = 2048`

#### Scenario: Modelo destino
- **GIVEN** opciones con modelo destino `claude-haiku-4-5`
- **WHEN** se llama a `generate` o a `generate_structured` y el modelo falla
- **THEN** se produce el error de dominio sin llamar al respaldo

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

