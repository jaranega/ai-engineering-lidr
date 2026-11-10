## Context

Punto de partida: `main` tras la PR #2 (S03, chat Streamlit). Toda la lógica LLM vive en `app/services/llm_service.py`: `LLMProvider` (Protocol con `generate`/`stream`), `LLMStream` (envuelve un generador que *devuelve* el `LLMResult`, pensado para componer fallback y caché con `yield from`, D3 de S03), los adaptadores `OpenAIProvider`/`AnthropicProvider` sobre los SDK, `build_provider`/`get_llm_provider` (`lru_cache`) y `EstimationService`. `configure_logging` (stdlib) está en `app/config.py` y la usan el `lifespan` y `streamlit_app.py`.

Este change incorpora la base del directo de S03 (referencia `session_3_live`): LiteLLM `Router` con primario y respaldo, caché exact-match en Redis y structlog. Las decisiones del usuario ya tomadas están en `proposal.md` y no se reabren. Lo que sigue se ha verificado contra el código instalado de **litellm 1.106.4**, **redis 8.1.0**, **structlog 26.1.0** y **fakeredis 2.40.0** (venv de prueba); cada afirmación sobre LiteLLM cita el fichero comprobado.

Siguientes pasos que el diseño debe habilitar sin implementarlos: change 2 `s03-add-sse-streaming-and-docker` (endpoint SSE multi-turno con evento final `metrics`, Streamlit como cliente HTTP, docker-compose con Redis), change 3 `s02-add-generation-knobs-and-evaluation` (parámetros por petición, incluida la sobrescritura de modelo, y evaluación estructural) y S04 (`render_estimation_prompt(request) -> (system, user)` con Jinja2).

## Goals / Non-Goals

**Goals:**

- Una sola implementación de `LLMProvider` sobre LiteLLM, con el primario siempre primero y el respaldo solo si el primario falla.
- Caché y observabilidad como piezas componibles alrededor del proveedor, sin cambiar la forma de uso de `EstimationService`, el router ni Streamlit.
- Resultado con modelo/proveedor efectivos, `fallback_used`, `cache_hit` y `latency_ms`, coherente entre `generate` y `stream`.
- Ningún dato de cliente ni secret en logs; tests y CI sin red (ni LLM, ni Redis, ni el mapa de precios de LiteLLM).

**Non-Goals:**

- Endpoint SSE, Streamlit por HTTP, Docker (change 2). Parámetros por petición y sobrescritura de modelo (change 3). Caché semántica (S04). Llamadas async, trazas distribuidas, request IDs y métricas exportadas.

## Decisions

### D1. Dependencias: `litellm==1.106.4`, sin `openai`/`anthropic` directos

- `uv add litellm==1.106.4 redis structlog` y `uv add --dev fakeredis`. Se eliminan las dependencias directas `openai` y `anthropic`: solo las usaban los adaptadores que desaparecen y ningún módulo propio las importará.
- Motivo de la versión: litellm ≥ 1.108 es la primera que admite `openai` 3.x, pero tiene menos de dos semanas; 1.106.4 (2026-10-25) exige `openai<3`, así que `uv` bajará `openai` a 2.x como dependencia transitiva. LiteLLM habla con Anthropic con su propio cliente httpx: el SDK `anthropic` no se instala (comprobado en el venv de prueba).
- Pin exacto (`==`) y `uv.lock` con hashes: las 1.82.7/1.82.8 se retiraron de PyPI por un compromiso de la cadena de suministro, así que cualquier subida de versión es un cambio explícito y revisado. Sin extras (`litellm[proxy]` arrastra el proxy y su base de datos).
- *Alternativas descartadas*: litellm ≥ 1.108 con `openai` 3.x (demasiado reciente); rango `>=1.106,<1.107` (una release de parche podría colarse sin revisión).

### D2. Forma de los módulos: `LiteLLMProvider` + `CachedProvider`

```
app/services/llm_service.py   prompt (sin cambios), LLMResult, LLMStream, LLMProvider, LLMProviderError,
                              LiteLLMProvider, CachedProvider, build_router, build_provider,
                              get_llm_provider (lru_cache), EstimationService
app/services/cache.py         ResponseCache: clave, get/set en Redis, serialización; sin tipos LLM
app/services/pricing.py       sin cambios (ver D4)
app/config.py                 Settings, resolución de modelos (D7), configure_logging con structlog (D9)
```

