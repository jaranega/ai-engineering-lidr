# estimation-form-ui Specification

## Purpose
Ofrecer una interfaz Streamlit con un formulario tipado (descripción, tipo de proyecto, nivel de detalle, formato de salida y versión del prompt) que llama a `POST /api/v1/estimate` como cliente HTTP y muestra la estimación, las métricas y la validación estructural.
## Requirements
### Requirement: Aplicación Streamlit con formulario tipado
El repositorio SHALL incluir `streamlit_app.py` en la raíz, ejecutable con `uv run streamlit run streamlit_app.py`, con una interfaz conversacional sobre una sesión de la API: los turnos anteriores y, debajo, un formulario `st.form` que contenga un área de texto para la transcripción del turno (máximo 20 000 caracteres), un selector de tipo de proyecto (App móvil, SaaS web, Herramienta interna, Pipeline de datos), un selector de nivel de detalle (Resumen, Medio, Detallado; preseleccionado Medio), un selector de formato de salida (Tabla por fases, Partidas, Narrativa; preseleccionado Partidas), un selector múltiple de ficheros "Adjuntos" que admite solo `pdf` y `docx`, un desplegable "Opciones avanzadas" con solo la versión del prompt de sesión (`v4`, la única) y un botón "Enviar". Los tres selectores MUST conservar su valor entre turnos. Al enviarlo, la app MUST hacer `POST /api/v1/sessions/{session_id}/estimate?prompt_version=<versión>` en `multipart/form-data` con los campos `transcript`, `project_type`, `detail_level` y `output_format` (con los valores exactos de los enums de la API) y una parte `attachments` por fichero con su nombre, su contenido y su tipo MIME (`application/pdf` o `application/vnd.openxmlformats-officedocument.wordprocessingml.document`). Tras un envío con éxito, el selector de ficheros MUST quedar vacío. La app MUST NOT importar ningún módulo de `app` ni LiteLLM, Redis o los SDK de los proveedores, y MUST NOT exponer `reference_projects` ni los demás parámetros de generación.

#### Scenario: Arranque de la interfaz
- **WHEN** se ejecuta `uv run streamlit run streamlit_app.py`
- **THEN** se muestra el formulario con la transcripción, los tres selectores con `Medio` y `Partidas` preseleccionados, el selector de adjuntos y el botón "Enviar"

#### Scenario: Envío tipado con adjuntos
- **GIVEN** una sesión `S`, una transcripción válida, "App móvil", "Detallado", "Tabla por fases" y un fichero `spec.pdf`
- **WHEN** el usuario pulsa "Enviar"
- **THEN** la app hace `POST /api/v1/sessions/S/estimate?prompt_version=v4` en multipart con `transcript` recortada, `project_type = "mobile_app"`, `detail_level = "detailed"`, `output_format = "phases_table"` y una parte `attachments` con `spec.pdf` y `application/pdf`

#### Scenario: Sin dependencias del servicio
- **WHEN** se analizan los imports de `streamlit_app.py`
- **THEN** no importa `app` ni LiteLLM, Redis ni los SDK de los proveedores

#### Scenario: Valores alineados con la API
- **WHEN** se comparan los valores y límites del formulario (enums, longitud de la transcripción, número y tamaño de los adjuntos) y las versiones del prompt de sesión con los enums, límites, valores por defecto de `Settings` y versiones de `app`
- **THEN** coinciden exactamente

### Requirement: Validación en el cliente
La app SHALL recortar los espacios de la transcripción y, si queda vacía o tiene más de 20 000 caracteres, o si hay más de 5 adjuntos o alguno pasa de 10 MiB, mostrar un aviso en español con el límite incumplido sin enviar la petición. Los límites del cliente son una copia de los valores por defecto del servidor, que sigue siendo la fuente de verdad.

#### Scenario: Transcripción vacía
- **WHEN** el usuario envía el formulario con una transcripción de solo espacios
- **THEN** se muestra el aviso y no se hace ninguna petición a la API

#### Scenario: Demasiados adjuntos
- **WHEN** el usuario envía el formulario con 6 ficheros
- **THEN** se muestra el aviso del número máximo de adjuntos y no se hace ninguna petición a la API

### Requirement: Resultado de la estimación
Tras una respuesta 200, la app SHALL añadir el turno a la conversación, conservada en el estado de la sesión de Streamlit, y mostrar cada turno con un `st.chat_message`: el del usuario con la transcripción (los primeros 500 caracteres y el resto en un desplegable) y los nombres de los adjuntos; el del asistente con un aviso en español si `result.out_of_scope` es `true` (antes del texto), un aviso si `truncated` es `true`, el `text` como Markdown y un desplegable "Métricas y validación", plegado, con la `prompt_version`, las métricas (modelo, proveedor, si se usó el respaldo, si vino de la caché, tokens, coste en USD o "no disponible" si es `null`, `latency_ms` del servidor y tiempo total medido en el cliente) y la validación (`score` como aprobadas sobre `checks_total`, las `issues` y el detalle). La app MUST pintar el `text`, no el `result`.

