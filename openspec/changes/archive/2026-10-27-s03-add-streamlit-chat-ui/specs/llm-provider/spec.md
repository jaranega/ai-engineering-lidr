## MODIFIED Requirements

### Requirement: Interfaz mínima de proveedor
El sistema SHALL exponer una interfaz común de proveedor LLM que reciba el system prompt y una lista ordenada de mensajes de conversación (cada uno con `role` `user` o `assistant` y `content`) y devuelva un resultado con: `text`, `model`, `provider`, `input_tokens` y `output_tokens`. Habrá dos implementaciones: OpenAI (Chat Completions, con el system prompt como primer mensaje `system` seguido de los mensajes de la conversación) y Anthropic (Messages, con el system prompt en el parámetro `system` y los mensajes de la conversación en `messages`).

#### Scenario: Llamada con OpenAI
- **GIVEN** `LLM_PROVIDER=openai`
- **WHEN** se solicita una generación
- **THEN** se llama al SDK de OpenAI con el mensaje `system` seguido de los mensajes de la conversación en el mismo orden, y el resultado incluye el texto y los tokens que informa la API

#### Scenario: Llamada con Anthropic
- **GIVEN** `LLM_PROVIDER=anthropic`
- **WHEN** se solicita una generación
- **THEN** se llama al SDK de Anthropic con `system=<prompt>` y los mensajes de la conversación en el mismo orden, y el resultado incluye el texto y los tokens que informa la API

#### Scenario: Conversación de varios turnos
- **GIVEN** los mensajes `user`, `assistant`, `user`
- **WHEN** se solicita una generación con cualquiera de los dos proveedores
- **THEN** el SDK recibe los tres mensajes con sus roles y contenidos sin modificar, y ningún mensaje de la conversación aparece dentro del system prompt

### Requirement: Errores del proveedor
Los errores del SDK del proveedor (autenticación, rate limit, timeout, error de servidor o conexión) MUST traducirse a un error de dominio propio, sin propagar trazas ni secrets al cliente. Durante la iteración de una respuesta en streaming, **cualquier** excepción (también los errores de transporte o de parseo que el SDK no envuelve) MUST traducirse al mismo error de dominio.

#### Scenario: Proveedor no disponible
- **WHEN** el SDK lanza un error de conexión o de servidor
- **THEN** el servicio produce un error de dominio que el router transforma en una respuesta HTTP 502

#### Scenario: Error durante el streaming
- **GIVEN** una respuesta en streaming que ya ha entregado algunos fragmentos
- **WHEN** el SDK lanza un error al obtener el siguiente fragmento
- **THEN** la iteración termina con el mismo error de dominio, que conserva la excepción original como causa

#### Scenario: Error de transporte no envuelto por el SDK
- **GIVEN** una respuesta en streaming que ya ha entregado algunos fragmentos
- **WHEN** se produce una excepción que no es del SDK (p. ej. un timeout de lectura del cliente HTTP)
- **THEN** la iteración termina con el error de dominio y el log registra el tipo de la excepción original

### Requirement: Límites de la llamada al LLM
Cada llamada al proveedor, en modo normal o en streaming, MUST usar `max_tokens = 2048`, un timeout de 60 segundos y como máximo 2 reintentos, definidos como constantes en el código. Si la salida se corta por alcanzar `max_tokens`, el sistema MUST registrar un warning en el log y devolver la estimación sin cambios en el contrato.

#### Scenario: Salida truncada
- **WHEN** el proveedor indica que la generación terminó por límite de tokens (`finish_reason=length` o `stop_reason=max_tokens`)
- **THEN** se registra un warning y la respuesta HTTP es 200 con el texto recibido

#### Scenario: Salida truncada en streaming
- **WHEN** una respuesta en streaming termina por límite de tokens
- **THEN** se registra el mismo warning y el texto entregado se conserva

#### Scenario: Errores sin datos sensibles en el log
- **WHEN** el SDK lanza un error
- **THEN** el log registra el tipo de error y el status HTTP, sin el mensaje completo del SDK ni ninguna API key

## ADDED Requirements

### Requirement: Generación en streaming
La interfaz de proveedor SHALL ofrecer, con las mismas entradas que la generación normal, un modo streaming que entregue el texto de la respuesta como una secuencia de fragmentos a medida que los produce la API. Al agotarse la secuencia, MUST quedar disponible un resultado con los mismos campos que la generación normal (`text` igual a la concatenación de los fragmentos, `model`, `provider`, `input_tokens` y `output_tokens`), con los tokens que informa la API en el propio stream (en OpenAI, solicitando el uso con `stream_options={"include_usage": true}`).

#### Scenario: Streaming con OpenAI
- **GIVEN** `LLM_PROVIDER=openai` y una API que devuelve tres fragmentos de texto y un último chunk con el uso de tokens
- **WHEN** se consume la respuesta en streaming
- **THEN** se obtienen los tres fragmentos en orden y, al terminar, el resultado tiene como texto su concatenación y los tokens del chunk de uso

#### Scenario: Streaming con Anthropic
- **GIVEN** `LLM_PROVIDER=anthropic` y una API que emite `message_start` con los tokens de entrada, varios `content_block_delta` de texto y `message_delta` con los tokens de salida
- **WHEN** se consume la respuesta en streaming
- **THEN** se obtienen los fragmentos de texto en orden y, al terminar, el resultado tiene su concatenación y los tokens de entrada y salida informados

#### Scenario: Resultado antes de terminar
- **WHEN** se consulta el resultado de un stream que aún no se ha consumido por completo
- **THEN** se produce un error de programación explícito en lugar de un resultado incompleto

#### Scenario: Uso de tokens ausente
- **GIVEN** un stream de OpenAI que no incluye el chunk de uso
- **WHEN** se consume por completo
- **THEN** el resultado informa 0 tokens de entrada y de salida, igual que en la generación normal
