## 1. Preparación

- [x] 1.1 Calcular y guardar, antes de tocar el código, los SHA-256 del system y del user de `v5` para las combinaciones del test dorado (qa-engineer: `tests/prompts/test_v5_golden.py`)
- [x] 1.2 Revisar la rama `session_05_live` (Critic, Boss y evals) contra `design.md` y anotar en el Context cualquier diferencia que afecte a las decisiones

## 2. Configuración (backend-engineer)

- [x] 2.1 `app/config.py`: `critic_model` (vacío = `None`, validado con `resolve_request_model`) y `boss_max_iterations` (`1..5`, por defecto 3)
- [x] 2.2 `.env.example`: `CRITIC_MODEL` y `BOSS_MAX_ITERATIONS` comentadas con su valor por defecto
- [x] 2.3 Tests de configuración (qa-engineer): valores por defecto, rango, modelo vacío y modelo sin key; `uv run pytest tests/test_config*.py` en verde

## 3. Prompts (ai-engineer)

- [x] 3.1 `app/prompts/estimation/v6/`: copia literal de `v5` y bloque `<critic_feedback>` al final de `user.j2` (solo problemas `critical`/`major`)
- [x] 3.2 `app/prompts/loader.py`: versiones `v6` (por defecto), `MEMORY_PROMPT_VERSIONS = {"v5", "v6"}`, `REVIEW_PROMPT_VERSIONS = {"v6"}`, `critic_feedback` en `render_session_prompt` (nunca en `history_user`) y `critic_issues` en `prompt_rendered`
- [x] 3.3 `app/prompts/critic/v1/{system,user}.j2` y `render_critic_prompt` con su `Environment` y `CRITIC_PROMPT_VERSION = "v1"`
- [x] 3.4 Tests (qa-engineer): golden de `v5`; `v6` sin feedback igual a `v5` byte a byte; feedback en `v6` (posición, filtrado de `minor`, sin feedback en `history_user`); `v4`/`v5` ignoran el feedback; prompt del Critic y eventos sin contenido

## 4. Guardrails (ai-engineer)

- [x] 4.1 `app/guardrails/input.py`: bloquear `<critic_feedback>`, `<estimation_under_review>`, `<resolved_tier>`, `<response_language>` y sus cierres
- [x] 4.2 Tests (qa-engineer): etiquetas bloqueadas en la transcripción y en un adjunto, y "revisión", "auditor" y "feedback" sin etiqueta no bloqueados

## 5. Critic y Boss (ai-engineer)

- [x] 5.1 `app/services/critic.py`: `CriticIssue` y `CriticFeedback` con sus validadores, y `Critic` (llamada estructurada con `llm_purpose="critic"`, `CRITIC_MODEL`, fail-open con `critic_failed`, evento `critic_completed`)
- [x] 5.2 `app/services/boss.py`: `run_review` puro con motivos de parada, traza por iteración, evento `boss_iteration` y la síntesis (avisos ES/EN en `assumptions_and_risks`, tope de 8, confianza con suelo 30)
- [x] 5.3 Tests unitarios (qa-engineer): validadores del feedback, todas las ramas de `run_review` y la síntesis, con dobles y sin LLM

## 6. Integración en el servicio (ai-engineer)

- [x] 6.1 `app/services/llm_service.py`: partir `_generate` en generar y finalizar; `estimate_turn(..., review=ReviewConfig | None)` con el bucle Actor-Critic-Boss (Actor que re-renderiza con feedback, Critic sobre el borrador calculado), métricas sumadas, `cache_hit` de todas las llamadas y `review_iteration` en contexto; 422 en `review` con `v4`/`v5`
- [x] 6.2 `EstimationOutcome`/`TurnOutcome` con la traza de la revisión y evento `acb_completed`
- [x] 6.3 `app/services/session_service.py`: flag `review` en `estimate`, `ReviewConfig` desde `Settings`, `last_review` en la sesión y el snapshot, y `review`/`review_decision` en `session_turn_completed`; actualización de la sesión y compresión una sola vez
- [x] 6.4 Tests de servicio (qa-engineer): guardrails y moderación una vez, feedback solo en el reintento, historial guardado una vez, métricas sumadas, fallo del Critic, fallo del Actor en el reintento, 502 en el primer borrador sin cambios en la sesión y eventos sin texto del Critic