- `LiteLLMProvider(router, primary, fallback)` implementa el Protocol actual. `primary`/`fallback` son `Deployment(model, provider)` ya resueltos por la configuración (D7). El `Router` se inyecta: los tests pasan un doble o un `Router` real con `mock_response` (D11).
- `CachedProvider(inner, cache, key_model)` es un decorador que también implementa el Protocol: en `generate` consulta, delega y guarda; en `stream` envuelve con `yield from inner.stream(...)` y guarda al terminar. Es la composición que preparó D3 de S03.
- `build_provider(settings) -> LLMProvider` devuelve `CachedProvider(LiteLLMProvider(build_router(settings), ...), get_response_cache(), ...)`. `get_llm_provider()` sigue siendo el getter con `lru_cache` que usan el router (vía `Depends`) y Streamlit; los tests de AppTest siguen parcheando `build_provider`.
- `cache.py` solo conoce dicts y Redis; `CachedProvider` vive en `llm_service.py` porque necesita `LLMResult`/`LLMStream`. Así la dependencia va en un único sentido (`llm_service → cache`) y no hay import circular.
- `LLMResult` gana `fallback_used: bool = False`, `cache_hit: bool = False` y `latency_ms: int = 0`, con valores por defecto para que `FakeProvider` y los stubs sigan siendo válidos. `EstimationResult` copia los tres campos en `_to_estimation_result`, que sigue siendo el único sitio donde se calcula el coste.
- `streamlit_app.py`, los routers y `schemas.py` no importan `litellm` ni `redis` (un test de imports lo comprueba, como ya se hace con los SDK). `structlog` sí se usa en todas las capas, porque es la librería de logging del proyecto (D9).
- *Alternativas descartadas*: un `LLMWrapper` con `complete()`/`complete_stream()` como en la referencia (rompe el Protocol y obliga a reescribir servicio, router y UI, y su stream no devuelve resultado ni tokens); la caché dentro de `LiteLLMProvider` (mezcla dos responsabilidades y no se puede probar ni desactivar por separado); usar la caché integrada de LiteLLM (`cache_responses`) (no controla qué no se guarda, ni el comportamiento con Redis caído, ni expone `cache_hit` de forma estable).

### D3. Router: primario siempre primero, reintentos y timeout

```python
Router(
    model_list=[
        {"model_name": "primary", "litellm_params": {"model": "openai/gpt-4o-mini", "api_key": ..., "timeout": T},
         "model_info": {"id": "primary"}},
        {"model_name": "fallback", "litellm_params": {"model": "anthropic/claude-haiku-4-5", "api_key": ..., "timeout": T},
         "model_info": {"id": "fallback"}},  # solo si hay respaldo (D7)
    ],
    fallbacks=[{"primary": ["fallback"]}],  # [] sin respaldo
    num_retries=R, timeout=T,
)
router.completion(model="primary", messages=[{"role": "system", ...}, *messages], max_tokens=MAX_TOKENS)
```

- **La configuración de la referencia no garantiza el orden.** Con dos despliegues bajo el mismo `model_name` la estrategia por defecto es `simple-shuffle` (`router.py`, `Router.__init__`, `routing_strategy="simple-shuffle"`), que elige al azar entre los despliegues sanos (`router_strategy/simple_shuffle.py`, `random.choices`/`random.choice`): es reparto de carga, y en torno a la mitad de las peticiones irían al respaldo. `fallbacks=[{"estimator": ["estimator"]}]` vuelve a sortear dentro del mismo grupo.
- Con dos grupos de un solo despliegue, `_completion` siempre elige el único despliegue de `primary`; si falla, `async_function_with_retries` reintenta y, al agotar los reintentos, `async_function_with_fallbacks_common_utils` busca `primary` en `fallbacks` y llama al grupo `fallback` (`router.py`). Los grupos de un solo despliegue no entran en cooldown por defecto (`router_utils/cooldown_handlers.py`, "BASE CASE - single deployment"), así que cada petición nueva vuelve a empezar por el primario.
- **Reintentos**: los hace solo el Router. En una llamada del Router, `litellm.completion` fuerza `max_retries = 0` en el cliente del proveedor (`main.py`, `is_router_call`), así que no se multiplican. `should_retry_this_error` no reintenta 4xx salvo 429 (ni 401/403 con un solo despliegue), pero esos errores **sí** pasan al respaldo. Los reintentos se aplican **por grupo**: el respaldo también tiene `R` reintentos.
- **Timeout**: `LLM_TIMEOUT` en `litellm_params.timeout` de cada despliegue y en el Router. En streaming es el timeout del cliente HTTP (entre lecturas), no un límite del stream completo, igual que con los SDK.
- `router.completion` es síncrono: ejecuta la lógica async del Router en un bucle nuevo (`litellm_core_utils/asyncify.py`, `run_async_function`), compatible con los endpoints síncronos de FastAPI y con Streamlit.
- El modelo se pasa con prefijo de proveedor explícito (`openai/…`, `anthropic/…`) a partir de la resolución de D7, para no depender de la inferencia por nombre en cada llamada. Las keys se pasan explícitamente desde `Settings` (no por variables de entorno que LiteLLM lea por su cuenta).
- *Alternativas descartadas*: un único grupo con `order: 1/2` en los despliegues (también es ordenado en 1.106.4, pero es un mecanismo menos documentado y depende de la lógica de "order fallbacks"); un fallback propio sin Router (decisión del usuario: Router como en la referencia).

