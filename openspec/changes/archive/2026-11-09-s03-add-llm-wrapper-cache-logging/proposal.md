## Why

El enunciado de S04 (`/Users/javiaranega/claude/AIEngineer-notas/04_IA_avansados.md/00_ejercicio-del-chat-a-la-interfaz-de-producto.md`) da por hecho el punto de partida del **directo de S03**: un wrapper de proveedores con fallback, caché exact-match y observabilidad con structlog. En nuestra rama solo hicimos el ejercicio previo de S03 (chat Streamlit), así que falta esa base. Este change la incorpora tomando como referencia la rama `session_3_live` del repo oficial (`LIDR-academy-temp/ai-engineering`) y los apuntes `03_Patrones_diseno/02_abstraccion-de-proveedores-y-estrategias-de-fallback.md`, `03_cacheo-inteligente-de-respuestas-de-llms.md` y `05_observabilidad-logging-y-trazabilidad.md`.

Sesión: **S03 (catch-up del directo)**, implementado en la rama `pre-session-04` antes del ejercicio de S04. Es el primero de tres changes de puesta al día: `s03-add-llm-wrapper-cache-logging` (este), `s03-add-sse-streaming-and-docker` y `s02-add-generation-knobs-and-evaluation`.

## What Changes

- **BREAKING (configuración)**: las llamadas al LLM pasan por **LiteLLM** con un `Router` de dos despliegues: modelo primario (`PRIMARY_MODEL`, por defecto `gpt-4o-mini`) y de respaldo (`FALLBACK_MODEL`, por defecto `claude-haiku-4-5`). Si el primario falla tras los reintentos, se usa el de respaldo, aunque sea de otro proveedor. `LLM_TIMEOUT` y `LLM_RETRIES` pasan a ser configurables (por defecto 60 s y 2, los valores actuales).
- **BREAKING (configuración)**: se eliminan `LLM_PROVIDER` y `LLM_MODEL`, y con ellos los adaptadores propios `OpenAIProvider` y `AnthropicProvider` sobre los SDK oficiales. El servicio exige la API key del proveedor del modelo primario. La del respaldo es opcional: si falta, o si `FALLBACK_MODEL` está vacío, el servicio arranca sin fallback y lo avisa en el log.
- Se mantiene la interfaz `LLMProvider` (`generate` / `stream` con `LLMStream`): la implementación pasa a ser una sola, sobre LiteLLM, y `EstimationService`, el router y la app Streamlit no cambian de forma de uso.
- **Caché exact-match en Redis** (`REDIS_URL`, `CACHE_TTL`, por defecto 24 h): la clave es un SHA-256 del system prompt, los mensajes, el modelo primario y `max_tokens`. Un acierto devuelve la respuesta guardada sin llamar al LLM, también en streaming (reproducida como un único fragmento). Si Redis no responde, el servicio sigue funcionando sin caché y lo registra como warning. Las respuestas truncadas o vacías no se guardan.
- **Logging con structlog**: consola legible en desarrollo y JSON cuando `APP_ENV=production`. Eventos `llm_call_started`, `llm_call_completed` (modelo y proveedor efectivos, tokens, coste, latencia, `finish_reason`, `cache_hit`, si hubo fallback) y `llm_call_failed`, y eventos de caché. Nunca incluyen la transcripción, los mensajes, el prompt ni las API keys.
- **Respuesta de `POST /api/v1/estimate`** (cambio aditivo): nuevos campos `latency_ms`, `cache_hit` y `fallback_used`. `model` y `provider` pasan a ser los **efectivos** (el modelo configurado del despliegue que respondió, primario o respaldo) y `provider` deja de limitarse a `openai | anthropic` en el schema. Se mantienen `truncated`, los tokens y `cost_usd` (calculado con nuestra tabla de precios; `null` si el modelo no está).
- **`GET /health`**: informa el modelo primario, el de respaldo y si la caché está disponible, en lugar de `provider` y `model`.
- **App Streamlit**: sigue en el mismo proceso (pasa a HTTP en el change 2). La barra lateral muestra el modelo primario y el de respaldo, y las métricas incluyen el modelo efectivo, `cache_hit` y si hubo fallback.

## Capabilities

