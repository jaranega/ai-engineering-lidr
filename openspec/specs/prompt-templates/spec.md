# prompt-templates Specification

## Purpose
Sacar el prompt de estimación del código a templates Jinja2 versionados (`app/prompts/estimation/<versión>/`), con un loader que renderiza `(system, user)` de forma determinista, falla ante variables no definidas o versiones inexistentes, detecta el idioma de la descripción y registra cada render sin su contenido.
## Requirements
### Requirement: Templates versionados de estimación
El sistema SHALL guardar el prompt de estimación fuera del código, en templates Jinja2 dentro de `app/prompts/estimation/<versión>/`, con tres ficheros por versión: `system.j2` (instrucciones), `user.j2` (envoltorio de la entrada del usuario) y `examples.j2` (ejemplos few-shot, incluido desde `system.j2` con `{% include %}`). Las versiones disponibles MUST ser `v1` y `v2`, y cada versión MUST ser una copia completa e independiente: ningún template MUST incluir, extender ni importar templates de otra versión. La lista de versiones admitidas MUST definirse en un único sitio del código y coincidir con las carpetas existentes.

#### Scenario: Estructura de una versión
- **WHEN** se inspecciona `app/prompts/estimation/`
- **THEN** existen las carpetas `v1` y `v2`, cada una con `system.j2`, `user.j2` y `examples.j2`, y no hay otras carpetas de versión

#### Scenario: Versiones aisladas
- **WHEN** se renderiza `v2`
- **THEN** todos los templates usados son los de la carpeta `v2`

### Requirement: Loader de prompts
`app/prompts/loader.py` SHALL exponer `render_estimation_prompt(request, version="v1", *, num_examples=3) -> tuple[str, str]`, que devuelve `(system, user)` listos para enviar al modelo, sin espacios al principio ni al final. El loader MUST usar un `Environment` de Jinja2 con `undefined=StrictUndefined`, `trim_blocks=True`, `lstrip_blocks=True` y `autoescape=False`. Una variable no definida en el contexto MUST producir un error, nunca un texto vacío. Una versión que no esté en la lista de versiones admitidas MUST producir un error de dominio antes de acceder al sistema de ficheros. El render MUST ser determinista y no hacer llamadas de red.

#### Scenario: Render por defecto
- **GIVEN** una entrada válida
- **WHEN** se llama a `render_estimation_prompt(request)`
- **THEN** devuelve el system y el user de `v1`

#### Scenario: Cambio de versión
- **WHEN** se llama a `render_estimation_prompt(request, "v2")`
- **THEN** devuelve el system y el user de `v2`, sin cambiar nada más en el código que llama

#### Scenario: Variable no definida
- **GIVEN** un template que usa una variable que el contexto no proporciona
- **WHEN** se renderiza
- **THEN** se produce un error de Jinja2 (`UndefinedError`)

#### Scenario: Versión desconocida
- **WHEN** se llama con `version = "v9"` o `version = "../v1"`
- **THEN** se produce un error de dominio de versión desconocida

#### Scenario: Determinismo
- **WHEN** se renderiza dos veces con la misma entrada, versión y número de ejemplos
- **THEN** los dos resultados son idénticos

#### Scenario: Todas las combinaciones renderizan
- **WHEN** se renderiza cada combinación de versión, `project_type`, `detail_level`, `output_format`, 0 o 3 ejemplos y con o sin proyectos de referencia
- **THEN** todas renderizan sin error y ningún prompt contiene la representación Python de un enum (`ProjectType.`, `DetailLevel.`, `OutputFormat.`)

### Requirement: Mensaje del usuario
`user.j2` SHALL contener una línea con el tipo de proyecto, el bloque `<reference_projects>` con un `<reference_project>` por proyecto de referencia (nombre, resumen y, si están, horas reales y duración en semanas) recorrido con `{% for %}` y omitido entero si no hay proyectos, y al final el bloque `<project_description>` con la descripción literal, sin escapar ni modificar. Ni la descripción ni los proyectos de referencia MUST aparecer en el system prompt.

#### Scenario: Descripción literal
- **GIVEN** una descripción con el texto `App para reservar pistas de pádel con pagos <online> & avisos`
- **WHEN** se renderiza
- **THEN** el user contiene ese texto literal dentro de `<project_description>…</project_description>` y el system no lo contiene

#### Scenario: Proyectos de referencia
- **GIVEN** dos proyectos de referencia
- **WHEN** se renderiza
- **THEN** el user contiene un bloque `<reference_projects>` con dos `<reference_project>`, antes de `<project_description>`, y el system no contiene sus nombres

#### Scenario: Sin proyectos de referencia
- **WHEN** se renderiza con `reference_projects` `null` o `[]`
- **THEN** el user no contiene `<reference_projects>`

### Requirement: Versión v2
`v2` SHALL diferir de `v1` solo en el tono: el rol pide respuestas directas y concisas y una regla de estilo pide frases cortas, sin relleno, y supuestos en una línea. Los ejemplos, las etiquetas, los esqueletos en inglés, las reglas de idioma y el contenido de `user.j2` MUST ser idénticos a los de `v1`.

#### Scenario: Tono distinto
- **WHEN** se renderiza la misma entrada con `v1` y con `v2`
- **THEN** los system prompts son distintos y los mensajes user son idénticos

#### Scenario: Idioma y formato conservados
- **WHEN** se renderiza la misma entrada con `v1` y con `v2`
- **THEN** ambos system prompts empiezan y terminan por la misma regla de idioma y contienen el mismo esqueleto en inglés del formato pedido

### Requirement: Evento de render
Cada render SHALL registrar con structlog el evento `prompt_rendered` con `prompt = "estimation"`, `version`, `output_format`, `detail_level`, `project_type`, `num_examples`, `reference_projects` (número de elementos), `response_language` (`es`, `en` o `null`, el idioma detectado de la descripción), `system_sha256` y `user_sha256` (los 12 primeros caracteres hexadecimales del SHA-256 de cada texto) y `system_chars` y `user_chars`. El evento MUST NOT contener la descripción, los proyectos de referencia ni el texto de los prompts.

#### Scenario: Evento registrado
- **WHEN** se renderiza una petición con `v2` y `phases_table`
- **THEN** se registra `prompt_rendered` con `version = "v2"`, `output_format = "phases_table"` y dos hashes de 12 caracteres hexadecimales

#### Scenario: Hash estable
- **WHEN** se renderiza dos veces la misma entrada
- **THEN** los dos eventos tienen los mismos `system_sha256` y `user_sha256`

#### Scenario: Sin contenido
- **GIVEN** una descripción con el texto `DESCRIPCION-SECRETA-123`
- **WHEN** se renderiza
- **THEN** el evento `prompt_rendered` no contiene `DESCRIPCION-SECRETA-123`