### D4. Modelo y proveedor efectivos, `fallback_used` y coste

- Cada despliegue lleva `model_info.id` (`"primary"`/`"fallback"`). El Router copia ese id en `response._hidden_params["model_id"]` (`router.py`, `make_call` → `set_response_headers` → `apply_response_model_id` en `router_utils/add_retry_fallback_headers.py`). En streaming se lee de los `_hidden_params` de cada chunk (el último que lo traiga gana); tras un fallback a mitad de stream el Router aplica los del respaldo a cada chunk (`_completion_streaming_iterator`, `_apply_fallback_hidden_params_to_item`).
- Con ese id, `LiteLLMProvider` informa `model` y `provider` **del despliegue configurado** (`Deployment` de D7) y `fallback_used = (id == "fallback")`. Si el id falta (cambio de comportamiento de LiteLLM), se informa el primario y se emite el warning `llm_deployment_unknown`; los tests de contrato de D11 lo detectan antes.
- No se usa `response.model`: varía según el modo y el proveedor. En `generate`, OpenAI devuelve el snapshot (`gpt-4o-mini-2024-07-18`) y Anthropic el de su API (`llms/anthropic/chat/transformation.py`, `model_response.model = completion_response["model"]`). En streaming, el chunk lleva `CustomStreamWrapper.model`, que en OpenAI se actualiza con el del chunk y en Anthropic es el nombre pedido (`litellm_core_utils/streaming_handler.py`). Con el despliegue configurado, el `model` es igual en ambos modos y en la caché, coincide con `/health` y siempre es una clave conocida de la tabla de precios.
- **Coste**: `estimate_cost(result.model, ...)` sin cambios en `pricing.py`. La normalización de prefijos la hace la resolución de D7 (`get_llm_provider("anthropic/claude-haiku-4-5")` devuelve `claude-haiku-4-5`), así que la tabla ya no ve prefijos. Si se configura un snapshot, la tabla ya lo incluye. Decisión confirmada por el usuario (*Open Questions* 1).
- *Alternativas descartadas*: cabecera `x-litellm-model-group` en `_hidden_params["additional_headers"]` (equivalente, pero la clave `model_id` es la que ponemos nosotros); deducir el despliegue comparando `response.model` con los nombres configurados (falla con snapshots); prefijos por nombre (`claude*` → anthropic) como la referencia (frágil con modelos nuevos).

### D5. Streaming a través del Router

- `router.completion(..., stream=True, stream_options={"include_usage": True})`. `CustomStreamWrapper` emite un último chunk con `usage` cuando `stream_options.include_usage` es `True`, para cualquier proveedor (`streaming_handler.py`, `check_send_stream_usage` y el bloque `sent_stream_usage`). Ese chunk puede tener `choices` vacío: se comprueba antes de indexar. Sin chunk de uso → 0 tokens, como hoy.
- `finish_reason` llega normalizado al formato de OpenAI: `max_tokens` de Anthropic → `"length"` (`litellm_core_utils/core_helpers.py`, `map_finish_reason`). `truncated = finish_reason == "length"` en ambos modos y proveedores.
- **Fallback y stream**: los errores al abrir el stream (401, 5xx, conexión) salen de `router.completion` y siguen el flujo normal de reintentos y fallback, porque `_completion` abre la conexión antes de devolver (`fetch_sync_stream`). Un error **durante** la iteración llega como `MidStreamFallbackError` (`streaming_handler.py`, salvo 4xx distintos de 429, que se relanzan tal cual), y el envoltorio del Router (`router.py`, `_completion_streaming_iterator.stream_with_fallbacks`) **solo** pasa al respaldo si aún no se ha generado contenido (`is_pre_first_chunk` o sin `generated_content`). Si ya hubo texto, relanza la excepción original. Esto cumple el requisito "sin fallback tras el primer fragmento" sin código propio.
- Se mantiene el contrato de `LLMStream`: el generador privado llama al Router al empezar a iterar, emite solo fragmentos no vacíos, envuelve **cualquier** `Exception` (al abrir y durante la iteración) en `LLMProviderError` y llama a `close()` en un `finally` (el envoltorio del Router cierra a su vez el stream del proveedor). `GeneratorExit` (Stop de Streamlit) no se envuelve y el `finally` libera la conexión.
- Mismos kwargs en ambos modos (escenario existente) con un único `_request_kwargs(system, messages)`.

