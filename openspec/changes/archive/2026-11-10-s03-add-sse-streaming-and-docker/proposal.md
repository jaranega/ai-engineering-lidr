## Why

El directo de S03 (rama de referencia `session_3_live` de `LIDR-academy-temp/ai-engineering`; apuntes `/Users/javiaranega/claude/AIEngineer-notas/03_Patrones_diseno/04_streaming-y-manejo-de-respuestas-largas.md`) expone el streaming por HTTP con Server-Sent Events y convierte Streamlit en un cliente HTTP del servicio, que se levanta con Docker junto a Redis. Hoy nuestro streaming solo existe dentro del proceso de Streamlit y no hay forma de servir el servicio en contenedores. Además, el enunciado de S04 (`/Users/javiaranega/claude/AIEngineer-notas/04_IA_avansados.md/00_ejercicio-del-chat-a-la-interfaz-de-producto.md`) exige que el cliente llame al servicio por HTTP.

Sesión: **S03 (catch-up del directo)**, rama `pre-session-04`. Segundo de tres changes; depende de `s03-add-llm-wrapper-cache-logging` (proveedor LiteLLM con caché y `EstimationResult` con modelo efectivo, `cache_hit`, `fallback_used` y `latency_ms`).

## What Changes

- **Nuevo endpoint `POST /api/v1/estimate/stream`** (SSE): recibe la conversación completa (`messages`: lista de `{role, content}` alternando `user`/`assistant` y empezando y terminando por `user`), aplica el mismo prompt por turno que el chat (`system_prompt_for`) y emite eventos `token` con cada fragmento (string JSON), un evento final `metrics` con los datos de la llamada (modelo y proveedor efectivos, tokens, coste, `truncated`, `cache_hit`, `fallback_used`, `latency_ms`) y un evento `error` genérico si el proveedor falla, sin detalles; no hay evento `done`, porque `metrics` y `error` son terminales. Cualquier fallo del proveedor, también antes del primer fragmento, se comunica con `200` + evento `error`. Las validaciones de los mensajes (vacíos, límite de 20 000 caracteres por mensaje, roles) responden 422 antes de abrir el stream.
- **Streamlit pasa a ser cliente HTTP** (**BREAKING** para el uso local: hay que tener la API levantada): envía la conversación al endpoint SSE con `httpx` y pinta los fragmentos con `st.write_stream`; las métricas de la barra lateral salen del evento `metrics` (el tiempo hasta el primer fragmento lo sigue midiendo el cliente). Deja de importar `app/services`; la URL del servicio se configura con `ESTIMATOR_API_BASE_URL` (por defecto `http://localhost:8000`). La barra lateral obtiene modelos y estado de la caché de `GET /health` y el system prompt y los ejemplos CAG del nuevo `GET /api/v1/context`. Si la API no responde, la app no se detiene: muestra un aviso con la URL y cómo arrancar la API, y mantiene visibles el historial y el chat.
- **Nuevo endpoint `GET /api/v1/context?message_count=N`** (solo lectura): `system_prompt` que usará una petición con `N` mensajes, `examples` CAG y `max_message_chars`, para que la UI no importe `app`.
- Se mantienen el comportamiento del chat (historial, ajustes multi-turno, Stop, aviso de truncado, respuesta vacía como error, "Nueva conversación") y el `POST /api/v1/estimate` sin cambios.
- **Docker**: `Dockerfile` multi-stage con `uv` (imagen final sin herramientas de build, usuario no root, healthcheck sobre `/health`) y `docker-compose.yml` con la API y Redis (healthchecks, `REDIS_URL` apuntando al servicio de Redis, Redis sin persistencia y puertos ligados a `127.0.0.1`). Streamlit se sigue ejecutando fuera de Docker. La imagen no instala las dependencias de la interfaz.
- **CI**: job paralelo con `docker build` y `docker compose config --quiet`.

## Capabilities

### New Capabilities
- `streaming-api`: endpoint SSE de estimación multi-turno: contrato de entrada, formato de eventos (`token`, `metrics`, `error`), validación, errores y cierre del stream.
- `container-runtime`: imagen Docker y docker-compose del servicio con Redis.

### Modified Capabilities
- `chat-ui`: la app consume la API por HTTP (SSE) en lugar de llamar a `app/services`; configuración por `ESTIMATOR_API_BASE_URL`; errores de conexión con la API; métricas desde el evento `metrics`; la configuración del LLM deja de leerse en la app.
- `estimation-api`: nuevo `GET /api/v1/context` con el contexto CAG para la barra lateral; el texto de `EstimationRequest.transcription` comparte tipo con los mensajes del SSE (sin cambio de contrato).
- `observability`: eventos nuevos del stream SSE y del cliente Streamlit.
- `ci-pipeline`: tests del endpoint SSE y de Streamlit con la API simulada por httpx; ficheros de Docker en el test de estructura; job de Docker.

## Impact

- **Código**: `app/routers/estimations.py` (SSE y contexto), `app/schemas.py` (request de streaming, `EstimationMetrics`, `ContextResponse`), `app/services/llm_service.py` (`prompt_context`), `.github/workflows/` (job de Docker), `streamlit_app.py` (cliente HTTP), `README.md`, `.env.example` (`ESTIMATOR_API_BASE_URL`), nuevos `Dockerfile`, `.dockerignore` y `docker-compose.yml`.
- **Dependencias**: `streamlit` y `httpx` pasan a un dependency group `ui` incluido en `default-groups` (`uv sync` local los instala; la imagen usa `--no-default-groups`). SSE nativo de FastAPI (`fastapi.sse`), sin librería nueva.
- **Arranque local**: dos procesos (`uvicorn` y `streamlit`), o `docker compose up` + `streamlit run`.

## Desviaciones respecto a la referencia (`session_3_live`)

- El endpoint SSE recibe el **historial multi-turno** (la referencia, solo la transcripción) para conservar el chat con ajustes hasta que S04 lo sustituya por el formulario.
- Evento final **`metrics`** (la referencia no envía métricas), evento `error` **genérico** (la referencia envía `str(exc)`), **sin evento `done`** (la referencia lo envía) y `token` como **string JSON** (la referencia envía texto plano).
- **Sin `sse-starlette`**: se usa el SSE nativo de FastAPI.
- Docker: imagen de `uv` con versión fijada (la referencia usa `latest`), `.dockerignore` en lista blanca, Redis sin persistencia y sin bind-mount ni `--reload` en el compose.
- **Sin página de demo HTML ni CORS** (decisión del usuario): Streamlit es el cliente y `curl -N` sirve para probar el SSE.
- Streamlit **fuera** de docker-compose, como en la referencia.

## Fuera de alcance

- Parámetros de generación por petición y evaluación estructural → `s02-add-generation-knobs-and-evaluation`.
- Formulario tipado y prompts Jinja2 → ejercicio de S04.
- Despliegue en la nube, imágenes publicadas en un registry, autenticación del API y rate limiting.
