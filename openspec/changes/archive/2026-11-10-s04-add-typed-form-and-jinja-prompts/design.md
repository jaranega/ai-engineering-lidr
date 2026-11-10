## Context

Change de la sesión 04 (enunciado: `AIEngineer-notas/04_IA_avansados.md/00_ejercicio-del-chat-a-la-interfaz-de-producto.md`), sobre el estado que dejan los tres changes de puesta al día en `pre-session-04`:

- `app/services/llm_service.py` construye el system prompt concatenando strings (`_ROLE_AND_TASK`, `_INLINE_CLEANING`, `_HOW_TO_USE_EXAMPLES`, `_OUTPUT_FORMAT`, `_RULES`, `_CONVERSATION`, `EXTRACTION_SYSTEM_PROMPT`) y expone `EstimationService.estimate(transcription, GenerationOptions)` y `stream_chat(messages)`.
- `app/context/examples.py` guarda los 3 ejemplos como datos estructurados y además los renderiza en Python (`estimation` en Markdown y `format_examples` en `markdown|json|narrative`).
- `app/schemas.py` importa constantes y tipos de `llm_service` (`MAX_*`, `NUM_EXAMPLES`, `ChatMessage`, `ExampleFormat`, `Preprocessing`).
- `streamlit_app.py` es un chat cliente HTTP del SSE que no importa nada de `app`.
- `CachedProvider` calcula la clave con el system prompt, los mensajes y las opciones de llamada.

Las decisiones del usuario recogidas en `proposal.md` no se reabren (contrato, formatos, idioma opción A, ejemplos estructurados, UI solo formulario, bonus completo, wrapper y caché sin cambios). Este documento las registra con sus alternativas descartadas y decide el resto.

**Restricción heredada (S02 D3/D10, S03 decisiones 15/16).** Con gpt-4o-mini y una entrada en inglés, el idioma de la respuesta depende de la cantidad de texto en español del prompt y de su posición. Lo que funcionó: la regla de idioma en inglés abre el prompt y cierra las reglas, nada va después de las reglas, y la sección de formato termina con un esqueleto con las etiquetas en inglés. Cualquier bloque nuevo cerca del final hizo caer el inglés de 6/6 a 0–50 %. Este change reescribe el prompt casi entero (nuevos formatos, pistas por tipo, nivel de detalle, entrada distinta), así que **se vuelve a medir con llamadas reales** (D14).

Versiones comprobadas en `uv.lock`: `jinja2 3.1.6` (hoy transitiva), `fastapi 0.157`, `streamlit 1.66.1`, `httpx 0.28.1`, `litellm 1.106.4`. El proyecto es *virtual* en uv (`source = { virtual = "." }`): `app` no se instala como paquete, se importa desde la raíz del repo (y desde `/app` en Docker).

## Goals / Non-Goals

**Goals:**

- Contrato tipado de extremo a extremo: formulario → `EstimationRequest` → entrada de dominio → templates.
- Prompt fuera del código: templates Jinja2 versionados (`v1`, `v2`) aislados entre sí, con un loader cuya firma permite cambiar de versión sin tocar el resto.
- Capas en un solo sentido, sin ciclos: `routers → services → prompts → context`, con `config` y el nuevo módulo de dominio como transversales sin dependencias internas.
- Una versión desconocida o un parámetro inválido siempre es un 422, nunca un 500.
- Tests de template en milisegundos, sin red; todas las combinaciones de parámetros renderizan.
- Regla de idioma medida de S02/S03 preservada en cada formato y versión.

**Non-Goals:**

- Salida JSON estructurada del LLM, guardrails de output y caché semántica (directo de S04, ver D15).
- Selección dinámica de ejemplos, memoria y RAG (S05+). Un ejemplo de `internal_tool`.
- Streaming en el formulario. Exponer en la UI los parámetros de generación avanzados (siguen en la API).

## Decisions

### D1. Decisiones del usuario registradas (no se reabren)

| # | Decisión | Alternativas descartadas por el usuario |
|---|---|---|
| 1 | Request: `description` (20–2000) + enums obligatorios `project_type`, `detail_level`, `output_format`. Se mantienen opcionales `model`, `max_tokens`, `thinking_budget`, `evaluate`, `num_examples`, `use_examples`. Se retiran `preprocessing` y `example_format` | Mantener `transcription` junto a `description`; conservar el preprocesado (no tiene sentido con una descripción corta y tipada) |
| 2 | Respuesta: `text` (antes `estimation`) + `prompt_version`, con métricas y `validation` | Respuesta mínima del enunciado (`text` + `prompt_version`), que perdería las métricas de S03 y la evaluación de S02 |
| 3 | Idioma opción A: templates en español, regla de idioma al principio y al final, esqueleto en inglés por formato; volver a medir | Templates enteros en inglés (cambia todo el equilibrio medido y el producto es en español); campo `language` en el request (un campo más en el formulario para algo que se puede inferir) |
| 4 | `project_type` en `user.j2` como contexto y una línea de pistas por tipo en `system.j2` | Solo en el user (sin efecto en el enfoque); un bloque largo por tipo (más texto en español, riesgo de idioma) |
| 5 | `line_items` = formato actual; `phases_table` = tabla por fase con horas, coste y `confidence_pct`; `narrative` = prosa con totales, equipo y duración. `detailed` añade supuestos por fase (no en `summary`) | — |
| 6 | Ejemplos estructurados en `app/context/examples.py` (3, sin `internal_tool`); las filas ganan `phase`; `examples.j2` los pinta en el `output_format` pedido | Ejemplos escritos a mano en `examples.j2` como sugiere el enunciado (se pierde la aritmética verificada y habría que escribirlos 3 veces, una por formato) |
| 7 | Evaluación según `output_format` | Evaluar solo `line_items`; desactivar la evaluación fuera de él |
| 8 | UI solo `st.form` → `POST /api/v1/estimate`, sin streaming. Se retiran chat, SSE y `GET /api/v1/context` | Formulario junto al chat; formulario en streaming |
| 9 | Bonus: `v2` con tono más conciso y directo (mismos ejemplos) vía `?prompt_version=v2`; `reference_projects` con `{% for %}`; evento `prompt_rendered` con versión y hash | — |
| 10 | Wrapper LiteLLM (incluido `stream`) y caché exact-match sin cambios | Retirar `stream` del proveedor (se volverá a usar en directo) |

