## MODIFIED Requirements

### Requirement: Errores del proveedor en la interfaz
La app SHALL añadir al historial el mensaje del usuario y la respuesta del asistente solo cuando la respuesta termina correctamente y contiene texto, de forma que el historial siempre alterne `user`/`assistant`, no contenga turnos vacíos y el usuario pueda reenviar un mensaje fallido. Si el proveedor falla (antes o durante el streaming) o la respuesta termina sin ningún fragmento de texto, la app MUST mostrar un mensaje de error genérico, sin trazas ni detalles del SDK, y MUST NOT guardar en el historial ni el mensaje del usuario ni la respuesta parcial.

#### Scenario: Proveedor caído
- **GIVEN** un proveedor que lanza `LLMProviderError`
- **WHEN** el usuario envía una transcripción
- **THEN** se muestra un error genérico, el historial queda como estaba antes del envío y la app sigue operativa

#### Scenario: Fallo a mitad del streaming
- **GIVEN** un proveedor que entrega algunos fragmentos y después lanza `LLMProviderError`
- **WHEN** el usuario envía una transcripción
- **THEN** se muestra un error genérico y ni el mensaje del usuario ni el texto parcial quedan guardados en el historial

#### Scenario: Respuesta sin texto
- **GIVEN** un proveedor cuyo stream termina correctamente sin entregar ningún fragmento de texto
- **WHEN** el usuario envía una transcripción
- **THEN** se muestra el mismo error genérico, sin traza, y el historial y las métricas quedan como estaban antes del envío

#### Scenario: Generación interrumpida
- **GIVEN** una respuesta que se está escribiendo en streaming
- **WHEN** el usuario detiene la ejecución o envía otro mensaje antes de que termine
- **THEN** el historial no contiene el mensaje interrumpido ni su respuesta parcial

### Requirement: Métricas de la última llamada
Tras cada respuesta completa, la app SHALL mostrar en el panel lateral las métricas de esa llamada: modelo, proveedor, tokens de entrada, tokens de salida, tiempo de respuesta total (segundos, desde el envío hasta el último fragmento), tiempo hasta el primer fragmento, coste estimado en USD (o "n/d" si el modelo no tiene precio conocido) y si la respuesta se cortó por el límite de tokens. Las métricas MUST conservarse entre reruns hasta la siguiente llamada.

#### Scenario: Métricas tras una respuesta
- **GIVEN** un proveedor que informa 1200 tokens de entrada y 300 de salida
- **WHEN** termina una respuesta
- **THEN** el panel lateral muestra el modelo, el proveedor, 1200, 300, el tiempo total, el tiempo hasta el primer fragmento, el coste calculado con la tabla de precios y que la respuesta no se cortó

#### Scenario: Métricas de una respuesta truncada
- **GIVEN** un proveedor cuyo resultado tiene `truncated = true`
- **WHEN** termina la respuesta
- **THEN** el panel lateral indica que la respuesta se cortó por el límite de tokens

#### Scenario: Antes de la primera llamada
- **WHEN** se abre la app y aún no se ha enviado ningún mensaje
- **THEN** el panel lateral indica que todavía no hay métricas

#### Scenario: Llamada fallida
- **GIVEN** que hay métricas de una llamada anterior
- **WHEN** la siguiente llamada falla
- **THEN** las métricas mostradas siguen siendo las de la última llamada completada

## ADDED Requirements

### Requirement: Aviso de respuesta truncada
Cuando una respuesta del asistente termina con `truncated = true`, la app SHALL guardarla en el historial como cualquier otra respuesta y MUST mostrar debajo de ella, en español, un aviso de que se cortó por el límite de longitud y de que se puede pedir un ajuste o una versión más breve. El aviso MUST seguir visible junto a esa respuesta en los reruns posteriores, y el indicador MUST NOT enviarse al LLM como parte de los mensajes de la conversación.

#### Scenario: Respuesta truncada
- **GIVEN** un proveedor cuyo resultado tiene `truncated = true`
- **WHEN** el usuario envía una transcripción
- **THEN** la respuesta se guarda en el historial y debajo aparece el aviso de truncado

#### Scenario: Aviso persistente
- **GIVEN** una respuesta truncada en el historial
- **WHEN** el usuario envía otro mensaje y la app vuelve a pintar el historial
- **THEN** el aviso sigue debajo de la respuesta truncada y no aparece en las respuestas completas

#### Scenario: Indicador fuera de los mensajes enviados
- **GIVEN** una respuesta truncada en el historial
- **WHEN** el usuario envía un ajuste
- **THEN** el proveedor recibe los mensajes solo con `role` y `content`, sin el indicador de truncado

#### Scenario: Nueva conversación
- **GIVEN** una respuesta truncada en el historial
- **WHEN** el usuario pulsa "Nueva conversación"
- **THEN** desaparecen el historial y los avisos de truncado
