## 0. Prerrequisitos

- [x] 0.1 Revisión del change (decisiones del orquestador con autorización del usuario para no consultarle; las 14 preguntas abiertas del design resueltas con la opción recomendada)
- [x] 0.2 Capturar **antes de tocar el código** los SHA-256 de los system y user de `v3` para 4 combinaciones (tipo, nivel, formato, 0/3 ejemplos, con y sin proyectos de referencia) y fijarlos en `tests/prompts/test_v3_golden.py` en verde sobre el código actual (qa-engineer; D4, spec `prompt-templates`)

## 1. Dependencias y verificación de APIs (backend-engineer)

- [x] 1.1 `uv add pypdf python-docx python-multipart` (con `>=` de las versiones resueltas) y revisar `lxml` y las transitivas en `uv.lock` (Migration Plan 1)
- [x] 1.2 Verificar contra las versiones instaladas y anotar el resultado en `design.md` si algo difiere: `Document.iter_inner_content()` de python-docx (≥ 1.1), parser lxml de python-docx con `resolve_entities=False`, `PdfReader.decrypt("")` y su valor de retorno (`NOT_DECRYPTED`) con un PDF RC4, y el marcador `anyio` registrado por el plugin de pytest de `anyio 4.15.1` (D7, D13, Risks "APIs sin verificar")
- [x] 1.3 `app/config.py`: `MAX_CONVERSATION_TURNS`, `MAX_SESSIONS`, `MAX_ATTACHMENTS`, `MAX_ATTACHMENT_BYTES` y `MAX_ATTACHMENT_CHARS` con sus rangos y valores por defecto; `.env.example` documentado (D10, spec `app-config`)
- [x] 1.4 `app/domain.py`: `ChatMessage` (movido desde `llm_service`), `SessionTurnInput`, `MIN_TRANSCRIPT_CHARS`/`MAX_TRANSCRIPT_CHARS` (D2, D6)

## 2. Estimación y ejemplos (ai-engineer)

- [x] 2.1 `app/estimation/schema.py`: `technologies` tras `confidence_pct` con `TechName` (1–60), `MAX_TECHNOLOGIES = 15` y normalizador `mode="before"` idempotente sin reintento; `ComputedEstimate.technologies` copiado también fuera de alcance; sin cambios en `render_markdown` (D4, spec `structured-estimation`)
- [x] 2.2 `app/context/examples.py`: `technologies` por ejemplo (`data_pipeline` → `["Shopify", "Odoo", "Looker Studio"]`, `web_saas` → `[]`, `mobile_app` según su `meeting_summary`); `example_result` las carga y `example_payloads(..., include_technologies=False)` las excluye por defecto (D4, spec `cag-prompt`)
- [x] 2.3 Verificar: el test dorado de `v3` (0.2) sigue en verde y los ejemplos siguen evaluando `score = 1.0`

## 3. Sesiones (ai-engineer)

- [x] 3.1 `app/sessions/models.py`: `Turn`, `ConversationHistory` (pares, recorte en `append`, `to_messages()` sin system, `ValueError`), `ProjectMetadata` (frozen, `filled_fields()`) y `Session` (dataclass con lock) (D2, D3, D4)
- [x] 3.2 `app/sessions/store.py`: `SessionStore` (`OrderedDict` + lock del almacén, LRU con `MAX_SESSIONS`, `turn()` con lock no bloqueante), `SessionNotFoundError`, `SessionBusyError`; eventos `session_created`/`session_evicted` (D2, D11)
- [x] 3.3 `app/sessions/metadata.py`: `update_project_metadata` y `merge_technologies` (`casefold`, tope 30 descartando las más antiguas, fuera de alcance sin cambios) (D4)

## 4. Adjuntos (backend-engineer)