### D6. Errores

- Las excepciones de LiteLLM heredan de las de `openai` (`litellm/exceptions.py`: `AuthenticationError(openai.AuthenticationError)`, `RateLimitError`, `Timeout(openai.APITimeoutError)`, `InternalServerError`, `ServiceUnavailableError`, `MidStreamFallbackError(ServiceUnavailableError)`…) y casi todas tienen `status_code`. El Router puede lanzar además excepciones que no son de esa jerarquía (p. ej. sin despliegues disponibles).
- Por eso `LiteLLMProvider` captura `Exception` en `generate` y en el stream, y la convierte en `LLMProviderError(provider=<proveedor del primario>) from exc`. Si fallan los dos modelos, el Router relanza la excepción **del primario** (`async_function_with_fallbacks_common_utils`, `raise original_exception`).
- Log `llm_call_failed`: `error_type=type(exc).__name__`, `status_code=getattr(exc, "status_code", None)`, `mode`, `latency_ms` y `fragments` emitidos (para distinguir el corte a mitad de stream). Nunca `str(exc)` ni `exc_info`: los mensajes de LiteLLM incluyen el cuerpo del proveedor.
- El router HTTP sigue mapeando `LLMProviderError` a 502 genérico.
- *Alternativa descartada*: capturar solo `openai.APIError`/`litellm.exceptions.*` (no cubre los errores del Router ni los de transporte, el mismo problema que llevó a D3 de S03).

### D7. Configuración: variables, resolución de modelos y regla de API keys

- `Settings` añade los campos de la delta de `app-config` (`primary_model`, `fallback_model`, `llm_timeout`, `llm_retries`, `redis_url`, `cache_ttl`) con sus validaciones de rango, y elimina `llm_provider`, `llm_model`, `effective_model` y `DEFAULT_MODELS`.
- Un `model_validator(mode="after")` resuelve cada modelo con `litellm.get_llm_provider(model)` (importado con alias, `resolve_litellm_provider`, para no confundirlo con nuestro `get_llm_provider`). Esa función no hace red y lanza `BadRequestError` si no reconoce el modelo (`litellm_core_utils/get_llm_provider_logic.py`). El resultado es un `Deployment(model=<sin prefijo>, provider=…)`.
- **Respaldo por defecto según el primario** (decisión del usuario, 2026-11-09, tras los tests): si `FALLBACK_MODEL` no está definido, se usa el modelo por defecto del otro proveedor (`gpt-4o-mini` ↔ `claude-haiku-4-5`), para que cambiar solo `PRIMARY_MODEL` no deje primario y respaldo iguales. Definido (aunque sea igual al primario) se respeta; vacío desactiva el fallback.
- Reglas, en este orden, todas con mensajes sin valores de keys (siguen pasando por `format_config_error`):
  1. Modelo no reconocido o proveedor fuera de `{openai, anthropic}` → error que nombra la variable y los proveedores admitidos. Mapa `provider → variable de key` en `config.py`; añadir un proveedor es añadir una entrada y su variable.
  2. Falta la key del proveedor del primario → error que nombra la variable y `PRIMARY_MODEL`.
  3. `FALLBACK_MODEL` igual al primario tras resolverlo → error.
  4. `FALLBACK_MODEL` vacío, o falta la key del respaldo → configuración válida sin respaldo: la propiedad `fallback` vale `None` y `fallback_disabled_reason` indica la variable que falta. El warning `llm_fallback_disabled` lo emiten el `lifespan` y Streamlit tras `configure_logging`, porque el validador corre antes de configurar el logging.
