# chat-ui Specification

## Purpose
Interfaz conversacional Streamlit (`streamlit_app.py`) que usa en el mismo proceso la capa de servicios: chat multi-turno con historial, respuesta en streaming, panel lateral con el contexto CAG y las métricas de la última llamada, y tratamiento de errores de configuración y del proveedor sin trazas ni secrets.
## Requirements
### Requirement: Aplicación Streamlit de chat
El repositorio SHALL incluir `streamlit_app.py` en la raíz, ejecutable con `uv run streamlit run streamlit_app.py`, que muestre una interfaz de chat construida con `st.chat_message` y `st.chat_input`. La app MUST generar las estimaciones como **cliente HTTP** del servicio, llamando a `POST /api/v1/estimate/stream`, y MUST NOT importar ningún módulo de `app` ni LiteLLM, Redis o los SDK de los proveedores.

#### Scenario: Arranque de la interfaz
- **GIVEN** la API levantada en `ESTIMATOR_API_BASE_URL`
- **WHEN** se ejecuta `uv run streamlit run streamlit_app.py`
- **THEN** se abre en el navegador una interfaz de chat con un campo de entrada para pegar la transcripción

#### Scenario: Estimación de una transcripción
- **GIVEN** la interfaz abierta y sin mensajes previos
- **WHEN** el usuario envía una transcripción de reunión
- **THEN** la transcripción aparece como mensaje del usuario y la estimación generada por el LLM aparece como mensaje del asistente

#### Scenario: Petición enviada a la API
- **GIVEN** una conversación con una transcripción y su estimación
- **WHEN** el usuario envía un ajuste
- **THEN** la app hace POST a `/api/v1/estimate/stream` con `messages` con los tres mensajes (`role` y `content`) en orden

#### Scenario: Sin imports del servicio
- **WHEN** se analizan los imports de `streamlit_app.py`
- **THEN** no importa `app` ni ninguno de sus módulos, ni `litellm`, `redis`, `openai` o `anthropic`

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
La app SHALL mostrar la respuesta del asistente de forma incremental, a medida que llegan los eventos `token` del endpoint SSE (con `st.write_stream`), en lugar de mostrarla de golpe al terminar la generación. La app MUST ignorar los comentarios de keep-alive y los eventos desconocidos. Al recibir el evento `metrics`, el texto completo MUST guardarse en el historial como mensaje `assistant`.

#### Scenario: Texto incremental
- **GIVEN** una API que emite tres eventos `token` y un evento `metrics`
- **WHEN** el usuario envía una transcripción
- **THEN** los fragmentos se escriben en el mensaje del asistente según llegan, y el mensaje guardado en el historial es la concatenación de todos ellos

#### Scenario: Keep-alive
- **GIVEN** una API que intercala comentarios `: ping` entre los eventos
- **WHEN** se consume el stream
- **THEN** la respuesta mostrada y guardada es la misma que sin los comentarios

### Requirement: Nueva conversación
La app SHALL ofrecer en el panel lateral un botón "Nueva conversación" que vacíe el historial y las métricas de la última llamada sin llamar al LLM.

#### Scenario: Empezar de cero
- **GIVEN** una conversación con mensajes y métricas
- **WHEN** el usuario pulsa "Nueva conversación"
- **THEN** el historial queda vacío, el panel lateral indica que no hay métricas y el siguiente mensaje se envía al proveedor como único mensaje `user`

### Requirement: Validación del mensaje del usuario
La app SHALL recortar los espacios iniciales y finales de cada mensaje antes de validarlo y enviarlo, igual que la API. La app MUST rechazar, sin llamar a la API de estimación, los mensajes que queden vacíos y los que superen el límite de caracteres tras el recorte, mostrando un aviso al usuario y sin añadirlos al historial. El límite MUST obtenerse de la API (`max_message_chars` de `GET /api/v1/context`), de modo que siga definido en un único sitio; si no se ha podido obtener, la app solo rechaza los mensajes vacíos y deja el límite a la validación de la API.

#### Scenario: Espacios alrededor del mensaje
- **WHEN** el usuario envía `"  T  \n"`
- **THEN** la API recibe `"T"` y el historial guarda `"T"`

#### Scenario: Mensaje demasiado largo
- **GIVEN** que la API informa `max_message_chars = 20000`
- **WHEN** el usuario envía un texto de 20 001 caracteres
- **THEN** se muestra un aviso con el límite, no se llama al endpoint de estimación y el historial no cambia