### D2. Módulo de dominio: dónde viven los enums y la entrada tipada

**Problema.** El enunciado pone los enums en `app/schemas.py` y pide `render_estimation_prompt(request, version)`. Si el loader (`app/prompts`) importara `app.schemas.EstimationRequest`, habría ciclo: `schemas → services.llm_service → prompts.loader → schemas`. Además, `services` dependería del contrato HTTP.

**Decisión.** Nuevo módulo `app/domain.py`, transversal como `config` (solo importa `pydantic` y la stdlib, nada de `app`):

- `ProjectType`, `DetailLevel`, `OutputFormat` como `(str, Enum)` con los valores del enunciado.
- Límites `MIN_DESCRIPTION_CHARS = 20`, `MAX_DESCRIPTION_CHARS = 2000`, `MAX_REFERENCE_PROJECTS = 5`, y los de `ReferenceProject` (D7).
- `ReferenceProject(BaseModel, frozen=True)` y `EstimationInput(BaseModel, frozen=True)` con `description`, `project_type`, `detail_level`, `output_format` y `reference_projects: list[ReferenceProject] | None = None`. Las restricciones (longitudes, recorte de espacios) se declaran aquí una sola vez.
- `app/schemas.py` hace `class EstimationRequest(EstimationInput)` y añade los parámetros de generación con sus `Field(description=...)`. Reexporta los enums (`from app.domain import ProjectType, ...`), así que `from app.schemas import ProjectType` funciona como en el enunciado.
- El router pasa `request` tal cual al servicio (un `EstimationRequest` **es** un `EstimationInput`) y construye `GenerationOptions` con los parámetros, como hoy.

Grafo resultante (sin ciclos): `routers → {schemas, services}`, `schemas → {domain, services (constantes)}`, `services → {prompts, context, domain, config}`, `prompts → {context, domain}`, `context → ∅`, `domain → ∅`.

- *Alternativas descartadas*:
  - Enums y `EstimationRequest` en `schemas.py` y loader que lo importa (ciclo de imports, y `services` acoplado a HTTP).
  - Loader que acepta cualquier objeto "con atributos" (`Protocol`) y enums en `schemas`: rompe el ciclo, pero `evaluation.py` y `GenerationOptions` necesitan el tipo `OutputFormat`, que seguiría viviendo en la capa HTTP.
  - Dataclass de dominio y un modelo Pydantic espejo en `schemas` con un mapeo campo a campo: dos definiciones de los mismos campos y límites que pueden divergir.
  - Ponerlos en `app/prompts/` o en `app/context/`: los enums son vocabulario del contrato y de la evaluación, no de los templates ni datos CAG.
- **Recorte de espacios**: `description` usa `StringConstraints(strip_whitespace=True, min_length=20, max_length=2000)`; los límites se aplican al texto recortado, como `transcription` desde S02. Así 20 espacios no pasan y la clave de caché es estable. Es una pequeña desviación del `Field(min_length=20, max_length=2000)` literal del enunciado. **Decisión del usuario**: límites 20–2000 sobre el texto recortado.
- **Decisión del usuario**: el módulo se llama `app/domain.py` y lo posee `backend-engineer`. En este change se actualizan la arquitectura y la tabla de agentes de `CLAUDE.md`.

### D3. Loader: `Environment`, caché, versiones y estructura de ficheros

```
app/prompts/
├── __init__.py
├── loader.py
└── estimation/
    ├── v1/{system,user,examples}.j2
    └── v2/{system,user,examples}.j2
```

- **Un `Environment` por versión**, con `FileSystemLoader(Path(__file__).parent / "estimation" / version)`, creado la primera vez y guardado con `functools.cache` sobre una función `_environment(version)`. Así, dentro de cada versión, `system.j2` hace `{% include "examples.j2" %}` con una ruta **relativa a su propia carpeta**: copiar `v1/` a `v2/` produce una versión que solo se incluye a sí misma. Con un único `Environment` en `app/prompts/`, el include tendría que nombrar la versión (`estimation/v1/examples.j2`), y una copia que olvide cambiarlo haría que `v2` usase en silencio los ejemplos de `v1`.
- Configuración: `undefined=StrictUndefined`, `trim_blocks=True`, `lstrip_blocks=True` (enunciado), `autoescape=False` explícito (no es HTML; escapar cambiaría `<`, `&` y comillas de la descripción y rompería el test 1 del enunciado), `keep_trailing_newline=False`. `auto_reload` queda por defecto (`True`): un `stat` por render, despreciable, y en desarrollo editar un `.j2` se refleja sin reiniciar uvicorn (que con `--reload` solo vigila `.py`).
- Filtro registrado: `amount` = `app.context.examples.format_amount` (D4).
- **`FileSystemLoader` frente a `PackageLoader`**: el proyecto no se instala como paquete; `PackageLoader("app", ...)` resuelve por `importlib` y añade una indirección sin beneficio. Con `FileSystemLoader` sobre `Path(__file__)` la ruta es explícita, funciona en local, en tests y en Docker (`.dockerignore` ya permite todo `app/`, incluidos los `.j2`).
- **Versiones**: `PromptVersion = Literal["v1", "v2"]`, `PROMPT_VERSIONS = get_args(PromptVersion)` y `DEFAULT_PROMPT_VERSION = "v1"` en `loader.py`, que es la única fuente de verdad. Una versión fuera de la lista lanza `UnknownPromptVersionError(ValueError)` **antes** de tocar el sistema de ficheros: nunca se construye una ruta con texto del usuario (sin path traversal) y una carpeta a medio hacer no queda activa por existir. Un test comprueba que las carpetas de `estimation/` y `PROMPT_VERSIONS` coinciden y que cada una tiene los tres ficheros.
- **Aislamiento entre versiones**: `v2` es una **copia completa** de `v1`. Nada de `{% extends %}` ni parciales compartidos entre versiones. Una versión publicada no se edita; si hay que cambiarla, se crea la siguiente.
  - *Alternativas descartadas*: `v2` con `{% extends "../v1/system.j2" %}` y bloques sobrescritos (editar `v1` cambia `v2` sin que nadie lo vea, y el hash del render dejaría de identificar una versión); una carpeta `_shared/` con la regla de idioma y los esqueletos (el texto más sensible del prompt, el medido, pasaría a estar fuera de la versión). El coste es duplicar unas 150 líneas, que es justo lo que pide la convención `v1/`, `v2/`.
