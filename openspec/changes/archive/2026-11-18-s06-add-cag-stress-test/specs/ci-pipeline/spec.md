## ADDED Requirements

### Requirement: Estructura y cobertura del stress test
El test de estructura SHALL exigir:
- `evals/stress/{__init__,scenarios,metrics,run,report}.py`;
- `evals/stress/fixtures/build_pdfs.py`;
- los entregables `evals/stress/REPORT.md` y `evals/stress/results.csv`;
- que `evals/stress/fixtures/*.pdf` esté ignorado por git.

La suite MUST cubrir, sin red ni coste:
- `turn_observed` y `last_observation`;
- el endpoint de depuración, activo e inactivo, y `DEBUG_ENDPOINTS`;
- los escenarios: 20 turnos, hechos declarados y el pivote y la contradicción en los turnos esperados;
- la generación de PDF: determinista y con tamaños de texto extraído dentro de un ±15 % del objetivo;
- las tres métricas: pasa, falla y caso límite;
- el runner, en proceso con un proveedor simulado: columnas del CSV, una fila por turno, error de depuración desactivada con código 2 y tope de coste;
- el generador del informe sobre un CSV de ejemplo.

CI MUST NOT ejecutar el runner del stress test.

#### Scenario: Falta el informe
- **WHEN** se elimina `evals/stress/REPORT.md`
- **THEN** el test de estructura falla indicando el fichero ausente

#### Scenario: Suite sin red
- **WHEN** se ejecuta `uv run pytest` sin API keys
- **THEN** pasan los tests del stress test sin llamadas externas