#### Scenario: Mensaje en blanco
- **WHEN** el usuario envía un mensaje compuesto solo de espacios
- **THEN** no se llama al endpoint de estimación y el historial no cambia

### Requirement: Errores del proveedor en la interfaz
La app SHALL añadir al historial el mensaje del usuario y la respuesta del asistente solo cuando el stream termina con el evento `metrics` y la respuesta contiene texto, de forma que el historial siempre alterne `user`/`assistant`, no contenga turnos vacíos y el usuario pueda reenviar un mensaje fallido. Si la API responde con un estado distinto de 200, emite el evento `error`, el stream termina sin evento terminal, se corta la conexión a mitad o la respuesta termina sin ningún fragmento de texto, la app MUST mostrar un mensaje de error genérico, sin trazas ni detalles, y MUST NOT guardar en el historial ni el mensaje del usuario ni la respuesta parcial.

#### Scenario: Proveedor caído
- **GIVEN** una API que responde con un único evento `error`
- **WHEN** el usuario envía una transcripción
- **THEN** se muestra un error genérico, el historial queda como estaba antes del envío y la app sigue operativa

#### Scenario: Fallo a mitad del streaming
- **GIVEN** una API que emite algunos eventos `token` y después un evento `error`
- **WHEN** el usuario envía una transcripción
- **THEN** se muestra un error genérico y ni el mensaje del usuario ni el texto parcial quedan guardados en el historial

#### Scenario: Stream sin evento terminal
- **GIVEN** una API que emite eventos `token` y cierra la conexión sin `metrics` ni `error`
- **WHEN** el usuario envía una transcripción
- **THEN** se muestra el error genérico y no se guarda nada

#### Scenario: Estado HTTP de error
- **GIVEN** una API que responde 500 o 422
- **WHEN** el usuario envía una transcripción
- **THEN** se muestra el error genérico, sin detalles de la respuesta, y no se guarda nada

#### Scenario: Respuesta sin texto
- **GIVEN** una API que emite `metrics` sin ningún evento `token`
- **WHEN** el usuario envía una transcripción
- **THEN** se muestra el mismo error genérico, sin traza, y el historial y las métricas quedan como estaban antes del envío

#### Scenario: Generación interrumpida
- **GIVEN** una respuesta que se está escribiendo en streaming
- **WHEN** el usuario detiene la ejecución o envía otro mensaje antes de que termine
- **THEN** el historial no contiene el mensaje interrumpido ni su respuesta parcial, y la conexión con la API se cierra

### Requirement: Panel de contexto CAG
La app SHALL mostrar en `st.sidebar`, con los datos que obtiene de la API: el system prompt que se enviará al LLM en el siguiente turno (de `GET /api/v1/context?message_count=<mensajes del historial + 1>`), en un elemento de solo lectura; los ejemplos CAG (resumen de la reunión y estimación de cada uno, del mismo endpoint); el modelo primario y el de respaldo (o que no hay respaldo) y si la caché está disponible (de `GET /health`). Si la API no responde, el panel MUST mostrar el aviso de API no disponible en lugar de esos datos.

#### Scenario: Contexto visible
- **WHEN** se abre la app con la API disponible
- **THEN** el panel lateral muestra el system prompt del endpoint, cada ejemplo CAG con su `meeting_summary` y su `estimation`, los modelos primario y de respaldo y el estado de la caché

#### Scenario: Sin respaldo
- **GIVEN** una API cuyo `/health` informa `fallback_model = null`
- **WHEN** se abre la app
- **THEN** el panel lateral indica que no hay modelo de respaldo

#### Scenario: Caché no disponible
- **GIVEN** una API cuyo `/health` informa `cache = "unavailable"`
- **WHEN** se abre la app
- **THEN** el panel lateral indica que la caché no está disponible

#### Scenario: Panel visible tras interrumpir
- **GIVEN** una respuesta que se está escribiendo en streaming
- **WHEN** el usuario detiene la ejecución
- **THEN** el panel lateral sigue visible con el contexto y las métricas de la última llamada completada

#### Scenario: Prompt tras la primera respuesta
- **GIVEN** que el usuario ya recibió una estimación
- **WHEN** se renderiza el panel lateral
- **THEN** la app pide el contexto con `message_count = 3` y muestra el system prompt con la sección de conversación, que es el que se enviará en el siguiente turno