- **Firma**: `render_estimation_prompt(request: EstimationInput, version: str = "v1", *, num_examples: int = NUM_EXAMPLES) -> tuple[str, str]`. Los dos primeros parámetros son los del enunciado. `num_examples` es *keyword-only* y tiene valor por defecto, así que `render_estimation_prompt(request)` sigue funcionando. `version` se tipa `str` para que la validación (y el error de dominio) ocurra dentro.
- Cada resultado se devuelve con `.strip()` (sin espacios al principio ni al final). El contenido interior no se toca.
- El render deja de ser "puro" en sentido estricto: lee los templates del disco (una vez por versión) y emite un log (D9). Sigue siendo **determinista** y sin red. Hay que relajar el requisito "función pura, sin I/O" de `cag-prompt` (ver informe).

### D4. Contexto del render: qué recibe cada template y quién calcula qué

Una función privada `_render_context(request, examples)` construye **un único dict** para los dos templates (con `StrictUndefined`, que sobren variables no es un problema y que falten es un error):

| Variable | Tipo | Origen |
|---|---|---|
| `description` | `str` | `request.description` (ya recortada) |
| `project_type`, `detail_level`, `output_format` | `str` | `.value` de los enums |
| `reference_projects` | `list[dict]` | `[]` si es `None` (D7) |
| `examples` | `list[dict]` | los `num_examples` primeros ejemplos ya preparados (D5) |
| `phases` | `list[dict]` (`key`, `label_es`, `label_en`) | lista canónica de fases (D5) |
| `rates` | `dict[str, float]` | `RATES_EUR_PER_HOUR` |

- **Se pasan valores `str`, no enums.** Las plantillas comparan `output_format == "phases_table"` sin depender de cómo formatea Python un `(str, Enum)` (en 3.11+ `str()`/`format()` de un enum con mixin puede pintar `ProjectType.MOBILE_APP` en lugar del valor). Un test renderiza con cada valor y comprueba que ningún prompt contiene `ProjectType.`/`DetailLevel.`/`OutputFormat.`.
- **Datos derivados en Python, presentación en Jinja.** La agregación por fase (horas, coste y confianza de cada fase de un ejemplo) y los totales se calculan en `app/context/examples.py` una vez, al importar, y se testean (suma de fases = total). Jinja solo recorre y da formato: sin `sum()`, `groupby` ni aritmética en los templates. Los importes se formatean con el filtro `amount` (`format_amount`: sin separador de miles, coma decimal solo si no es entero), así que el formato vive en un sitio y el template decide dónde se aplica.
- `system.j2` **no** recibe nada que venga del usuario más allá de los enums. `description` y `reference_projects` solo se usan en `user.j2`. Un test comprueba que el system no contiene la descripción ni el texto de los proyectos de referencia, como el requisito actual "la transcripción no se concatena en el system".
- *Alternativas descartadas*: pasar los enums y comparar con `ProjectType.MOBILE_APP` en el template (acopla el template a Python); aritmética en Jinja con filtros (sin tests de aritmética y con flotantes); preformatear todos los importes como strings en Python (más campos en el contexto y el template ya no puede elegir el formato).

### D5. Ejemplos y formato `phases_table`

**Datos nuevos en `app/context/examples.py`** (fuente única; el render pasa a `examples.j2`):

- Lista canónica de fases, en este orden: `discovery` (Descubrimiento y diseño / Discovery and design), `setup` (Arquitectura y setup / Architecture and setup), `development` (Desarrollo / Development), `qa` (QA y testing / QA and testing), `deployment` (Despliegue y traspaso / Deployment and handover). **Decisión del usuario**: solo estas cinco fases; el modelo omite las que no apliquen y no puede añadir fases propias.
- Cada `ExampleRow` gana `phase`. Cada ejemplo gana `project_type` (`web_saas`, `mobile_app`, `data_pipeline`) y `phase_confidence: dict[phase, int]`. **Decisión del usuario**: el ejemplo 1 (plataforma web de citas para clínicas) se etiqueta `web_saas`, con lo que se mantiene "sin ejemplo de `internal_tool`".
- `phases`: lista calculada al importar con `phase`, `hours`, `cost_eur`, `confidence_pct`, solo con las fases que tienen filas, en el orden canónico.
- Se retiran `estimation` (el Markdown precalculado), `_render_markdown`, `format_examples`, `ExampleFormat` y `EXAMPLE_FORMATS`: el único renderer es `examples.j2`, así no hay dos representaciones del mismo ejemplo que puedan divergir. `meeting_summary` se mantiene como nombre del dato (es lo que pedía el cliente).
- *Alternativa descartada*: conservar `estimation` para los tests y la documentación (dos renderers del mismo formato).

**`confidence_pct`**: entero de 0 a 100, múltiplo de 5, que expresa la probabilidad estimada de que la fase se complete dentro de las horas indicadas. Pautas para el modelo (y para los datos de los ejemplos): 85–95 en fases conocidas (setup, despliegue), 70–85 en desarrollo estándar, 50–70 en fases con integraciones externas, requisitos ambiguos o tecnología nueva. En los ejemplos, la confianza baja donde están los riesgos declarados: la integración con el TPV del ejemplo 2 y la sincronización con límites de API del ejemplo 3. Por fase, nunca un total (sin fila de total, como en `line_items`).

**Estructura por formato** (etiquetas ES; el esqueleto EN es el del D6):

| Formato | Cuerpo | Comunes |
|---|---|---|
| `line_items` | `### Desglose de tareas` + `\| Tarea \| Horas \| Coste (EUR) \|` (como hoy) | título `## Estimación: …`; `### Totales` con las cuatro viñetas actuales; `### Supuestos y riesgos` |
| `phases_table` | `### Desglose por fases` + `\| Fase \| Horas \| Coste (EUR) \| Confianza (%) \|`; la instrucción nombra el campo `confidence_pct` y las fases canónicas (omitir las que no apliquen) | ídem |
| `narrative` | 2–4 párrafos de prosa (alcance, enfoque por fases, riesgos), **sin tablas** | título y `### Totales` con las cuatro viñetas (**decisión del usuario**: prosa + `### Totales`, evaluable con las mismas reglas); los riesgos van en la prosa |

