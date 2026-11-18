## MODIFIED Requirements

### Requirement: Eventos de sesión y de adjuntos
El sistema SHALL registrar, sin contenido: `session_created` (`session`, `sessions` con el total en el almacén), `session_evicted` (`session`, `turn_count`, `idle_s`), `session_not_found` (`session`), `session_busy` (`session`), `session_turn_completed` (`session`, `turn`, `history_messages` con los mensajes del historial enviados al LLM antes del nuevo `user`, incluidos los de las anclas, `history_turns`, `attachments` con el número de adjuntos, `out_of_scope`, `language`, `metadata_updated`, `metadata_fields` con los nombres de los campos con valor, `technologies` con su número, `anchors`, `summary_chars`, `tier`, `tier_rule`, y `constraints` y `rejected` con el número de elementos de `explicit_constraints` y `rejected_options` del metadata), `attachment_extracted` (`index`, `kind`, `size_bytes`, `chars`, `pages`), `attachment_rejected` (`index`, `reason` y, si se conocen, `kind`, `size_bytes` y `error_type`), `attachment_pii_masked` (`index` y los recuentos `email`, `phone` e `iban`, solo si hay alguno) y `estimation_requested` con `mode = "session"`, `attachments` e `history_messages`. El campo `session` MUST ser solo los 8 primeros caracteres del `session_id`, nunca el id completo. Durante un turno, todos los eventos (incluidos `llm_call_*`, `prompt_rendered`, `input_guardrail_blocked` y los de la memoria y el tier) MUST llevar el campo `session`. Ningún evento MUST contener la transcripción, el texto ni el nombre de los adjuntos, los valores del `project_metadata`, los mensajes del historial, el texto del resumen ni el `session_id` completo.

#### Scenario: Turno registrado
- **GIVEN** una sesión con un turno previo
- **WHEN** se completa un segundo turno con un adjunto
- **THEN** se registran `attachment_extracted` con `index = 0` y `session_turn_completed` con `turn = 2`, `history_messages = 2`, `attachments = 1`, `anchors = 0`, `summary_chars = 0`, `tier` y `tier_rule`, y `llm_call_completed` lleva el campo `session`

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

## ADDED Requirements

### Requirement: Eventos de la memoria y del tier
El sistema SHALL registrar, sin contenido: `anchor_detected` (`turn`, `rules` con los identificadores de las reglas, `source`), `anchor_demoted` (`turn`, `anchors`), `anchor_classifier_failed` (`turn`, `error_type`), `history_compressed` (`evicted`, `anchored`, `demoted`, `summarized`, `backlog`, `dropped`, `anchors`, `summary_updated`, `summary_chars`, `summary_sha256` con los 12 primeros caracteres hexadecimales y `latency_ms`), `summary_failed` (`error_type`, `backlog`), `summary_backlog_dropped` (`dropped`), `history_compression_failed` (`error_type`) y `tier_resolved` (`tier`, `rule`, `explicit` como booleano, `prompt_version`). Las llamadas al LLM de la memoria MUST registrar sus `llm_call_*` con `llm_purpose` (`conversation_summary` o `anchor_classifier`) mediante un contextvar, sin cambios en el proveedor. Ningún evento MUST contener el fragmento que casó una regla, el texto del resumen, el texto de las restricciones u opciones descartadas ni la transcripción.

#### Scenario: Compresión registrada
- **GIVEN** un compresor determinista y `MAX_CONVERSATION_TURNS = 2`
- **WHEN** se envía el tercer turno de una sesión
- **THEN** se registra `history_compressed` con `evicted = 1`, `summary_updated = true` y `summary_chars` mayor que 0

#### Scenario: Llamada de la memoria atribuida
- **WHEN** el resumidor llama al proveedor simulado
- **THEN** su `llm_call_completed` lleva `llm_purpose = "conversation_summary"`

#### Scenario: Memoria sin contenido en los logs
- **GIVEN** `LOG_LEVEL=DEBUG`, un resumidor falso que copia los turnos al resumen, una transcripción con `RESUMIDO-SECRETO-123` que sale de la ventana y una estimación con `explicit_constraints = ["RESTRICCION-SECRETA-456"]`
- **WHEN** se completan los turnos
- **THEN** ningún log contiene `RESUMIDO-SECRETO-123` ni `RESTRICCION-SECRETA-456`

#### Scenario: Tier registrado
- **WHEN** se resuelve el tier de un turno con un NDA
- **THEN** se registra `tier_resolved` con `tier = "executive"`, `rule = "nda_detected"` y `explicit = false`, sin el texto del turno
