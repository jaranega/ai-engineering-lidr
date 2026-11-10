# estimation-evaluation Specification

## Purpose
Comprobar de forma determinista, sin llamar al LLM, que la estimación generada respeta el formato de salida (título, desglose, totales, supuestos y riesgos) y que sus totales de horas y coste cuadran con las filas, devolviendo una puntuación y la lista de problemas encontrados.
## Requirements
### Requirement: Evaluación estructural de la estimación
El sistema SHALL evaluar cada estimación generada por `POST /api/v1/estimate` (también las servidas desde la caché) con una función pura, sin LLM ni red, según el `output_format` de la petición, cuando `evaluate` es `true`, y devolver el resultado en `validation`. Con `evaluate = false`, `validation` MUST ser `null`. Las comprobaciones MUST aceptar las etiquetas en español y en inglés, sin distinguir mayúsculas ni el marcado de negrita:

| Comprobación | `line_items` | `phases_table` | `narrative` |
|---|---|---|---|
| título (`Estimación:` / `Estimate:` en un encabezado) | ✓ | ✓ | ✓ |
| tabla de desglose, cuyas columnas se identifican por la cabecera; las filas que empiezan por `Total` no cuentan | `Tarea\|Task`, `Horas\|Hours`, `Coste\|Cost` | `Fase\|Phase`, `Horas\|Hours`, `Coste\|Cost`, `Confianza\|Confidence` | — |
| totales declarados de horas (`Total de horas` / `Total hours`) y de coste (`Coste total` / `Total cost`) | ✓ | ✓ | ✓ |
| equipo (`Equipo recomendado` / `Recommended team`) | ✓ | ✓ | ✓ |
| duración (`Duración estimada` / `Estimated duration`) con un número y semanas | ✓ | ✓ | ✓ |
| suma de horas de las filas igual al total declarado (tolerancia de 1 hora) | ✓ | ✓ | — |
| suma de costes de las filas igual al total declarado (tolerancia del 2 % del total) | ✓ | ✓ | — |
| respuesta no truncada | ✓ | ✓ | ✓ |
| **número de comprobaciones** | **8** | **8** | **5** |

La confianza de `phases_table` MUST NOT validarse fila a fila.

#### Scenario: Estimación correcta en español
- **GIVEN** una estimación `line_items` con título, tabla `| Tarea | Horas | Coste (EUR) |` de filas que suman 120 horas y 7500 EUR, totales 120 y 7500, equipo y "Duración estimada: 4-5 semanas"
- **WHEN** se evalúa con `output_format = "line_items"`
- **THEN** todas las comprobaciones pasan, `score = 1.0` e `issues` está vacío

#### Scenario: Estimación correcta en inglés
- **GIVEN** la misma estimación con etiquetas en inglés (`## Estimate:`, `| Task | Hours | Cost (EUR) |`, `Total hours`, `Total cost`, `Recommended team`, `Estimated duration: 4-5 weeks`)
- **WHEN** se evalúa con `output_format = "line_items"`
- **THEN** `score = 1.0`

#### Scenario: Tabla por fases
- **GIVEN** una estimación con la tabla `| Fase | Horas | Coste (EUR) | Confianza (%) |` cuyas filas suman 300 horas y 18750 EUR, y los totales, el equipo y la duración correctos
- **WHEN** se evalúa con `output_format = "phases_table"`
- **THEN** `score = 1.0` y `checks_total = 8`

#### Scenario: Tabla por fases sin confianza
- **GIVEN** una estimación cuya tabla tiene las columnas `Fase`, `Horas` y `Coste` pero no `Confianza`
- **WHEN** se evalúa con `output_format = "phases_table"`
- **THEN** `has_breakdown_table = false`

#### Scenario: Narrativa correcta
- **GIVEN** una estimación en prosa, sin tablas, con título y `### Totales` con las cuatro viñetas
- **WHEN** se evalúa con `output_format = "narrative"`
- **THEN** `score = 1.0`, `checks_total = 5`, `has_breakdown_table`, `hours_match` y `cost_match` son `null` y no hay incidencias sobre la tabla

#### Scenario: Totales que no cuadran
- **GIVEN** una tabla cuyas filas suman 120 horas y un total declarado de 130
- **WHEN** se evalúa con `output_format = "line_items"`
- **THEN** `hours_match = false`, `score < 1.0` e `issues` incluye la discrepancia con los dos números

#### Scenario: Sin tabla
- **GIVEN** una estimación en lista numerada, sin tabla
- **WHEN** se evalúa con `output_format = "line_items"`
- **THEN** `has_breakdown_table = false`, `hours_match` y `cost_match` son `null` y cuentan como no aprobadas

#### Scenario: Respuesta truncada
- **GIVEN** una estimación con `truncated = true`
- **WHEN** se evalúa
- **THEN** `finish_ok = false` e `issues` lo indica

#### Scenario: Evaluación desactivada
- **WHEN** el body incluye `evaluate: false`
- **THEN** la respuesta tiene `validation = null`

### Requirement: Lectura de números
La evaluación SHALL leer importes y horas en formato español e inglés: ignorar `EUR`, `€`, `h`, espacios y asteriscos; si aparecen punto y coma, el último es el separador decimal; si aparece solo uno seguido de exactamente tres dígitos, es separador de miles; en otro caso, es decimal.

#### Scenario: Formatos de número
- **WHEN** se leen `19500`, `19.500`, `19,500`, `937,50`, `937.5` y `1.937,50 EUR`
- **THEN** se obtienen 19500, 19500, 19500, 937.5, 937.5 y 1937.5

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

