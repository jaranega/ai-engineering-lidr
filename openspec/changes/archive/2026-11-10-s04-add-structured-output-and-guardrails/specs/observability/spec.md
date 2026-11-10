## ADDED Requirements

### Requirement: Eventos de los guardrails y de la salida estructurada
El sistema SHALL registrar, sin contenido de la petición ni de la respuesta: `input_guardrail_blocked` (`reason`, `field`, `rule`), `moderation_checked` (`flagged`, número de textos, `latency_ms` y, si se marca, solo los nombres de las categorías), `moderation_failed` (`error_type`, `status_code`), `moderation_disabled` una vez al construir el moderador (`reason`: `config` o `no_openai_key`), `structured_output_retry` (`attempt`, número de errores, `error_locs` con las rutas de campo y el tipo de error, y `coherence_errors` con los mensajes fijos de los validadores de coherencia del schema; nunca el mensaje de Pydantic de un campo, que incluye valores), `structured_output_failed` (`reason`, `attempts`), `llm_call_started`/`llm_call_completed` con `mode = "structured"` y `attempts`, `cache_entry_invalid`, `estimation_out_of_scope` (`confidence_pct`) y `estimation_rendered` (`output_format`, `language`, `language_source` `detected` o `llm`).

#### Scenario: Reintento sin valores
- **GIVEN** una descripción con `DESCRIPCION-SECRETA-123` y un LLM que devuelve primero un objeto inválido que la repite
- **WHEN** se genera la estimación
- **THEN** se registra `structured_output_retry` y ningún log contiene `DESCRIPCION-SECRETA-123`

#### Scenario: Moderación sin contenido
- **GIVEN** una moderación que marca la descripción `TEXTO-MARCADO-789`
- **WHEN** se procesa la petición
- **THEN** se registran `moderation_checked` con `flagged = true` e `input_guardrail_blocked`, y ningún log contiene `TEXTO-MARCADO-789`
