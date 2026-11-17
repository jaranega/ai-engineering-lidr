## MODIFIED Requirements

### Requirement: Validación de la estructura del proyecto
La suite SHALL incluir un test que verifique la existencia de la estructura del enunciado: `app/__init__.py`, `app/main.py`, `app/config.py`, `app/routers/{__init__,estimations,sessions}.py`, `app/services/{__init__,llm_service,session_service,memory}.py`, `app/context/{__init__,examples}.py`, `app/sessions/{__init__,models,store,metadata,tier}.py`, `app/sessions/compression/{__init__,anchors,policy}.py`, `app/attachments/{__init__,extractor}.py`, `app/prompts/loader.py`, `app/prompts/estimation/v3/{system,user,examples}.j2`, `app/prompts/estimation/v4/{system,user,examples,project_metadata}.j2`, `app/prompts/estimation/v5/{system,user,examples,project_metadata,conversation_summary,audience}.j2`, `app/prompts/conversation_summary/v1/{system,user}.j2`, `app/prompts/anchor_classifier/v1/{system,user}.j2`, `streamlit_app.py`, `.env.example`, `.gitignore`, `pyproject.toml`, `README.md`, `Dockerfile`, `.dockerignore` y `docker-compose.yml`. El test MUST comprobar también que `app/sessions` (incluidos `compression/` y `tier.py`) y `app/attachments` no importan `app.services`, `app.prompts`, LiteLLM ni Instructor.

#### Scenario: Falta un fichero obligatorio
- **WHEN** se elimina `app/context/examples.py`
- **THEN** el test de estructura falla indicando el fichero ausente

#### Scenario: Falta la app Streamlit
- **WHEN** se elimina `streamlit_app.py`
- **THEN** el test de estructura falla indicando el fichero ausente

#### Scenario: Falta un fichero de Docker
- **WHEN** se elimina `docker-compose.yml`
- **THEN** el test de estructura falla indicando el fichero ausente

#### Scenario: Falta un template
- **WHEN** se elimina `app/prompts/estimation/v5/audience.j2` o `app/prompts/conversation_summary/v1/system.j2`
- **THEN** el test de estructura falla indicando el fichero ausente

#### Scenario: Capas sin dependencias del LLM
- **WHEN** un módulo de `app/sessions` (incluido `app/sessions/compression`) o `app/attachments` importa `app.services`, `app.prompts` o LiteLLM
- **THEN** el test de estructura falla

### Requirement: Tests de integración de sesiones
La suite SHALL incluir en `tests/test_sessions_api.py` los tests de integración de sesiones, asíncronos con `httpx.AsyncClient` sobre `httpx.ASGITransport(app=app)` y el plugin de pytest de `anyio` (backend `asyncio`, sin `pytest-asyncio`), con `get_settings`, el proveedor, el moderador, el almacén de sesiones y `get_memory_compression` sustituidos por dobles mediante la inyección de dependencias (un almacén nuevo por test, porque `ASGITransport` no ejecuta el *lifespan*; un compresor determinista con el detector heurístico y un resumen `"S:"` seguido de los números de turno, para que el proveedor simulado solo reciba las llamadas de estimación) y un proveedor simulado que admite una lista de respuestas o una función `(system, messages) -> respuesta`:
1. **Metadata en dos turnos**: tras el turno 1 (título "Clinic booking platform", equipo de 3 personas, `["Twilio"]`) y el turno 2 (equipo de 4, `["Stripe", "twilio"]`), el `project_metadata` de cada respuesta y del `GET` refleja los cambios (`assumed_team_size` 3 → 4, `mentioned_technologies = ["Twilio", "Stripe"]`, `turn_count = 2`), la segunda llamada al proveedor recibe 3 mensajes (`user`, `assistant` con el Markdown del turno 1, `user`) y su system contiene `project_name: Clinic booking platform` dentro de `<project_metadata>`, mientras que el system de la primera tiene el bloque vacío.
2. **PDF que cambia la estimación**: un PDF generado en el test con un marcador que solo está en él hace que el proveedor simulado añada una fila más, de modo que `result.total_hours` con el PDF es mayor que sin él; el texto del PDF aparece entre `--- attachment: spec.pdf ---` y `</attachments>` en el mensaje enviado, y en el turno siguiente el mensaje `user` guardado lleva `[…]` y no el marcador.
3. **Ocho turnos y ventana**: con `MAX_CONVERSATION_TURNS = 6` y turnos sin anclas, la llamada `k` al proveedor recibe `2 × min(k − 1, 6) + 1` mensajes, nunca más de 13; tras el turno 8, el marcador del turno 1 no aparece en ningún mensaje enviado, el system de la llamada 8 contiene `<conversation_summary>`, y el `GET` devuelve `turn_count = 8`, `history_turns = 6`, `anchors_count = 0` y `summary_chars` mayor que 0.
4. **Nueve turnos con un ancla**: con el turno 2 "Hemos firmado un NDA y el presupuesto está cerrado en 40 000 €" y un proveedor simulado que devuelve desde ese turno `explicit_constraints` con el NDA, la llamada 9 recibe `2 × (1 + 6) + 1` mensajes cuyo primer par es el turno 2 literal, su system contiene `<conversation_summary>` y `<audience>`, el `GET` devuelve `anchors_count = 1`, y los turnos 2 a 9 responden `tier = "executive"` y `tier_rule = "nda_detected"`.

