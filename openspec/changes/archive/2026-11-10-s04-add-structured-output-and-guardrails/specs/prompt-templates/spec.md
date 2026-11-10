## MODIFIED Requirements

### Requirement: Templates versionados de estimación
El sistema SHALL guardar el prompt de estimación fuera del código, en templates Jinja2 dentro de `app/prompts/estimation/<versión>/`, con tres ficheros por versión: `system.j2` (instrucciones), `user.j2` (envoltorio de la entrada del usuario) y `examples.j2` (ejemplos few-shot, incluido desde `system.j2` con `{% include %}`). La única versión disponible MUST ser `v3`, que pide la salida estructurada (`v1` y `v2`, que pedían Markdown libre, se retiran). Cada versión MUST ser una copia completa e independiente: ningún template MUST incluir, extender ni importar templates de otra versión. La lista de versiones admitidas MUST definirse en un único sitio del código y coincidir con las carpetas existentes.

#### Scenario: Estructura de una versión
- **WHEN** se inspecciona `app/prompts/estimation/`
- **THEN** existe la carpeta `v3` con `system.j2`, `user.j2` y `examples.j2`, y no hay otras carpetas de versión

#### Scenario: Versiones aisladas
- **WHEN** se renderiza `v3`
- **THEN** todos los templates usados son los de la carpeta `v3`

### Requirement: Loader de prompts
`app/prompts/loader.py` SHALL exponer `render_estimation_prompt(request, version="v3", *, num_examples=3) -> tuple[str, str]`, que devuelve `(system, user)` listos para enviar al modelo, sin espacios al principio ni al final. El loader MUST usar un `Environment` de Jinja2 con `undefined=StrictUndefined`, `trim_blocks=True`, `lstrip_blocks=True` y `autoescape=False`. Una variable no definida en el contexto MUST producir un error, nunca un texto vacío. Una versión que no esté en la lista de versiones admitidas MUST producir un error de dominio antes de acceder al sistema de ficheros. El render MUST ser determinista y no hacer llamadas de red.

#### Scenario: Render por defecto
- **GIVEN** una entrada válida
- **WHEN** se llama a `render_estimation_prompt(request)`
- **THEN** devuelve el system y el user de `v3`

#### Scenario: Variable no definida
- **GIVEN** un template que usa una variable que el contexto no proporciona
- **WHEN** se renderiza
- **THEN** se produce un error de Jinja2 (`UndefinedError`)

#### Scenario: Versión desconocida o retirada
- **WHEN** se llama con `version = "v1"`, `"v9"` o `"../v3"`
- **THEN** se produce un error de dominio de versión desconocida

#### Scenario: Determinismo
- **WHEN** se renderiza dos veces con la misma entrada, versión y número de ejemplos
- **THEN** los dos resultados son idénticos

#### Scenario: Todas las combinaciones renderizan
- **WHEN** se renderiza cada combinación de versión, `project_type`, `detail_level`, `output_format`, 0 o 3 ejemplos y con o sin proyectos de referencia
- **THEN** todas renderizan sin error y ningún prompt contiene la representación Python de un enum (`ProjectType.`, `DetailLevel.`, `OutputFormat.`)

### Requirement: Evento de render
Cada render SHALL registrar con structlog el evento `prompt_rendered` con `prompt = "estimation"`, `version`, `output_format`, `detail_level`, `project_type`, `num_examples`, `reference_projects` (número de elementos), `response_language` (`es`, `en` o `null`, el idioma detectado de la descripción), `system_sha256` y `user_sha256` (los 12 primeros caracteres hexadecimales del SHA-256 de cada texto) y `system_chars` y `user_chars`. El evento MUST NOT contener la descripción, los proyectos de referencia ni el texto de los prompts.

#### Scenario: Evento registrado
- **WHEN** se renderiza una petición con `v3` y `phases_table`
- **THEN** se registra `prompt_rendered` con `version = "v3"`, `output_format = "phases_table"` y dos hashes de 12 caracteres hexadecimales

#### Scenario: Hash estable
- **WHEN** se renderiza dos veces la misma entrada
- **THEN** los dos eventos tienen los mismos `system_sha256` y `user_sha256`

#### Scenario: Sin contenido
- **GIVEN** una descripción con el texto `DESCRIPCION-SECRETA-123`
- **WHEN** se renderiza
- **THEN** el evento `prompt_rendered` no contiene `DESCRIPCION-SECRETA-123`

## REMOVED Requirements

### Requirement: Versión v2
**Reason**: `v1` y `v2` pedían Markdown libre y no son compatibles con la salida estructurada. La única versión es `v3`.
**Migration**: Usar `prompt_version=v3` (el valor por defecto). El tono conciso de `v2` no se traslada; una versión futura puede añadir otro tono sobre `v3`.
