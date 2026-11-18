## MODIFIED Requirements

### Requirement: Aplicación Streamlit con formulario tipado
El repositorio SHALL incluir `streamlit_app.py` en la raíz, ejecutable con `uv run streamlit run streamlit_app.py`, con una interfaz conversacional sobre una sesión de la API: los turnos anteriores y, debajo, un formulario `st.form` que contenga un área de texto para la transcripción del turno (máximo 20 000 caracteres), un selector de tipo de proyecto (App móvil, SaaS web, Herramienta interna, Pipeline de datos), un selector de nivel de detalle (Resumen, Medio, Detallado; preseleccionado Medio), un selector de formato de salida (Tabla por fases, Partidas, Narrativa; preseleccionado Partidas), un selector múltiple de ficheros "Adjuntos" que admite solo `pdf` y `docx`, un desplegable "Opciones avanzadas" con la versión del prompt de sesión (`SESSION_PROMPT_VERSIONS = ("v5", "v4")`, preseleccionada `v5`) y el selector "Audiencia" (requisito "Selector de audiencia"), y un botón "Enviar". Los selectores MUST conservar su valor entre turnos. Al enviarlo, la app MUST hacer `POST /api/v1/sessions/{session_id}/estimate?prompt_version=<versión>` en `multipart/form-data` con los campos `transcript`, `project_type`, `detail_level` y `output_format` (con los valores exactos de los enums de la API), `tier` solo si la audiencia no es "Automático", y una parte `attachments` por fichero con su nombre, su contenido y su tipo MIME (`application/pdf` o `application/vnd.openxmlformats-officedocument.wordprocessingml.document`). Tras un envío con éxito, el selector de ficheros MUST quedar vacío. La app MUST NOT importar ningún módulo de `app` ni LiteLLM, Redis o los SDK de los proveedores, y MUST NOT exponer `reference_projects` ni los demás parámetros de generación.

#### Scenario: Arranque de la interfaz
- **WHEN** se ejecuta `uv run streamlit run streamlit_app.py`
- **THEN** se muestra el formulario con la transcripción, los tres selectores con `Medio` y `Partidas` preseleccionados, el selector de adjuntos, "Opciones avanzadas" con `v5` y "Automático" preseleccionados y el botón "Enviar"

#### Scenario: Envío tipado con adjuntos
- **GIVEN** una sesión `S`, una transcripción válida, "App móvil", "Detallado", "Tabla por fases", la audiencia "Automático" y un fichero `spec.pdf`
- **WHEN** el usuario pulsa "Enviar"
- **THEN** la app hace `POST /api/v1/sessions/S/estimate?prompt_version=v5` en multipart con `transcript` recortada, `project_type = "mobile_app"`, `detail_level = "detailed"`, `output_format = "phases_table"`, sin campo `tier` y una parte `attachments` con `spec.pdf` y `application/pdf`

#### Scenario: Sin dependencias del servicio
- **WHEN** se analizan los imports de `streamlit_app.py`
- **THEN** no importa `app` ni LiteLLM, Redis ni los SDK de los proveedores

#### Scenario: Valores alineados con la API
- **WHEN** se comparan los valores y límites del formulario (enums, longitud de la transcripción, número y tamaño de los adjuntos), las versiones del prompt de sesión, los tiers y las etiquetas de las reglas del tier con los enums, límites, valores por defecto de `Settings`, versiones, `Tier` y `TierRule` de `app`
- **THEN** coinciden exactamente

### Requirement: Resultado de la estimación
Tras una respuesta 200, la app SHALL añadir el turno a la conversación, conservada en el estado de la sesión de Streamlit, y mostrar cada turno con un `st.chat_message`: el del usuario con la transcripción (los primeros 500 caracteres y el resto en un desplegable) y los nombres de los adjuntos; el del asistente con un aviso en español si `result.out_of_scope` es `true` (antes del texto), un aviso si `truncated` es `true`, el `text` como Markdown y un desplegable "Métricas y validación", plegado, con la `prompt_version`, la línea `Audiencia: <tier> (<regla>)` con las etiquetas en español, las métricas (modelo, proveedor, si se usó el respaldo, si vino de la caché, tokens, coste en USD o "no disponible" si es `null`, `latency_ms` del servidor y tiempo total medido en el cliente) y la validación (`score` como aprobadas sobre `checks_total`, las `issues` y el detalle). La app MUST pintar el `text`, no el `result`.

