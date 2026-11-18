# cag-stress-test Specification

## Purpose
Medir dónde rompe el CAG antes de pasar a RAG: escenarios de carga multiturno y de adjuntos con fact-tracker, métricas deterministas de presupuesto de latencia y coste y de deriva de memoria, un runner que deja un CSV por turno y el informe `evals/stress/REPORT.md` con las tres curvas canónicas. Ejercicio de S06.
## Requirements
### Requirement: Escenarios de carga con fact-tracker
`evals/stress/scenarios.py` SHALL definir los escenarios `growing`, `pivot`, `contradiction` y `attachments`, con textos fijos en el código, como `Scenario(name, language, project_type, turns)`. Cada turno es un `ScenarioTurn(index, transcript, facts)`, donde `facts` son los hechos que el cliente declara en ese turno y que deben recordarse en todos los turnos posteriores.

- **`growing`** (español): 20 turnos. El turno 1 introduce el proyecto "Nimbus" con React y Postgres, y cada turno siguiente añade un requisito coherente.
- **`pivot`** (inglés): 20 turnos. El turno 1 pide React Native, y el turno 5 cambia a Flutter y descarta React Native. `React Native` es un hecho vigente en los turnos 1–4 y deja de exigirse desde el 5.
- **`contradiction`** (español): 20 turnos. El turno 3 fija un presupuesto de "30.000 €" y el turno 8 lo cambia a "80.000 €". El hecho del presupuesto vigente MUST ser `30.000` en los turnos 3–7 y `80.000` desde el 8; el valor reemplazado deja de exigirse.
- **`attachments`** (español): un único turno, con transcripción corta y fija, y un PDF de 0, 5, 20, 50 o 100 KB. El PDF contiene los marcadores únicos que se buscan en la respuesta.

`select_turns(scenario, n)` MUST devolver los `n` primeros turnos.

#### Scenario: Perfiles de 20 turnos
- **WHEN** se cargan los escenarios
- **THEN** `growing`, `pivot` y `contradiction` tienen 20 turnos con índices del 1 al 20 y `attachments` tiene 1

#### Scenario: Hechos vigentes de la contradicción
- **WHEN** se piden los hechos que deben recordarse en el turno 10 de `contradiction`
- **THEN** incluyen `80.000` y no `30.000`

#### Scenario: Pivote
- **WHEN** se lee el turno 5 de `pivot`
- **THEN** su transcripción menciona Flutter y descarta React Native, y sus hechos incluyen `Flutter`

### Requirement: PDFs sintéticos deterministas
`evals/stress/fixtures/build_pdfs.py` SHALL generar con `fpdf2` los ficheros `attach_5kb.pdf`, `attach_20kb.pdf`, `attach_50kb.pdf` y `attach_100kb.pdf` en `evals/stress/fixtures/`. Su contenido MUST ser determinista: pliego técnico sintético con una semilla fija y los marcadores del escenario `attachments` al principio, a la mitad y al final.

El texto extraído con el extractor de `app/attachments` MUST rondar el tamaño nominal, con una tolerancia del ±15 %. El de 100 KB MUST superar `MAX_ATTACHMENT_CHARS`: el extractor lo rechaza (413 `too_much_text`) en lugar de truncarlo, y el runner lo registra como fila de error. Se ejecuta con `python -m evals.stress.fixtures.build_pdfs`, y el runner lo invoca si faltan los ficheros. Los PDF MUST NOT commitearse.

#### Scenario: Determinismo
- **WHEN** se generan los PDF dos veces
- **THEN** el texto extraído es idéntico en ambas

#### Scenario: Tamaño de 20 KB
- **WHEN** se extrae el texto de `attach_20kb.pdf`
- **THEN** tiene entre 17 000 y 23 000 caracteres y contiene los marcadores

### Requirement: Métricas de presupuesto y deriva de memoria
`evals/stress/metrics.py` SHALL definir tres métricas sin LLM ni red que devuelven `MetricResult` (de `evals.metrics`):

