## MODIFIED Requirements

### Requirement: Schema de la estimación estructurada
El sistema SHALL definir en `app/estimation/schema.py` el modelo Pydantic `EstimationResult`, que es lo único que rellena el LLM, con los campos en este orden: `language` (`"es" | "en"`, idioma de los campos de texto libre), `title` (1–120 caracteres), `summary` (1–600), `confidence_pct` (0–100, confianza global), `technologies` (0–15 textos de 1 a 60 caracteres, por defecto `[]`; ver el requisito "Tecnologías mencionadas por el cliente"), `rows` (0–60 filas con `task` de 1–200 caracteres, `phase` de la lista canónica `discovery | setup | development | qa | deployment`, `rate_profile` `"dev" | "design"` y `hours` de 1 a 2000), `phases` (0–5 elementos con `phase`, `confidence_pct` de 0 a 100 y `assumptions`, de 0 a 3 textos), `team` (0–8 miembros con `role`, `count` de 1 a 20 y `part_time`), `duration_weeks` (`{min_weeks, max_weeks}` de 1 a 104, con `min_weeks ≤ max_weeks`, o `null`), `assumptions_and_risks` (0–8 textos) y `narrative` (0–6 párrafos). El schema MUST NOT contener importes, tarifas ni totales. El módulo MUST NOT importar Instructor, LiteLLM ni `app.services`.

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

#### Scenario: Posición de las tecnologías
- **WHEN** se inspeccionan las propiedades de `EstimationResult.model_json_schema()`
- **THEN** `technologies` va justo después de `confidence_pct` y antes de `rows`, y no está entre los campos obligatorios

## ADDED Requirements

### Requirement: Tecnologías mencionadas por el cliente
`EstimationResult.technologies` SHALL recoger las tecnologías nombradas explícitamente por el cliente (lenguajes, frameworks, plataformas, servicios cloud y de terceros) en la descripción, la conversación o los adjuntos, sin recomendaciones propias del modelo, con una descripción de campo que lo diga. Un normalizador previo a la validación MUST, sin reintento y de forma idempotente: recortar los espacios, quitar los textos vacíos y los de más de 60 caracteres, quitar los duplicados sin distinguir mayúsculas (`casefold`, conservando la primera grafía) y quedarse con las 15 primeras, de modo que este campo nunca provoque un reintento de la llamada estructurada. `ComputedEstimate` MUST incluir `technologies` copiado tal cual del resultado, también fuera de alcance. `render_markdown` MUST NOT mostrar las tecnologías: el texto de los tres formatos y su evaluación no cambian. `POST /api/v1/estimate` MUST devolver el campo en `result.technologies` sin más cambios en su contrato.

#### Scenario: Normalización de tecnologías
- **WHEN** se valida un resultado con `technologies = [" Stripe ", "", "stripe", "Twilio", <texto de 61 caracteres>]`
- **THEN** queda `["Stripe", "Twilio"]` sin error de validación ni reintento

#### Scenario: Tope por turno
- **WHEN** se valida un resultado con 20 tecnologías distintas
- **THEN** quedan las 15 primeras

#### Scenario: Sin tecnologías
- **WHEN** se valida un resultado sin el campo `technologies`
- **THEN** la validación pasa con `technologies = []`

#### Scenario: Fuera de alcance conserva las tecnologías
- **GIVEN** un resultado con `confidence_pct = 10` y `technologies = ["SAP"]`
- **WHEN** se calcula la estimación
- **THEN** `out_of_scope = true` y `technologies = ["SAP"]`

#### Scenario: Sin render en el Markdown
- **GIVEN** un resultado con `technologies = ["TECNOLOGIA-MARCADOR"]`
- **WHEN** se renderiza en cada formato
- **THEN** el texto no contiene `TECNOLOGIA-MARCADOR`
