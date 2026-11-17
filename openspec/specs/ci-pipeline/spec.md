# ci-pipeline Specification

## Purpose
Validar automáticamente, en cada push y pull request, la estructura del proyecto, la calidad del código, el comportamiento del servicio (sin llamadas reales a LLMs) y la coherencia de las specs.
## Requirements
### Requirement: Pipeline de integración continua
El repositorio SHALL incluir un workflow de GitHub Actions que se ejecute en cada push y pull request con dos jobs en paralelo:
- `checks`, en este orden: instalación con `uv sync --locked`, `ruff check`, `ruff format --check`, `pytest` y `openspec validate --all --strict`;
- `docker`: `docker build .` (sin publicar la imagen) y `docker compose config --quiet` (con un `.env` vacío creado en el propio job).

Si cualquier paso falla, el workflow MUST fallar.

#### Scenario: Cambio correcto
- **WHEN** se hace push de código que cumple lint, formato, tests y specs, y cuya imagen se construye
- **THEN** el workflow termina en verde

#### Scenario: Test roto
- **WHEN** un test falla
- **THEN** el workflow termina en rojo e indica el paso de pytest

#### Scenario: Imagen rota
- **WHEN** el `Dockerfile` no se puede construir (p. ej. `uv.lock` desincronizado o un fichero necesario fuera del contexto de build)
- **THEN** el job `docker` termina en rojo

### Requirement: Tests sin coste ni red
La suite de tests MUST ejecutarse sin API keys reales, sin servidor Redis, sin servidor HTTP real y sin ninguna llamada de red (ni a proveedores LLM ni a recursos que LiteLLM pueda descargar, como su mapa de precios), sustituyendo el proveedor por un doble de prueba mediante la inyección de dependencias de FastAPI en la API, la llamada de LiteLLM por un doble en los tests del proveedor, Redis por `fakeredis`, y el cliente HTTP de la app Streamlit por un `httpx.Client` con transporte simulado (`httpx.MockTransport` o el transporte ASGI de la propia app).

#### Scenario: Ejecución en CI
- **GIVEN** un entorno sin API keys reales ni Redis
- **WHEN** se ejecuta `uv run pytest`
- **THEN** todos los tests pasan y no se realiza ninguna llamada de red

#### Scenario: Mapa de precios de LiteLLM local
- **WHEN** se importa la aplicación
- **THEN** LiteLLM carga su mapa de modelos desde la copia local incluida en el paquete, sin descargarlo

#### Scenario: Streamlit sin API real
- **WHEN** se ejecutan los tests de la app Streamlit
- **THEN** todas sus peticiones HTTP las atiende el transporte simulado

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

### Requirement: Tests de comportamiento del servicio
La suite SHALL cubrir, como mínimo: `GET /health` (200, también con Redis caído), `POST /api/v1/estimate` correcto con el proveedor simulado (incluidos `result` y el texto renderizado), el 422 por `description` fuera de límites, enums inválidos o `prompt_version` retirada o desconocida, el 400 de cada guardrail sin llamar al LLM, el 502 por fallo del proveedor o salida estructurada inválida o cortada, los templates de prompt (los tres tests del enunciado de S04 sobre `v3` en `tests/prompts/test_estimation_v3.py`: descripción dentro de `<project_description>`, bloque del formato tabla por fases (el que pide mostrar la `confidence_pct` de cada fase) presente con `phases_table` y ausente con `narrative`, porque `confidence_pct` es un campo del schema y aparece en todos los formatos, e instrucción de supuestos por fase presente con `detailed` y ausente con `summary`; además el loader, los ejemplos como JSON válido y `reference_projects`), el schema estructurado (normalización, coherencia, requisitos por formato y nivel), el cálculo de importes y totales, el filtro de fuera de alcance, el render por formato, nivel e idioma con los ejemplos evaluados con `score = 1.0`, los guardrails de entrada (positivos en español y en inglés, corpus de negativos con las muestras, IBAN con checksum, moderación simulada y fail-open), la generación estructurada con un Router simulado (reintento por validación con tokens sumados, reintentos agotados, salida cortada, error de transporte sin reintento de Instructor, llaves en la descripción sin modificar), la evaluación por formato, el proveedor LiteLLM (generación, streaming, truncado, fallback, errores) con LiteLLM simulado, la caché (acierto, fallo, qué no se guarda, streaming, estructurada con revalidación, Redis caído) con `fakeredis`, los eventos de log y la ausencia de datos sensibles en ellos, y la app Streamlit ejecutada sin navegador (`streamlit.testing.v1.AppTest`) con la API simulada por httpx: envío del formulario con el JSON tipado, validación en cliente, resultado con métricas y validación, aviso de fuera de alcance, 400 por motivo, estado HTTP de error, API no disponible y que la app no importa `app`. Los tests de templates, schema, render y guardrails MUST ejecutarse sin LLM ni red.

