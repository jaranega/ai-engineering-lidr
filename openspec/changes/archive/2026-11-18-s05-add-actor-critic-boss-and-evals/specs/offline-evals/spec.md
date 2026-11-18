## ADDED Requirements

### Requirement: Golden dataset tipado
El sistema SHALL incluir el paquete `evals/` en la raíz del repositorio. `app/` MUST NOT importarlo. El paquete MUST incluir `evals/dataset.py`, con el modelo Pydantic `GoldenCase` y `load_dataset(path=None) -> list[GoldenCase]`, que por defecto lee `evals/golden_dataset.json`.

**Campos de `GoldenCase`.**
- `id`: con el patrón `case-NNN-slug` y único en el dataset.
- `language`: `es` o `en`.
- `transcript`: de 20 a 20 000 caracteres.
- `project_type`.
- `detail_level`: por defecto `medium`.
- `output_format`: por defecto `line_items`.
- `tier`: `auto` (por defecto) o un valor de `Tier`.
- `expected_blocked` y `expected_out_of_scope`: booleanos, por defecto `false`.
- `expected_in_summary` y `expected_technologies_any_of`: listas de strings.
- `expected_hours_range`, `expected_cost_range_eur`, `expected_duration_weeks_range` y `expected_phase_count_range`: pares `[lo, hi]` opcionales.
- `expected_tier`: un `Tier`, opcional.

**Validación.**
- Un rango con `lo > hi` MUST ser inválido.
- Un caso con `expected_blocked` o `expected_out_of_scope` MUST NOT llevar rangos.
- `load_dataset` MUST fallar con un error que nombre el caso si un id está repetido o un caso no valida.

**Contenido.** El dataset MUST tener 16 casos, 8 con `language = "es"` y 8 con `"en"`, que cubran:
- los cuatro `project_type`;
- al menos un caso con NDA y otro con normativa con `expected_tier = "executive"`;
- uno técnico con `expected_tier = "developer"`;
- uno con equipo de 2 con `expected_tier = "pm"`;
- uno vago con `expected_out_of_scope`;
- uno con inyección con `expected_blocked`.

#### Scenario: Dataset por defecto
- **WHEN** se llama a `load_dataset()`
- **THEN** devuelve 16 casos con ids únicos, 8 en español y 8 en inglés

#### Scenario: Rango invertido
- **WHEN** se valida un caso con `expected_hours_range = [500, 100]`
- **THEN** la validación falla

#### Scenario: Id repetido
- **GIVEN** un fichero con dos casos con el mismo `id`
- **WHEN** se llama a `load_dataset(path)`
- **THEN** falla con un error que nombra el id

### Requirement: Métricas deterministas
`evals/metrics.py` SHALL definir el dataclass inmutable `MetricResult(name, score, passed, details)` y el dataclass `CaseOutcome(blocked_reason, response)`, donde:
- `blocked_reason` es el `reason` de un 400 de los guardrails o `None`;
- `response` es la respuesta del turno validada como `SessionEstimateResponse` o `None`.

Cada métrica MUST tener un atributo `name` y `evaluate(case, outcome) -> MetricResult`, con `score` 1.0 o 0.0, sin LLM ni red, y `details` explicando qué falló.

**Métricas.**
- **`SchemaAdherenceMetric`** (`schema_adherence`):
  - Un bloqueo pasa solo si el caso lo espera, y una respuesta 200 falla si se esperaba un bloqueo.
  - Con respuesta, la suma de `hours` de las filas MUST coincidir con `total_hours`, `validation.score` MUST ser 1.0 cuando hay `validation` y el número de fases MUST estar en `expected_phase_count_range` si lo hay.
- **`CostBoundsMetric`** (`cost_bounds`):
  - Con `expected_out_of_scope`, exige `out_of_scope = true` y `total_hours = 0`.
  - Sin él, exige `out_of_scope = false` y `total_hours`, `total_cost_eur` y la duración dentro de sus rangos, cuando los hay.
  - La duración cumple si `[min_weeks, max_weeks]` se solapa con el rango esperado.
- **`ContentRecallMetric`** (`content_recall`):
  - Cada término de `expected_in_summary` MUST aparecer en el título, el resumen o las tareas.
  - Al menos una de `expected_technologies_any_of` MUST estar en `result.technologies`.
  - Las comparaciones no distinguen mayúsculas ni tildes, y `result.language` MUST coincidir con `language`.
- **`TierMatchMetric`** (`tier_match`): `response.tier` MUST ser igual a `expected_tier`.

**Métricas que se saltan.** `ContentRecallMetric` se salta en casos bloqueados o fuera de alcance, y `TierMatchMetric` se salta sin `expected_tier` o con bloqueo. Una métrica que se salta MUST devolver `passed = true`, `score = 1.0` y `details` empezando por `skipped`.

`run_all_metrics(case, outcome) -> list[MetricResult]` MUST aplicar las cuatro en ese orden.

#### Scenario: Coste fuera de rango
- **GIVEN** un caso con `expected_cost_range_eur = [10000, 40000]` y una respuesta con `total_cost_eur = 55000`
- **WHEN** se evalúa `CostBoundsMetric`
- **THEN** `passed = false`, `score = 0.0` y `details` menciona `55000` y el rango

