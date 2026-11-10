## MODIFIED Requirements

### Requirement: Resultado de la evaluación
`validation` SHALL tener los campos: `output_format` (`string`), `checks_total` (`integer`, número de comprobaciones del formato), `has_title`, `has_totals`, `has_team`, `has_duration` (`boolean`), `has_breakdown_table` (`boolean | null`; `null` en `narrative`), `rows_parsed` (`integer`; `0` en `narrative`), `declared_total_hours`, `sum_row_hours` (`number | null`), `hours_match` (`boolean | null`), `declared_total_cost`, `sum_row_cost` (`number | null`), `cost_match` (`boolean | null`), `finish_ok` (`boolean`), `score` (`number`, de 0 a 1, aprobadas entre `checks_total` redondeado a 3 decimales) e `issues` (lista de `string` en inglés que nunca reproducen texto de la estimación). El log MUST registrar `estimation_evaluated` con `output_format`, `score` y el número de incidencias.

#### Scenario: Puntuación parcial
- **GIVEN** una estimación `line_items` que pasa 6 de las 8 comprobaciones
- **WHEN** se evalúa
- **THEN** `score = 0.75` e `issues` tiene 2 elementos

#### Scenario: Puntuación parcial narrativa
- **GIVEN** una estimación `narrative` que pasa 4 de las 5 comprobaciones
- **WHEN** se evalúa
- **THEN** `score = 0.8`

#### Scenario: Ejemplos de referencia
- **WHEN** se evalúa el texto de cada ejemplo de `ESTIMATION_EXAMPLES` renderizado por `render_markdown` en cada `output_format`, nivel e idioma
- **THEN** todas obtienen `score = 1.0`

## ADDED Requirements

### Requirement: Evaluación del texto renderizado
La evaluación SHALL aplicarse al `text` que renderiza el servidor a partir del resultado estructurado (también en los aciertos de caché), con las mismas comprobaciones por formato. Si la estimación está fuera de alcance (`result.out_of_scope = true`), `validation` MUST ser `null` y MUST registrarse `estimation_evaluated` con `skipped = "out_of_scope"`, porque un aviso sin tablas ni totales no se puede puntuar.

#### Scenario: Estimación en alcance
- **GIVEN** un LLM que devuelve un resultado válido en alcance
- **WHEN** se hace POST a `/api/v1/estimate` con `evaluate = true`
- **THEN** `validation.score = 1.0` y `validation.finish_ok = true`

#### Scenario: Fuera de alcance sin evaluación
- **GIVEN** un LLM que devuelve `confidence_pct = 10`
- **WHEN** se hace POST a `/api/v1/estimate` con `evaluate = true`
- **THEN** `validation = null` y el log contiene `estimation_evaluated` con `skipped = "out_of_scope"`