#### Scenario: Resultado completo
- **GIVEN** una API que responde 200 con `text`, `result`, `prompt_version = "v5"`, `tier = "executive"`, `tier_rule = "nda_detected"`, métricas y `validation` con `score = 0.75` y `checks_total = 8`
- **WHEN** el usuario envía el formulario
- **THEN** la página muestra el turno del usuario, el texto en el turno del asistente y, en el desplegable, `v5`, `Audiencia: Dirección (NDA detectado)`, las métricas y la validación como 6/8 con sus incidencias

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

### Requirement: Panel de memoria del proyecto
El panel lateral SHALL mostrar, debajo del estado de la API, la sección "Memoria del proyecto" con el `project_metadata` de la sesión obtenido de `GET /api/v1/sessions/{session_id}`: nombre del proyecto, equipo asumido, tecnologías mencionadas, alcance acordado, "Restricciones" (lista de `explicit_constraints`) y "Descartado" (lista de `rejected_options`) (solo los que tienen valor, o "Aún no hay datos del proyecto" si no hay ninguno); la línea `Turnos: <turn_count> · en la ventana: <history_turns>/<max_turns> · anclas: <anchors_count> · resumen: sí (<summary_chars> caracteres)` (o `resumen: no` si `summary_chars = 0`) con los 8 primeros caracteres del `session_id`; y, si `last_tier` no es `null`, la línea `Audiencia del último turno: <tier> — regla: <regla>` con las etiquetas en español.

#### Scenario: Panel vacío
- **GIVEN** una sesión sin turnos
- **WHEN** se renderiza la app
- **THEN** el panel muestra "Aún no hay datos del proyecto" y `Turnos: 0 · en la ventana: 0/6 · anclas: 0 · resumen: no`, sin la línea de audiencia

#### Scenario: Panel con datos
- **GIVEN** una API cuyo `GET` de la sesión devuelve `project_name = "Clinic booking platform"`, `mentioned_technologies = ["Twilio", "Stripe"]`, `turn_count = 2` y `history_turns = 2`
- **WHEN** se renderiza la app
- **THEN** el panel muestra el nombre, `Twilio` y `Stripe`, y `Turnos: 2 · en la ventana: 2/6 · anclas: 0 · resumen: no`

#### Scenario: Panel con memoria y audiencia
- **GIVEN** una API cuyo `GET` devuelve `explicit_constraints = ["NDA firmado"]`, `rejected_options = ["Firebase"]`, `turn_count = 8`, `history_turns = 6`, `anchors_count = 1`, `summary_chars = 340`, `last_tier = "executive"` y `last_tier_rule = "nda_detected"`
- **WHEN** se renderiza la app
- **THEN** el panel muestra "Restricciones" con `NDA firmado`, "Descartado" con `Firebase`, `Turnos: 8 · en la ventana: 6/6 · anclas: 1 · resumen: sí (340 caracteres)` y `Audiencia del último turno: Dirección — regla: NDA detectado`

## ADDED Requirements

### Requirement: Selector de audiencia
El desplegable "Opciones avanzadas" SHALL incluir el selector "Audiencia" con las opciones "Automático" (preseleccionada; no envía `tier`), "Dirección (ejecutivo)" (`executive`), "Gestión (PM)" (`pm`), "Técnica (desarrollo)" (`developer`) y "Sin adaptar" (`default`). Con la versión `v4` elegida, el selector MUST deshabilitarse con la indicación "Solo con v5" y la app MUST NOT enviar `tier`. Las etiquetas de las reglas MUST definirse en `TIER_RULE_LABELS`: `explicit` → "elegida por ti", `nda_detected` → "NDA detectado", `regulatory_context` → "normativa detectada", `technical_audience` → "vocabulario técnico", `small_team` → "equipo pequeño", `default` → "sin señales", `prompt_version` → "v4 no adapta la audiencia".

#### Scenario: Audiencia explícita
- **GIVEN** la audiencia "Técnica (desarrollo)" y la versión `v5`
- **WHEN** el usuario envía el formulario
- **THEN** el multipart contiene `tier = "developer"`

#### Scenario: Audiencia con v4
- **GIVEN** la versión `v4`
- **WHEN** se renderiza el formulario y el usuario lo envía
- **THEN** el selector "Audiencia" está deshabilitado y el multipart no contiene `tier`