- *Alternativas descartadas*: inferir el proveedor por prefijos de nombre como la referencia (frágil); validar en `services` con un error propio (habría que duplicar el camino de error de configuración en el `lifespan` y en Streamlit); exigir al menos una key como la referencia (descartado en el proposal). Coste asumido: `config.py` depende de `litellm` (ver *Risks*).

### D8. Caché exact-match

- **Clave**: `estimator:llm:v1:` + SHA-256 de `json.dumps({"system", "messages", "model": <modelo primario resuelto>, "max_tokens": MAX_TOKENS}, sort_keys=True, ensure_ascii=False)`. El prefijo `v1` versiona el **formato guardado**; los cambios de prompt invalidan solos porque el system prompt entero entra en la clave. El modo (`generate`/`stream`) no entra en la clave: una transcripción estimada por la API es un acierto en el primer turno del chat, porque `system_prompt_for` devuelve el mismo prompt con un solo mensaje.
- **Interacción con el fallback**: la clave usa el modelo **primario** configurado (el de la petición), no el efectivo. Una respuesta del respaldo se guarda con su `model`/`provider`/`fallback_used = true` y se devuelve tal cual en los aciertos (delta de `response-cache`).
- **Valor**: JSON con `text`, `model`, `provider`, `input_tokens`, `output_tokens`, `fallback_used`. No se guardan `cache_hit` ni `latency_ms`, que son de cada petición, ni `truncated`, porque nunca se guardan respuestas truncadas. Una entrada que no se puede deserializar cuenta como fallo de caché, con el warning `cache_entry_invalid`.
- **No se guarda**: `truncated`, texto vacío o en blanco, errores y streams no terminados. En streaming el `set` está **después** del `yield from` del stream interno, así que ni una excepción ni un `GeneratorExit` llegan a él. El motivo se registra en `cache_skipped` (`reason=truncated|empty`).
- **Acierto**: `generate` devuelve el `LLMResult` guardado con `cache_hit = true` y `latency_ms` de la petición. `stream` devuelve un `LLMStream` cuyo generador hace `yield text` una vez y `return` del resultado. Coste: se calcula con los tokens guardados (delta de `response-cache`).
- **TTL**: `SETEX` con `CACHE_TTL` (86 400 s por defecto).
- **Redis caído sin coste apreciable**: el cliente se crea con `redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=0.2, socket_timeout=0.2, retry=Retry(NoBackoff(), 0))`. Es imprescindible: redis 8.1.0 trae por defecto timeouts de 5 s y **10 reintentos** con backoff (`redis/_defaults.py`, `redis/client.py`), lo que añadiría segundos a cada petición. Si el `get` falla por indisponibilidad, esa petición no intenta el `set` (peor caso de unos 0,4 s por petición). Cualquier `redis.RedisError` produce el warning `cache_unavailable` (`op`, `error_type`) y la petición sigue sin caché.
- **Creación perezosa**: `from_url` no conecta hasta el primer comando. `get_response_cache()` (`lru_cache`) devuelve `ResponseCache` o `None` si `REDIS_URL` está vacío; con `None`, `CachedProvider` delega sin más. Se puede sobrescribir en FastAPI (`dependency_overrides`) y en los tests.
- **Latencia**: `CachedProvider`, la capa exterior, mide `latency_ms` de extremo a extremo (consulta, llamada y guardado; en stream, desde que empieza la iteración hasta el final) y la escribe en el resultado con `dataclasses.replace`. `LiteLLMProvider` mide solo la llamada al LLM para su propio evento de log.
- *Alternativas descartadas*: clave con el modelo efectivo (no se conoce antes de llamar); no guardar las respuestas del respaldo (contradice la delta); cortocircuito temporal tras un fallo de Redis (más estado para un caso que ya cuesta menos de medio segundo; se puede añadir si molesta); `pickle` del `LLMResult` (inseguro y acoplado a la clase).

### D9. Logging con structlog y LiteLLM silenciado