#### Scenario: Resultado completo
- **GIVEN** una API que responde 200 con `text`, `result`, `prompt_version = "v4"`, métricas y `validation` con `score = 0.75` y `checks_total = 8`
- **WHEN** el usuario envía el formulario
- **THEN** la página muestra el turno del usuario, el texto en el turno del asistente y, en el desplegable, `v4`, las métricas y la validación como 6/8 con sus incidencias

#### Scenario: Conversación de varios turnos
- **GIVEN** dos turnos enviados con éxito
- **WHEN** se vuelve a renderizar la página
- **THEN** se muestran los dos pares de mensajes en orden

#### Scenario: Respuesta truncada
- **GIVEN** una respuesta con `truncated = true`
- **WHEN** se muestra el resultado
- **THEN** aparece el aviso de estimación incompleta junto al texto

#### Scenario: Fuera de alcance
- **GIVEN** una respuesta con `result.out_of_scope = true` y `validation = null`
- **WHEN** se muestra el resultado
- **THEN** aparece el aviso de que el proyecto no se puede estimar con la información disponible, el texto y las métricas, sin error

### Requirement: Errores de la API en la interfaz
Si la API responde 400 con `detail.reason`, la app SHALL mostrar un mensaje en español propio de cada motivo (`prompt_injection`: contiene instrucciones dirigidas al asistente; `pii`: contiene datos personales; `moderation`: el contenido no está permitido), referido a la transcripción o, si `detail.field = "attachments"`, al adjunto con el nombre local que corresponde a `detail.index`, sin usar el `message` de la API. Si responde 413, 415 o 422 con un `detail.reason` de adjunto, MUST mostrar un mensaje en español propio de cada `reason` con el nombre local del adjunto por `detail.index`. Si responde 404 al enviar, MUST crear una sesión nueva, vaciar la conversación, conservar la transcripción en el formulario y avisar de que la conversación anterior se ha perdido, sin reenviar el turno. Si responde 409, MUST avisar de que ya hay una estimación en curso para esta conversación. Si responde otro 422, MUST mostrar los nombres de los campos rechazados (tomados de `loc`), sin mostrar el valor enviado. Si responde 502, otro estado distinto de 200, un JSON inválido o se agota el tiempo de lectura (180 segundos), MUST mostrar un mensaje genérico de error del proveedor; un motivo desconocido o un cuerpo ilegible también muestran el mensaje genérico. En ningún caso MUST mostrar trazas, detalles internos ni API keys, ningún error MUST añadir un turno a la conversación, y el formulario MUST conservar los valores introducidos. Un 400 MUST registrarse como `api_request_failed` con `reason = "guardrail"` y `guardrail_reason`.

#### Scenario: Error de validación
- **GIVEN** una API que responde 422 con un error en `["body", "transcript"]`
- **WHEN** el usuario envía el formulario
- **THEN** se muestra un aviso que nombra `transcript` y no contiene el texto de la transcripción

#### Scenario: Entrada bloqueada
- **GIVEN** una API que responde 400 con `{"detail": {"reason": "pii", "message": "...", "field": "transcript", "index": null}}`
- **WHEN** el usuario envía el formulario
- **THEN** se muestra el mensaje en español de datos personales, no se añade ningún turno y el formulario conserva la transcripción

#### Scenario: Adjunto bloqueado
- **GIVEN** dos adjuntos `a.pdf` y `b.docx` y una API que responde 400 con `reason = "prompt_injection"`, `field = "attachments"` e `index = 1`
- **WHEN** el usuario envía el formulario
- **THEN** el mensaje en español nombra `b.docx`

#### Scenario: Adjunto rechazado
- **GIVEN** un adjunto `escaneado.pdf` y una API que responde 422 con `{"detail": {"reason": "no_text", "index": 0, "message": "..."}}`
- **WHEN** el usuario envía el formulario
- **THEN** se muestra el mensaje en español de adjunto sin texto que nombra `escaneado.pdf`

#### Scenario: Sesión perdida al enviar
- **GIVEN** una API que responde 404 al turno
- **WHEN** el usuario envía el formulario
- **THEN** la app crea una sesión nueva, vacía la conversación, avisa y conserva la transcripción sin reenviarla

#### Scenario: Turno en curso
- **GIVEN** una API que responde 409
- **WHEN** el usuario envía el formulario
- **THEN** se muestra el aviso de estimación en curso y no se añade ningún turno

#### Scenario: Fallo del proveedor
- **GIVEN** una API que responde 502
- **WHEN** el usuario envía el formulario
- **THEN** se muestra el mensaje genérico y no se añade ningún turno

### Requirement: URL del servicio por configuración
La app SHALL obtener la URL base de la API de la variable `ESTIMATOR_API_BASE_URL`, del entorno o, si no está definida, de esa única clave en `.env`, con valor por defecto `http://localhost:8000`. La app MUST NOT cargar las API keys de los proveedores ni mostrarlas en la interfaz.

#### Scenario: URL por defecto
- **GIVEN** que `ESTIMATOR_API_BASE_URL` no está definida
- **WHEN** se abre la app
- **THEN** la petición de creación de sesión va a `http://localhost:8000/api/v1/sessions`