#### Scenario: Cobertura mínima
- **WHEN** se ejecuta `uv run pytest`
- **THEN** existen y pasan tests para cada uno de los casos listados

#### Scenario: Tests de template rápidos
- **WHEN** se ejecuta `uv run pytest tests/prompts tests/estimation tests/guardrails`
- **THEN** los tests pasan sin proveedor LLM, sin Redis y sin red

### Requirement: Tests de integración de sesiones
La suite SHALL incluir en `tests/test_sessions_api.py` los tests de integración de sesiones, asíncronos con `httpx.AsyncClient` sobre `httpx.ASGITransport(app=app)` y el plugin de pytest de `anyio` (backend `asyncio`, sin `pytest-asyncio`), con `get_settings`, el proveedor, el moderador, el almacén de sesiones y `get_memory_compression` sustituidos por dobles mediante la inyección de dependencias (un almacén nuevo por test, porque `ASGITransport` no ejecuta el *lifespan*; un compresor determinista con el detector heurístico y un resumen `"S:"` seguido de los números de turno, para que el proveedor simulado solo reciba las llamadas de estimación) y un proveedor simulado que admite una lista de respuestas o una función `(system, messages) -> respuesta`:
1. **Metadata en dos turnos**: tras el turno 1 (título "Clinic booking platform", equipo de 3 personas, `["Twilio"]`) y el turno 2 (equipo de 4, `["Stripe", "twilio"]`), el `project_metadata` de cada respuesta y del `GET` refleja los cambios (`assumed_team_size` 3 → 4, `mentioned_technologies = ["Twilio", "Stripe"]`, `turn_count = 2`), la segunda llamada al proveedor recibe 3 mensajes (`user`, `assistant` con el Markdown del turno 1, `user`) y su system contiene `project_name: Clinic booking platform` dentro de `<project_metadata>`, mientras que el system de la primera tiene el bloque vacío.
2. **PDF que cambia la estimación**: un PDF generado en el test con un marcador que solo está en él hace que el proveedor simulado añada una fila más, de modo que `result.total_hours` con el PDF es mayor que sin él; el texto del PDF aparece entre `--- attachment: spec.pdf ---` y `</attachments>` en el mensaje enviado, y en el turno siguiente el mensaje `user` guardado lleva `[…]` y no el marcador.
3. **Ocho turnos y ventana**: con `MAX_CONVERSATION_TURNS = 6` y turnos sin anclas, la llamada `k` al proveedor recibe `2 × min(k − 1, 6) + 1` mensajes, nunca más de 13; tras el turno 8, el marcador del turno 1 no aparece en ningún mensaje enviado, el system de la llamada 8 contiene `<conversation_summary>`, y el `GET` devuelve `turn_count = 8`, `history_turns = 6`, `anchors_count = 0` y `summary_chars` mayor que 0.
4. **Nueve turnos con un ancla**: con el turno 2 "Hemos firmado un NDA y el presupuesto está cerrado en 40 000 €" y un proveedor simulado que devuelve desde ese turno `explicit_constraints` con el NDA, la llamada 9 recibe `2 × (1 + 6) + 1` mensajes cuyo primer par es el turno 2 literal, su system contiene `<conversation_summary>` y `<audience>`, el `GET` devuelve `anchors_count = 1`, y los turnos 2 a 9 responden `tier = "executive"` y `tier_rule = "nda_detected"`.

#### Scenario: Integración de sesiones
- **WHEN** se ejecuta `uv run pytest tests/test_sessions_api.py`
- **THEN** los cuatro tests pasan sin red, sin keys reales y sin Redis

