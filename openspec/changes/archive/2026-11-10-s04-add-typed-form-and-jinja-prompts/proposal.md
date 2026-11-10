## Why

Sesión 04 (enunciado: `AIEngineer-notas/04_IA_avansados.md/00_ejercicio-del-chat-a-la-interfaz-de-producto.md`). El estimador deja el prompting en manos del usuario (un chat libre) y el prompt vive como strings dentro de `app/services/llm_service.py`. El ejercicio corrige las dos cosas a la vez: el frontend pasa a ser un formulario que produce parámetros tipados y el servicio saca el prompt a templates Jinja2 versionados, que es la base sobre la que se trabaja en el directo de S04 (salida estructurada, guardrails y caché semántica).

## What Changes

- **BREAKING** `POST /api/v1/estimate` recibe `description` (20–2000 caracteres) y tres enums obligatorios, `project_type` (`mobile_app`, `web_saas`, `internal_tool`, `data_pipeline`), `detail_level` (`summary`, `medium`, `detailed`) y `output_format` (`phases_table`, `line_items`, `narrative`), en lugar de `transcription`. Se mantienen como opcionales `model`, `max_tokens`, `thinking_budget`, `evaluate`, `num_examples` y `use_examples`, y se añade el campo opcional `reference_projects` (bonus).
- **BREAKING** La respuesta devuelve `text` (antes `estimation`) y el nuevo `prompt_version`, y mantiene las métricas y `validation`. Desaparecen los campos de preprocesado.
- Nueva carpeta `app/prompts/` con `loader.py` (`render_estimation_prompt(request, version="v1") -> (system, user)`, Jinja2 con `StrictUndefined`, `trim_blocks` y `lstrip_blocks`) y `estimation/v1/{system,user,examples}.j2`. El system prompt tiene bloques condicionales por `output_format` y `detail_level`, una pista por `project_type` e incluye los ejemplos con `{% include %}`.
- Tres formatos de salida: `line_items` (el formato actual: tabla de tareas con horas y coste, totales, equipo, duración, supuestos y riesgos), `phases_table` (tabla por fases con horas, coste y `confidence_pct`) y `narrative` (prosa con los totales, el equipo y la duración). `detail_level = detailed` añade las asunciones por fase.
- Los ejemplos CAG siguen siendo datos estructurados en `app/context/examples.py` (las filas ganan su fase) y `examples.j2` los muestra en el mismo formato de salida que se pide.
- La evaluación estructural pasa a depender del formato: `line_items` como hoy, `phases_table` con la tabla por fases y `narrative` sin la comprobación de filas.
- Bonus: `estimation/v2/` con un tono más conciso y directo (mismos ejemplos) seleccionable con `?prompt_version=v2`; `reference_projects` recorrido con `{% for %}`; evento de log `prompt_rendered` con la versión y un hash del render.
- **BREAKING** La app Streamlit sustituye el chat por un formulario (`st.form`) que hace `POST /api/v1/estimate` y muestra el texto, las métricas y la validación.
- **BREAKING** Se retiran el chat multi-turno, el endpoint SSE `POST /api/v1/estimate/stream`, `GET /api/v1/context` y las opciones `preprocessing` (`inline_cleaning`, `two_phase`) y `example_format`.
- `jinja2` pasa a ser dependencia directa (hoy solo es transitiva).

### Desviaciones respecto al enunciado

