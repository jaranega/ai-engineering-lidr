## ADDED Requirements

### Requirement: Evento `turn_observed`
Al final de cada turno de sesión con éxito, después de la compresión, el sistema SHALL registrar un único evento `turn_observed`, desde `SessionService.estimate`, con exactamente estos campos:

- `turn_index`: ordinal del turno en la sesión, empezando en 1.
- `session_id`: los 8 primeros caracteres del identificador, como el campo `session` del resto de eventos; nunca el completo.
- `enriched_transcript_chars`: caracteres de la transcripción más los del texto enmascarado de los adjuntos.
- `attachments_total_chars`: caracteres del texto de los adjuntos; 0 sin adjuntos.
- `messages_in_window`: número de mensajes de anclas y ventana tras la compresión.
- `anchors_count`.
- `summary_chars`: 0 sin resumen.
- `tokens_in` y `tokens_out`: los del turno, sumados si hubo revisión.
- `cost_usd`: `null` si es desconocido.
- `latency_ms`: duración completa de `SessionService.estimate`, incluida la compresión.
- `cache_hit_kind`: `"exact"` si el resultado salió de la caché exacta, `"none"` en otro caso; `"semantic"` está reservado.
- `last_resolved_tier`.

El evento MUST NOT contener texto del cliente, del resumen ni de la estimación. Un turno con error MUST NOT emitir `turn_observed`.

#### Scenario: Primer turno sin adjuntos
- **GIVEN** una sesión vacía y un proveedor simulado
- **WHEN** termina con éxito un turno con una transcripción de 120 caracteres
- **THEN** se registra un `turn_observed` con `turn_index = 1`, `enriched_transcript_chars = 120`, `attachments_total_chars = 0`, `messages_in_window = 2`, `anchors_count = 0`, `summary_chars = 0`, `cache_hit_kind = "none"` y los tokens y el coste del turno

#### Scenario: Turno con adjunto
- **GIVEN** un turno con una transcripción de 100 caracteres y un PDF cuyo texto extraído tiene 5000 caracteres
- **WHEN** termina con éxito
- **THEN** `attachments_total_chars = 5000` y `enriched_transcript_chars = 5100`

#### Scenario: Acierto de caché
- **GIVEN** un proveedor simulado que devuelve el resultado desde la caché exacta
- **WHEN** termina el turno
- **THEN** `cache_hit_kind = "exact"`

#### Scenario: Sin texto en el evento
- **GIVEN** una transcripción con `TRANSCRIPCION-SECRETA-123`
- **WHEN** termina el turno
- **THEN** el evento `turn_observed` no contiene `TRANSCRIPCION-SECRETA-123` ni el `session_id` completo, y su `session_id` son los 8 primeros caracteres

#### Scenario: Turno fallido
- **WHEN** un turno termina con 502
- **THEN** no se registra `turn_observed`