- `configure_logging(settings)` en `config.py` (D6 de S03):
  - `structlog.configure` con procesadores compartidos (`merge_contextvars`, `add_log_level`, `add_logger_name`, `TimeStamper(fmt="iso", key="timestamp")`) y `ProcessorFormatter.wrap_for_formatter`, `stdlib.LoggerFactory()`, `stdlib.BoundLogger` y `cache_logger_on_first_use=False` (para que `capture_logs` funcione en los tests).
  - Un único handler en el root con `structlog.stdlib.ProcessorFormatter(foreign_pre_chain=<compartidos>)` y renderizador `JSONRenderer()` si `APP_ENV == "production"` o `ConsoleRenderer()` en otro caso. Así los logs de `logging` (propios y de terceros) salen con el mismo formato. Se sustituyen los handlers del root en cada llamada, de modo que repetirla (reruns de Streamlit) no los duplica.
  - Los loggers de `uvicorn` pierden sus handlers y propagan al root, para usar el mismo formato.
- Los módulos propios pasan a `structlog.get_logger(__name__)`, incluido el warning de respuesta vacía de Streamlit (`empty_llm_reply`). El camino de error de configuración del `lifespan` configura el logging con valores por defecto antes de registrar `config_invalid`.
- **Eventos** (nombres en inglés, campos sin contenido):
  - `llm_call_started` (`mode`, `primary_model`, `messages`: número de mensajes). Lo emite `LiteLLMProvider`.
  - `llm_call_completed` (`mode`, `model`, `provider`, `fallback_used`, `cache_hit`, `input_tokens`, `output_tokens`, `cost_usd`, `latency_ms`, `finish_reason`, `truncated`). Lo emite `LiteLLMProvider` con `cache_hit=false` y `CachedProvider` en los aciertos con `cache_hit=true`; un acierto no es una llamada al LLM, así que no tiene `llm_call_started`.
  - `llm_call_failed` (D6), `llm_output_truncated` (warning, sustituye a `_log_truncated`), `llm_fallback_disabled`, `llm_deployment_unknown`.
  - `cache_hit`, `cache_miss`, `cache_stored` (`key_prefix`: 12 caracteres del hash, `ttl`), `cache_skipped`, `cache_unavailable`, `cache_entry_invalid`.
- **Silenciar LiteLLM**:
  - En 1.106.4 `litellm.telemetry` no existe (comprobado en `litellm/__init__.py`): no hay nada que desactivar. `set_verbose` queda en `False` y nunca se llama a `litellm._turn_on_debug()`. El `Router` ya pone `litellm.suppress_debug_info = True` (`router.py`, `__init__`).
  - Los loggers `LiteLLM`, `LiteLLM Router` y `LiteLLM Proxy` (`litellm/_logging.py`) no tienen nivel propio y heredan el del root (DEBUG por defecto, que vuelca payloads). Además llevan su propio handler, cuyo nivel es `LITELLM_LOG` (DEBUG por defecto). Su ERROR "Fallback also failed: %s" incluye el mensaje de la excepción. Decisión: nivel `CRITICAL` (en la práctica, silenciados), sin handlers y con `propagate=False`. Nuestros eventos cubren el resultado de cada llamada. Ver *Open Questions* 2.
  - `NOISY_LOGGERS` pasa a `("openai", "httpx", "httpcore")`, con la regla de nunca por debajo de INFO. **Corrección tras la verificación manual (2026-11-09)**: el diseño inicial quitaba `openai` porque ya no se importa el SDK directamente, pero LiteLLM lo usa por dentro para las llamadas a OpenAI y `openai._base_client` vuelca en DEBUG el cuerpo de la petición (system prompt y transcripción, sin la key). Los tests no lo detectaron porque el doble de prueba no pasa por el SDK; se añade un test de regresión sobre el nivel efectivo de ese logger. Ojo: LiteLLM sube `httpx` a WARNING al importarse (`_suppress_loggers`) y nuestra regla lo deja en INFO; esas líneas solo llevan método, URL y status.
- *Alternativas descartadas*: `PrintLoggerFactory` como la referencia (los logs de `logging` saldrían en otro formato); dejar los loggers de LiteLLM en WARNING con su handler (formato distinto y mensajes de error literales, aunque redactados); `LITELLM_LOG` por entorno (depende de que el operador lo ponga).

### D10. Contrato HTTP, `/health` y Streamlit

