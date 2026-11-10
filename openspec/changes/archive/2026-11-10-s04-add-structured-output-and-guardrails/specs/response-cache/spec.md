## MODIFIED Requirements

### Requirement: Caché exact-match de respuestas
El sistema SHALL guardar en Redis las respuestas completas del LLM y reutilizarlas cuando llega una petición idéntica. La clave MUST derivarse de forma determinista (hash SHA-256) del tipo de llamada (texto o estructurada), el system prompt, la lista completa de mensajes (roles y contenidos, en orden), el modelo destino (el pedido en la petición o, si no hay, el primario configurado), si el modelo es una sobrescritura de la petición y el límite de tokens de salida solicitado; en la llamada estructurada, además, del nombre y un hash del JSON Schema de `response_model` y de sus requisitos por formato y nivel. Cualquier cambio en cualquiera de ellos MUST producir una clave distinta. Lo que solo afecta al prompt (versión del prompt, `project_type`, `detail_level`, `output_format`, número de ejemplos y proyectos de referencia) entra en la clave a través del system prompt y el mensaje `user` renderizados; la evaluación no entra. Las entradas MUST caducar a los `CACHE_TTL` segundos.

#### Scenario: Segunda petición idéntica
- **GIVEN** una caché disponible y una primera petición que se completó correctamente
- **WHEN** llega una segunda petición con el mismo system prompt, los mismos mensajes y las mismas opciones
- **THEN** la respuesta se sirve desde la caché, no se llama al LLM y el resultado indica `cache_hit = true`

#### Scenario: Petición distinta
- **GIVEN** una respuesta guardada para una descripción
- **WHEN** llega una petición cuya descripción difiere en un solo carácter, o con otros proyectos de referencia
- **THEN** se llama al LLM y el resultado indica `cache_hit = false`

#### Scenario: Cambio de configuración
- **GIVEN** una respuesta guardada con `PRIMARY_MODEL=gpt-4o-mini`
- **WHEN** se reinicia el servicio con otro `PRIMARY_MODEL` y llega la misma petición
- **THEN** no se reutiliza la respuesta guardada

#### Scenario: Opciones distintas
- **GIVEN** una respuesta guardada para una descripción con los valores por defecto
- **WHEN** llega la misma descripción con otro `max_tokens`, otro `output_format`, otro `detail_level`, otro `project_type` o `model` igual al primario pedido como sobrescritura
- **THEN** no se reutiliza la respuesta guardada

#### Scenario: Cambio del schema
- **GIVEN** una respuesta estructurada guardada
- **WHEN** cambia el JSON Schema de `response_model` y llega la misma petición
- **THEN** no se reutiliza la respuesta guardada

#### Scenario: Evaluación fuera de la clave
- **GIVEN** una respuesta guardada con `evaluate = true`
- **WHEN** llega la misma petición con `evaluate = false`
- **THEN** la respuesta se sirve desde la caché

#### Scenario: Caducidad
- **GIVEN** una respuesta guardada hace más de `CACHE_TTL` segundos
- **WHEN** llega la misma petición
- **THEN** se llama al LLM

## ADDED Requirements

### Requirement: Caché de la generación estructurada
La generación estructurada SHALL guardar el objeto validado que devolvió el LLM (antes del cálculo de importes y del filtro de fuera de alcance) junto con el modelo, el proveedor, los tokens, `fallback_used` y `attempts`. Un acierto MUST volver a validar el objeto guardado con el `response_model` de la petición; si no valida, MUST registrarse `cache_entry_invalid` y tratarse como un fallo de caché. MUST NOT guardarse nada cuando la llamada termina en error (salida inválida tras los reintentos, salida cortada o fallo del proveedor).

#### Scenario: Acierto revalidado
- **GIVEN** un objeto estructurado guardado para una petición
- **WHEN** llega la misma petición
- **THEN** se devuelve el objeto validado con `cache_hit = true`, los tokens originales y sin llamar al LLM

#### Scenario: Entrada corrupta
- **GIVEN** una entrada de caché cuyo objeto no valida contra el `response_model`
- **WHEN** llega la petición
- **THEN** se registra `cache_entry_invalid` y se llama al LLM

#### Scenario: Error no guardado
- **GIVEN** una llamada estructurada que termina en `StructuredOutputError`
- **WHEN** llega la misma petición
- **THEN** se vuelve a llamar al LLM
