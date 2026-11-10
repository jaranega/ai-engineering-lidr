## MODIFIED Requirements

### Requirement: Caché exact-match de respuestas
El sistema SHALL guardar en Redis las respuestas completas del LLM y reutilizarlas cuando llega una petición idéntica. La clave MUST derivarse de forma determinista (hash SHA-256) del system prompt, la lista completa de mensajes (roles y contenidos, en orden), el modelo destino (el pedido en la petición o, si no hay, el primario configurado), si el modelo es una sobrescritura de la petición, el límite de tokens de salida solicitado y el presupuesto de razonamiento, de modo que cualquier cambio en cualquiera de ellos produzca una clave distinta. Las opciones que solo afectan al prompt (preprocesado, formato y número de ejemplos) entran en la clave a través del system prompt y los mensajes; la evaluación no entra. Las entradas MUST caducar a los `CACHE_TTL` segundos.

#### Scenario: Segunda petición idéntica
- **GIVEN** una caché disponible y una primera petición que se completó correctamente
- **WHEN** llega una segunda petición con el mismo system prompt, los mismos mensajes y las mismas opciones
- **THEN** la respuesta se sirve desde la caché, no se llama al LLM y el resultado indica `cache_hit = true`

#### Scenario: Petición distinta
- **GIVEN** una respuesta guardada para una transcripción
- **WHEN** llega una petición cuya transcripción difiere en un solo carácter, o con otro historial de conversación
- **THEN** se llama al LLM y el resultado indica `cache_hit = false`

#### Scenario: Cambio de configuración
- **GIVEN** una respuesta guardada con `PRIMARY_MODEL=gpt-4o-mini`
- **WHEN** se reinicia el servicio con otro `PRIMARY_MODEL` y llega la misma petición
- **THEN** no se reutiliza la respuesta guardada

#### Scenario: Opciones distintas
- **GIVEN** una respuesta guardada para una transcripción con los valores por defecto
- **WHEN** llega la misma transcripción con otro `max_tokens`, otro `thinking_budget`, otro `example_format` o `model` igual al primario pedido como sobrescritura
- **THEN** no se reutiliza la respuesta guardada

#### Scenario: Evaluación fuera de la clave
- **GIVEN** una respuesta guardada con `evaluate = true`
- **WHEN** llega la misma petición con `evaluate = false`
- **THEN** la respuesta se sirve desde la caché

#### Scenario: Caducidad
- **GIVEN** una respuesta guardada hace más de `CACHE_TTL` segundos
- **WHEN** llega la misma petición
- **THEN** se llama al LLM
