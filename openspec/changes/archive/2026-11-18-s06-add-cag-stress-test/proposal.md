## Why

Ejercicio de la sesión 06 (módulo 3, Data-driven AI): **"Stress test del CAG: medir dónde rompe"** (enunciado en `AIEngineer-notas/06_Data_driven_IA/00_stress-test-del-cag.md`), en la rama `session-06`. Hasta ahora el CAG (resumen + anclas + ventana + `project_metadata` + tier + transcripción + adjuntos) nunca se ha probado con carga. No sabemos en qué turno empieza a olvidar el proyecto, cuánto cuesta el turno 20 frente al 1, ni a partir de qué tamaño de adjunto la latencia se dispara. El directo de S06 introduce RAG como respuesta a esos límites. Este ejercicio produce el **baseline cuantitativo** con el que se comparará: un `REPORT.md` con tres curvas y dos párrafos de lectura, y el `results.csv` del que salen.

Se apoya en lo hecho en la puesta al día del directo de S05: el marco de evals (`MetricResult`, runner con `--http`/`TestClient`), la memoria con anclas y resumen, y las métricas del turno sumadas. El ejercicio **mide, no optimiza**: no se toca ninguna constante del CAG.

## What Changes

- **Observación unificada por turno.** Cada turno de sesión con éxito registra un único evento `turn_observed` con los 13 campos del enunciado:
  - `turn_index`, `session_id`;
  - `enriched_transcript_chars`, `attachments_total_chars`;
  - `messages_in_window`, `anchors_count`, `summary_chars`;
  - `tokens_in`, `tokens_out`, `cost_usd`, `latency_ms`;
  - `cache_hit_kind`, `last_resolved_tier`.

  Contiene solo números e identificadores, nunca texto. La última observación se guarda en la sesión.
- **Endpoint de depuración opcional** `GET /api/v1/sessions/{id}/debug`, activo solo con `DEBUG_ENDPOINTS=true` (por defecto `false`; sin activar responde 404). Devuelve la última observación y el estado de la memoria (texto del resumen, mensajes de cliente de las anclas y `project_metadata`), que `MemoryDriftMetric` necesita también en modo `--http`.
- **Paquete `evals/stress/`:**
  - `scenarios.py`: tres perfiles de 20 turnos ("crece", "pivota" y "se contradice") con su *fact-tracker*, y el escenario de adjuntos.
  - `fixtures/build_pdfs.py`: PDF deterministas de 5, 20, 50 y 100 KB con `fpdf2` (dependencia nueva del grupo `dev`); los PDF no se commitean.
  - `metrics.py`: `LatencyBudgetMetric`, `CostBudgetMetric` y `MemoryDriftMetric`, reutilizando `MetricResult`.
  - `run.py`: `python -m evals.stress.run` con `--http`, `--scenarios`, `--attachment-sizes`, `--repeats`, `--output` y `--max-cost-usd`. Escribe un CSV con una fila por turno: las columnas de `turn_observed` más una por métrica.
  - `report.py`: genera las tablas del informe a partir del CSV.
- **Entregables commiteados:** `evals/stress/REPORT.md`, con la tabla resumen, las tres curvas en tabla y dos párrafos de lectura con afirmaciones cuantitativas, y `evals/stress/results.csv`.
- **Ejecución real** de unos 195 turnos (≈0,35 USD, tope 0,60), con permiso del usuario.

### Desviaciones respecto al enunciado

- **`EstimationService.estimate_conversational()` no existe aquí.** El turno lo orquesta `SessionService.estimate`, y es ahí, tras la compresión, donde se emite `turn_observed`.
- **La matriz no es completa (decisión del usuario).** Los perfiles multiturno no se cruzan con los tamaños de adjunto:
  - 3 perfiles × 20 turnos × 3 repeticiones, donde cada N ∈ {1, 3, 6, 10, 20} es el turno N de la conversación de 20;
  - 5 tamaños × 3 repeticiones de una estimación inicial, que es lo que pide el bloque 3;
  - en total, unas 195 filas en lugar de unas 900, muy por encima de las 50 exigidas.
- **`cache_hit_kind` solo puede ser `none` o `exact`.** No hay caché semántica (quedó pendiente del directo de S04), y la ejecución se hace sin Redis para no falsear latencias, así que las tasas de acierto salen 0 y así se informa.
- **El turno 1 del perfil "se contradice" fija el contexto.** El enunciado sitúa el presupuesto en los turnos 3 y 8, y así se mantiene.
- **Los hechos se buscan sin distinguir mayúsculas ni tildes**, porque las conversaciones son bilingües.

## Capabilities

### New Capabilities
- `cag-stress-test`: escenarios, fixtures de PDF, métricas de presupuesto y de deriva de memoria, runner CSV, generador del informe y entregables.

### Modified Capabilities
- `observability`: evento `turn_observed`.
- `conversation-sessions`: última observación en la sesión y endpoint `GET /sessions/{id}/debug`.
- `app-config`: `DEBUG_ENDPOINTS`.
- `ci-pipeline`: estructura y cobertura del stress test sin red.

## Impact

- **Código:**
  - `app/services/session_service.py` emite el evento y guarda la observación;
  - `app/sessions/models.py`, `app/schemas.py`, `app/routers/sessions.py` y `app/config.py` se modifican;
  - `evals/stress/` es nuevo;
  - se actualizan `.gitignore` (los PDF), `pyproject.toml` (`fpdf2` en `dev`), el README y `CLAUDE.md`.
- **API:** un endpoint nuevo, desactivado por defecto. Sin cambios en los existentes.
- **Coste:** la ejecución real (≈0,35 USD) requiere permiso. Los tests no usan red.

## Fuera de alcance

- Implementar RAG: es el directo de S06.
- Optimizar el CAG (prompt, compresión o `MAX_CONVERSATION_TURNS`): el ejercicio mide, no optimiza.
- Comparar proveedores: se usa solo gpt-4o-mini.
- Notebooks o gráficos (las curvas van en tablas Markdown), persistencia en base de datos y paneles de interfaz.
- LLM-as-judge para la deriva: la comparación es determinista.
- Mejorar la extracción de `technologies` y un Critic de otro modelo, que quedaron como líneas futuras del change anterior.
