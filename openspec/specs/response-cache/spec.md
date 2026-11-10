# response-cache Specification

## Purpose
Caché exact-match en Redis de las respuestas completas del LLM, compartida entre la generación normal y el streaming: clave, caducidad, qué se guarda y qué no, y degradación sin errores cuando Redis no está disponible.
## Requirements
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

### Requirement: Contenido del acierto de caché
Un acierto de caché SHALL devolver el mismo texto, modelo y proveedor efectivos, tokens y `truncated` que la respuesta original, con `cache_hit = true`, `fallback_used` igual al de la respuesta original y la latencia de la petición actual. El coste MUST seguir calculándose a partir de los tokens guardados, de modo que el campo `cost_usd` muestre lo que costó generar la respuesta.

#### Scenario: Acierto con los datos originales
- **GIVEN** una respuesta guardada con 1200 tokens de entrada y 300 de salida, servida por el modelo de respaldo
- **WHEN** se produce un acierto de caché
- **THEN** el resultado tiene el mismo texto, los mismos tokens, el modelo y proveedor del respaldo, `fallback_used = true` y `cache_hit = true`

### Requirement: Qué no se guarda
El sistema MUST NOT guardar en la caché respuestas truncadas por el límite de tokens, respuestas sin texto, respuestas fallidas ni streams interrumpidos antes de terminar.

#### Scenario: Respuesta truncada
- **WHEN** una respuesta termina con `truncated = true`
- **THEN** no se guarda y la misma petición vuelve a llamar al LLM

#### Scenario: Stream interrumpido
- **GIVEN** una respuesta en streaming
- **WHEN** el consumidor deja de leerla antes del final o el proveedor falla a mitad
- **THEN** no se guarda nada en la caché

### Requirement: Caché en streaming
La generación en streaming SHALL consultar y alimentar la misma caché que la generación normal. Ante un acierto, el stream MUST entregar el texto guardado como un único fragmento y dejar disponible el resultado con `cache_hit = true`, sin llamar al LLM. Ante un fallo, MUST guardar la respuesta completa al terminar el stream.

#### Scenario: Acierto en streaming
- **GIVEN** una respuesta guardada para una conversación
- **WHEN** se pide la misma conversación en streaming
- **THEN** el stream entrega el texto guardado en un único fragmento, no se llama al LLM y el resultado indica `cache_hit = true`

#### Scenario: Compartida entre modos
- **GIVEN** una respuesta generada y guardada mediante `POST /api/v1/estimate`
- **WHEN** la app Streamlit envía la misma transcripción como primer mensaje
- **THEN** la respuesta se sirve desde la caché

### Requirement: Degradación sin Redis
Si Redis no está disponible (no configurado, caído o con error en una operación), el sistema SHALL seguir respondiendo llamando al LLM, sin error para el cliente, y MUST registrar un warning sin datos de la petición. Cada operación de caché MUST tener un timeout corto para no añadir más de un segundo a la petición cuando Redis no responde.

#### Scenario: Redis caído
- **GIVEN** que Redis no acepta conexiones
- **WHEN** llega una petición de estimación
- **THEN** la respuesta es correcta, `cache_hit = false` y el log contiene un warning de caché no disponible

#### Scenario: Arranque sin Redis
- **GIVEN** que Redis no está en ejecución
- **WHEN** se arranca el servicio
- **THEN** el servicio arranca y responde a las peticiones sin caché