## 7. API (backend-engineer)

- [x] 7.1 `app/schemas.py`: `ReviewIssue`, `ReviewIteration`, `ReviewTrace`, `ReviewSummary`; `acb` en `SessionEstimateResponse` y `last_review` en `SessionInfo`
- [x] 7.2 `app/routers/sessions.py`: Form `review: bool = False`, `prompt_version` con `v6` por defecto, mapeo de la traza y del 422 en `["body", "review"]`, OpenAPI documentado
- [x] 7.3 Tests HTTP (qa-engineer): turno sin revisión (`acb = null`, una llamada), con revisión, 422 con `v5`, `review` no booleano, `GET` con `last_review` y OpenAPI; ajustar los tests que esperaban `v5` por defecto

## 8. Interfaz (frontend-engineer)

- [x] 8.1 `streamlit_app.py`: casilla "Revisión Actor-Critic-Boss" en Opciones avanzadas (envía `review=true` solo marcada) y desplegable con la traza y las etiquetas en español
- [x] 8.2 Tests de la interfaz (qa-engineer): casilla marcada y desmarcada, y traza sintetizada

## 9. Evals (ai-engineer para el dataset y las métricas; qa-engineer para los tests)

- [x] 9.1 `evals/dataset.py`: `GoldenCase` con validadores y `load_dataset`
- [x] 9.2 `evals/golden_dataset.json`: 16 casos (8 ES y 8 EN) con la cobertura de la spec y rangos generosos razonados
- [x] 9.3 `evals/metrics.py`: `MetricResult`, `CaseOutcome`, las cuatro métricas y `run_all_metrics`
- [x] 9.4 `evals/run.py`: `run_cases` y la CLI (`--mode`, `--limit`, `--case`, `--http`, `--output`, `--max-cost-usd`), en proceso con un `SessionStore` aislado, sin imprimir transcripciones ni respuestas
- [x] 9.5 `evals/__init__.py` con las reexportaciones; comprobar que pytest no recoge `evals/` y que ruff lo revisa
- [x] 9.6 Tests en `tests/evals/` (qa-engineer): dataset (16 casos, ids únicos, rangos, id repetido), cada métrica (pasa, falla, se salta) y el runner con proveedor simulado (modos `actor` y `acb`, bloqueo, error, tope de coste y código de salida 2)

## 10. Estructura y CI (qa-engineer y backend-engineer)

- [x] 10.1 `tests/test_structure.py`: ficheros nuevos (`critic.py`, `boss.py`, `v6/`, `critic/v1/`, `evals/`), `app/` sin importar `evals` y `boss.py` sin LiteLLM ni Instructor
- [x] 10.2 Comprobar que el workflow de CI ejecuta ruff sobre `evals/` y no ejecuta la CLI de evals
- [x] 10.3 `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .` y `openspec validate --all --strict` en verde

## 11. Medición real y documentación

- [x] 11.1 Pedir permiso al usuario para la medición real (≈ 0,15 USD, tope 0,25 USD)
- [x] 11.2 Con permiso y `REDIS_URL` vacío: `python -m evals.run --mode actor` y `--mode acb` con los 16 casos; anotar resultados y umbrales en `design.md` (D14) y, si falla alguno, parar y preguntar
- [x] 11.3 README: sección "Actor-Critic-Boss" (cuándo activarlo, coste y traza), sección "Evals" (cómo ejecutarlos, coste y resultados de la medición), variables nuevas, `review`, `acb` y `last_review`, y desviaciones respecto al directo
- [x] 11.4 CLAUDE.md: fila de la sesión, rutas de `ai-engineer` (`critic.py`, `boss.py`, `evals/`), árbol de arquitectura y patrón de mensajes con `v6`
- [x] 11.5 Archivar el change y escribir el Purpose de `actor-critic-boss` y `offline-evals`
