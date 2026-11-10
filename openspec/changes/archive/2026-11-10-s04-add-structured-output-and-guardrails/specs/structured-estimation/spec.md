## ADDED Requirements

### Requirement: Schema de la estimación estructurada
El sistema SHALL definir en `app/estimation/schema.py` el modelo Pydantic `EstimationResult`, que es lo único que rellena el LLM, con los campos en este orden: `language` (`"es" | "en"`, idioma de los campos de texto libre), `title` (1–120 caracteres), `summary` (1–600), `confidence_pct` (0–100, confianza global), `rows` (0–60 filas con `task` de 1–200 caracteres, `phase` de la lista canónica `discovery | setup | development | qa | deployment`, `rate_profile` `"dev" | "design"` y `hours` de 1 a 2000), `phases` (0–5 elementos con `phase`, `confidence_pct` de 0 a 100 y `assumptions`, de 0 a 3 textos), `team` (0–8 miembros con `role`, `count` de 1 a 20 y `part_time`), `duration_weeks` (`{min_weeks, max_weeks}` de 1 a 104, con `min_weeks ≤ max_weeks`, o `null`), `assumptions_and_risks` (0–8 textos) y `narrative` (0–6 párrafos). El schema MUST NOT contener importes, tarifas ni totales. El módulo MUST NOT importar Instructor, LiteLLM ni `app.services`.

Las horas y las confianzas MUST normalizarse sin reintento al múltiplo de 5 más cercano (mínimo 5 horas), se recortan los espacios y se quitan los textos vacíos de las listas; la normalización MUST ser idempotente. Una validación fallida de rangos, longitudes o valores admitidos MUST provocar un reintento de la llamada estructurada. Además, MUST validarse con reintento: `phases` sin fases repetidas y con el mismo conjunto de fases que `rows`; y, si `confidence_pct ≥ 30`, `rows`, `team` y `assumptions_and_risks` no vacíos y `duration_weeks` no nulo. `response_model_for(output_format, detail_level)` SHALL devolver una subclase con el mismo nombre y el mismo JSON Schema que exige además, en alcance, de 1 a 6 párrafos de `narrative` con `output_format = narrative` y de 1 a 3 supuestos por fase con `detail_level = detailed`.

#### Scenario: Normalización sin reintento
- **WHEN** se valida un resultado con una fila de 12 horas y una fase con confianza 72
- **THEN** la fila queda con 10 horas y la fase con confianza 70, sin error de validación

#### Scenario: Fases incoherentes
- **WHEN** se valida un resultado con filas en `development` y `qa` y `phases` solo con `development`
- **THEN** la validación falla

#### Scenario: En alcance sin filas
- **WHEN** se valida un resultado con `confidence_pct = 60` y `rows` vacío
- **THEN** la validación falla

#### Scenario: Fuera de alcance sin desglose
- **WHEN** se valida un resultado con `confidence_pct = 10`, `summary` con el motivo y `rows`, `phases`, `team` vacíos y `duration_weeks = null`
- **THEN** la validación pasa

#### Scenario: Requisitos por formato y nivel
- **WHEN** se valida con `response_model_for("narrative", "detailed")` un resultado en alcance sin `narrative` o con una fase sin supuestos
- **THEN** la validación falla, y el mismo resultado pasa con `response_model_for("line_items", "medium")`

#### Scenario: Mismo JSON Schema
- **WHEN** se compara el JSON Schema de las 9 combinaciones de `response_model_for`
- **THEN** todos son idénticos y se llaman `EstimationResult`

### Requirement: Cálculo de costes y totales en el código
`compute_estimate(result, language)` en `app/estimation/compute.py` SHALL ser una función pura que devuelve un `ComputedEstimate` con: `language`, `title`, `summary`, `confidence_pct`, `out_of_scope`, `rows` con `rate_eur_per_hour` y `cost_eur = horas × tarifa del perfil` (`RATES_EUR_PER_HOUR`: 62,50 EUR/h `dev` y 50 EUR/h `design`), `phases` agregadas en el orden canónico (solo las que tienen filas) con horas, coste, confianza y supuestos, `team`, `duration_weeks`, `assumptions_and_risks`, `narrative`, `total_hours` (suma de las filas) y `total_cost_eur` (suma de los costes, redondeada a 2 decimales). El LLM MUST NOT aportar ningún importe ni total.

