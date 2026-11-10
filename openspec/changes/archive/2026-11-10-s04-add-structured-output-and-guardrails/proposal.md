## Why

Puesta al día con el directo de la sesión 04 (rama oficial `session_4_live` de https://github.com/LIDR-academy-temp/ai-engineering, solo el servicio Python) antes del ejercicio de S05 (`AIEngineer-notas/05_Funciones_avanzadas/00_memoria`). El enunciado de S05 parte de "una estimación estructurada validada por schema Pydantic" y de "guardrails programáticos sobre la salida", y hoy el servicio devuelve Markdown libre. Además, la medición de S04 (D14 del change `s04-add-typed-form-and-jinja-prompts`) dejó un problema abierto: gpt-4o-mini declara totales que no cuadran con la suma de las filas en 25 de 35 respuestas. Con salida estructurada, esos totales los calcula el código.

## What Changes

- **Salida estructurada**: el LLM devuelve un objeto `EstimationResult` (Pydantic) con Instructor sobre el wrapper LiteLLM, que reintenta cuando la validación falla. El objeto trae el título, las filas (tarea, fase, perfil de tarifa y horas), la confianza por fase, el equipo, la duración, los supuestos, los riesgos, los supuestos por fase y los párrafos de la narrativa, según el formato y el nivel pedidos.
- **Cálculos en el código**: el coste de cada fila (horas × tarifa de su perfil), la agregación por fase y los totales de horas y coste los calcula el servidor, nunca el LLM.
- **Render determinista**: el texto Markdown de los tres `output_format` lo genera el servidor a partir del objeto, con las etiquetas en el idioma detectado de la descripción (español o inglés). La evaluación estructural se mantiene sobre ese texto.
- **BREAKING** La respuesta de `POST /api/v1/estimate` añade `result` (el objeto validado, con los importes calculados) y mantiene `text`, `prompt_version`, las métricas y `validation`.
- **Guardrails de entrada** sobre `description` y `reference_projects`, antes de la caché y del LLM:
  - patrones de prompt injection en español y en inglés;
  - datos personales (email, teléfono, IBAN);
  - moderación de OpenAI vía LiteLLM, que deja pasar la petición si el servicio de moderación falla.
  - Si salta alguno, la respuesta es **400** con `reason` (`prompt_injection`, `pii` o `moderation`) y un mensaje que no repite el texto del usuario.
- **Guardrail de salida**: si la confianza global es menor que 30, la estimación se marca como fuera de alcance (`out_of_scope = true`) y el texto lo explica en lugar de mostrar importes.
- **BREAKING** Nueva versión de prompt `v3`, la única disponible y la de por defecto, con las instrucciones de salida estructurada y los ejemplos como objetos. Se retiran `v1` y `v2`, que pedían Markdown libre: `?prompt_version=v1` pasa a ser 422.
- **BREAKING** Se retira `thinking_budget`: el razonamiento extendido de Anthropic no es compatible con la llamada de herramienta forzada que usa la salida estructurada.
- La caché exact-match guarda el objeto validado (JSON) y no las respuestas que no pasaron la validación.
- Streamlit muestra los errores 400 de los guardrails y el aviso de fuera de alcance.
- Dependencia nueva: `instructor`.

### Desviaciones respecto al directo

- **Totales calculados por el código** en lugar de un validador que exige al LLM que la suma cuadre y le reintenta. Con totales calculados, la aritmética no puede fallar y no se gastan reintentos en ella (consejo del mentor en S05: sacar del LLM los cálculos deterministas).
- **Se conservan los tres formatos de salida y la evaluación de S04**, renderizados desde el objeto. En el directo, la respuesta pasa a ser solo el objeto.
- **Idioma**: el directo fija las respuestas en inglés. Aquí se mantiene la respuesta en el idioma de la descripción, con las etiquetas generadas por el código.
- **Guardrails con patrones en español además de en inglés** (pregunta de la sesión 5 sobre prompt injection multilingüe), y mensajes de error que no reproducen el fragmento detectado.
- **Sin caché semántica** (redisvl, Redis Stack y embeddings): el ejercicio de S05 no la necesita y añade dependencias e infraestructura. Queda como pendiente opcional.
- **Versión nueva `v3`** en lugar de editar `v1`, por la regla de no editar versiones publicadas.

## Capabilities

### New Capabilities
- `structured-estimation`: schema `EstimationResult`, sus validaciones, el cálculo de costes y totales en el código, el render Markdown determinista por formato e idioma y el guardrail de salida de fuera de alcance.
- `input-guardrails`: prompt injection, datos personales y moderación sobre la entrada, con su política de errores y sus logs.

### Modified Capabilities
- `estimation-api`: `result` en la respuesta, 400 por guardrail, `prompt_version` solo `v3`, flujo guardrails → caché → LLM → render.
- `prompt-templates`: versión `v3` (estructurada) y retirada de `v1` y `v2`.
- `cag-prompt`: el system prompt pide el objeto estructurado en lugar del Markdown; los ejemplos se muestran como objetos; el formato y el nivel ajustan qué campos rellenar.
- `llm-provider`: generación estructurada con reintentos por validación, con fallback, coste y tokens acumulados.
- `generation-options`: se retira `thinking_budget`.
- `response-cache`: guarda el objeto validado.
- `estimation-evaluation`: se evalúa el texto renderizado por el servidor.
- `estimation-form-ui`: errores 400 de los guardrails y aviso de fuera de alcance.
- `observability`: eventos de los guardrails y de la generación estructurada, sin contenido.
- `app-config`: reintentos de la salida estructurada y activación de la moderación.
- `ci-pipeline`: cobertura mínima de tests (salida estructurada, render y guardrails).

## Impact

- **Código**: nuevos `app/estimation/` (schema, cálculo, render y guardrail de salida) y `app/guardrails/` (entrada); `app/services/llm_service.py` (llamada estructurada y caché), `app/schemas.py`, `app/routers/estimations.py`, `app/config.py`, `app/prompts/` (`v3`; fuera `v1`/`v2`), `app/context/examples.py`, `app/services/evaluation.py`, `streamlit_app.py`.
- **API**: respuesta con `result`; nuevo 400; `prompt_version` solo `v3`; sin `thinking_budget`.
- **Dependencias**: `instructor` (compatible con `litellm==1.106.4` y `openai<3`, comprobado con `uv pip install --dry-run`).
- **Coste**: cada reintento por validación es una llamada más (máximo configurable). La moderación de OpenAI es gratuita. Una medición final con llamadas reales.
- **Docker**: sin cambios.

## Fuera de alcance

- Caché semántica (pendiente opcional).
- Sesiones, memoria conversacional y adjuntos: change siguiente (ejercicio S05).
- Tier dinámico, compresión de memoria y Actor-Critic-Boss: directo de S05.
- La parte Rails (`estimator-web`) del repositorio oficial.
