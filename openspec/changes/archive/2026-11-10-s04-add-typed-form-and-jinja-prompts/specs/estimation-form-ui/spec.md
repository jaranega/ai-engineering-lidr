## ADDED Requirements

### Requirement: Aplicación Streamlit con formulario tipado
El repositorio SHALL incluir `streamlit_app.py` en la raíz, ejecutable con `uv run streamlit run streamlit_app.py`, con un formulario `st.form` que contenga: un área de texto para la descripción del proyecto (máximo 2000 caracteres), un selector de tipo de proyecto (App móvil, SaaS web, Herramienta interna, Pipeline de datos), un selector de nivel de detalle (Resumen, Medio, Detallado; preseleccionado Medio), un selector de formato de salida (Tabla por fases, Partidas, Narrativa; preseleccionado Partidas), un desplegable "Opciones avanzadas" con solo la versión del prompt (`v1`, `v2`; por defecto `v1`) y un botón "Estimar". Al enviarlo, la app MUST hacer `POST /api/v1/estimate?prompt_version=<versión>` con el JSON `{"description", "project_type", "detail_level", "output_format"}` con los valores exactos de los enums de la API. La app MUST NOT importar ningún módulo de `app` ni LiteLLM, Redis o los SDK de los proveedores, y MUST NOT exponer `reference_projects` ni los demás parámetros de generación.

#### Scenario: Arranque de la interfaz
- **WHEN** se ejecuta `uv run streamlit run streamlit_app.py`
- **THEN** se muestra el formulario con sus campos, `Medio` y `Partidas` preseleccionados, y no hay chat

#### Scenario: Envío tipado
- **GIVEN** una descripción válida, "App móvil", "Detallado" y "Tabla por fases"
- **WHEN** el usuario pulsa "Estimar"
- **THEN** la app hace `POST /api/v1/estimate?prompt_version=v1` con `{"description": <texto recortado>, "project_type": "mobile_app", "detail_level": "detailed", "output_format": "phases_table"}`

#### Scenario: Versión del prompt
- **WHEN** el usuario elige `v2` en "Opciones avanzadas" y envía el formulario
- **THEN** la petición lleva `prompt_version=v2`

#### Scenario: Sin dependencias del servicio
- **WHEN** se analizan los imports de `streamlit_app.py`
- **THEN** no importa `app` ni LiteLLM, Redis ni los SDK de los proveedores

#### Scenario: Valores alineados con la API
- **WHEN** se comparan los valores y límites del formulario con los enums y límites de `app.domain`
- **THEN** coinciden exactamente

### Requirement: Validación en el cliente
La app SHALL recortar los espacios de la descripción y, si tiene menos de 20 o más de 2000 caracteres, mostrar un aviso en español con el límite incumplido sin enviar la petición.

#### Scenario: Descripción corta
- **WHEN** el usuario envía el formulario con una descripción de 10 caracteres
- **THEN** se muestra el aviso y no se hace ninguna petición a la API

### Requirement: Resultado de la estimación
Tras una respuesta 200, la app SHALL mostrar el `text` como Markdown, la `prompt_version` usada, un aviso si `truncated` es `true`, las métricas (modelo, proveedor, si se usó el respaldo, si vino de la caché, tokens, coste en USD o "no disponible" si es `null`, `latency_ms` del servidor y tiempo total medido en el cliente) y la validación (`score` como aprobadas sobre `checks_total`, las `issues` y el detalle en un desplegable), y conservarlo en el estado de la sesión para que no desaparezca al interactuar con la página.

#### Scenario: Resultado completo
- **GIVEN** una API que responde 200 con `text`, `prompt_version = "v2"`, métricas y `validation` con `score = 0.75` y `checks_total = 8`
- **WHEN** el usuario envía el formulario
- **THEN** la página muestra el texto, `v2`, las métricas y la validación como 6/8 con sus incidencias

#### Scenario: Respuesta truncada
- **GIVEN** una respuesta con `truncated = true`
- **WHEN** se muestra el resultado
- **THEN** aparece el aviso de estimación incompleta junto al texto

#### Scenario: Sin validación
- **GIVEN** una respuesta con `validation = null`
- **WHEN** se muestra el resultado
- **THEN** se muestran el texto y las métricas sin error

### Requirement: Errores de la API en la interfaz
Si la API responde 422, la app SHALL mostrar un aviso en español con los nombres de los campos rechazados (tomados de `loc`), sin mostrar el valor enviado. Si responde 502, otro estado distinto de 200, un JSON inválido o se agota el tiempo de lectura (120 segundos), MUST mostrar un mensaje genérico de error del proveedor. En ningún caso MUST mostrar trazas, detalles internos ni API keys, y el formulario MUST conservar los valores introducidos.

#### Scenario: Error de validación
- **GIVEN** una API que responde 422 con un error en `["body", "description"]`
- **WHEN** el usuario envía el formulario
- **THEN** se muestra un aviso que nombra `description` y no contiene el texto de la descripción

#### Scenario: Fallo del proveedor
- **GIVEN** una API que responde 502
- **WHEN** el usuario envía el formulario
- **THEN** se muestra el mensaje genérico y no se muestra ningún resultado nuevo

### Requirement: URL del servicio por configuración
La app SHALL obtener la URL base de la API de la variable `ESTIMATOR_API_BASE_URL`, del entorno o, si no está definida, de esa única clave en `.env`, con valor por defecto `http://localhost:8000`. La app MUST NOT cargar las API keys de los proveedores ni mostrarlas en la interfaz.

#### Scenario: URL por defecto
- **GIVEN** que `ESTIMATOR_API_BASE_URL` no está definida
- **WHEN** se envía el formulario
- **THEN** la petición va a `http://localhost:8000/api/v1/estimate`

#### Scenario: URL configurada
- **GIVEN** `ESTIMATOR_API_BASE_URL=http://api.local:9000`
- **WHEN** se envía el formulario
- **THEN** la petición va a `http://api.local:9000/api/v1/estimate`

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
La app SHALL registrar con structlog `api_unreachable` (tipo de error) cuando no puede conectar con la API y `api_request_failed` (`reason`: `http_status` con el código, `timeout`, `transport_error` o `invalid_payload`) cuando la petición no termina en una respuesta válida. Ningún evento MUST incluir la descripción, la respuesta ni mensajes de error literales.

#### Scenario: Fallo registrado sin contenido
- **GIVEN** una descripción con el texto `DESCRIPCION-SECRETA-123` y una API que responde 502
- **WHEN** el usuario envía el formulario
- **THEN** se registra `api_request_failed` con `reason = "http_status"` y `status_code = 502`, y ningún log contiene `DESCRIPCION-SECRETA-123`
