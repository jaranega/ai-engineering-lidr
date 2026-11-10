# estimation-evaluation Specification

## Purpose
Comprobar de forma determinista, sin llamar al LLM, que la estimación generada respeta el formato de salida (título, desglose, totales, supuestos y riesgos) y que sus totales de horas y coste cuadran con las filas, devolviendo una puntuación y la lista de problemas encontrados.
## Requirements
### Requirement: Evaluación estructural de la estimación
El sistema SHALL evaluar cada estimación generada por `POST /api/v1/estimate` (también las servidas desde la caché) con una función pura, sin LLM ni red, cuando `evaluate` es `true`, y devolver el resultado en `validation`. Con `evaluate = false`, `validation` MUST ser `null`. Las comprobaciones MUST aceptar las etiquetas en español y en inglés, sin distinguir mayúsculas ni el marcado de negrita:
- título (`Estimación:` / `Estimate:` en un encabezado);
- tabla de desglose con cabecera `Tarea|Task`, `Horas|Hours` y `Coste|Cost`, cuyas columnas se identifican por la cabecera; las filas cuya tarea empieza por `Total` no cuentan;
- totales declarados de horas (`Total de horas` / `Total hours`) y de coste (`Coste total` / `Total cost`);
- equipo (`Equipo recomendado` / `Recommended team`);
- duración (`Duración estimada` / `Estimated duration`) con un número y semanas;
- suma de horas de las filas igual al total declarado con una tolerancia de 1 hora;
- suma de costes de las filas igual al total declarado con una tolerancia del 2 % del total;
- respuesta no truncada.

#### Scenario: Estimación correcta en español
- **GIVEN** una estimación con título, tabla `| Tarea | Horas | Coste (EUR) |` de filas que suman 120 horas y 7500 EUR, totales 120 y 7500, equipo y "Duración estimada: 4-5 semanas"
- **WHEN** se evalúa
- **THEN** todas las comprobaciones pasan, `score = 1.0` e `issues` está vacío

#### Scenario: Estimación correcta en inglés
- **GIVEN** la misma estimación con etiquetas en inglés (`## Estimate:`, `| Task | Hours | Cost (EUR) |`, `Total hours`, `Total cost`, `Recommended team`, `Estimated duration: 4-5 weeks`)
- **WHEN** se evalúa
- **THEN** `score = 1.0`

#### Scenario: Totales que no cuadran
- **GIVEN** una tabla cuyas filas suman 120 horas y un total declarado de 130
- **WHEN** se evalúa
- **THEN** `hours_match = false`, `score < 1.0` e `issues` incluye la discrepancia con los dos números

#### Scenario: Sin tabla
- **GIVEN** una estimación en lista numerada, sin tabla
- **WHEN** se evalúa
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
`validation` SHALL tener los campos: `has_title`, `has_breakdown_table`, `has_totals`, `has_team`, `has_duration` (`boolean`), `rows_parsed` (`integer`), `declared_total_hours`, `sum_row_hours` (`number | null`), `hours_match` (`boolean | null`), `declared_total_cost`, `sum_row_cost` (`number | null`), `cost_match` (`boolean | null`), `finish_ok` (`boolean`), `score` (`number`, de 0 a 1, aprobadas entre 8 redondeado a 3 decimales) e `issues` (lista de `string` en inglés que nunca reproducen texto de la estimación). El log MUST registrar `estimation_evaluated` con `score` y el número de incidencias.

#### Scenario: Puntuación parcial
- **GIVEN** una estimación que pasa 6 de las 8 comprobaciones
- **WHEN** se evalúa
- **THEN** `score = 0.75` e `issues` tiene 2 elementos

#### Scenario: Ejemplos de referencia
- **WHEN** se evalúa la `estimation` de cada ejemplo de `ESTIMATION_EXAMPLES`
- **THEN** todas obtienen `score = 1.0`