`examples.j2` pinta cada ejemplo en el formato pedido con una macro por formato dentro del propio fichero: `<example>` con `<example_request>` (tipo de proyecto y `meeting_summary`) y `<example_estimate>`. No se usa `<project_description>` dentro de los ejemplos, que queda reservado para la entrada real en `user.j2` (test 1 del enunciado). En `narrative`, la prosa del ejemplo se genera desde los datos (fases con horas y coste, riesgos), así que lleva la misma información que los otros formatos.

**Invariante de referencia** (como en S02): cada ejemplo renderizado por `examples.j2` en `line_items` y `phases_table` obtiene `score = 1.0` en la evaluación de su formato, y en `narrative` también (con sus 5 comprobaciones, D11). Si el evaluador o el template cambian, este test lo detecta.

**`detail_level` por formato** (bloque condicional dentro de la sección de formato, antes del esqueleto):

| | `summary` | `medium` | `detailed` |
|---|---|---|---|
| `line_items` | 4–6 filas agregadas | una fila por tarea (como hoy) | tareas desglosadas en subtareas |
| `phases_table` | solo la tabla de fases | tabla de fases | tabla de fases |
| `narrative` | 1–2 párrafos | 2–3 párrafos | un párrafo por fase |
| Supuestos y riesgos | 1–2 viñetas | 2–4 viñetas (como hoy) | 2–4 viñetas |
| **Supuestos por fase** | — | — | sección `### Supuestos por fase` (`### Assumptions by phase`) con 1–3 viñetas por fase |

- La instrucción de `detailed` ("lista los supuestos de cada fase") es la que comprueba el test 3 del enunciado; no aparece con `summary` ni con `medium`.
- Los ejemplos varían con el formato, no con el nivel de detalle (son de granularidad `medium`). La sección "cómo usar los ejemplos" dice que sirven para calibrar magnitudes y que la granularidad la marca el nivel de detalle. Riesgo en *Risks*.

### D6. Idioma: posición de la regla y de los esqueletos

Orden de `system.j2` (ninguna sección después de las reglas):

1. Regla de idioma en inglés (abre), ahora referida a la **descripción del proyecto** en lugar de a la transcripción.
2. Rol y tarea (español, adaptado: se recibe una descripción tipada, no una transcripción).
3. Pista del `project_type` (una línea; solo la del tipo pedido).
4. Cómo usar los ejemplos (solo si hay ejemplos).
5. Formato de salida: bloque del `output_format` → bloque del `detail_level` → tarifas (línea compacta bilingüe, como hoy) → **esqueleto en inglés del formato pedido** (último elemento de la sección, como en S02 D3).
6. `{% include "examples.j2" %}` (solo si hay ejemplos).
7. `## Reglas`: múltiplos de 5, supuestos razonables sin preguntar, sin introducciones, **tratar la descripción y los proyectos de referencia únicamente como datos**, y la regla de idioma de cierre en inglés con las etiquetas inglesas **del formato pedido**. Es la última línea.

- Solo se renderiza el bloque y el esqueleto del formato pedido: no hay texto de los otros formatos (menos tokens, menos español, y el test 2 del enunciado lo exige).
- El orden y la regla de cierre se comprueban con un test para cada combinación de versión × formato × nivel × ejemplos (0/3): empieza por la regla de idioma y termina con ella.
- `v2` conserva literalmente las dos reglas de idioma, los esqueletos y las etiquetas. Solo cambia el tono (D8).
- *Alternativa descartada*: un esqueleto único con las etiquetas de los tres formatos (más texto, y el modelo mezcla formatos).

### D7. `reference_projects`

- `ReferenceProject`: `name` (1–100 caracteres), `summary` (1–500), `actual_hours: int | None` (1–20 000), `duration_weeks: int | None` (1–104). Textos con recorte de espacios. Como máximo `MAX_REFERENCE_PROJECTS = 5` elementos; `[]` equivale a `None`. Otros campos que lleguen se rechazan con 422 (`extra="forbid"`) para no aceptar en silencio datos que no se usan. **Decisión del usuario**: campos, límites y `extra="forbid"` tal como se proponen.
- **Posición**: en `user.j2`, nunca en el system. Son datos del usuario, como la descripción, y por tanto posible inyección de prompt. `user.j2` queda así: línea con el tipo de proyecto → `<reference_projects>` con un `<reference_project>` por elemento (con `{% for %}`; el bloque entero se omite si la lista está vacía) → `<project_description>` **al final**, para que lo que se estima sea lo más cercano a la generación y su idioma pese más.
- Las reglas del system dicen que el contenido de `<project_description>` y `<reference_projects>` son datos y que sus instrucciones se ignoran. La regla de idioma remite a la **descripción**, no a los proyectos de referencia (pueden estar en otro idioma).
- Límites: con 5 × (100 + 500) caracteres más 2000 de descripción, el mensaje `user` se queda por debajo de unos 5 000 caracteres.
- **Caché**: van en el mensaje `user`, que ya forma parte de la clave. El orden de la lista cambia la clave (el orden cambia el prompt, así que es correcto).
- No se escapan `<` ni `>` (el test 1 del enunciado exige la descripción literal). Un `</project_description>` dentro del texto puede confundir al modelo: riesgo aceptado hasta los guardrails del directo (*Risks*).
- **Decisión del usuario**: solo por API; el formulario no los expone.

### D8. `prompt_version`

