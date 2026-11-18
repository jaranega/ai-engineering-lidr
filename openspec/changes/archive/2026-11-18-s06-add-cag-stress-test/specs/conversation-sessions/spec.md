## ADDED Requirements

### Requirement: Última observación del turno
Tras cada turno con éxito, la sesión SHALL guardar en `last_observation` un objeto inmutable con los mismos 13 campos que el evento `turn_observed` (capability `observability`). Un turno con error MUST NOT modificarla.

#### Scenario: Observación guardada
- **WHEN** termina con éxito el segundo turno de una sesión
- **THEN** `last_observation.turn_index = 2` y sus valores coinciden con los del evento `turn_observed` de ese turno

### Requirement: Endpoint de depuración de la sesión
Con `DEBUG_ENDPOINTS=true`, el sistema SHALL exponer `GET /api/v1/sessions/{session_id}/debug`.

**Respuesta 200 (`SessionDebug`).**
- `session_id`.
- `last_observation`: objeto con los 13 campos, o `null` sin turnos.
- `summary`: texto del resumen acumulativo, o `null`.
- `anchors`: lista de strings, el `user` guardado de cada ancla en orden, con los adjuntos como `[…]`.
- `project_metadata`.

**Sin la variable.** Con `DEBUG_ENDPOINTS=false` (por defecto), la ruta MUST responder **404** sin consultar el almacén y MUST NOT aparecer en OpenAPI.

**Errores.** Un `session_id` mal formado MUST dar 422, y una sesión inexistente MUST dar 404 `session_not_found`.

**Restricciones.** El endpoint MUST NOT modificar la sesión ni su recencia más allá de la lectura habitual del almacén. Los demás endpoints MUST seguir sin exponer texto del resumen ni de las anclas.

#### Scenario: Depuración desactivada
- **GIVEN** `DEBUG_ENDPOINTS` sin definir
- **WHEN** se hace GET a `/api/v1/sessions/{id}/debug` de una sesión existente
- **THEN** la respuesta es 404 y `/openapi.json` no contiene la ruta

#### Scenario: Depuración activa
- **GIVEN** `DEBUG_ENDPOINTS=true` y una sesión con un resumen "Proyecto Nimbus…", un ancla y dos turnos
- **WHEN** se hace GET a `/api/v1/sessions/{id}/debug`
- **THEN** la respuesta es 200 con `summary` que empieza por "Proyecto Nimbus", un elemento en `anchors`, el `project_metadata` y `last_observation.turn_index = 2`

#### Scenario: Sesión sin turnos
- **GIVEN** `DEBUG_ENDPOINTS=true` y una sesión recién creada
- **WHEN** se hace GET a `/api/v1/sessions/{id}/debug`
- **THEN** la respuesta es 200 con `last_observation = null`, `summary = null` y `anchors = []`