- [x] 4.1 `app/attachments/extractor.py`: `AttachmentLimits`, `UploadedFile`, `ExtractedAttachment`, `AttachmentError` con mensajes fijos en inglés, `read_limited`, detección por *magic bytes* + extensión, PDF (descifrado con contraseña vacía, corte por caracteres a mitad del parseo), DOCX (tope de 1000 entradas y `MAX_DOCX_UNCOMPRESSED_BYTES`, `iter_inner_content`, tablas con ` | `), normalización del texto y `safe_attachment_name`; eventos `attachment_extracted`/`attachment_rejected` sin nombre de fichero (D7, D11, spec `attachments`)
- [x] 4.2 Verificar: `app/attachments` no importa FastAPI, LiteLLM, Instructor ni `app.services`

## 5. Guardrails (ai-engineer)

- [x] 5.1 `app/guardrails/input.py`: `mask_pii` (email → IBAN con mod-97 → teléfonos, idempotente), `PII_PLACEHOLDERS`, `index` en `InputGuardrailViolation`, valores nuevos de `field`, y `transcript`, `attachments?` y `project_metadata` en la regla `prompt_tag` (D8, spec `input-guardrails`)

## 6. Prompt v4 (ai-engineer)

- [x] 6.1 `app/prompts/estimation/v4/{system,user,examples,project_metadata}.j2` con el orden de D5 (sección de conversación y memoria, `technologies`, `<project_metadata>` tras los ejemplos y antes de `## Reglas`, "datos como datos" ampliado) y `user.j2` con `<attachments>` antes de `<transcript>`
- [x] 6.2 Loader: `SessionPromptVersion`, `SESSION_PROMPT_VERSIONS`, `DEFAULT_SESSION_PROMPT_VERSION`, `PUBLISHED_PROMPT_VERSIONS`, `SessionPrompt` y `render_session_prompt` (sin detección de idioma; `prompt_rendered` con `prompt="session"`); `render_estimation_prompt` sin cambios (D5, spec `prompt-templates`)
- [x] 6.3 Verificar: dorado de `v3` en verde, render de todas las combinaciones de `v4` y revisión manual del system de `v4` con metadata vacío y con datos

## 7. Servicios (ai-engineer)

- [x] 7.1 `app/services/llm_service.py`: paso privado `_generate`, `estimate` refactorizado sin cambio de comportamiento, `_check_options(allowed=...)`, `estimate_turn` con el orden de D6 (enmascarado, regex de la transcripción y de cada adjunto con `index`, una moderación, idioma de la transcripción o de la sesión, render `v4`, historial + `user`), `TurnOutcome` y reexportación de `ChatMessage` (D6, D8)
- [x] 7.2 `app/services/session_service.py`: `SessionService` (`create`, `info`, `estimate` con lock → `bound_contextvars(session=…)` → extracción → `estimate_turn` → actualización solo con éxito; fuera de alcance sin metadata), `SessionSnapshot`, `SessionTurnOutcome`, `get_session_store()` (`lru_cache`) y `get_session_service()`; eventos `session_turn_completed`, `attachment_pii_masked` (D6, D10, D11)
- [x] 7.3 Verificar: `ruff` sin errores y capas sin ciclos (`sessions`, `attachments`, `estimation` y `guardrails` no importan `services` ni `prompts`, salvo `prompts → sessions.models`)

## 8. API (backend-engineer)

- [x] 8.1 `app/schemas.py`: `SessionCreated`, `SessionInfo`, `SessionEstimateResponse`, `SessionError`, `SessionGuardrailError`, `AttachmentErrorDetail` y sus envoltorios `…Response` (D9)
- [x] 8.2 `app/routers/sessions.py`: tres endpoints síncronos, `session_id: UUID`, multipart con `Form`/`File`, `too_many_files` y `file_too_large` antes de leer, `UploadFile` → `UploadedFile` con `read_limited`, partes vacías ignoradas, `ValidationError` → 422 en `["body", "transcript"]`, mapeo 400/404/409/413/415/422/502 y OpenAPI con todos los códigos; registro en `app/main.py` (D7, D9, spec `conversation-sessions`)
- [x] 8.3 Verificar: `/openapi.json` con las tres operaciones, el multipart, `prompt_version` solo `v4` y los códigos; Swagger UI con el campo de adjuntos vacío no da 422