### Requirement: Métricas de la última llamada
Tras cada respuesta completa, la app SHALL mostrar en el panel lateral las métricas de esa llamada, tomadas del evento `metrics` salvo los tiempos, que mide el cliente: modelo y proveedor efectivos, si respondió el modelo de respaldo, si la respuesta vino de la caché, tokens de entrada, tokens de salida, tiempo de respuesta total (segundos, desde el envío hasta el evento terminal), tiempo hasta el primer fragmento, coste estimado en USD (o "n/d" si `cost_usd` es `null`) y si la respuesta se cortó por el límite de tokens. Las métricas MUST conservarse entre reruns hasta la siguiente llamada.

#### Scenario: Métricas tras una respuesta
- **GIVEN** una API cuyo evento `metrics` informa 1200 tokens de entrada y 300 de salida
- **WHEN** termina una respuesta
- **THEN** el panel lateral muestra el modelo, el proveedor, que no se usó el respaldo, que no vino de la caché, 1200, 300, el tiempo total, el tiempo hasta el primer fragmento, el coste del evento y que la respuesta no se cortó

#### Scenario: Métricas de una respuesta del respaldo
- **GIVEN** un evento `metrics` con `fallback_used = true`
- **WHEN** termina la respuesta
- **THEN** el panel lateral muestra el modelo del respaldo e indica que respondió el respaldo

#### Scenario: Métricas de una respuesta desde la caché
- **GIVEN** un evento `metrics` con `cache_hit = true`
- **WHEN** termina la respuesta
- **THEN** el panel lateral indica que la respuesta vino de la caché

#### Scenario: Métricas de una respuesta truncada
- **GIVEN** un evento `metrics` con `truncated = true`
- **WHEN** termina la respuesta
- **THEN** el panel lateral indica que la respuesta se cortó por el límite de tokens

#### Scenario: Antes de la primera llamada
- **WHEN** se abre la app y aún no se ha enviado ningún mensaje
- **THEN** el panel lateral indica que todavía no hay métricas

#### Scenario: Llamada fallida
- **GIVEN** que hay métricas de una llamada anterior
- **WHEN** la siguiente llamada falla
- **THEN** las métricas mostradas siguen siendo las de la última llamada completada

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

### Requirement: URL del servicio por configuración
La app SHALL obtener la URL base de la API de la variable `ESTIMATOR_API_BASE_URL`, del entorno o, si no está definida, de esa única clave en `.env`, con valor por defecto `http://localhost:8000`. La app MUST NOT cargar las API keys de los proveedores ni mostrarlas en la interfaz.

#### Scenario: URL por defecto
- **GIVEN** que `ESTIMATOR_API_BASE_URL` no está definida
- **WHEN** se envía un mensaje
- **THEN** la petición va a `http://localhost:8000/api/v1/estimate/stream`

#### Scenario: URL configurada
- **GIVEN** `ESTIMATOR_API_BASE_URL=http://api.local:9000`
- **WHEN** se envía un mensaje
- **THEN** la petición va a `http://api.local:9000/api/v1/estimate/stream`

#### Scenario: Key fuera de la interfaz
- **GIVEN** un `.env` con API keys de los proveedores
- **WHEN** se renderiza la app, incluido el panel lateral
- **THEN** ningún elemento de la interfaz contiene el valor de ninguna API key

### Requirement: API no disponible
Si la app no puede conectar con la API (conexión rechazada, nombre no resuelto o timeout de conexión), SHALL mostrar un aviso que incluye la URL configurada y cómo arrancar la API (`uv run uvicorn app.main:app` o `docker compose up`), sin detener la app: el historial y el campo de chat MUST seguir visibles y la conversación no se pierde. Un envío con la API caída MUST mostrar el mismo aviso sin guardar nada en el historial.

#### Scenario: Abrir la app con la API parada
- **GIVEN** que la API no acepta conexiones
- **WHEN** se abre la app
- **THEN** el panel lateral muestra el aviso con la URL y el comando de arranque, y el campo de chat está visible

#### Scenario: Envío con la API parada
- **GIVEN** una conversación en curso y la API parada
- **WHEN** el usuario envía un mensaje
- **THEN** se muestra el aviso de API no disponible, el historial no cambia y, al volver a levantar la API, el siguiente envío funciona sin recargar la página

