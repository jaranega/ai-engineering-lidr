# chat-ui Specification

## Purpose
Interfaz conversacional Streamlit (`streamlit_app.py`) que usa en el mismo proceso la capa de servicios: chat multi-turno con historial, respuesta en streaming, panel lateral con el contexto CAG y las métricas de la última llamada, y tratamiento de errores de configuración y del proveedor sin trazas ni secrets.

## Requirements
### Requirement: Aplicación Streamlit de chat
El repositorio SHALL incluir `streamlit_app.py` en la raíz, ejecutable con `uv run streamlit run streamlit_app.py`, que muestre una interfaz de chat construida con `st.chat_message` y `st.chat_input`. La app MUST generar las estimaciones llamando directamente a la capa `app/services` (sin depender de que el servidor FastAPI esté arrancado) y MUST NOT importar los SDKs de OpenAI o Anthropic.

#### Scenario: Arranque de la interfaz
- **GIVEN** una configuración válida en `.env`
- **WHEN** se ejecuta `uv run streamlit run streamlit_app.py`
- **THEN** se abre en el navegador una interfaz de chat con un campo de entrada para pegar la transcripción

#### Scenario: Estimación de una transcripción
- **GIVEN** la interfaz abierta y sin mensajes previos
- **WHEN** el usuario envía una transcripción de reunión
- **THEN** la transcripción aparece como mensaje del usuario y la estimación generada por el LLM aparece como mensaje del asistente

#### Scenario: Sin servidor FastAPI
- **GIVEN** que uvicorn no está en ejecución
- **WHEN** el usuario envía una transcripción
- **THEN** la estimación se genera igualmente

### Requirement: Historial de la conversación
La app SHALL guardar los mensajes de la conversación (rol y contenido) en `st.session_state` y MUST volver a mostrarlos todos, en orden, en cada rerun de Streamlit durante la sesión del navegador.

#### Scenario: Varios mensajes seguidos
- **GIVEN** que el usuario ya recibió una estimación
- **WHEN** envía un segundo mensaje
- **THEN** en pantalla se ven, en orden, la transcripción, la primera estimación, el segundo mensaje y la nueva respuesta

#### Scenario: Sesión nueva
- **WHEN** el usuario abre la app en una sesión nueva del navegador
- **THEN** el historial empieza vacío

### Requirement: Conversación multi-turno con el LLM
La app SHALL enviar al LLM el historial completo de la conversación (mensajes `user` y `assistant` alternos, empezando por la transcripción) junto con el system prompt del chat, de modo que los mensajes posteriores a la transcripción se interpreten como peticiones de ajuste sobre la estimación.

#### Scenario: Ajuste de la estimación
- **GIVEN** una conversación con una transcripción y su estimación
- **WHEN** el usuario envía "quita la sección de blog"
- **THEN** el proveedor recibe los tres mensajes (transcripción, estimación previa y petición) en ese orden, con el system prompt que incluye la sección de conversación, y el asistente responde con la estimación completa actualizada

#### Scenario: Primer mensaje
- **GIVEN** un historial vacío
- **WHEN** el usuario envía una transcripción
- **THEN** el proveedor recibe un único mensaje `user` cuyo contenido es exactamente la transcripción sin los espacios iniciales y finales, con el system prompt del endpoint (sin sección de conversación)

### Requirement: Respuesta en streaming
La app SHALL mostrar la respuesta del asistente de forma incremental, a medida que el proveedor entrega fragmentos de texto (con `st.write_stream`), en lugar de mostrarla de golpe al terminar la generación. Al terminar, el texto completo MUST guardarse en el historial como mensaje `assistant`.

#### Scenario: Texto incremental
- **GIVEN** un proveedor que entrega la respuesta en varios fragmentos
- **WHEN** el usuario envía una transcripción
- **THEN** los fragmentos se escriben en el mensaje del asistente según llegan, y el mensaje guardado en el historial es la concatenación de todos ellos

### Requirement: Nueva conversación
La app SHALL ofrecer en el panel lateral un botón "Nueva conversación" que vacíe el historial y las métricas de la última llamada sin llamar al LLM.

#### Scenario: Empezar de cero
- **GIVEN** una conversación con mensajes y métricas
- **WHEN** el usuario pulsa "Nueva conversación"
- **THEN** el historial queda vacío, el panel lateral indica que no hay métricas y el siguiente mensaje se envía al proveedor como único mensaje `user`

### Requirement: Validación del mensaje del usuario
La app SHALL recortar los espacios iniciales y finales de cada mensaje antes de validarlo y enviarlo, igual que `POST /api/v1/estimate`. La app MUST rechazar, sin llamar al LLM, los mensajes que queden vacíos y los que superen 20 000 caracteres tras el recorte (el mismo límite que la API, definido en un único sitio), mostrando un aviso al usuario y sin añadirlos al historial.

#### Scenario: Espacios alrededor del mensaje
- **WHEN** el usuario envía `"  T  \n"`
- **THEN** el proveedor recibe `"T"` y el historial guarda `"T"`