- **`LatencyBudgetMetric(budget_ms=10000)`**: `evaluate(observation)` da 1.0 si `latency_ms <= budget_ms` y 0.0 si no.
- **`CostBudgetMetric(budget_usd=0.003)`**: `evaluate(observation)` da 1.0 si `cost_usd <= budget_usd`. Un `cost_usd` nulo MUST dar 0.0 con `details` "unknown cost".
- **`MemoryDriftMetric(facts, where=("summary", "anchors", "metadata"))`**: `evaluate(snapshot)` busca cada hecho, sin distinguir mayúsculas ni tildes, en el resumen, en los mensajes de las anclas y en la metadata aplanada a texto (nombre, alcance, tecnologías, restricciones y descartes), según `where`.
  - `score` es la fracción de hechos encontrados y `passed` es `score == 1.0`.
  - `details` MUST indicar, para cada hecho, la fuente donde se encontró o `missing`.
  - Sin hechos, `score = 1.0`.

#### Scenario: Latencia en el límite
- **WHEN** se evalúa `LatencyBudgetMetric(10000)` con `latency_ms = 10000`
- **THEN** `passed = true`

#### Scenario: Coste por encima
- **WHEN** se evalúa `CostBudgetMetric(0.003)` con `cost_usd = 0.0041`
- **THEN** `passed = false` y `details` menciona los dos valores

#### Scenario: Hecho solo en la metadata
- **GIVEN** un snapshot sin resumen ni anclas y con `project_name = "Nimbus"`
- **WHEN** se evalúa `MemoryDriftMetric(["nimbus", "Flutter"])`
- **THEN** `score = 0.5` y `details` indica `nimbus: metadata` y `Flutter: missing`

#### Scenario: Hecho fuera de las fuentes pedidas
- **GIVEN** un hecho que solo está en el resumen
- **WHEN** se evalúa con `where = ("metadata",)`
- **THEN** el hecho cuenta como `missing`

### Requirement: Runner del stress test
`python -m evals.stress.run` SHALL ejecutar los escenarios contra la API.

**Opciones:**
- `--http URL`: sin ella, la ejecución es en proceso con `TestClient`, un `SessionStore` aislado y la depuración activada.
- `--scenarios`: por defecto `growing,pivot,contradiction`.
- `--attachment-sizes`: por defecto `0,5,20,50,100`.
- `--repeats`: por defecto 3.
- `--turns`: por defecto 20.
- `--output`: por defecto `evals/stress/results.csv`.
- `--max-cost-usd`.
- `--latency-budget-ms` y `--cost-budget-usd`.

**Ejecución:**
- Cada repetición de cada escenario MUST usar una sesión nueva.
- Cada tamaño de adjunto MUST ejecutarse como una sesión de un turno del escenario `attachments`.
- Tras cada turno, el runner MUST leer `GET /api/v1/sessions/{id}/debug` y escribir una fila al CSV antes de seguir, de modo que un corte no pierda lo medido.

**Columnas del CSV:**
- `scenario`, `repeat`, `attachment_kb`;
- los 13 campos de `turn_observed`;
- `latency_budget`, `cost_budget` y `memory_drift` (puntuaciones);
- `facts_total` y `facts_recalled`;
- `stale_tech` (en `pivot`: 1 si `React Native` sigue en `mentioned_technologies`);
- `budget_30k_present` y `budget_80k_present` (en `contradiction`, en el resumen, las anclas o la metadata) y `budget_30k_sources` y `budget_80k_sources` (fuentes donde se encontró cada uno, separadas por `|`);
- `attachment_markers_recalled` (en `attachments`: marcadores del PDF presentes en el título, el resumen, las tareas o los supuestos de la respuesta);
- `cost_estimated_usd` (solo en filas de error);
- `error`.

Las columnas que no aplican a un escenario MUST quedar vacías.