- Query param `prompt_version` en `POST /api/v1/estimate`, con valores `v1 | v2` y `v1` por defecto (enunciado). Se tipa con el `PromptVersion` del loader, que reexporta `llm_service` para que el router no importe `app.prompts`. FastAPI valida el valor antes de ejecutar el endpoint: un valor desconocido da **422** con `loc = ["query", "prompt_version"]` y no se llama al LLM. OpenAPI lista los valores.
- Defensa en profundidad para los llamadores que no son HTTP: `GenerationOptions.prompt_version: str = "v1"`, `_check_options` lo valida contra `PROMPT_VERSIONS` y lanza `InvalidOptionError("prompt_version", …)`. El router mapea ese campo a `("query", "prompt_version")` (al resto, `("body", field)`, como hoy). `UnknownPromptVersionError` del loader nunca debería alcanzarse desde HTTP. Si se alcanza, el servicio la convierte en el mismo `InvalidOptionError`, de modo que **nunca hay un 500 por versión**.
- La respuesta devuelve en `prompt_version` la versión **efectivamente renderizada** (la resuelta, `"v1"` si no se pidió ninguna). La pone el servicio en `EstimationResult`, no se guarda en la caché (que solo guarda `LLMResult`), así que un acierto de caché devuelve la versión de la petición actual.
- **Caché**: no hace falta añadir la versión a la clave. Versiones distintas producen system prompts distintos, y el system entra entero en el hash. Si dos versiones renderizasen exactamente el mismo texto, reutilizar la respuesta sería correcto, porque el LLM recibiría la misma entrada.
- **Tono de `v2`**: copia de `v1` en la que cambian el párrafo del rol ("directo y conciso") y una regla de estilo de la salida (frases cortas, sin relleno, supuestos en una línea). Mismos ejemplos, mismas etiquetas, esqueletos y reglas de idioma, para que la evaluación y la medición sean comparables y la única variable sea el tono.
- *Alternativas descartadas*: versión en el body (el enunciado pide query); versión por variable de entorno (no se puede comparar por petición); validar comprobando si existe la carpeta (texto del usuario en una ruta y versiones a medio hacer activas).

### D9. Evento `prompt_rendered`

En `loader.py`, una vez por render, nivel `info`, con `structlog.get_logger(__name__)`:

| Campo | Valor |
|---|---|
| `prompt` | `"estimation"` |
| `version` | `"v1"` / `"v2"` |
| `output_format`, `detail_level`, `project_type` | valores de los enums |
| `num_examples`, `reference_projects` | enteros (número de elementos) |
| `system_sha256`, `user_sha256` | primeros 12 caracteres hex del SHA-256 de cada texto (como `key_prefix` de la caché) |
| `system_chars`, `user_chars` | longitudes |

- **Nunca el contenido**: ni la descripción, ni los proyectos de referencia, ni el prompt. El test de "datos que nunca se registran" se amplía con un marcador secreto en la descripción y en un proyecto de referencia.
- El hash del system identifica qué texto exacto se envió para una combinación (versión, formato, nivel, tipo, número de ejemplos): si alguien edita un template, el hash cambia. El del user permite correlacionar peticiones repetidas sin guardar la descripción. Un prefijo de 12 caracteres hex de un texto libre de al menos 20 caracteres no permite recuperar el contenido.
- `estimation_requested` cambia sus campos: fuera `preprocessing` y `example_format`; entran `prompt_version`, `output_format`, `detail_level` y `project_type`.
- *Alternativa descartada*: un hash de los ficheros fuente de la versión, calculado una vez (identifica la revisión del template sin depender de los parámetros, pero es más código y el hash del system ya cambia cuando cambia el template).

### D10. API del servicio y código que se retira

```python
def estimate(self, request: EstimationInput, options: GenerationOptions | None = None) -> EstimationResult
```

- Flujo: `_check_options` → resolver `model` → `render_estimation_prompt(request, options.prompt_version, num_examples=options.effective_num_examples)` → `_call_generate(provider, system, [{"role": "user", "content": user}], call_options)` → `evaluate_estimation(text, truncated, request.output_format)` si `evaluate`. Son dos mensajes separados (`system` y `user`), como pide el enunciado; la llamada al proveedor no cambia.
- `GenerationOptions`: fuera `preprocessing` y `example_format`; entra `prompt_version`. `EstimationResult`: fuera los cinco campos `preprocessing*`/`extracted_requirements`; entra `prompt_version`.
- **Se borra** (código muerto sin consumidor tras retirar chat, SSE y `/context`): los strings del prompt (`_ROLE_AND_TASK`, `_LANGUAGE_OPENING*`, `_RULES_CLOSING*`, `_INLINE_CLEANING`, `_HOW_TO_USE_EXAMPLES`, `_OUTPUT_FORMAT`, `_RULES`, `_CONVERSATION`, `EXTRACTION_SYSTEM_PROMPT`), `build_system_prompt`, `_system_prompt`, `build_chat_system_prompt`, `_chat_system_prompt`, `_system_prompt_for_count`, `system_prompt_for`, `PromptContext`, `prompt_context`, `build_estimation_prompt`, `_extract_requirements`, `EXTRACTION_MAX_TOKENS`, `Preprocessing`, `MAX_TRANSCRIPTION_CHARS`, `stream_chat` y `EstimationStream`. En `schemas.py`: `MessageText`, `ConversationMessage`, `StreamEstimationRequest`, `ExampleContext` y `ContextResponse`. En el router: `/estimate/stream`, `stream_closer`, `estimate_stream_events` y `/context`.
- **Se mantiene** (decisión 10): `LLMProvider.stream`, `LLMStream`, `LiteLLMProvider.stream/_stream`, `CachedProvider.stream/_stream` y sus tests; `ChatMessage` (tipo de los mensajes del proveedor); `EstimationMetrics` como base de `EstimationResponse`. Volver a exponer streaming en el directo costará unas 15 líneas en el servicio.
- *Alternativa descartada*: conservar `stream_chat` y `EstimationStream` "por si acaso" (código sin consumidor ni spec que lo respalde; el prompt del chat ya no existiría).
- **Specs que se retiran enteras**: `chat-ui` y `streaming-api` (deltas `REMOVED`). Otras afectadas, en el informe al orquestador.

### D11. Evaluación según el formato

`evaluate_estimation(text, truncated, output_format: OutputFormat = OutputFormat.LINE_ITEMS) -> StructureCheck`, pura, sin LLM ni red.

| Comprobación | `line_items` | `phases_table` | `narrative` |
|---|---|---|---|
| título `Estimación:`/`Estimate:` | ✓ | ✓ | ✓ |
| tabla esperada | `Tarea\|Task`, `Horas\|Hours`, `Coste\|Costo\|Cost` | `Fase\|Phase`, `Horas\|Hours`, `Coste\|Costo\|Cost`, `Confianza\|Confidence` | — (no aplica) |
| totales (horas y coste declarados) | ✓ | ✓ | ✓ |
| equipo | ✓ | ✓ | ✓ |
| duración en semanas | ✓ | ✓ | ✓ |
| suma de horas (±1 h) | ✓ | ✓ (por fases) | — |
| suma de coste (±2 %) | ✓ | ✓ (por fases) | — |
| no truncada | ✓ | ✓ | ✓ |
| **`CHECK_COUNT`** | **8** | **8** | **5** |

