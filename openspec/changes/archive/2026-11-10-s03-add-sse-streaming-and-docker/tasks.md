## 0. Prerrequisitos

- [x] 0.1 Revisión del change por el usuario (proposal, specs, design y tasks)
- [x] 0.2 `s03-add-llm-wrapper-cache-logging` implementado y archivado (las deltas de `chat-ui`, `ci-pipeline` y `observability` de este change parten de su estado)

## 1. Dependencias (backend-engineer)

- [x] 1.1 `streamlit` y `httpx` a un dependency group `ui` incluido en `[tool.uv] default-groups` (con `dev`); `uv lock`; comprobar que `uv sync --locked --no-default-groups` no instala `streamlit` ni `httpx` como dependencia directa (D8)
- [x] 1.2 Verificar: `uv sync --locked` correcto y `ruff` sin errores

## 2. Servicios (ai-engineer)

- [x] 2.1 `prompt_context(message_count) -> PromptContext(system_prompt, examples)` en `llm_service.py`; `system_prompt_for(messages)` delega en la misma regla de turno (D5)
- [x] 2.2 Verificar: `ruff` sin errores y los tests de prompt existentes en verde

## 3. API (backend-engineer)

- [x] 3.1 `schemas.py`: `MessageText` compartido con `EstimationRequest.transcription` (sin cambio de contrato), `ConversationMessage`, `StreamEstimationRequest` con el validador de estructura, `EstimationMetrics` y `EstimationResponse(EstimationMetrics)`, `ContextResponse` (D2, D3, D5)
- [x] 3.2 Función de mapeo `EstimationResult → EstimationMetrics` compartida por `POST /estimate` y el SSE (D2)
- [x] 3.3 `POST /api/v1/estimate/stream` con `response_class=EventSourceResponse` sobre un generador síncrono: eventos `token` (string JSON), `metrics` y `error` genérico, `estimate_stream_cancelled` en `GeneratorExit`, `estimate_stream_failed` para excepciones inesperadas (D1, D2, D4)
- [x] 3.4 `GET /api/v1/context?message_count=N` con `prompt_context` y `max_message_chars` (D5)
- [x] 3.5 Verificar: `ruff` sin errores, `/openapi.json` con las dos rutas y una prueba con `curl -N` contra uvicorn local con un proveedor simulado o sin llamar al LLM (solo 422 y `/api/v1/context`)

## 4. Interfaz (frontend-engineer)

- [x] 4.1 `ESTIMATOR_API_BASE_URL` desde el entorno o, solo esa clave, desde `.env` (`dotenv_values`); eliminar todos los imports de `app` (D6)
- [x] 4.2 Cliente con `httpx.Client` en bloques `with` (nunca funciones de módulo), timeouts de D6 y parser SSE propio (`event:`/`data:`, comentarios ignorados, `json.loads`, eventos desconocidos ignorados, fin sin terminal → fallo) (D6)
- [x] 4.3 `handle_user_message` sobre el stream HTTP dentro de `contextlib.closing`: `metrics` → `CallMetrics` con los tiempos del cliente; `error`, estado no 200, corte o respuesta vacía → `PROVIDER_ERROR_MESSAGE`; error de conexión → `API_UNAVAILABLE_MESSAGE`; se mantienen truncado, `st.rerun()` y la regla de no guardar nada en los fallos (D6)
- [x] 4.4 Barra lateral desde `GET /health` (modelos, respaldo, estado de la caché) y `GET /api/v1/context?message_count=len+1` (prompt, ejemplos, límite); con la API caída, aviso y chat visible sin `st.stop()`; validación con `max_message_chars` si se obtuvo (D6)
- [x] 4.5 Logs `api_unreachable`, `api_stream_failed` y `empty_llm_reply` con structlog, sin contenido (D6)
- [x] 4.6 Verificar: `ruff` sin errores y la app arranca con la API parada (aviso) y con la API local levantada (sin enviar mensajes con coste)

## 5. Docker y CI (backend-engineer)

