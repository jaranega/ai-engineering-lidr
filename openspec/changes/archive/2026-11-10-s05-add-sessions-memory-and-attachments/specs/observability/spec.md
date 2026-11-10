## ADDED Requirements

### Requirement: Eventos de sesión y de adjuntos
El sistema SHALL registrar, sin contenido: `session_created` (`session`, `sessions` con el total en el almacén), `session_evicted` (`session`, `turn_count`, `idle_s`), `session_not_found` (`session`), `session_busy` (`session`), `session_turn_completed` (`session`, `turn`, `history_messages` con los mensajes del historial enviados al LLM antes del nuevo `user`, `history_turns`, `attachments` con el número de adjuntos, `out_of_scope`, `language`, `metadata_updated`, `metadata_fields` con los nombres de los campos con valor y `technologies` con su número), `attachment_extracted` (`index`, `kind`, `size_bytes`, `chars`, `pages`), `attachment_rejected` (`index`, `reason` y, si se conocen, `kind`, `size_bytes` y `error_type`), `attachment_pii_masked` (`index` y los recuentos `email`, `phone` e `iban`, solo si hay alguno) y `estimation_requested` con `mode = "session"`, `attachments` e `history_messages`. El campo `session` MUST ser solo los 8 primeros caracteres del `session_id`, nunca el id completo. Durante un turno, todos los eventos (incluidos `llm_call_*`, `prompt_rendered` e `input_guardrail_blocked`) MUST llevar el campo `session`. Ningún evento MUST contener la transcripción, el texto ni el nombre de los adjuntos, los valores del `project_metadata`, los mensajes del historial ni el `session_id` completo.

#### Scenario: Turno registrado
- **GIVEN** una sesión con un turno previo
- **WHEN** se completa un segundo turno con un adjunto
- **THEN** se registran `attachment_extracted` con `index = 0` y `session_turn_completed` con `turn = 2`, `history_messages = 2` y `attachments = 1`, y `llm_call_completed` lleva el campo `session`

#### Scenario: Sin contenido en los logs
- **GIVEN** `LOG_LEVEL=DEBUG`, una transcripción con `TRANSCRIPCION-SECRETA-123` y un PDF llamado `NOMBRE-SECRETO.pdf` cuyo texto contiene `PDF-SECRETO-456`
- **WHEN** se completa el turno y se envía un segundo turno a la misma sesión
- **THEN** ningún log contiene `TRANSCRIPCION-SECRETA-123`, `NOMBRE-SECRETO`, `PDF-SECRETO-456` ni el `session_id` completo

#### Scenario: Adjunto rechazado
- **WHEN** se envía un PDF cifrado como segundo adjunto
- **THEN** se registra `attachment_rejected` con `index = 1` y `reason = "encrypted"`, sin el nombre del fichero

#### Scenario: Enmascarado registrado
- **WHEN** se envía un adjunto con dos emails
- **THEN** se registra `attachment_pii_masked` con `email = 2` y sin los emails