**Errores y salida:**
- Si la ruta de depuración responde 404 en la primera lectura, el runner MUST terminar con código 2 y un mensaje que indique activar `DEBUG_ENDPOINTS`.
- Un error en un turno MUST registrarse en la fila y abandonar esa conversación, pero no la ejecución.
- `--max-cost-usd` MUST detener la ejecución al superarse, o ante un turno con éxito con coste desconocido. Una fila de error de un turno que llegó al LLM (5xx o respuesta inválida) MUST NOT detenerla: suma al acumulado una estimación de (`STRUCTURED_MAX_RETRIES` + 1) × el coste máximo por turno observado (0,005 USD si aún no hay ninguno), que se registra en la columna `cost_estimated_usd`.
- El runner MUST NOT escribir transcripciones ni respuestas en el CSV ni en la salida.

#### Scenario: Ejecución en proceso con proveedor falso
- **GIVEN** un proveedor simulado
- **WHEN** se ejecuta el runner con `--scenarios growing --turns 3 --repeats 2 --attachment-sizes 0,5`
- **THEN** el CSV tiene 10 filas (6 de `growing` y 4 de `attachments`: 2 tamaños × 2 repeticiones), con todas las columnas y una sesión distinta por repetición

#### Scenario: Depuración desactivada en el servidor
- **GIVEN** un servidor sin `DEBUG_ENDPOINTS`
- **WHEN** se ejecuta el runner con `--http`
- **THEN** termina con código 2 y un mensaje que nombra `DEBUG_ENDPOINTS`

#### Scenario: Tope de coste
- **GIVEN** `--max-cost-usd 0.001` y un turno simulado que cuesta 0.002 USD
- **WHEN** se ejecuta el runner
- **THEN** se detiene tras la primera fila y lo indica

### Requirement: Informe del stress test
`python -m evals.stress.report <csv>` SHALL imprimir en Markdown:

1. **Tabla resumen**, una fila por escenario y tamaño de adjunto: P50 y P95 de `latency_ms`, coste total en USD, tasa de `cache_hit_kind = exact`, tasa de `semantic` y recall medio del *fact-tracker*.
2. **`latency_ms` frente a `tokens_in`**, en tabla por cubos de tokens.
3. **`cost_usd` acumulado frente a `turn_index`**, por escenario y como media de las repeticiones.
4. **`memory_drift` medio frente a N ∈ {1, 3, 6, 10, 20}**, por escenario.
5. **Tablas de las columnas de cada escenario**: `stale_tech`, presupuestos presentes y marcadores del adjunto por tamaño.

El generador MUST ser determinista y no escribir la lectura.

**Entregables.** `evals/stress/REPORT.md` MUST contener esas tablas, generadas con el CSV commiteado, y dos párrafos de lectura escritos a partir de los datos. Los párrafos responden dónde empieza a romperse el CAG, qué dimensión domina la degradación y qué caso límite justificaría RAG, con al menos una afirmación cuantitativa. `evals/stress/results.csv` MUST commitearse con al menos 50 filas.

#### Scenario: Informe de un CSV de ejemplo
- **GIVEN** un CSV con 2 escenarios de 3 turnos y latencias conocidas
- **WHEN** se ejecuta el generador
- **THEN** la tabla resumen muestra los P50 y P95 esperados y la curva de coste acumulado suma los costes por turno

#### Scenario: Entregables
- **WHEN** se inspecciona el repositorio tras la ejecución real
- **THEN** existen `evals/stress/REPORT.md` con las tablas y los dos párrafos y `evals/stress/results.csv` con al menos 50 filas

#### Scenario: Error del proveedor en un turno
- **GIVEN** `--max-cost-usd 0.60`, un turno simulado que cuesta 0.002 USD y otro que responde 502
- **WHEN** se ejecuta el runner
- **THEN** la fila del 502 tiene `error`, `cost_usd` vacío y `cost_estimated_usd = 0.006`, y la ejecución continúa