- `EstimationResponse` añade `fallback_used`, `cache_hit` y `latency_ms`. `provider` pasa a `str` y las descripciones de `model`/`provider` dicen "efectivo". El router solo mapea campos.
- `/health` según la delta: `primary_model`, `fallback_model` (o `null`) y `cache: "ok" | "unavailable"`. Depende de `get_settings` y de `get_response_cache` (sobrescribibles); `ResponseCache.ping()` usa el mismo timeout corto, y `REDIS_URL` vacío también da `"unavailable"`. No llama al LLM y siempre responde 200.
- Streamlit: la barra lateral muestra primario y respaldo (o "sin respaldo") desde `Settings`. `CallMetrics` añade `fallback_used` y `cache_hit`, y el panel muestra "Respaldo: sí/no" y "Desde caché: sí/no" junto al modelo y el proveedor efectivos. `load_config` emite `llm_fallback_disabled` cuando corresponde.

### D11. Tests sin red

- **Sin red al importar**: `litellm/__init__.py` descarga el mapa de precios al importarse (`model_cost = get_model_cost_map(url=...)`, con reintentos en segundo plano; `litellm_core_utils/get_model_cost_map.py`), salvo que `LITELLM_LOCAL_MODEL_COST_MAP=true`. `app/__init__.py` hace `os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")` antes de cualquier import de LiteLLM; vale para la API, Streamlit, los tests y la CI. El mapa local incluye `gpt-4o-mini` y `claude-haiku-4-5` (y sus snapshots), que es lo que necesita `get_llm_provider`. Un test comprueba `get_model_cost_map_source_info()["source"] == "local"`.
- **`LiteLLMProvider` unitario**: un doble de Router (`SimpleNamespace(completion=...)`) que devuelve objetos con la forma de LiteLLM (`choices`, `usage`, `_hidden_params`) o secuencias de chunks. Cubre kwargs (mismos en ambos modos, `stream_options`), fragmentos, uso ausente, `length`, `model_id` primario/respaldo/ausente, errores al abrir y a mitad de stream y `close()`.
- **Contrato con LiteLLM real, sin red**: un `Router` real con `mock_response` en `litellm_params` (`main.py`, `mock_completion`; `"litellm.InternalServerError"` o una excepción simulan el fallo), `num_retries=0` y keys ficticias. Fijan lo que este diseño supone de la 1.106.4: orden primario → respaldo, `model_id` en `_hidden_params` en `generate` y en `stream`, chunk de uso y `finish_reason`. Si falla tras subir de versión, ha cambiado el comportamiento.
- **Caché**: `fakeredis.FakeRedis(decode_responses=True)` inyectado en `ResponseCache`. Para Redis caído, `FakeServer` con `connected = False` o un doble que lanza `redis.ConnectionError`. `CachedProvider` se prueba con `FakeProvider` como proveedor interno.
- **API y Streamlit**: siguen con `FakeProvider` (con los nuevos campos opcionales) y `dependency_overrides`/`build_provider` parcheado. El fixture autouse añade las variables nuevas a `CONFIG_ENV_VARS` y fija `REDIS_URL=""`, para que ningún test toque un Redis local.
- **Logs**: `structlog.testing.capture_logs()` para eventos y campos, y una comprobación de que ninguna línea capturada contiene la transcripción centinela ni una key ficticia (también con `LOG_LEVEL=DEBUG` y un error cuyo mensaje contiene la key).
- *Alternativa descartada*: parchear `litellm.completion` en todos los tests (funciona, porque el Router lo llama por atributo de módulo en `_completion`, pero los dobles de Router son más simples y `mock_response` ejercita el Router real).

### D12. Cómo habilita los siguientes changes

- **Change 2 (SSE + `metrics`)**: el endpoint SSE consumirá `EstimationService.stream_chat(messages)` igual que Streamlit hoy. Los fragmentos son eventos y el evento `metrics` se construye con `stream.result` (`EstimationResult` con modelo efectivo, tokens, coste, `latency_ms`, `cache_hit`, `fallback_used`, `truncated`). Al ser un iterador síncrono, `StreamingResponse` lo itera en el threadpool sin migrar a async. Los aciertos de caché se reproducen como un fragmento y también funcionan por SSE. Docker añade Redis sin cambiar el código (`REDIS_URL`).
- **Change 3 (parámetros por petición)**: la clave de caché sale de un dict de parámetros de generación (`model`, `max_tokens`), así que añadir `max_tokens`, la sobrescritura de modelo u otros parámetros es añadir campos a ese dict y a `_request_kwargs`. La sobrescritura de modelo podrá resolverse con la misma función de D7 y llamar a `litellm.completion` sin fallback, como en la referencia, o con un despliegue propio. Esa decisión queda para ese change. La evaluación estructural leerá `EstimationResult.text`.
- **S04 (Jinja2)**: `render_estimation_prompt(request) -> (system, user)` sustituirá a `build_estimation_prompt` dentro de `EstimationService`. Proveedor y caché no cambian, y como el system prompt entero entra en la clave, cambiar la versión del template invalida la caché sin tocar `v1`.