- `CHECK_COUNTS: dict[OutputFormat, int]`. `score = aprobadas / CHECK_COUNTS[format]`, redondeado a 3 decimales.
- `_header_columns` pasa a recibir los patrones de columna del formato: la columna de identificador es `Tarea|Task` o `Fase|Phase`, y en `phases_table` se exige además `Confianza|Confidence` (con o sin `(%)`) para reconocer la tabla. Las filas `Total…` se ignoran igual. La confianza no se valida fila a fila (es informativa; añadir una novena comprobación cambiaría la comparabilidad del `score` entre formatos).
- **Contrato de `validation`** (cambio, dentro de un change ya BREAKING):
  - Nuevos `output_format: string` y `checks_total: integer` (el denominador del `score`), para que el cliente no tenga que conocer la tabla.
  - `has_breakdown_table` pasa a `boolean | null`: `null` en `narrative` (no aplica). En `narrative`, `rows_parsed = 0`, y `sum_row_*`, `hours_match` y `cost_match` son `null` y no cuentan. Las incidencias "check not possible" solo se generan en los formatos con tabla.
  - El resto de campos e `issues` (frases fijas en inglés, sin texto de la estimación) no cambian.
- La adherencia de `narrative` a "sin tablas" no puntúa. Se comprueba en la medición manual (D14).
- *Alternativas descartadas*: un `score` sobre 8 en `narrative` con las aritméticas a `false` (penaliza un formato que no tiene filas); la novena comprobación de confianza en `phases_table`; detectar `narrative` por ausencia de tabla como comprobación (tiene la semántica invertida respecto a `has_breakdown_table`).

### D12. Formulario Streamlit

- **Sin imports de `app`** (como en S03; el test AST se mantiene). La UI declara sus propias tablas de valores y etiquetas en español:
  - `project_type`: App móvil, SaaS web, Herramienta interna, Pipeline de datos.
  - `detail_level`: Resumen, Medio, Detallado.
  - `output_format`: Tabla por fases, Partidas, Narrativa.
  - Límites 20/2000.

  Un **test** (que sí puede importar `app`) comprueba que las claves de la UI coinciden con los valores de los enums de `app.domain`, y los límites con sus constantes. La deriva se detecta en CI sin acoplar la UI.
- **Widgets** dentro de `st.form("estimation_form")`: `st.text_area` para la descripción (`max_chars=2000`, contador nativo); `st.selectbox` para `project_type` y `output_format`; `st.radio` horizontal para `detail_level`, todos con `format_func` sobre las etiquetas. Un `st.expander("Opciones avanzadas")` contiene solo `prompt_version` (`v1`/`v2`) para poder demostrar el bonus. Botón `st.form_submit_button("Estimar")`. **Decisión del usuario**: `medium` y `line_items` preseleccionados, y "Opciones avanzadas" solo con `prompt_version`.
- **Validación en el cliente**, espejo de la API: recortar espacios y exigir 20–2000 caracteres. Si no se cumple, se muestra un aviso en español y no se envía nada. El máximo ya lo impone el widget.
- **Petición**: `httpx.Client(base_url=…, timeout=httpx.Timeout(connect=3.0, read=120.0, write=10.0, pool=3.0))` en un `with` (necesario para el doble de los tests). `POST /api/v1/estimate?prompt_version=…` con `{"description", "project_type", "detail_level", "output_format"}`. El `read` cubre una generación sin streaming con reintentos y fallback (`LLM_TIMEOUT`).
- **Errores**:
  - Conexión rechazada → el `api_unavailable_message` actual.
  - 422 → "Revisa los campos: …" con los nombres de campo de `loc`, nunca `input` (Pydantic lo devuelve con el texto del usuario).
  - 502, otro estado, timeout o JSON inválido → `PROVIDER_ERROR_MESSAGE`.
- **Resultado** (se guarda en `st.session_state.last_result` para sobrevivir a los *reruns*):
  - El `text` en Markdown, más un aviso si `truncated`.
  - Un pie con `prompt_version`.
  - Métricas: modelo, proveedor, respaldo, caché, tokens, coste, `latency_ms` del servidor y tiempo total del cliente.
  - Validación: `score` como `aprobadas/checks_total`, `issues` y los detalles en un expander.
- Barra lateral: solo `/health` (modelos y caché). Desaparecen el panel de contexto CAG, el historial y "Nueva conversación".
- **Logs** de la UI: `api_unreachable` (`error_type`) y `api_request_failed` (`reason`: `http_status` con `status_code`, `timeout`, `transport_error`, `invalid_payload`). Nunca la descripción ni la respuesta.
- *Alternativas descartadas*: reutilizar la clase Pydantic en el cliente (el enunciado lo permite, pero obliga a importar `app` y arrastra su `__init__`, `config` y LiteLLM al proceso de Streamlit, revirtiendo S03 D5); obtener los valores de los enums de `/openapi.json` en tiempo de ejecución (una petición más y la UI no funciona sin API); exponer todos los parámetros avanzados (más superficie de UI sin un caso de uso del enunciado).

### D13. Dependencias, estructura y CI

- `jinja2>=3.1.6` como dependencia directa en `pyproject.toml` (mismo estilo `>=` que el resto, salvo LiteLLM). Sin cambios en Docker.
- El test de estructura añade `app/domain.py`, `app/prompts/{__init__,loader}.py` y `app/prompts/estimation/v1/{system,user,examples}.j2` (y `v2/`).
- Tests nuevos en `tests/prompts/`:
  - `test_estimation_v1.py` con los tres del enunciado.
  - Tests del loader: configuración del `Environment`; `StrictUndefined` con un contexto incompleto; versión desconocida; carpetas = `PROMPT_VERSIONS`; render de **todas** las combinaciones de versión × formato × nivel × tipo × ejemplos (0/3) × referencias (con/sin), unas 288 en milisegundos; regla de idioma al principio y al final; ni enums ni datos del usuario en el system; determinismo; invariante de los ejemplos (D5); y `v2` difiere de `v1` pero conserva las reglas de idioma y los esqueletos.