- [x] 5.1 `Dockerfile` multi-stage con `python:3.12-slim`, imagen de `uv` con versión exacta, `uv sync --locked --no-default-groups --no-install-project`, usuario no root con UID 10001, código en solo lectura, `HEALTHCHECK` con `urllib` y `CMD` de uvicorn (D9)
- [x] 5.2 `.dockerignore` en lista blanca (`pyproject.toml`, `uv.lock`, `app/`, sin `__pycache__`) (D9)
- [x] 5.3 `docker-compose.yml`: `api` con `env_file: .env`, `REDIS_URL=redis://redis:6379/0`, `depends_on` sano y puerto en `127.0.0.1`; `redis:7.4-alpine` sin persistencia, healthcheck y puerto en `127.0.0.1` (D10)
- [x] 5.4 Job `docker` en paralelo en `.github/workflows/`: `.env` vacío, `docker build .` y `docker compose config --quiet` (D11)
- [x] 5.5 Verificar: `docker build .`, `docker compose config --quiet`, `docker compose up` con `/health` en 200 y `cache: "ok"`, y que la imagen no contiene `streamlit` ni `.env` (sin llamadas al LLM)

## 6. Tests (qa-engineer)

- [x] 6.1 SSE con `TestClient` y `FakeProvider`: orden de eventos, fragmento multilínea íntegro, payload exacto de `metrics`, truncado, acierto de caché, `error` antes y a mitad sin detalles ni key, respuesta vacía, 422 (vacío, empieza o termina en `assistant`, roles no alternos, en blanco, > 20 000), 21 mensajes válidos, prompt por turno, OpenAPI
- [x] 6.2 Desconexión a nivel de función: cerrar el generador tras un evento cierra el doble de proveedor, no guarda en `fakeredis` y registra `estimate_stream_cancelled`
- [x] 6.3 `GET /api/v1/context`: `message_count` 1 y 3, ejemplos, límite, 422 con 0, sin llamada al LLM, OpenAPI
- [x] 6.4 Streamlit con `AppTest` y `httpx.Client` parcheado (`MockTransport`): petición enviada, texto incremental, keep-alive, `error`, stream sin terminal, 500/422, respuesta vacía, métricas desde `metrics` (respaldo, caché, truncado), API caída al abrir y al enviar (chat visible, historial intacto), URL configurada, panel lateral (sin respaldo, caché no disponible, prompt con `message_count=3`), validación con `max_message_chars`
- [x] 6.5 Uno o dos tests de extremo a extremo UI → API → servicio con `TestClient(app)` como `httpx.Client`
- [x] 6.6 Imports y estructura: `streamlit_app.py` no importa `app`, `litellm`, `redis`, `openai` ni `anthropic` (AST); `Dockerfile`, `.dockerignore` y `docker-compose.yml` en el test de estructura
- [x] 6.7 Verificar: `uv run pytest -q` en verde sin red, sin Redis ni servidor HTTP real, y tabla de cobertura de los escenarios del change

## 7. Documentación y cierre

- [x] 7.1 README: arranque en dos procesos (uvicorn + streamlit) y con `docker compose up` + streamlit, `ESTIMATOR_API_BASE_URL`, contrato SSE (eventos y ejemplo con `curl -N`), `GET /api/v1/context`, grupo `ui`, Docker (Redis sin persistencia, puertos locales); `.env.example` con `ESTIMATOR_API_BASE_URL`; CLAUDE.md (estructura y comandos) (orquestador)
- [x] 7.2 Verificación manual con consentimiento del usuario (llamadas reales con coste): `curl -N` contra el SSE (fragmentos y `metrics`), `error` con una key inválida y sin respaldo, Stop en Streamlit con `estimate_stream_cancelled` en el log y sin entrada en la caché, API parada con la UI abierta, y `docker compose up` con una estimación y un acierto de caché desde Streamlit en el host
- [x] 7.3 `openspec validate --all --strict`, `ruff` y `pytest` en verde en local, y job `docker` en verde en la CI (local en verde: 383 tests; el job `docker` de la CI se comprueba tras el push)
- [x] 7.4 Archivar el change (`/opsx:archive`) tras la aprobación del usuario
- [x] 7.5 Commit en `pre-session-04` (con la aprobación del usuario)