## Risks / Trade-offs

- [Latencia en el peor caso: `(R+1)` intentos en el primario más `(R+1)` en el respaldo, cada uno de hasta `LLM_TIMEOUT`, más el backoff; con 2 y 60 s, hasta unos 6 minutos] → Los fallos típicos (401, 5xx, conexión rechazada) son rápidos y los 4xx no se reintentan. Los valores son configurables y se documentan en el README. Ver *Open Questions* 3.
- [`config.py` depende de LiteLLM, que tarda en importarse (segundos) y es una dependencia de proveedor en una capa transversal] → El coste se paga una vez por proceso. El mapa local evita la red. La dependencia se limita a `get_llm_provider`.
- [Si fallan los dos modelos, solo se registra el error del primario y el del respaldo se pierde, porque LiteLLM queda silenciado] → El tipo y el status del primario sí se registran. Para depurar se puede subir temporalmente el nivel de los loggers de LiteLLM en local.
- [Dependencia de detalles internos de LiteLLM (`_hidden_params["model_id"]`, `MidStreamFallbackError`)] → Versión fijada y tests de contrato con `mock_response`. Si `model_id` falta, se informa el primario con un warning en lugar de fallar.
- [La caché guarda en Redis, durante `CACHE_TTL`, texto derivado de las reuniones de clientes] → Redis local o del compose del change 2, sin exponer. Las claves son hashes. Se documenta en el README.
- [Un acierto informa los tokens y el coste originales (delta de `response-cache`): sumar `cost_usd` de los logs contaría dos veces] → `llm_call_completed` lleva `cache_hit`, así que las agregaciones pueden filtrar.
- [Bajar `openai` de 3.x a 2.x como dependencia transitiva] → Ningún módulo propio lo importa. LiteLLM 1.106.4 se publica probado contra `openai<3`.
- [Los logs de `uvicorn` y de terceros cambian de formato] → Es el objetivo de la delta de `observability`. Se verifica a mano en consola y en JSON.

## Migration Plan

1. Rama `pre-session-04`. Orden de implementación: dependencias (D1) → `app/__init__.py` y `Settings` (D7) → `configure_logging` (D9) → `cache.py` (D8) → `LiteLLMProvider`/`CachedProvider` (D2–D6) → schema, router y `/health` (D10) → Streamlit → tests (D11) → README y `.env.example`.
2. `.env` local: eliminar `LLM_PROVIDER`/`LLM_MODEL` (si quedan, se ignoran), definir `PRIMARY_MODEL`/`FALLBACK_MODEL` si no se quieren los valores por defecto y, opcionalmente, `REDIS_URL` (`docker run -p 6379:6379 redis:7-alpine` hasta el change 2).
3. Verificación manual: estimación con ambos modelos como primario, fallback forzado (key del primario inválida), acierto de caché por la API y en el chat, Redis parado y logs en consola y con `APP_ENV=production`.
4. Rollback: revertir los commits del change (las dependencias vuelven con `uv.lock`). Los clientes de la API solo reciben campos nuevos.

## Open Questions

Resueltas por el usuario el 2026-11-09 (todas con la opción recomendada). Specs y proposal actualizados en consecuencia:

1. **`model` efectivo**: se informa el modelo **del despliegue configurado**, sin prefijo de proveedor (D4). `pricing.py` no cambia. En la delta de `llm-provider`, el escenario del snapshot devuelto se sustituye por "modelo configurado con prefijo de proveedor" y "snapshot configurado".
2. **Logs propios de LiteLLM**: silenciados por completo (D9).
3. **Reintentos en el respaldo**: se acepta que el respaldo tenga también `LLM_RETRIES`. El peor caso (~6 min con los valores por defecto) se documenta en el README.
4. **`FALLBACK_MODEL=` vacío**: desactiva el fallback a propósito, con el warning `llm_fallback_disabled` (escenario añadido en la delta de `app-config`). `/health` informa `fallback_model: null`.
5. **Mapa de precios local**: `LITELLM_LOCAL_MODEL_COST_MAP=True` por defecto en todos los entornos (`setdefault` en `app/__init__.py`), con un escenario en la delta de `ci-pipeline`.