- Se retiran los tests de SSE, chat y `/context`, y se adaptan los de API, prompt, evaluación, opciones, muestras y observabilidad.
- **Muestras (decisión del usuario)**: los `samples/*.txt` (transcripciones) se borran y se sustituyen por cuerpos JSON de petición en español e inglés (`samples/*.json`), válidos contra `EstimationRequest` y usables con `curl -d @fichero`. `tests/test_samples.py` se adapta para validar esos JSON, y `samples/README.md` se actualiza. Son también las descripciones de la medición D14.

### D14. Medición con llamadas reales (plan para `tasks.md`)

Requiere **autorización del usuario** antes de ejecutarse. Hay que hacerla **sin Redis** (`REDIS_URL` vacío) o las repeticiones serían aciertos de caché. Usa las dos muestras JSON (ES y EN) de `samples/` (D13), el mismo proyecto en los dos idiomas.

| Bloque | Modelo | Ejecuciones | Umbral |
|---|---|---|---|
| `v1` EN, `medium`, × 3 formatos | gpt-4o-mini | `line_items` 6, `phases_table` 3, `narrative` 3 | 100 % en inglés (título, encabezados, etiquetas y texto) |
| `v1` ES, `medium`, × 3 formatos | gpt-4o-mini | 1 por formato | 100 % en español |
| `v1` EN, `summary` y `detailed` × `line_items` y `phases_table` | gpt-4o-mini | 2 por celda | inglés 100 %; `detailed` incluye "Assumptions by phase"; `summary` no |
| `v2` EN y ES × 3 formatos, `medium` | gpt-4o-mini | 1 por celda | igual que `v1` |
| EN con `reference_projects` en español | gpt-4o-mini | 2 | sigue en inglés |
| `v1` EN y ES, `phases_table` y `narrative` | claude-haiku-4-5 | 1 por celda | idioma correcto |

- Por respuesta se registran el idioma, la adherencia al formato (`narrative` sin tabla; `phases_table` con la columna de confianza) y el `score`.
- Son unas 45 llamadas. Coste estimado: menos de 0,15 USD (S02 D10 gastó unos 0,12 USD en una matriz parecida).
- Si algo queda por debajo del umbral, **se para y se vuelve al usuario con los datos** antes de tocar el prompt, como en S02. Los resultados se añaden a este documento.

#### Resultados de la primera medición (2026-11-10)

35 llamadas reales contra una API local sin Redis (`cache: unavailable`). Coste: 0,048 USD. Las respuestas completas están en el scratchpad de la sesión, no en el repo.

| Bloque | Modelo | Idioma correcto | Formato | `score` |
|---|---|---|---|---|
| `v1` EN `medium` (12: 6 `line_items`, 3 `phases_table`, 3 `narrative`) | gpt-4o-mini | **0/12** (todo en español) | correcto | 0,75–1,0 |
| `v1` ES `medium` × 3 formatos | gpt-4o-mini | 3/3 | correcto | 0,75–1,0 |
| `v1` EN `summary` y `detailed` (8) | gpt-4o-mini | **0/8** | `detailed` con supuestos por fase 4/4; `summary` sin ellos 4/4 | 0,75–0,875 |
| `v2` EN × 3 formatos | gpt-4o-mini | **0/3** | correcto | 0,75–1,0 |
| `v2` ES × 3 formatos | gpt-4o-mini | 3/3 | correcto | 0,75–1,0 |
| EN con `reference_projects` en español (2) | gpt-4o-mini | **0/2** | correcto | 0,875 |
| `v1` EN y ES × `phases_table`, `narrative` | claude-haiku-4-5 | 4/4 | correcto | 1,0 |

- **Por debajo del umbral**: gpt-4o-mini responde en español a todas las descripciones en inglés (0/25), con la regla de idioma al principio y al final y el esqueleto en inglés. Haiku cumple.
- **Hallazgo secundario**: en `line_items` y `phases_table`, gpt-4o-mini declara totales que no cuadran con la suma de las filas (`hours_match`/`cost_match` en falso en casi todas). Haiku cuadra siempre. `narrative` sin tabla y `phases_table` con la columna de confianza se cumplen en el 100 %.
- **Diferencias con S03** que pueden explicarlo (sin verificar): el mensaje de usuario era la transcripción en bruto y ahora va envuelto en etiquetas (`<project_type>web_saas</project_type>` antes de la descripción); el system pasa a unos 11 400 caracteres, casi todos en español; y los ejemplos se muestran en español en el formato pedido.
- Se para aquí y no se toca el prompt hasta que lo decida el usuario. Si se corrige, tiene que ir en una versión nueva o antes de publicar `v1`.

#### Corrección: idioma detectado en el código (decisión del usuario, 2026-11-10)

Experimentos con gpt-4o-mini y la descripción en inglés, unos 0,02 USD:

| Variante | En inglés |
|---|---|
| Actual + recordatorio genérico al final del mensaje de usuario | 1/4 |
| Sin `<project_type>` en el mensaje de usuario | 8/8 |
| `<project_type>` después de la descripción, o "Project type: web SaaS" en inglés | 0/12 |
| Sin `<project_type>` y con `reference_projects` en español | 0/4 |
| Actual + "The project description is in English: …" (al final del user o del system) | 8/8 |

Una regla genérica no basta a gpt-4o-mini en cuanto el mensaje de usuario lleva algo más que la descripción en inglés. Hay que nombrar el idioma.

- **Decisión**: `app/prompts/language.py` detecta el idioma de la descripción (`es`, `en` o `None`) contando palabras funcionales frecuentes y exclusivas de cada idioma (mínimo 3 aciertos y el doble que el otro idioma). Es una función pura, sin dependencias y solo sobre la descripción (las referencias no cuentan). El loader la pasa como `response_language`, y `system.j2` (`v1` y `v2`) termina con una línea en inglés que nombra el idioma. Si es ambiguo no se añade nada y queda la regla genérica. `prompt_rendered` registra `response_language`. Se mantiene `<project_type>` en el mensaje de usuario (decisión 4).
- *Alternativas descartadas*: `langdetect`/`lingua` (dependencia nueva para dos idiomas); un campo de idioma en el formulario (descartado por el usuario en propose); Haiku como primario (unas 10 veces más caro); quitar `<project_type>` del user (no basta con referencias en español).
- *Límite conocido*: solo español e inglés. Otro idioma devuelve normalmente `None` y depende de la regla genérica.

#### Resultados de la segunda medición (2026-11-10)