### Requirement: Cobertura de sesiones y adjuntos
La suite SHALL cubrir, sin LLM ni red: `ConversationHistory` (ventana, pares alternos, sin system, `Turn.number` tras el recorte, `max_turns` inválido); `SessionStore` (creación, 404, LRU, `SessionBusyError` y lock liberado tras un error, creación concurrente sin superar el tope); `update_project_metadata` (reglas de cada campo, fuera de alcance, deduplicación por `casefold`, tope de 30); el normalizador y la posición de `technologies`; el test dorado de `v3` y el render de `v4` (bloque vacío en el primer turno, solo campos con valor, orden de secciones, prefijo estable, adjuntos antes de `<transcript>`, `history_user` con `[…]`, idioma heredado de la sesión, carpetas iguales a `PUBLISHED_PROMPT_VERSIONS`); el extractor (cada `reason`, *magic bytes* frente a extensión, límite total de caracteres entre ficheros, tablas DOCX, nombre saneado, lectura acotada); `mask_pii` (cada tipo, IBAN inválido, idempotencia, corpus de negativos); el servicio de sesión (orden de los guardrails, enmascarado antes del proveedor, una sola moderación, turnos fallidos sin rastro, fuera de alcance sin metadata); la API de sesiones (201, 404, 409, 413, 415, 422 con su forma y sin nombre de fichero, 400 con `field` e `index`, OpenAPI); los eventos de log sin contenido; y la interfaz Streamlit conversacional (sesión al cargar, recreación tras un 404, envío multipart con adjuntos, historial de turnos, panel de memoria, "Nueva conversación" y errores por `reason`). Los PDF y DOCX de prueba MUST generarse en los propios tests (PDF mínimo escrito a mano y validado con pypdf; DOCX con python-docx), sin ficheros binarios descargados.

#### Scenario: Cobertura de sesiones
- **WHEN** se ejecuta `uv run pytest`
- **THEN** existen y pasan tests para cada uno de los casos listados

#### Scenario: Fixture de PDF válido
- **WHEN** se extrae con pypdf un PDF generado por el fixture con dos líneas
- **THEN** el texto extraído contiene las dos líneas

### Requirement: Cobertura de la compresión de la memoria y del tier
La suite SHALL cubrir, sin LLM ni red: el detector de anclas (un positivo en español y otro en inglés por regla, varias reglas a la vez y su orden, mayúsculas de las siglas, el corpus de negativos y las muestras de `samples/`); el modo LLM del detector con un proveedor simulado (positivo heurístico sin llamada, positivo del LLM, fallo con vuelta al heurístico); `ConversationHistory` (`take_evicted`, anclas antes de la ventana, alternancia, `max_anchors` inválido, y los tests de S05 sin cambios); la política de compresión con `detect` y `summarize` locales (nada expulsado, ancla, no ancla con el resumen previo, tope y degradación, `max_anchors = 0`, fallo con backlog, reintento, backlog lleno) y la invariante de conservación con un test de propiedad sobre secuencias aleatorias con semilla fija, sin dependencias nuevas; el resumidor (prompt con y sin resumen previo, idioma, solo los mensajes del cliente, `[…]`, normalizador, `COMPRESSION_MODEL` como override, error propagado); el tier (cada regla en español y en inglés, precedencia, override, también `default`, NDA solo en `explicit_constraints`, umbral de términos técnicos, tamaños de equipo, negativos, las muestras de `samples/` y `prompt_version` con `v4`); las reglas nuevas del metadata (acumulación y tope de restricciones, olvido por `casefold` sin subcadenas, readopción, descarte y adopción en el mismo turno, fuera de alcance); el schema (normalizadores de los dos campos nuevos y orden de las propiedades); los prompts (dorado de `v4` capturado antes de tocar el código, dorado de `v3`, `v5` con y sin resumen y tier, orden de los bloques, prefijo estable, texto por tier, líneas nuevas del metadata solo con valor, ejemplos con los campos nuevos solo en `v5`, carpetas de los prompts de memoria); la regla `prompt_tag` con las etiquetas nuevas; el servicio de sesiones con un compresor falso (compresión tras el turno y dentro del lock, fallos de `summarize` y `detect` con 200, sin compresión tras un turno fallido, `last_tier` solo con éxito, tier en el render); la API (`tier` explícito, 422 con `v4` y con un valor inválido, campos nuevos del `GET`, OpenAPI); los eventos de log con marcadores secretos; y la interfaz Streamlit (paridad de `TIERS` y `TIER_RULE_LABELS` con `Tier` y `TierRule`, `tier` enviado solo fuera de "Automático", selector deshabilitado con `v4`, panel con restricciones, descartes, anclas, resumen y audiencia).

#### Scenario: Cobertura de memoria y tier
- **WHEN** se ejecuta `uv run pytest`
- **THEN** existen y pasan tests para cada uno de los casos listados, sin red ni keys reales

#### Scenario: Suites puras rápidas
- **WHEN** se ejecuta `uv run pytest tests/sessions tests/prompts tests/estimation tests/guardrails`
- **THEN** los tests pasan sin proveedor LLM, sin Redis y sin red