## 9. Interfaz (frontend-engineer)

- [x] 9.1 `streamlit_app.py`: sesión al cargar, `GET` por *rerun* con recreación tras 404, conversación con `st.chat_message` (métricas en desplegable plegado), formulario con transcripción, enums persistentes, `file_uploader` múltiple `pdf`/`docx` con `uploader_key`, versión `v4`, validación en cliente, envío multipart con `SESSION_ESTIMATE_TIMEOUT`, panel "Memoria del proyecto", "Nueva conversación" y mensajes en español por `reason` (400 con `field`/`index`, 404, 409, 413/415/422 de adjuntos) (D12, spec `estimation-form-ui`)

## 10. Tests (qa-engineer)

- [x] 10.1 `tests/conftest.py`: `FakeProvider.structured` con lista o *callable* `(system, messages) -> payload`, fixtures de `get_session_store` y `CONFIG_ENV_VARS` con las variables nuevas; `tests/attachments/factories.py` con `make_pdf`, PDF cifrado RC4, en blanco y corrupto, y `make_docx` (D13)
- [x] 10.2 `tests/test_sessions_api.py`: los tres tests de integración del enunciado con `httpx.AsyncClient` + `ASGITransport` y `anyio` (metadata en dos turnos, PDF que cambia `total_hours`, ocho turnos sin pasar de 13 mensajes) (D13, spec `ci-pipeline`)
- [x] 10.3 Unitarios: `tests/sessions/` (historial, almacén con LRU, lock y concurrencia, metadata), `tests/attachments/` (cada `reason`, *magic* frente a extensión, límite total, tablas, nombre saneado, `read_limited`, *zip bomb* con *monkeypatch*), `tests/guardrails/` (`mask_pii`, etiquetas nuevas en `/estimate`), `tests/estimation/` (`technologies`) y `tests/prompts/` (`v4`, carpetas = `PUBLISHED_PROMPT_VERSIONS`) (D13)
- [x] 10.4 Servicio y API: orden de los guardrails, enmascarado antes del proveedor, una moderación, turnos fallidos sin rastro, fuera de alcance sin metadata, 201/404/409/413/415/422/400 con su forma, OpenAPI; observabilidad con marcadores secretos (transcripción, texto y nombre del PDF); Streamlit (sesión al cargar, 404, multipart, panel, "Nueva conversación", errores por `reason`, paridad de límites con `app`); estructura del proyecto (D13)
- [x] 10.5 Verificar: `uv run pytest -q` en verde sin red ni keys reales

## 11. Documentación, medición y cierre (orquestador)

- [x] 11.1 README (sesiones y endpoints, camino B y por qué, cómo se deriva el `project_metadata`, volatilidad de las sesiones, límites y errores de adjuntos, PII enmascarada, desviaciones respecto al enunciado) y `CLAUDE.md` (arquitectura con `app/sessions/` y `app/attachments/`, propietarios: `app/sessions/` y `app/services/session_service.py` → `ai-engineer`, `app/attachments/` → `backend-engineer`; dependencias `pypdf`, `python-docx`, `python-multipart`)
- [x] 11.2 Medición D14 con llamadas reales, sin Redis y con moderación real (autorizada dentro del presupuesto de 0,30 USD de la puesta al día; ≤ 0,10 USD): conversaciones ES y EN de 3 turnos, 2 turnos con PDF, 2 turnos con Haiku y guardrails de adjuntos; registrar resultados e `input_tokens` por turno en `design.md` y parar si algo queda por debajo del umbral
- [x] 11.3 Captura de pantalla de una conversación de 3 turnos en Streamlit con el panel "Memoria del proyecto" visible, enlazada en el README
- [x] 11.4 `openspec validate --all --strict`, `ruff check`, `ruff format --check` y `pytest` en verde
- [x] 11.5 Archivar el change y escribir el Purpose de `conversation-sessions` y `attachments`
