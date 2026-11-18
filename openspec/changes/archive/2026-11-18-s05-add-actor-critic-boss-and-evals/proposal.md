## Why

Segunda parte de la puesta al día con el directo de la sesión 05 (rama oficial `session_05_live` de https://github.com/LIDR-academy-temp/ai-engineering, carpeta `estimator/`), en la rama `session-06`. El change `s05-add-memory-compression-and-dynamic-tier` dejó fuera, por decisión del usuario, el **Actor-Critic-Boss** y los **evals**; este change los incorpora.

Hay dos motivos para hacerlo ahora:

- **Calidad de la estimación.** Una estimación en una sola pasada puede omitir riesgos, contradecir una restricción del cliente (un NDA, un presupuesto cerrado) o volver a meter una tecnología descartada. Lo que falta no es una instrucción más en el prompt, sino **verificación** (material de la sesión, `05_actor-critic-boss.md`): un Critic que revisa con criterios explícitos y un Boss que decide si se acepta, se repite o se sintetiza.
- **Requisito previo del ejercicio de S06.** El enunciado de S06 (`AIEngineer-notas/06_Data_driven_IA/00_stress-test-del-cag.md`, "Stress test del CAG: medir dónde rompe") da por hecho un marco de evals con casos golden, métricas `MetricResult` + `run_all_metrics` y un runner con modos `actor` y `acb`, y lo amplía con métricas de latencia, coste y memoria. Sin esta base, el ejercicio no puede hacerse. El material de testing (`04_testing-y-evaluacion-de-sistemas-con-llms.md`) define el alcance mínimo: tests deterministas sobre propiedades, no igualdad estricta, con un golden dataset curado.

## What Changes

- **Actor-Critic-Boss opcional en el turno de sesión** (`review=true` en el formulario de `POST /api/v1/sessions/{id}/estimate`; por defecto, desactivado):
  - **Actor**: la estimación estructurada de siempre. En un reintento recibe el feedback del Critic en un bloque `<critic_feedback>` del mensaje `user` del turno. Ese bloque nunca se guarda en el historial.
  - **Critic** (`app/services/critic.py`, prompt `critic/v1`): una llamada estructurada que devuelve `CriticFeedback`. Contiene un veredicto (`accept`, `needs_iteration` o `reject`) y problemas con categoría, gravedad, campo afectado, descripción en el idioma de la sesión y corrección sugerida. Si falla, deja pasar la estimación (fail-open).
  - **Boss** (`app/services/boss.py`): lógica pura, sin LLM, con un máximo de iteraciones (`BOSS_MAX_ITERATIONS=3`). Acepta, pide otro intento o sintetiza. Sintetizar es devolver el último borrador con los problemas pendientes como avisos y la confianza reducida.
  - El historial, el `project_metadata`, la evaluación estructural y la compresión se actualizan **una sola vez**, con el resultado final.
  - La respuesta del turno incluye la traza `acb` (iteraciones, veredictos, problemas y decisión final) solo si se pidió la revisión. `GET /api/v1/sessions/{id}` añade `last_review` (decisión final e iteraciones del último turno revisado).
- **Prompt `v6`**: copia de `v5` con el bloque `<critic_feedback>` en `user.j2`. Sin feedback, renderiza byte a byte lo mismo que `v5`. Pasa a ser la versión por defecto de las sesiones. `v5` no se edita (está publicada) y queda fijada con un test dorado.
- **Evals deterministas** (paquete `evals/`, fuera de `app/`):
  - `dataset.py`: `GoldenCase` sobre nuestro dominio.
  - `golden_dataset.json`: 16 casos, la mitad en español.
  - `metrics.py`: `MetricResult(name, score, passed, details)`, cuatro métricas binarias (`SchemaAdherenceMetric`, `CostBoundsMetric`, `ContentRecallMetric`, `TierMatchMetric`) y `run_all_metrics`, con la misma API que el directo para que S06 la amplíe.
  - `run.py`: CLI `python -m evals.run --mode actor|acb`, en proceso por defecto (`TestClient`) o contra un servidor (`--http`), con salida JSON opcional.
  - Los evals llaman a la API real y **no** corren en CI. Las métricas, el dataset y el runner se prueban con un proveedor falso.
- **Streamlit**: la casilla "Revisión Actor-Critic-Boss" en "Opciones avanzadas" y un desplegable con la traza.
- **Configuración**: `CRITIC_MODEL` (vacío = el modelo primario con fallback) y `BOSS_MAX_ITERATIONS` (1–5, por defecto 3).
- **Guardrails**: las etiquetas de los prompts nuevos (`<critic_feedback>`, `<estimation_under_review>`, `<resolved_tier>`, `<response_language>` y sus cierres) se bloquean en la entrada.
- **Observabilidad**: los eventos `critic_completed`, `critic_failed`, `boss_iteration` y `acb_completed`, solo con contadores, veredictos y categorías, nunca con texto.

### Desviaciones respecto al directo

- **Flag en el turno en lugar de un endpoint aparte** (`/estimate-acb`). Mantiene un solo contrato, como el `tier`.
- **Categorías del Critic adaptadas a nuestro esquema.** Desaparece `math_error`, porque los importes y los totales los calcula el código, no el LLM. Aparece `constraint_conflict`: la estimación contradice `explicit_constraints` o recupera una opción de `rejected_options`.
- **Avisos de la síntesis en `assumptions_and_risks`.** El directo los antepone al `summary`, recortándolo. Aquí los avisos quedan visibles en el render de todos los formatos sin romper el límite del `summary`.
- **Borrador fuera de alcance aceptado sin Critic.** Si la confianza del Actor queda por debajo de 30, el Boss lo acepta sin revisarlo: no hay estimación que auditar y se ahorra la llamada.
- **Descripciones del Critic en el idioma de la sesión**, porque pueden acabar como avisos al usuario.
- **Evals sobre nuestro dominio y bilingües**: rangos de horas y de coste calculado, tier y idioma esperados, y la mitad de los casos en español. DeepEval y LLM-as-judge se descartan (decisión del usuario).
- **Prompt `v6` nuevo** en lugar de editar `v5`, por la regla de no editar versiones publicadas.

## Capabilities

### New Capabilities
- `actor-critic-boss`: Critic estructurado, Boss determinista, bucle con presupuesto de iteraciones, síntesis y traza de la revisión.
- `offline-evals`: golden dataset, métricas deterministas, runner CLI en modos `actor` y `acb`.

### Modified Capabilities
- `conversation-sessions`: `review` en el turno, pipeline con el Boss, `acb` en la respuesta y `last_review` en `GET /sessions/{id}`; `v6` por defecto.
- `prompt-templates`: versión `v6`, templates `critic/v1` y `v5` inmutable.
- `cag-prompt`: bloque `<critic_feedback>` en el mensaje `user` de `v6`.
- `input-guardrails`: etiquetas de los prompts del Critic y del feedback.
- `app-config`: `CRITIC_MODEL` y `BOSS_MAX_ITERATIONS`.
- `observability`: eventos del Critic y del Boss.
- `estimation-form-ui`: casilla de revisión y traza.
- `ci-pipeline`: estructura con `evals/`, cobertura del ACB y de los evals sin red.

## Impact

- **Código nuevo**: `app/services/critic.py`, `app/services/boss.py`, `app/prompts/critic/v1/`, `app/prompts/estimation/v6/`, `evals/{__init__,dataset,metrics,run}.py` y `evals/golden_dataset.json`.
- **Código modificado**: `app/services/llm_service.py` (bucle de revisión dentro de la generación del turno), `app/services/session_service.py`, `app/prompts/loader.py`, `app/guardrails/input.py`, `app/schemas.py`, `app/routers/sessions.py`, `app/config.py`, `.env.example`, `streamlit_app.py`, `README.md` y `CLAUDE.md`.
- **API**: campos opcionales nuevos (`review` en el formulario; `acb` y `last_review` en las respuestas). La versión por defecto del prompt de sesión pasa a `v6`, que sin revisión renderiza lo mismo que `v5`. Sin cambios incompatibles.
- **Coste**: sin revisión, ninguno. Con revisión, una llamada al Critic por iteración y hasta `BOSS_MAX_ITERATIONS` borradores, así que en el peor caso son 6 llamadas por turno. La medición real con los 16 casos en los dos modos se estima en unos 0,15 USD y requiere el permiso del usuario.
- **Sin dependencias nuevas** (`httpx` ya está en el grupo `dev`).

## Fuera de alcance

- El ejercicio de S06 (evento `turn_observed`, escenarios de stress, PDFs sintéticos, métricas de latencia, coste y memoria, `REPORT.md` y `results.csv`) irá en un change aparte que se apoya en este.
- LLM-as-judge (DeepEval `GEval`) y los tests "soft" de consistencia con N ejecuciones; se tratan en la sesión 15.
- La revisión en `POST /api/v1/estimate` (sin sesión) y la caché semántica.
- Ejecutar los evals reales en CI (coste y claves).