#### Scenario: Integración de sesiones
- **WHEN** se ejecuta `uv run pytest tests/test_sessions_api.py`
- **THEN** los cuatro tests pasan sin red, sin keys reales y sin Redis

## ADDED Requirements

### Requirement: Cobertura de la compresión de la memoria y del tier
La suite SHALL cubrir, sin LLM ni red: el detector de anclas (un positivo en español y otro en inglés por regla, varias reglas a la vez y su orden, mayúsculas de las siglas, el corpus de negativos y las muestras de `samples/`); el modo LLM del detector con un proveedor simulado (positivo heurístico sin llamada, positivo del LLM, fallo con vuelta al heurístico); `ConversationHistory` (`take_evicted`, anclas antes de la ventana, alternancia, `max_anchors` inválido, y los tests de S05 sin cambios); la política de compresión con `detect` y `summarize` locales (nada expulsado, ancla, no ancla con el resumen previo, tope y degradación, `max_anchors = 0`, fallo con backlog, reintento, backlog lleno) y la invariante de conservación con un test de propiedad sobre secuencias aleatorias con semilla fija, sin dependencias nuevas; el resumidor (prompt con y sin resumen previo, idioma, solo los mensajes del cliente, `[…]`, normalizador, `COMPRESSION_MODEL` como override, error propagado); el tier (cada regla en español y en inglés, precedencia, override, también `default`, NDA solo en `explicit_constraints`, umbral de términos técnicos, tamaños de equipo, negativos, las muestras de `samples/` y `prompt_version` con `v4`); las reglas nuevas del metadata (acumulación y tope de restricciones, olvido por `casefold` sin subcadenas, readopción, descarte y adopción en el mismo turno, fuera de alcance); el schema (normalizadores de los dos campos nuevos y orden de las propiedades); los prompts (dorado de `v4` capturado antes de tocar el código, dorado de `v3`, `v5` con y sin resumen y tier, orden de los bloques, prefijo estable, texto por tier, líneas nuevas del metadata solo con valor, ejemplos con los campos nuevos solo en `v5`, carpetas de los prompts de memoria); la regla `prompt_tag` con las etiquetas nuevas; el servicio de sesiones con un compresor falso (compresión tras el turno y dentro del lock, fallos de `summarize` y `detect` con 200, sin compresión tras un turno fallido, `last_tier` solo con éxito, tier en el render); la API (`tier` explícito, 422 con `v4` y con un valor inválido, campos nuevos del `GET`, OpenAPI); los eventos de log con marcadores secretos; y la interfaz Streamlit (paridad de `TIERS` y `TIER_RULE_LABELS` con `Tier` y `TierRule`, `tier` enviado solo fuera de "Automático", selector deshabilitado con `v4`, panel con restricciones, descartes, anclas, resumen y audiencia).

#### Scenario: Cobertura de memoria y tier
- **WHEN** se ejecuta `uv run pytest`
- **THEN** existen y pasan tests para cada uno de los casos listados, sin red ni keys reales

#### Scenario: Suites puras rápidas
- **WHEN** se ejecuta `uv run pytest tests/sessions tests/prompts tests/estimation tests/guardrails`
- **THEN** los tests pasan sin proveedor LLM, sin Redis y sin red
