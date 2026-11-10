## Why

La rama de referencia `session_3_live` (`LIDR-academy-temp/ai-engineering`) incorpora lo trabajado en el directo de S02 (apuntes `/Users/javiaranega/claude/AIEngineer-notas/02_CAG/04_gestion-contexto-cag.md` y `05_arquitectura-conversaciones-modelos.md`): parámetros de generación por petición para comparar en vivo estrategias de contexto y modelos, una evaluación estructural automática de cada estimación (el "score") y ejemplos CAG estructurados con varios formatos. Nuestro servicio no permite comparar estrategias sin tocar código ni mide la calidad formal de la respuesta, y el formato de salida (lista numerada de horas sin coste) no se puede validar aritméticamente en coste.

Sesión: **S02 (catch-up del directo)**, rama `pre-session-04`. Tercero de tres changes; depende de `s03-add-llm-wrapper-cache-logging` (proveedor LiteLLM y clave de caché construida a partir de un dict de parámetros) y es independiente de `s03-add-sse-streaming-and-docker` (el endpoint SSE no expone los parámetros).

## What Changes

- **Formato de salida con coste** (cambio de comportamiento del prompt): la estimación pasa a incluir una tabla `Tarea | Horas | Coste (EUR)` (etiquetas en el idioma de la transcripción), totales de horas y de coste, equipo, duración y riesgos, con tarifas fijas en el prompt (62,50 EUR/h desarrollo, 50 EUR/h diseño) y la instrucción de calcular los parciales antes que los totales.
- **Ejemplos CAG estructurados**: los 3 ejemplos actuales se reescriben con coste; cada ejemplo guarda sus datos (resumen, filas de desglose con horas y coste, totales, equipo, duración) y su Markdown precalculado; un test garantiza que horas y costes suman sus totales. Formateadores `markdown` (por defecto), `json` y `narrative` derivados de la misma fuente.
- **Parámetros opcionales en `POST /api/v1/estimate`** (cambio aditivo; los valores por defecto reproducen el comportamiento actual):
  - `preprocessing`: `none` | `inline_cleaning` (bloque de limpieza en el system prompt) | `two_phase` (una llamada previa extrae los requisitos y la estimación se hace sobre ellos);
  - `example_format`: `markdown` | `json` | `narrative`;
  - `num_examples` (0..N) y `use_examples`;
  - `model`: sobrescribe el modelo de la petición (solo modelos de OpenAI/Anthropic con key configurada; 422 si no), **sin fallback**;
  - `max_tokens`: límite de salida de la petición (con máximo);
  - `thinking_budget`: razonamiento extendido, solo Anthropic (`max_tokens` efectivo = `max_tokens + thinking_budget`); con OpenAI se ignora con un aviso en el log;
  - `evaluate`: ejecutar o no la evaluación estructural.
- **Evaluación estructural** (sin LLM, por expresiones regulares sobre el Markdown): título, tabla de desglose, totales, equipo y duración presentes; suma de horas y de costes de las filas frente a los totales declarados (con tolerancia); `truncated`. Devuelve `validation` en la respuesta con cada comprobación, `score` (0..1) e `issues`.
- **Respuesta de `POST /api/v1/estimate`** (aditivo): `validation` (o `null` si `evaluate=false`), `preprocessing`, `extracted_requirements` (solo con `two_phase`) y los tokens y el coste de la fase previa **por separado** (`preprocessing_input_tokens`, `preprocessing_output_tokens`, `preprocessing_cost_usd`), sin sumarlos a `cost_usd`, que sigue siendo el de la llamada de estimación. `latency_ms` pasa a medir el tiempo total en el servidor (ambas fases y la evaluación).
- La caché incluye en su clave todos los parámetros que alteran la generación.
- El endpoint SSE y el chat no exponen los parámetros (usan los valores por defecto), como en la referencia.

## Capabilities

### New Capabilities
- `estimation-evaluation`: evaluación estructural de una estimación: comprobaciones, aritmética de horas y coste, puntuación e incidencias.
- `generation-options`: parámetros de generación por petición, sus valores por defecto, límites y efecto en el prompt, el modelo y la caché.

### Modified Capabilities
- `cag-prompt`: formato de salida con tabla de coste y tarifas; ejemplos estructurados con formateadores; número y uso de ejemplos configurables; bloque de limpieza y extracción previa de requisitos; con `two_phase` el mensaje `user` de la estimación son los requisitos extraídos, no la transcripción.
- `estimation-api`: campos opcionales del request, campos nuevos de la respuesta y `latency_ms` como tiempo total del servidor.
- `llm-provider`: `max_tokens` pasa de constante a valor por defecto (2048) sobrescribible por petición; sobrescritura del modelo sin fallback; `thinking_budget` en Anthropic.
- `response-cache`: la clave incluye los parámetros de generación (requiere que el change `s03-add-llm-wrapper-cache-logging` esté archivado antes).

## Impact

- **Código**: `app/context/examples.py` (reescritura con datos estructurados), `app/services/llm_service.py` (opciones de generación, preprocesado, prompt), nuevo `app/services/evaluation.py`, `app/schemas.py`, `app/routers/estimations.py`, tests y `README.md`.
- **Coste**: `two_phase` añade una llamada al LLM por estimación; `thinking_budget` aumenta los tokens de salida.
- **Interfaz**: el chat muestra la nueva tabla con coste; la barra lateral sigue mostrando los ejemplos (en Markdown).

## Desviaciones respecto a la referencia (`session_3_live`)

- Se mantiene el **idioma de la transcripción** en la respuesta y las etiquetas traducidas; la evaluación MUST reconocer las etiquetas en español e inglés (la referencia solo en inglés).
- Errores del proveedor como **502 genérico** también en la fase previa de `two_phase` (la referencia devuelve 500 con el mensaje).
- `model` solo admite modelos de los proveedores soportados con key configurada (422 si no); en la referencia cualquier string llega a LiteLLM.
- `cost_usd` sigue siendo `null` para modelos sin precio conocido.
- Sin el interruptor `ACTIVE_OUTPUT_PROMPT` en código (prompt básico frente a estructurado): el formato estructurado es el único.

## Fuera de alcance

- Exponer los parámetros en el SSE o en Streamlit.
- Evaluación con LLM como juez, datasets de evaluación y comparativas automáticas (sesiones de evaluación, S05+).
- Formulario tipado y templates Jinja2 (ejercicio de S04, que reutilizará estos parámetros como opciones avanzadas).
