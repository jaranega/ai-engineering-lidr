## MODIFIED Requirements

### Requirement: Configuración y API key desde el entorno
La app SHALL obtener la configuración (modelos, límites, caché y API keys) exclusivamente de `Settings` (variables de entorno y `.env`). Ninguna API key MUST aparecer en el código, en la interfaz ni en los logs. Si la configuración no es válida, la app MUST mostrar el mismo mensaje claro que el arranque de la API (variable que falta y cómo resolverlo, sin valores de keys) y detener la ejecución sin mostrar el chat.

#### Scenario: Falta la API key del modelo primario
- **GIVEN** `PRIMARY_MODEL=claude-haiku-4-5` y `ANTHROPIC_API_KEY` sin definir
- **WHEN** se abre la app
- **THEN** se muestra un error que nombra `ANTHROPIC_API_KEY` y cómo resolverlo, no aparece el campo de chat y no se llama a ningún proveedor

#### Scenario: Key fuera de la interfaz
- **GIVEN** una configuración válida
- **WHEN** se renderiza la app, incluido el panel lateral
- **THEN** ningún elemento de la interfaz contiene el valor de ninguna API key

### Requirement: Panel de contexto CAG
La app SHALL mostrar en `st.sidebar`: el system prompt que se enviará al LLM en el siguiente turno (el del endpoint si el historial está vacío; el que incluye la sección de conversación si hay historial), en un elemento de solo lectura; los ejemplos de `ESTIMATION_EXAMPLES` (resumen de la reunión y estimación de cada uno); y el modelo primario y el de respaldo configurados (o que no hay respaldo si está desactivado o falta su API key).

#### Scenario: Contexto visible
- **WHEN** se abre la app con una configuración válida
- **THEN** el panel lateral muestra el system prompt del endpoint, cada ejemplo CAG con su `meeting_summary` y su `estimation`, y los modelos primario y de respaldo

#### Scenario: Sin respaldo
- **GIVEN** que falta la API key del proveedor del modelo de respaldo
- **WHEN** se abre la app
- **THEN** el panel lateral indica que no hay modelo de respaldo

#### Scenario: Panel visible tras interrumpir
- **GIVEN** una respuesta que se está escribiendo en streaming
- **WHEN** el usuario detiene la ejecución
- **THEN** el panel lateral sigue visible con el contexto y las métricas de la última llamada completada

#### Scenario: Prompt tras la primera respuesta
- **GIVEN** que el usuario ya recibió una estimación
- **WHEN** se renderiza el panel lateral
- **THEN** muestra el system prompt con la sección de conversación, que es el que se enviará en el siguiente turno

### Requirement: Métricas de la última llamada
Tras cada respuesta completa, la app SHALL mostrar en el panel lateral las métricas de esa llamada: modelo y proveedor efectivos, si respondió el modelo de respaldo, si la respuesta vino de la caché, tokens de entrada, tokens de salida, tiempo de respuesta total (segundos, desde el envío hasta el último fragmento), tiempo hasta el primer fragmento, coste estimado en USD (o "n/d" si el modelo no tiene precio conocido) y si la respuesta se cortó por el límite de tokens. Las métricas MUST conservarse entre reruns hasta la siguiente llamada.

#### Scenario: Métricas tras una respuesta
- **GIVEN** un proveedor que informa 1200 tokens de entrada y 300 de salida
- **WHEN** termina una respuesta
- **THEN** el panel lateral muestra el modelo, el proveedor, que no se usó el respaldo, que no vino de la caché, 1200, 300, el tiempo total, el tiempo hasta el primer fragmento, el coste calculado con la tabla de precios y que la respuesta no se cortó

#### Scenario: Métricas de una respuesta del respaldo
- **GIVEN** un resultado con `fallback_used = true`
- **WHEN** termina la respuesta
- **THEN** el panel lateral muestra el modelo del respaldo e indica que respondió el respaldo

#### Scenario: Métricas de una respuesta desde la caché
- **GIVEN** un resultado con `cache_hit = true`
- **WHEN** termina la respuesta
- **THEN** el panel lateral indica que la respuesta vino de la caché

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
