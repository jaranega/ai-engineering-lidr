## 1. Servicio (ai-engineer)

- [x] 1.1 `app/services/boss.py`: reintento fuera de alcance → síntesis con `retry_out_of_scope` (D1)
- [x] 1.2 `app/services/errors.py`, `llm_service.py` y `critic.py`: consumo de los intentos fallidos en las excepciones y suma en el turno revisado; `cost_usd = null` si es desconocido (D2)
- [x] 1.3 `app/guardrails/input.py` expone `PROMPT_TAG_PATTERN`; `app/services/memory.py` lo usa en `normalize_summary` (D5)

## 2. API (backend-engineer)

- [x] 2.1 `app/schemas.py` reutiliza los `Literal` del servicio (D6) y `app/routers/sessions.py` corrige la descripción de `tier`

## 3. Interfaz y evals

- [x] 3.1 `streamlit_app.py`: controles según la versión seleccionada con aviso, y ayuda sin "3" fijo (frontend-engineer)
- [x] 3.2 `evals/run.py`: coste desconocido con `--max-cost-usd` detiene el runner; sin tope se cuenta aparte (ai-engineer)

## 4. Tests y verificación (qa-engineer)

- [x] 4.1 Tests de los escenarios de las specs del change (Boss, coste de fallidos, runner, Streamlit, resumen, OpenAPI)
- [x] 4.2 `uv run pytest`, ruff y `openspec validate --all --strict` en verde

## 5. Cierre

- [x] 5.1 Archivar el change (con permiso del usuario) y commit/push en `session-06`
