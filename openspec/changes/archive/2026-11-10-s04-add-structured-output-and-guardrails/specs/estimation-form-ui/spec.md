## MODIFIED Requirements

### Requirement: Aplicación Streamlit con formulario tipado
El repositorio SHALL incluir `streamlit_app.py` en la raíz, ejecutable con `uv run streamlit run streamlit_app.py`, con un formulario `st.form` que contenga: un área de texto para la descripción del proyecto (máximo 2000 caracteres), un selector de tipo de proyecto (App móvil, SaaS web, Herramienta interna, Pipeline de datos), un selector de nivel de detalle (Resumen, Medio, Detallado; preseleccionado Medio), un selector de formato de salida (Tabla por fases, Partidas, Narrativa; preseleccionado Partidas), un desplegable "Opciones avanzadas" con solo la versión del prompt (`v3`, la única) y un botón "Estimar". Al enviarlo, la app MUST hacer `POST /api/v1/estimate?prompt_version=<versión>` con el JSON `{"description", "project_type", "detail_level", "output_format"}` con los valores exactos de los enums de la API. La app MUST NOT importar ningún módulo de `app` ni LiteLLM, Redis o los SDK de los proveedores, y MUST NOT exponer `reference_projects` ni los demás parámetros de generación.

#### Scenario: Arranque de la interfaz
- **WHEN** se ejecuta `uv run streamlit run streamlit_app.py`
- **THEN** se muestra el formulario con sus campos, `Medio` y `Partidas` preseleccionados, y no hay chat

#### Scenario: Envío tipado
- **GIVEN** una descripción válida, "App móvil", "Detallado" y "Tabla por fases"
- **WHEN** el usuario pulsa "Estimar"
- **THEN** la app hace `POST /api/v1/estimate?prompt_version=v3` con `{"description": <texto recortado>, "project_type": "mobile_app", "detail_level": "detailed", "output_format": "phases_table"}`

#### Scenario: Sin dependencias del servicio
- **WHEN** se analizan los imports de `streamlit_app.py`
- **THEN** no importa `app` ni LiteLLM, Redis ni los SDK de los proveedores

#### Scenario: Valores alineados con la API
- **WHEN** se comparan los valores y límites del formulario y las versiones del prompt con los enums, límites y versiones de `app`
- **THEN** coinciden exactamente

### Requirement: Resultado de la estimación
Tras una respuesta 200, la app SHALL mostrar el `text` como Markdown, la `prompt_version` usada, un aviso si `truncated` es `true`, un aviso en español si `result.out_of_scope` es `true` (antes del texto), las métricas (modelo, proveedor, si se usó el respaldo, si vino de la caché, tokens, coste en USD o "no disponible" si es `null`, `latency_ms` del servidor y tiempo total medido en el cliente) y la validación (`score` como aprobadas sobre `checks_total`, las `issues` y el detalle en un desplegable), y conservarlo en el estado de la sesión para que no desaparezca al interactuar con la página.

#### Scenario: Resultado completo
- **GIVEN** una API que responde 200 con `text`, `result`, `prompt_version = "v3"`, métricas y `validation` con `score = 0.75` y `checks_total = 8`
- **WHEN** el usuario envía el formulario
- **THEN** la página muestra el texto, `v3`, las métricas y la validación como 6/8 con sus incidencias

#### Scenario: Respuesta truncada
- **GIVEN** una respuesta con `truncated = true`
- **WHEN** se muestra el resultado
- **THEN** aparece el aviso de estimación incompleta junto al texto

#### Scenario: Fuera de alcance
- **GIVEN** una respuesta con `result.out_of_scope = true` y `validation = null`
- **WHEN** se muestra el resultado
- **THEN** aparece el aviso de que el proyecto no se puede estimar con la información disponible, el texto y las métricas, sin error

### Requirement: Errores de la API en la interfaz
Si la API responde 422, la app SHALL mostrar un aviso en español con los nombres de los campos rechazados (tomados de `loc`), sin mostrar el valor enviado. Si responde 400 con `detail.reason`, MUST mostrar un mensaje en español propio de cada motivo (`prompt_injection`: la descripción contiene instrucciones dirigidas al asistente; `pii`: contiene datos personales; `moderation`: el contenido no está permitido), sin usar el `message` de la API; un motivo desconocido o un cuerpo ilegible muestran el mensaje genérico. Si responde 502, otro estado distinto de 200, un JSON inválido o se agota el tiempo de lectura (120 segundos), MUST mostrar un mensaje genérico de error del proveedor. En ningún caso MUST mostrar trazas, detalles internos ni API keys, y el formulario MUST conservar los valores introducidos. Un 400 MUST registrarse como `api_request_failed` con `reason = "guardrail"` y `guardrail_reason`.

#### Scenario: Error de validación
- **GIVEN** una API que responde 422 con un error en `["body", "description"]`
- **WHEN** el usuario envía el formulario
- **THEN** se muestra un aviso que nombra `description` y no contiene el texto de la descripción

#### Scenario: Entrada bloqueada
- **GIVEN** una API que responde 400 con `{"detail": {"reason": "pii", "message": "..."}}`
- **WHEN** el usuario envía el formulario
- **THEN** se muestra el mensaje en español de datos personales, no se muestra ningún resultado nuevo y el formulario conserva la descripción

#### Scenario: Fallo del proveedor
- **GIVEN** una API que responde 502
- **WHEN** el usuario envía el formulario
- **THEN** se muestra el mensaje genérico y no se muestra ningún resultado nuevo