#### Scenario: Tecnología con otra grafía
- **GIVEN** un caso con `expected_technologies_any_of = ["PostgreSQL"]` y `result.technologies = ["postgresql", "React"]`
- **WHEN** se evalúa `ContentRecallMetric`
- **THEN** `passed = true`

#### Scenario: Bloqueo esperado
- **GIVEN** un caso con `expected_blocked = true` y un `CaseOutcome` con `blocked_reason = "prompt_injection"`
- **WHEN** se llama a `run_all_metrics`
- **THEN** las cuatro métricas pasan y `content_recall` y `tier_match` indican `skipped`

#### Scenario: Bloqueo no esperado
- **GIVEN** un caso normal y un `CaseOutcome` bloqueado por `pii`
- **WHEN** se evalúa `SchemaAdherenceMetric`
- **THEN** `passed = false`

#### Scenario: Tier distinto
- **GIVEN** un caso con `expected_tier = "executive"` y una respuesta con `tier = "default"`
- **WHEN** se evalúa `TierMatchMetric`
- **THEN** `passed = false` y `details` nombra los dos tiers

#### Scenario: Idioma distinto
- **GIVEN** un caso con `language = "es"` y una respuesta con `result.language = "en"`
- **WHEN** se evalúa `ContentRecallMetric`
- **THEN** `passed = false`

### Requirement: Runner de evals
El sistema SHALL ofrecer la CLI `uv run python -m evals.run`, que ejecuta los casos contra la API.

**Opciones.**
- `--mode actor|acb`, por defecto `actor`. En `acb` el turno se envía con `review=true`.
- `--limit N`.
- `--case ID`, repetible.
- `--http URL`. Sin ella, la ejecución es en proceso con `TestClient` de la app y un `SessionStore` aislado que sustituye a `get_session_store`.
- `--output PATH`, que escribe un informe JSON.
- `--max-cost-usd X`, que detiene la ejecución cuando el coste acumulado lo supera.

**Ejecución de cada caso.** Cada caso MUST crear su propia sesión y enviar un turno con los campos del caso, y `tier` solo si no es `auto`. Una respuesta 400 de los guardrails MUST registrarse como bloqueo con su `reason`. Cualquier otro código de error o excepción MUST registrarse como fila de error, que cuenta como caso fallido, y el runner MUST seguir con el siguiente caso.

**Salida.**
- Una línea por caso: id, idioma, latencia en ms medida por el cliente, `cost_usd`, PASS/FAIL de cada métrica y, en modo `acb`, la decisión final, el motivo y las iteraciones.
- Un resumen con los casos que pasan todas las métricas, los aciertos por métrica, el coste total, la latencia media, P50 y máxima, los aciertos de caché y, en modo `acb`, la distribución de `stop_reason` y la media de iteraciones.

**Código de salida.** 0 si todos los casos pasan, 1 si alguno falla o da error, y 2 ante un error de uso o de dataset.

**Núcleo.** La lógica MUST estar en `run_cases(cases, mode, client, *, max_cost_usd=None)`, separada de la CLI, que devuelve el informe. El runner MUST NOT imprimir ni guardar las transcripciones ni las respuestas completas, solo ids, métricas, contadores y los `details` de las métricas.

#### Scenario: Ejecución en proceso con proveedor falso
- **GIVEN** un proveedor simulado que devuelve una estimación válida y dos casos
- **WHEN** se llama a `run_cases` en modo `actor` con un `TestClient`
- **THEN** el informe tiene dos filas con sus métricas y cada caso usó una sesión distinta

#### Scenario: Modo acb
- **GIVEN** un proveedor simulado con un Critic que acepta
- **WHEN** se llama a `run_cases` en modo `acb`
- **THEN** cada turno se envió con `review=true` y cada fila incluye `final_decision` y `stop_reason`

#### Scenario: Caso bloqueado
- **GIVEN** un caso con inyección y `expected_blocked = true`
- **WHEN** se ejecuta el runner
- **THEN** la fila registra `blocked_reason = "prompt_injection"` y el caso pasa

#### Scenario: Error del proveedor
- **GIVEN** un proveedor simulado que falla en el primer caso
- **WHEN** se ejecutan dos casos
- **THEN** el primero queda como fila de error, el segundo se ejecuta y el código de salida es 1

#### Scenario: Tope de coste
- **GIVEN** `--max-cost-usd 0.001` y un proveedor simulado cuyo primer turno cuesta 0.002 USD
- **WHEN** se ejecutan tres casos
- **THEN** el runner se detiene tras el primero e indica que se alcanzó el tope

#### Scenario: Dataset inválido
- **WHEN** se ejecuta la CLI con un dataset con un id repetido
- **THEN** termina con código 2 y un mensaje que nombra el id

### Requirement: Evals fuera de la suite de tests
Los evals con llamadas reales SHALL ejecutarse solo a mano, con la CLI. pytest y CI MUST NOT ejecutarlos. Los tests de `tests/evals/` MUST cubrir el dataset, las métricas y el runner con un proveedor simulado, sin red ni coste. El README MUST explicar cómo ejecutarlos, cuánto cuestan aproximadamente y que conviene vaciar `REDIS_URL` para no medir aciertos de caché.

#### Scenario: Suite sin red
- **WHEN** se ejecuta `uv run pytest` sin API keys
- **THEN** los tests de `tests/evals/` pasan y no se hace ninguna llamada de red
