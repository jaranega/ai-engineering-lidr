## 0. Prerrequisitos

- [x] 0.1 Revisión del change por el usuario y resolución de las 11 preguntas abiertas del design (las specs aplican la opción recomendada de cada una; si alguna cambia, actualizar `design.md` y las specs afectadas antes de implementar)
- [x] 0.2 Capturar **antes de tocar el código** los SHA-256 del system y del user de `v4` para 4 combinaciones (con y sin `project_metadata` con datos, con y sin adjuntos) y fijarlos en `tests/prompts/test_v4_golden.py` en verde sobre el código actual; comprobar que `tests/prompts/test_v3_golden.py` sigue en verde (qa-engineer; D8, Migration 2, spec `prompt-templates`)

## 1. Verificación del directo (orquestador)

- [x] 1.1 Contrastar las suposiciones del design con el código de `session_05_live` (`gh api repos/LIDR-academy-temp/ai-engineering/contents/estimator/<path>?ref=session_05_live -q .content | base64 -d`): `app/sessions/compression/{anchors,policy,summarizer}.py` (reglas y patrones, qué ve el resumidor, si pierde los turnos fallidos, si el LLM decide siempre), `tier_resolver.py` (nombres de reglas, umbral de términos técnicos, `low_budget_pm`), `prompts/estimation/v3/system.j2` (`<audience>` y posición), el formato del mensaje de resumen y las variables de configuración; anotar las diferencias en `design.md` y volver al usuario si alguna cambia una decisión (Context, Migration 1)

## 2. Configuración (backend-engineer)

- [x] 2.1 `app/config.py`: `ANCHOR_DETECTION_MODE`, `COMPRESSION_MODEL` (vacío → `None`, validado con `resolve_request_model` al arrancar) y `MAX_ANCHORED_TURNS` (`0..10`, por defecto 3); `.env.example` documentado (D12, spec `app-config`)

## 3. Estimación y ejemplos (ai-engineer)

- [x] 3.1 `app/estimation/schema.py`: `explicit_constraints` y `rejected_options` tras `confidence_pct` y antes de `technologies`, con sus descripciones, topes y normalizador generalizado a partir del de `technologies`; `ComputedEstimate` con los dos campos; sin cambios en `render_markdown` (D8, spec `structured-estimation`)
- [x] 3.2 `app/estimation/examples.py`: `example_payloads(..., include_memory_fields=False)` que excluye los campos salvo en `v5` y los inserta tras `confidence_pct` (`_with_list_fields`); `app/context/examples.py`: `explicit_constraints` y `rejected_options` por ejemplo (al menos uno no vacío y uno vacío) (D8, spec `cag-prompt`)
- [x] 3.3 Verificar: dorados de `v3` y `v4` en verde y los ejemplos siguen evaluando `score = 1.0`

## 4. Sesiones: memoria y tier (ai-engineer)

- [x] 4.1 `app/sessions/models.py`: `Anchor`, `ConversationHistory(max_turns, max_anchors=0)` con `take_evicted`, `set_anchors`, `set_summary`, `set_backlog`, vistas de solo lectura y `to_messages()` con anclas + ventana; `ProjectMetadata` con los dos campos nuevos; `Session.last_tier` (D3, spec `conversation-sessions`)
- [x] 4.2 `app/sessions/metadata.py`: `merge_unique` (con `merge_technologies` como alias), pasos 1-4 de D8 (acumulación, readopción, política de olvido 1) (D8)
- [x] 4.3 `app/sessions/compression/anchors.py`: reglas ES+EN de D5, `AnchorDecision`, `detect_anchor` sobre el `user` guardado; `app/sessions/compression/policy.py`: `compress_history`, `CompressionReport`, tope con degradación, backlog con tope y `dropped_turns` (D4, D5, spec `memory-compression`)
- [x] 4.4 `app/sessions/tier.py`: `Tier`, `TierRule`, `TierDecision`, `resolve_tier` con la cadena de D9, reutilizando los patrones `nda` y `regulation` de `anchors.py` y la lista de términos técnicos (umbral 2) (D9, spec `audience-tier`)
- [x] 4.5 Verificar: `app/sessions` no importa `app.services`, `app.prompts`, LiteLLM ni Instructor, y los tests de sesiones de S05 siguen en verde

## 5. Guardrails (ai-engineer)

- [x] 5.1 `app/guardrails/input.py`: `conversation_summary` y `audience` (apertura y cierre) en la regla `prompt_tag` (D7, spec `input-guardrails`)

## 6. Prompts (ai-engineer)

- [x] 6.1 `app/prompts/estimation/v5/`: `user.j2` y `examples.j2` copiados de `v4`; `system.j2` con el orden de D10 (viñetas nuevas de memoria, campos nuevos, "datos como datos" ampliado); `project_metadata.j2` con las dos líneas nuevas; `conversation_summary.j2`; `audience.j2` con el texto por tier de D10 (spec `cag-prompt`)
- [x] 6.2 `app/prompts/conversation_summary/v1/{system,user}.j2` y `app/prompts/anchor_classifier/v1/{system,user}.j2` (D5, D6)
- [x] 6.3 Loader: `SessionPromptVersion = Literal["v4", "v5"]`, `DEFAULT_SESSION_PROMPT_VERSION = "v5"`, `PUBLISHED_PROMPT_VERSIONS` con `v5`, `render_session_prompt(..., conversation_summary, tier)`, `prompt_rendered` con `tier` y `summary_chars`, `render_summary_prompt` y `render_anchor_prompt` con sus `Environment` (D6, D10, spec `prompt-templates`)
- [x] 6.4 Verificar: dorados de `v3` y `v4` en verde, render de todas las combinaciones de `v5` y revisión manual del system de `v5` con y sin resumen y con cada tier