#### Scenario: URL configurada
- **GIVEN** `ESTIMATOR_API_BASE_URL=http://api.local:9000`
- **WHEN** se abre la app
- **THEN** la petición de creación de sesión va a `http://api.local:9000/api/v1/sessions`

#### Scenario: Key fuera de la interfaz
- **GIVEN** un `.env` con API keys de los proveedores
- **WHEN** se renderiza la app, incluido el panel lateral
- **THEN** ningún elemento de la interfaz contiene el valor de ninguna API key

### Requirement: API no disponible
Si la app no puede conectar con la API (conexión rechazada, nombre no resuelto o timeout de conexión), SHALL mostrar un aviso que incluye la URL configurada y cómo arrancar la API (`uv run uvicorn app.main:app` o `docker compose up`), sin detener la app: el formulario MUST seguir visible con sus valores. El panel lateral SHALL mostrar el estado de `GET /health` (modelos y caché) o el mismo aviso si la API no responde.

#### Scenario: Abrir la app con la API parada
- **GIVEN** que la API no acepta conexiones
- **WHEN** se abre la app
- **THEN** el panel lateral muestra el aviso con la URL y el comando de arranque, y el formulario está visible

#### Scenario: Envío con la API parada
- **GIVEN** la API parada
- **WHEN** el usuario envía el formulario
- **THEN** se muestra el aviso de API no disponible y, al volver a levantar la API, el siguiente envío funciona sin recargar la página

### Requirement: Logs de la interfaz
La app SHALL registrar con structlog `api_unreachable` (tipo de error) cuando no puede conectar con la API y `api_request_failed` (`reason`: `http_status` con el código, `timeout`, `transport_error`, `invalid_payload` o `guardrail`) cuando la petición no termina en una respuesta válida. Ningún evento MUST incluir la transcripción, los nombres ni el contenido de los adjuntos, la respuesta, el `project_metadata` ni mensajes de error literales.

#### Scenario: Fallo registrado sin contenido
- **GIVEN** una transcripción con el texto `TRANSCRIPCION-SECRETA-123`, un adjunto `ADJUNTO-SECRETO.pdf` y una API que responde 502
- **WHEN** el usuario envía el formulario
- **THEN** se registra `api_request_failed` con `reason = "http_status"` y `status_code = 502`, y ningún log contiene `TRANSCRIPCION-SECRETA-123` ni `ADJUNTO-SECRETO`

### Requirement: Sesión de la interfaz
La app SHALL crear una sesión con `POST /api/v1/sessions` al cargar la página si no tiene `session_id` en el estado de Streamlit, y conservarla entre interacciones. Si la creación falla, MUST mostrar el aviso de API no disponible y reintentarlo en la siguiente interacción. En cada renderizado, la app MUST consultar `GET /api/v1/sessions/{session_id}` (timeout de 3 segundos) para el panel lateral; si responde 404 (API reiniciada o sesión desalojada), MUST crear una sesión nueva, vaciar la conversación y avisar en español de que la conversación anterior ya no existe en el servidor. El botón "Nueva conversación" del panel lateral MUST crear una sesión nueva, vaciar la conversación y el selector de ficheros.

#### Scenario: Sesión al cargar
- **GIVEN** una API disponible
- **WHEN** se abre la app
- **THEN** se hace un `POST /api/v1/sessions` y los envíos siguientes usan el `session_id` devuelto

#### Scenario: Sesión perdida en el servidor
- **GIVEN** una conversación con dos turnos y una API que responde 404 al `GET` de la sesión
- **WHEN** se vuelve a renderizar la página
- **THEN** la app crea una sesión nueva, la conversación queda vacía y se muestra el aviso

#### Scenario: Nueva conversación
- **GIVEN** una conversación con turnos
- **WHEN** el usuario pulsa "Nueva conversación"
- **THEN** se hace un `POST /api/v1/sessions`, la conversación queda vacía y los envíos siguientes usan el nuevo `session_id`

### Requirement: Panel de memoria del proyecto
El panel lateral SHALL mostrar, debajo del estado de la API, la sección "Memoria del proyecto" con el `project_metadata` de la sesión obtenido de `GET /api/v1/sessions/{session_id}`: nombre del proyecto, equipo asumido, tecnologías mencionadas y alcance acordado (solo los que tienen valor, o "Aún no hay datos del proyecto" si no hay ninguno), y la línea `Turnos: <turn_count> · en la ventana: <history_turns>/<max_turns>` con los 8 primeros caracteres del `session_id`.

#### Scenario: Panel vacío
- **GIVEN** una sesión sin turnos
- **WHEN** se renderiza la app
- **THEN** el panel muestra "Aún no hay datos del proyecto" y `Turnos: 0 · en la ventana: 0/6`

#### Scenario: Panel con datos
- **GIVEN** una API cuyo `GET` de la sesión devuelve `project_name = "Clinic booking platform"`, `mentioned_technologies = ["Twilio", "Stripe"]`, `turn_count = 2` y `history_turns = 2`
- **WHEN** se renderiza la app
- **THEN** el panel muestra el nombre, `Twilio` y `Stripe`, y `Turnos: 2 · en la ventana: 2/6`