#### Scenario: Totales exactos
- **GIVEN** un resultado con filas de 40 h `design` y 60 h `dev`
- **WHEN** se calcula la estimación
- **THEN** los costes son 2000 y 3750 EUR, `total_hours = 100` y `total_cost_eur = 5750`

#### Scenario: Agregación por fase
- **WHEN** se calcula la estimación de un resultado con filas en `qa` antes que en `discovery`
- **THEN** `phases` está en el orden canónico y la suma de las horas y costes de las fases es igual a los totales

### Requirement: Guardrail de salida de fuera de alcance
Si `confidence_pct` es menor que 30, `compute_estimate` SHALL marcar `out_of_scope = true`, dejar vacíos `rows`, `phases`, `team` y `narrative`, `duration_weeks = null` y los totales a 0, y conservar `title`, `summary`, `confidence_pct` y `assumptions_and_risks`. MUST registrar `estimation_out_of_scope` con `confidence_pct`. La política es de filtro: nunca lanza un error.

#### Scenario: Confianza baja
- **GIVEN** un resultado con `confidence_pct = 20` y filas inventadas
- **WHEN** se calcula la estimación
- **THEN** `out_of_scope = true`, no hay filas ni importes y los totales son 0

#### Scenario: Umbral
- **WHEN** se calcula la estimación de un resultado con `confidence_pct = 30`
- **THEN** `out_of_scope = false`

### Requirement: Render Markdown determinista
`render_markdown(estimate, output_format, detail_level)` en `app/estimation/render.py` SHALL generar el `text` de la respuesta en Python, sin LLM, con las etiquetas en `estimate.language` (español o inglés) y la estructura de cada formato: título `## Estimación: <título>` (`## Estimate:`), en `line_items` la tabla `| Tarea | Horas | Coste (EUR) |` y en `phases_table` la tabla `| Fase | Horas | Coste (EUR) | Confianza (%) |` (sin fila de total), en `narrative` los párrafos del LLM seguidos de un párrafo generado por el código con las fases, sus horas y su coste, después `### Totales` (`### Totals`) con total de horas, coste total, equipo recomendado y duración estimada en semanas, `### Supuestos y riesgos` en los formatos con tabla y, con `detailed`, `### Supuestos por fase`. Los importes MUST escribirse sin separador de miles y con coma decimal en español y punto en inglés. Fuera de alcance, el texto MUST tener el título, un aviso localizado con la confianza, el `summary` y los supuestos, sin tablas ni importes. El idioma de las etiquetas MUST ser el detectado en la descripción y, si la detección es ambigua, el `language` del resultado.

#### Scenario: Etiquetas en inglés
- **GIVEN** una descripción detectada como inglés
- **WHEN** se renderiza en `phases_table`
- **THEN** el texto contiene `## Estimate:`, `| Phase | Hours | Cost (EUR) | Confidence (%) |` y `### Totals`, y ninguna etiqueta en español

#### Scenario: Idioma ambiguo
- **GIVEN** una descripción sin idioma detectable y un resultado con `language = "es"`
- **WHEN** se renderiza
- **THEN** las etiquetas están en español

#### Scenario: Narrativa sin tablas
- **WHEN** se renderiza en `narrative`
- **THEN** el texto no contiene tablas, incluye los párrafos del resultado y las cifras de horas y coste salen de los totales calculados

#### Scenario: Render evaluable
- **WHEN** se renderiza cada ejemplo de `ESTIMATION_EXAMPLES` convertido a `EstimationResult` en cada formato, nivel e idioma y se evalúa con la evaluación de ese formato
- **THEN** todos obtienen `score = 1.0`