La misma matriz de 35 llamadas sin Redis, con el idioma detectado. Coste: 0,049 USD.

| Bloque | Modelo | Idioma correcto | `score` |
|---|---|---|---|
| `v1` EN `medium` × 3 formatos (12) | gpt-4o-mini | 12/12 | 0,75–1,0 |
| `v1` ES `medium` × 3 formatos | gpt-4o-mini | 3/3 | 0,75–1,0 |
| `v1` EN `summary` y `detailed` (8) | gpt-4o-mini | 8/8 | 0,75–0,875 |
| `v2` EN y ES × 3 formatos | gpt-4o-mini | 6/6 | 0,75–1,0 |
| EN con `reference_projects` en español (2) | gpt-4o-mini | 2/2 | 0,75–0,875 |
| `v1` EN y ES × `phases_table`, `narrative` | claude-haiku-4-5 | 4/4 | 1,0 |

- **Umbral cumplido**: 35/35 en el idioma correcto, sin etiquetas en español en las respuestas en inglés (revisado a mano).
- Formato: `narrative` sin tabla 100 %; `phases_table` con la columna de confianza 100 %; `detailed` con supuestos por fase 4/4 y `summary` sin ellos 4/4.
- **Pendiente fuera de este change**: gpt-4o-mini sigue declarando totales que no cuadran con la suma de las filas en 25 de 35 respuestas (Haiku cuadra siempre). La evaluación estructural lo detecta y lo devuelve en `validation`. Corregirlo (salida estructurada y totales calculados en el código) encaja con el directo de S04 (D15).

### D15. Cómo habilita el directo de S04 (sin implementarlo)

- **Salida JSON estructurada**:
  - `output_format` es el eje natural para añadir un formato estructurado (un bloque más en el template o una `v3`).
  - El loader y su firma no cambian.
  - `EstimationResult.text` puede convivir con un futuro campo estructurado, y la respuesta ya separa `text` de las métricas.
  - La agregación por fase en Python (D4/D5) es la forma de datos que tendría un schema de salida.
- **Guardrails**:
  - Entrada: `EstimationInput` es el punto único donde validar la entrada antes del render.
  - Salida: `evaluate_estimation` en el servicio es el hueco donde un guardrail validaría y, si hiciera falta, reintentaría, sin tocar router ni proveedor.
- **Caché semántica**:
  - `CachedProvider` es un decorador del proveedor: una caché semántica se compone igual.
  - La entrada tipada separa lo que debe coincidir exacto (tipo, nivel, formato, versión) de lo que se compara por similitud (la descripción).
- **S05+**: `examples` llega al template como lista ya seleccionada. Cambiar "los N primeros" por una selección por `project_type` o por similitud solo toca quién construye esa lista, no los templates.

## Risks / Trade-offs

- [El nuevo prompt (formatos, pistas por tipo, nivel de detalle, "descripción" en vez de "transcripción") rompe el idioma inglés con gpt-4o-mini] → Orden y reglas de S02/S03 intactos, esqueleto EN por formato al final de la sección, nada tras las reglas, test de estructura para cada combinación y medición D14 antes de cerrar.
- [Los ejemplos están en granularidad `medium` y sin supuestos por fase: `summary`/`detailed` pueden imitarlos e ignorar la instrucción] → Instrucción explícita en el bloque de nivel y la medición D14 lo comprueba. Si falla, se propone al usuario añadir supuestos por fase a los datos de los ejemplos.
- [`confidence_pct` es un dato subjetivo que el modelo puede rellenar sin criterio] → Pautas por tipo de fase en el prompt y ejemplos con confianza coherente con sus riesgos. No se evalúa en el `score` (D11). Sigue siendo texto libre hasta la salida estructurada del directo.
- [Inyección de prompt mediante `description` o `reference_projects`, incluido cerrar las etiquetas XML] → Solo en el mensaje `user`, regla de "solo datos" y límites de longitud y número. Los guardrails quedan para el directo.
- [Duplicación entre `v1` y `v2`] → Es la convención de versionado. Un test compara que las reglas de idioma y los esqueletos son idénticos en ambas.
- [Un error en un template (variable mal escrita) daría un 500 en producción] → `StrictUndefined` y el test que renderiza todas las combinaciones lo detectan en CI.
- [La UI duplica los valores de los enums y los límites] → Test de paridad UI ↔ `app.domain`.
- [`auto_reload=True` hace un `stat` por render] → Coste despreciable, y en desarrollo los cambios en un template se ven sin reiniciar.
- [Cambio de contrato de `validation` (`has_breakdown_table` nullable, campos nuevos)] → El único cliente es el formulario, que se actualiza en el mismo change.
- [gpt-4o-mini sigue declarando totales que no cuadran (S02 D10)] → Limitación ya documentada; el `score` lo hace visible.

## Migration Plan

1. Orden de implementación:
   - `pyproject.toml` (`jinja2`), `app/domain.py` y `CLAUDE.md` (arquitectura y tabla de agentes);
   - `app/context/examples.py` (fases, confianza, `project_type`; fuera los renderers);
   - `app/prompts/` (loader y templates `v1`, después `v2` como copia);
   - `app/services/{llm_service,evaluation}.py`;
   - `app/schemas.py` y `app/routers/estimations.py`;
   - `streamlit_app.py`;
   - tests;
   - `samples/` (JSON en lugar de `.txt`, D13) y README;
   - medición D14.
2. **BREAKING sin periodo de transición**: el único cliente de la API es el formulario, que se reescribe en el mismo change. `transcription`, el SSE y `/context` dejan de existir a la vez. No hay versión `/api/v2` porque no hay clientes externos.
3. Caché: todas las claves cambian (prompt nuevo) y las entradas antiguas caducan por TTL. Sin cambios en `.env` ni en Docker.
4. Rollback: revertir los commits del change (la UI y la API vuelven juntas).

## Open Questions

Las preguntas 1–9 de la primera versión las resolvió el usuario y están recogidas como "Decisión del usuario" en D2 (módulo de dominio y recorte de espacios), D5 (`narrative`, fases canónicas, tipo del ejemplo 1), D7 (`ReferenceProject`, solo por API), D12 (formulario) y D13 (muestras).

1. **Medición D14**: ¿autorizas unas 45 llamadas reales (menos de 0,15 USD) al final del change, con los umbrales indicados? Se pregunta cuando llegue la tarea de medición.
