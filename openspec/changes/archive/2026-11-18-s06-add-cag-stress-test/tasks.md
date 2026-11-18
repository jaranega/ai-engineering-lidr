## 1. Configuración y dependencias (backend-engineer)

- [x] 1.1 `app/config.py`: `debug_endpoints: bool = False` y `warning` `debug_endpoints_enabled` al arrancar; `.env.example` con la advertencia
- [x] 1.2 `pyproject.toml`: `fpdf2` en el grupo `dev` (`uv add --group dev fpdf2`); `.gitignore` con `evals/stress/fixtures/*.pdf`

## 2. Observación del turno (ai-engineer)

- [x] 2.1 `app/sessions/models.py`: `TurnObservation` (13 campos, inmutable) y `Session.last_observation`
- [x] 2.2 `app/services/session_service.py`: construir la observación tras la compresión, guardarla y emitir `turn_observed`; exponer en el snapshot de depuración lo que necesita el endpoint (resumen, anclas, metadata, observación)

## 3. Endpoint de depuración (backend-engineer)

- [x] 3.1 `app/schemas.py`: `TurnObservationOut` y `SessionDebug`
- [x] 3.2 `app/routers/sessions.py`: `GET /api/v1/sessions/{id}/debug` montado solo con `DEBUG_ENDPOINTS=true` (404 y fuera de OpenAPI sin ella)

## 4. Stress test (ai-engineer)

- [x] 4.1 `evals/stress/scenarios.py`: los cuatro escenarios con hechos, `select_turns` y hechos vigentes por turno
- [x] 4.2 `evals/stress/fixtures/build_pdfs.py`: PDF deterministas calibrados con el extractor
- [x] 4.3 `evals/stress/metrics.py`: `LatencyBudgetMetric`, `CostBudgetMetric` y `MemoryDriftMetric`
- [x] 4.4 `evals/stress/run.py`: runner en proceso y `--http`, CSV fila a fila, columnas de escenario, códigos de salida y tope de coste
- [x] 4.5 `evals/stress/report.py`: tablas del informe a partir del CSV

## 5. Tests (qa-engineer)

- [x] 5.1 Tests de `turn_observed`, `last_observation`, endpoint de depuración y `DEBUG_ENDPOINTS`
- [x] 5.2 Tests de escenarios, PDF, métricas, runner (proveedor simulado) e informe; estructura del proyecto
- [x] 5.3 `uv run pytest`, ruff y `openspec validate --all --strict` en verde

## 6. Ejecución real y entregables

- [x] 6.1 Pedir permiso al usuario para la ejecución real (≈0,35 USD, tope 0,60)
- [x] 6.2 Ejecutar el runner (en proceso, `REDIS_URL` vacío) y comprobar `--http` con una ejecución corta; dejar `evals/stress/results.csv`
- [x] 6.3 Generar las tablas y escribir `evals/stress/REPORT.md` con los dos párrafos de lectura
- [x] 6.4 README (sección "Stress test del CAG", `DEBUG_ENDPOINTS`, entrega de S06) y CLAUDE.md (fila de sesión y árbol)
- [x] 6.5 Archivar el change (con permiso) y escribir el Purpose de `cag-stress-test`