#### Scenario: Mensaje demasiado largo
- **WHEN** el usuario envía un texto de 20 001 caracteres
- **THEN** se muestra un aviso con el límite, el proveedor no se llama y el historial no cambia

#### Scenario: Mensaje en blanco
- **WHEN** el usuario envía un mensaje compuesto solo de espacios
- **THEN** el proveedor no se llama y el historial no cambia

### Requirement: Configuración y API key desde el entorno
La app SHALL obtener la configuración (proveedor, modelo y API keys) exclusivamente de `Settings` (variables de entorno y `.env`). Ninguna API key MUST aparecer en el código, en la interfaz ni en los logs. Si la configuración no es válida, la app MUST mostrar el mismo mensaje claro que el arranque de la API (variable que falta y cómo resolverlo, sin valores de keys) y detener la ejecución sin mostrar el chat.

#### Scenario: Falta la API key del proveedor activo
- **GIVEN** `LLM_PROVIDER=anthropic` y `ANTHROPIC_API_KEY` sin definir
- **WHEN** se abre la app
- **THEN** se muestra un error que nombra `ANTHROPIC_API_KEY` y cómo resolverlo, no aparece el campo de chat y no se llama a ningún proveedor

#### Scenario: Key fuera de la interfaz
- **GIVEN** una configuración válida
- **WHEN** se renderiza la app, incluido el panel lateral
- **THEN** ningún elemento de la interfaz contiene el valor de ninguna API key

### Requirement: Errores del proveedor en la interfaz
La app SHALL añadir al historial el mensaje del usuario y la respuesta del asistente solo cuando la respuesta termina correctamente, de forma que el historial siempre alterne `user`/`assistant` y el usuario pueda reenviar un mensaje fallido. Si el proveedor falla (antes o durante el streaming), la app MUST mostrar un mensaje de error genérico, sin trazas ni detalles del SDK, y MUST NOT guardar en el historial ni el mensaje del usuario ni la respuesta parcial.

#### Scenario: Proveedor caído
- **GIVEN** un proveedor que lanza `LLMProviderError`
- **WHEN** el usuario envía una transcripción
- **THEN** se muestra un error genérico, el historial queda como estaba antes del envío y la app sigue operativa

#### Scenario: Fallo a mitad del streaming
- **GIVEN** un proveedor que entrega algunos fragmentos y después lanza `LLMProviderError`
- **WHEN** el usuario envía una transcripción
- **THEN** se muestra un error genérico y ni el mensaje del usuario ni el texto parcial quedan guardados en el historial

#### Scenario: Generación interrumpida
- **GIVEN** una respuesta que se está escribiendo en streaming
- **WHEN** el usuario detiene la ejecución o envía otro mensaje antes de que termine
- **THEN** el historial no contiene el mensaje interrumpido ni su respuesta parcial

### Requirement: Panel de contexto CAG
La app SHALL mostrar en `st.sidebar`: el system prompt que se enviará al LLM en el siguiente turno (el del endpoint si el historial está vacío; el que incluye la sección de conversación si hay historial), en un elemento de solo lectura; los ejemplos de `ESTIMATION_EXAMPLES` (resumen de la reunión y estimación de cada uno); y el proveedor y el modelo configurados.

#### Scenario: Contexto visible
- **WHEN** se abre la app con una configuración válida
- **THEN** el panel lateral muestra el system prompt del endpoint, cada ejemplo CAG con su `meeting_summary` y su `estimation`, y el proveedor y modelo activos

#### Scenario: Panel visible tras interrumpir
- **GIVEN** una respuesta que se está escribiendo en streaming
- **WHEN** el usuario detiene la ejecución
- **THEN** el panel lateral sigue visible con el contexto y las métricas de la última llamada completada

#### Scenario: Prompt tras la primera respuesta
- **GIVEN** que el usuario ya recibió una estimación
- **WHEN** se renderiza el panel lateral
- **THEN** muestra el system prompt con la sección de conversación, que es el que se enviará en el siguiente turno

### Requirement: Métricas de la última llamada
Tras cada respuesta completa, la app SHALL mostrar en el panel lateral las métricas de esa llamada: modelo, proveedor, tokens de entrada, tokens de salida, tiempo de respuesta total (segundos, desde el envío hasta el último fragmento), tiempo hasta el primer fragmento y coste estimado en USD (o "n/d" si el modelo no tiene precio conocido). Las métricas MUST conservarse entre reruns hasta la siguiente llamada.

#### Scenario: Métricas tras una respuesta
- **GIVEN** un proveedor que informa 1200 tokens de entrada y 300 de salida
- **WHEN** termina una respuesta
- **THEN** el panel lateral muestra el modelo, el proveedor, 1200, 300, el tiempo total, el tiempo hasta el primer fragmento y el coste calculado con la tabla de precios

#### Scenario: Antes de la primera llamada
- **WHEN** se abre la app y aún no se ha enviado ningún mensaje
- **THEN** el panel lateral indica que todavía no hay métricas

#### Scenario: Llamada fallida
- **GIVEN** que hay métricas de una llamada anterior
- **WHEN** la siguiente llamada falla
- **THEN** las métricas mostradas siguen siendo las de la última llamada completada