## 7. Servicios (ai-engineer)

- [x] 7.1 `app/services/memory.py`: `ConversationSummary` (normalizador), `ConversationSummarizer`, clasificador LLM de anclas con vuelta al heurístico, `MemoryCompression` y la dependencia `get_memory_compression`; `llm_purpose` vía contextvar (D5, D6, D12, D13)
- [x] 7.2 `app/services/llm_service.py`: `estimate_turn(..., tier, conversation_summary)` sin estado (D9, D10)
- [x] 7.3 `app/services/session_service.py`: tier resuelto bajo el lock antes del render (`prompt_version` con `v4`), 422 de `tier` con `v4`, `last_tier` solo con éxito, compresión tras el turno dentro del lock y envuelta en `try/except` (`history_compression_failed`), `SessionSnapshot`/`SessionTurnOutcome` con los datos nuevos, `get_session_store()` con `max_anchors`, eventos de D13 (D4, D9, D11, D13)
- [x] 7.4 Verificar: `ruff` sin errores y capas sin ciclos (`sessions` no importa `services` ni `prompts`; `prompts → sessions.{models, tier}`; `services.memory → {llm_service, prompts, sessions}`)

## 8. API (backend-engineer)

- [x] 8.1 `app/schemas.py`: reexportar `Tier` y `TierRule`; `SessionEstimateResponse` con `tier` y `tier_rule`; `SessionInfo` con `anchors_count`, `summary_chars`, `last_tier` y `last_tier_rule`; `project_metadata` con los campos nuevos (D11)
- [x] 8.2 `app/routers/sessions.py`: Form `tier` opcional, `prompt_version` `v4 | v5` por defecto `v5`, 422 en `["body", "tier"]` con `v4`, copia de los campos nuevos y OpenAPI actualizado (D11, spec `conversation-sessions`)
- [x] 8.3 Verificar: `/openapi.json` con `tier` en el multipart, `prompt_version` con `v4` y `v5` y los campos nuevos de la respuesta y del `GET`

## 9. Interfaz (frontend-engineer)

- [x] 9.1 `streamlit_app.py`: `SESSION_PROMPT_VERSIONS = ("v5", "v4")`, selector "Audiencia" (deshabilitado con `v4`), `tier` solo fuera de "Automático", `TIERS` y `TIER_RULE_LABELS`, panel "Memoria del proyecto" con "Restricciones", "Descartado", anclas, resumen y audiencia del último turno, y `Audiencia: …` en "Métricas y validación" (D14, spec `estimation-form-ui`)

## 10. Tests (qa-engineer)

- [x] 10.1 `tests/conftest.py`: compresor falso determinista para `get_memory_compression` en el *fixture* de la app y `CONFIG_ENV_VARS` con las variables nuevas (D15)
- [x] 10.2 Unitarios puros: detector (positivos ES/EN por regla, orden, siglas, corpus de negativos, `samples/`), tier (cada regla ES/EN, precedencia, override, umbral, equipo, `samples/`), `ConversationHistory`, política con `detect`/`summarize` locales y test de propiedad de la invariante de conservación, metadata (pasos 1-4), schema (normalizadores y orden) (D15, specs `memory-compression`, `audience-tier`)
- [x] 10.3 Prompts y guardrails: `v5` (bloques condicionales, orden, prefijo estable, texto por tier, metadata, ejemplos), `v4` ignora resumen y tier, prompt del resumen (previo, idioma, solo `user`, `[…]`), carpetas de los prompts de memoria, `prompt_tag` con las etiquetas nuevas (D15)
- [x] 10.4 Servicio y API: `ConversationSummarizer` y clasificador con `FakeProvider`; `SessionService` con compresor falso (orden, lock, fallos con 200, sin compresión tras 502, `last_tier`); `tests/test_sessions_api.py` con el test de ocho turnos actualizado y el nuevo de nueve turnos con ancla; `tier` explícito, 422 con `v4` e inválido, `GET` y OpenAPI; observabilidad con marcadores secretos en resumen y restricciones; Streamlit (paridad, `tier` enviado, selector con `v4`, panel); estructura del proyecto (D15, spec `ci-pipeline`)
- [x] 10.5 Verificar: `uv run pytest -q` en verde sin red ni keys reales

## 11. Documentación, medición y cierre (orquestador)

- [x] 11.1 README (memoria: anclas, resumen acumulativo, backlog y política de olvido; tier, sus reglas y el override; configuración nueva; desviaciones respecto al directo) y `CLAUDE.md` (S05 live en la tabla de sesiones; `v5`; `app/sessions/compression/`, `app/sessions/tier.py`, `app/services/memory.py` y `app/prompts/{conversation_summary,anchor_classifier}/` → `ai-engineer`) (Migration 12)
- [x] 11.2 Medición D16 con llamadas reales **previa autorización del usuario** (bloques A-D, en proceso, sin Redis, con moderación real y `MAX_CONVERSATION_TURNS=3`; ≈ 0,05 USD, tope 0,10 USD y parada si el gasto supera 0,08 USD o falla un umbral); registrar resultados e `input_tokens` por turno en `design.md`
- [x] 11.3 Captura de pantalla de una conversación en Streamlit con el panel "Memoria del proyecto" mostrando anclas, resumen y la audiencia del último turno, enlazada en el README
- [x] 11.4 `openspec validate --all --strict`, `ruff check`, `ruff format --check` y `pytest` en verde
- [x] 11.5 Archivar el change y escribir el Purpose de `memory-compression` y `audience-tier`
