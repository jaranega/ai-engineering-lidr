## ADDED Requirements

### Requirement: Eventos de la revisión Actor-Critic-Boss
Con `review=true`, el sistema SHALL registrar, con el identificador corto de la sesión en el contexto, estos eventos:
- **`critic_completed`**, por cada revisión con éxito, con `iteration`, `verdict`, `issues`, `critical`, `major`, `minor`, `categories` (lista ordenada sin repetidos), `confidence` y `latency_ms`.
- **`critic_failed`**, por cada fallo del Critic, con `iteration` y `error_type`.
- **`boss_iteration`**, por cada iteración, con `iteration`, `decision`, `verdict` (o `None`), `issues` y `draft_out_of_scope`.
- **`acb_completed`**, una vez por turno, con `final_decision`, `stop_reason`, `iterations`, `llm_calls`, `latency_ms`, `input_tokens` y `output_tokens` acumulados.

Además:
- Las llamadas del Critic MUST emitir sus eventos de proveedor (`llm_call_started` y `llm_call_completed`) con `llm_purpose = "critic"`.
- Los borradores del Actor MUST llevar `review_iteration` en el contexto.
- `session_turn_completed` MUST añadir `review` (bool) y `review_decision` (`final_decision` o `None`).

Ningún evento MUST contener la transcripción, el texto de los problemas (`description`, `suggested_fix`), el `field_path` ni la estimación.

#### Scenario: Eventos de una revisión con reintento
- **GIVEN** un Critic simulado que pide un reintento y después acepta
- **WHEN** se envía un turno con `review=true`
- **THEN** se registran dos `critic_completed`, dos `boss_iteration` (`iterate` y `accept`) y un `acb_completed` con `iterations = 2`, `llm_calls = 4` y `stop_reason = "accepted"`

#### Scenario: Sin texto del Critic en los logs
- **GIVEN** un Critic simulado cuya descripción de problema contiene `DESCRIPCION-SECRETA-123` y su `field_path` es `rows[7].hours`
- **WHEN** se completa un turno con `review=true`
- **THEN** ningún evento registrado contiene `DESCRIPCION-SECRETA-123` ni `rows[7]`

#### Scenario: Turno sin revisión
- **WHEN** se completa un turno con `review=false`
- **THEN** no se registra ningún evento `critic_*`, `boss_iteration` ni `acb_completed`, y `session_turn_completed` tiene `review = false`