### New Capabilities
- `response-cache`: caché exact-match de respuestas del LLM en Redis: clave, TTL, qué se guarda y qué no, comportamiento con Redis caído y uso en streaming.
- `observability`: logging estructurado con structlog: formato según entorno, eventos de la llamada al LLM y de la caché, y qué datos nunca se registran.

### Modified Capabilities
- `llm-provider`: la interfaz se implementa con LiteLLM; fallback entre modelo primario y de respaldo; límites (timeout, reintentos) configurables; el resultado informa el modelo/proveedor efectivos, si hubo fallback, si vino de caché y la latencia; desaparece la selección por `LLM_PROVIDER`.
- `app-config`: nuevas variables `PRIMARY_MODEL`, `FALLBACK_MODEL`, `LLM_TIMEOUT`, `LLM_RETRIES`, `REDIS_URL`, `CACHE_TTL`; se eliminan `LLM_PROVIDER` y `LLM_MODEL`; nueva regla de API keys obligatorias.
- `estimation-api`: nuevos campos de respuesta (`latency_ms`, `cache_hit`, `fallback_used`), `model`/`provider` efectivos y nuevo contenido de `/health`.
- `chat-ui`: la barra lateral muestra primario y respaldo; las métricas añaden modelo efectivo, `cache_hit` y fallback.
- `ci-pipeline`: los tests no necesitan Redis (doble de prueba `fakeredis`) y siguen sin llamar a ningún proveedor; actualizar la cobertura mínima listada.

## Impact

- **Código**: `app/services/llm_service.py` (proveedor LiteLLM, eliminar adaptadores), nuevo `app/services/cache.py`, `app/config.py` (variables nuevas y structlog), `app/main.py`, `app/schemas.py`, `app/routers/estimations.py`, `streamlit_app.py`, `.env.example`, `README.md`.
- **Dependencias**: nuevas `litellm` (versión reciente fijada; las 1.82.7 y 1.82.8 se retiraron de PyPI por un compromiso de la cadena de suministro), `redis`, `structlog`; dev: `fakeredis`. Las dependencias directas `openai` y `anthropic` se eliminan si ya no se importan (LiteLLM las usa internamente o no, según el proveedor).
- **Infraestructura local**: Redis es opcional para arrancar (sin él no hay caché). En el change 2 se añade `docker-compose.yml` con Redis; hasta entonces, `docker run -p 6379:6379 redis:7-alpine`.
- **Tests**: se sustituyen los tests de los adaptadores de SDK por tests del proveedor LiteLLM con `litellm.completion`/`Router` simulados; nuevos tests de caché con `fakeredis` y de logs con `structlog.testing.capture_logs`.

## Desviaciones respecto a la referencia (`session_3_live`)

- **Errores**: mantenemos el 502 genérico y el error de dominio `LLMProviderError`; la referencia devuelve 500 con `str(exc)`, que puede filtrar detalles del proveedor.
- **Coste**: mantenemos nuestra tabla verificada y `cost_usd = null` para modelos desconocidos; la referencia devuelve `0.0`.
- **Truncado y streaming**: mantenemos `truncated` y las métricas de tokens en streaming (la referencia guarda 0 tokens en streaming).
- **Caché en streaming**: no se guardan respuestas truncadas ni vacías ni las de un stream interrumpido; la referencia guarda cualquier stream terminado.
- **Sobrescritura de modelo por petición** (`model` en el request): la referencia la permite saltándose el fallback. Queda para `s02-add-generation-knobs-and-evaluation`.
- **API keys**: la referencia exige al menos una de las dos keys; nosotros exigimos la del primario, porque sin ella todas las peticiones dependerían del fallback.

## Fuera de alcance

- Endpoint SSE, Streamlit como cliente HTTP, CORS, página de demo y Docker/docker-compose → `s03-add-sse-streaming-and-docker`.
- Parámetros de generación por petición, evaluación estructural y ejemplos estructurados → `s02-add-generation-knobs-and-evaluation`.
- Caché semántica (directo de S04), guardrails y salida JSON estructurada (S04+).
- Trazabilidad distribuida, métricas exportadas (Prometheus, Langfuse…) y request IDs en los logs (S05+ si el curso lo trabaja).