- **Campos extra en el contrato**: además de los cuatro del enunciado, el request mantiene los parámetros de generación del directo de S02 (opcionales) y la respuesta mantiene las métricas de S03 y la validación de S02. El enunciado solo pide `text` y `prompt_version`; los demás campos no rompen el contrato que pide.
- **Templates en español con esqueleto en inglés por formato**: el producto responde en el idioma de la descripción, y en S02/S03 se midió que gpt-4o-mini necesita el esqueleto en inglés para responder en inglés a entradas en inglés. Exige volver a medir con llamadas reales.
- **Ejemplos estructurados**: `examples.j2` no contiene los ejemplos escritos a mano, los renderiza desde `app/context/examples.py`, para conservar la aritmética verificada de los ejemplos y mostrarlos en el formato pedido.
- **Ruta del endpoint**: se mantiene `/api/v1/estimate` (el enunciado dice `/estimate`), como desde S02.
- **Enums en `app/domain.py`**: el enunciado los define en `app/schemas.py`. Se definen en un módulo de dominio nuevo (con la entrada tipada `EstimationInput`) y `app/schemas.py` los reexporta, para que el loader no dependa del contrato HTTP y no haya ciclo de imports (`schemas → services → prompts → schemas`).
- **Límites de `description` sobre el texto recortado**: 20–2000 caracteres después de quitar los espacios del principio y del final (como `transcription` desde S02), para que una descripción de solo espacios no pase.
- **Muestras**: las transcripciones `samples/*.txt` se sustituyen por cuerpos JSON de petición (ES y EN).

## Capabilities

### New Capabilities
- `prompt-templates`: loader de templates Jinja2 versionados (`app/prompts/`), versiones `v1` y `v2`, contexto del render, fallo ante variables no definidas o versión inexistente, y evento de log del render.
- `estimation-form-ui`: app Streamlit con formulario tipado que llama a `POST /api/v1/estimate` y muestra el resultado, las métricas y la validación.

### Modified Capabilities
- `estimation-api`: nuevo contrato de request/response, `?prompt_version`, y retirada de `GET /api/v1/context`.
- `cag-prompt`: el system prompt se construye con los templates, con los formatos de salida, el nivel de detalle, la pista por tipo de proyecto y los ejemplos en el formato pedido; se retiran el prompt del chat y los formatos de ejemplo.
- `generation-options`: se retiran `preprocessing` y `example_format`; los demás parámetros se mantienen.
- `estimation-evaluation`: comprobaciones según `output_format`.
- `observability`: se retiran los eventos del streaming por HTTP; la UI registra los fallos de la petición a la API.
- `ci-pipeline`: la estructura del proyecto incluye `app/prompts/`, y la cobertura mínima de tests cambia (templates y formulario en lugar de SSE y chat).
- `chat-ui`: se retira entera (la sustituye `estimation-form-ui`).
- `streaming-api`: se retira entera.
- `response-cache`: los escenarios de la clave pasan de transcripción y `example_format` a descripción, formato, nivel, tipo y versión; el escenario de caché compartida deja de referirse al chat.
- `llm-provider`: los requisitos de límites y opciones por llamada dejan de mencionar el chat y el SSE (el streaming del proveedor se mantiene).

## Impact

- **Código**: nuevo `app/domain.py` (enums, límites y entrada tipada), `app/schemas.py`, `app/routers/estimations.py`, `app/services/llm_service.py` (sale el texto del prompt y el preprocesado), `app/services/evaluation.py`, `app/context/examples.py`, nueva `app/prompts/`, `streamlit_app.py`.
- **Se mantienen sin cambios de comportamiento**: el wrapper LiteLLM con fallback (incluido su método de streaming, que deja de exponerse por HTTP), la caché exact-match (su clave ya incluye el system prompt y los mensajes, así que distingue versiones y formatos) y la configuración.
- **Dependencias**: `jinja2` directa en `pyproject.toml`. Docker no cambia: `.dockerignore` ya permite todo `app/`, incluidos los `.j2`.
- **Muestras y docs**: `samples/*.json` en lugar de `samples/*.txt`; README y `CLAUDE.md` (arquitectura y tabla de agentes con `app/domain.py` para `backend-engineer`).
- **Tests**: nuevos `tests/prompts/` (los 3 del enunciado y los del loader) y del formulario; se retiran los de SSE, chat y contexto; se adaptan los de API, prompt, evaluación y opciones.
- **Coste**: una fase final de medición con llamadas reales (idioma y formato por `output_format`), solo con autorización del usuario.

## Fuera de alcance

- Forzar salida JSON estructurada del LLM, guardrails de validación del output y caché semántica: se trabajan en el directo de S04.
- Selección dinámica de ejemplos por tipo de proyecto, memoria y RAG (S05+).
- Un cuarto ejemplo de `internal_tool`.
- Streaming de la respuesta en el formulario.
