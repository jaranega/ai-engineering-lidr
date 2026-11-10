## 0. Prerrequisito

- [x] 0.1 Revisión del change por el usuario (proposal, specs, design y tasks)

## 1. Dependencias y configuración (backend-engineer)

- [x] 1.1 `uv add litellm==1.106.4 redis structlog`, `uv add --dev fakeredis`, `uv remove openai anthropic`; comprobar en `uv tree` que `openai` queda en 2.x y que no se instala `anthropic` (D1)
- [x] 1.2 `app/__init__.py`: `os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")` antes de cualquier import de LiteLLM (D11)
- [x] 1.3 `Settings`: añadir `primary_model`, `fallback_model` (vacío → `None`), `llm_timeout`, `llm_retries`, `redis_url`, `cache_ttl` con sus rangos; eliminar `llm_provider`, `llm_model`, `effective_model` y `DEFAULT_MODELS` (D7)
- [x] 1.4 Resolución de modelos con `litellm.get_llm_provider` (sin red): `Deployment(model, provider)` para primario y respaldo; reglas en orden (modelo/proveedor no admitido, falta key del primario, respaldo igual al primario, respaldo desactivado o sin key → `fallback = None` y `fallback_disabled_reason`); mensajes sin valores de keys vía `format_config_error` (D7)
- [x] 1.5 `configure_logging` con structlog: procesadores compartidos, `ProcessorFormatter` en el root (JSON si `APP_ENV=production`, consola si no), loggers de `uvicorn` propagando al root, loggers de LiteLLM silenciados (`CRITICAL`, sin handlers, `propagate=False`), `NOISY_LOGGERS = ("httpx", "httpcore")` nunca por debajo de INFO; idempotente entre reruns (D9)
- [x] 1.6 `.env.example`: variables nuevas comentadas con su valor por defecto, sin `LLM_PROVIDER`/`LLM_MODEL`
- [x] 1.7 Verificar: `ruff` sin errores y `uv sync --locked` correcto

## 2. Servicios (ai-engineer)

- [x] 2.1 `app/services/cache.py`: `ResponseCache` (clave `estimator:llm:v1:` + SHA-256, `get`/`set` con `SETEX`, `ping`, serialización JSON, entradas inválidas como fallo) con cliente Redis de timeouts de 0,2 s y sin reintentos; `get_response_cache()` con `lru_cache` que devuelve `None` si `REDIS_URL` está vacío; eventos `cache_*` (D8)
- [x] 2.2 `LLMResult` y `EstimationResult` con `fallback_used`, `cache_hit` y `latency_ms` (valores por defecto); `_to_estimation_result` los copia (D2)
- [x] 2.3 `build_router(settings)`: grupos `primary` y `fallback` con `model_info.id`, `fallbacks=[{"primary": ["fallback"]}]` (o `[]` sin respaldo), `num_retries`, `timeout` y keys explícitas desde `Settings` (D3)
- [x] 2.4 `LiteLLMProvider.generate` y `stream` con `_request_kwargs` común, `stream_options={"include_usage": True}`, `truncated` por `finish_reason == "length"`, modelo/proveedor efectivos y `fallback_used` desde `_hidden_params["model_id"]` (warning `llm_deployment_unknown` si falta), cualquier `Exception` → `LLMProviderError` y `close()` en `finally` (D3–D6)
- [x] 2.5 Eventos `llm_call_started`, `llm_call_completed`, `llm_call_failed` y `llm_output_truncated` sin contenido ni mensajes de error (D6, D9)
- [x] 2.6 `CachedProvider`: consulta, delega y guarda en `generate` y `stream` (guardado después del `yield from`), aciertos con `cache_hit = true` (un fragmento en streaming), no guarda truncadas ni vacías (`cache_skipped`), mide `latency_ms` de extremo a extremo y emite `llm_call_completed` en los aciertos (D2, D8)
- [x] 2.7 `build_provider(settings)` y `get_llm_provider()` componen `CachedProvider(LiteLLMProvider(...))`; eliminar `OpenAIProvider`, `AnthropicProvider` y sus helpers de SDK
- [x] 2.8 Verificar: `ruff` sin errores y ningún módulo fuera de `app/services` (y `app/config.py`) importa `litellm`

## 3. API (backend-engineer)

- [x] 3.1 `EstimationResponse`: `fallback_used`, `cache_hit`, `latency_ms`; `provider: str`; descripciones de modelo/proveedor efectivos; mapeo en el router (D10)
- [x] 3.2 `/health` con `primary_model`, `fallback_model` (o `null`) y `cache: "ok" | "unavailable"` (vía `get_response_cache().ping()`), siempre 200 (D10)
- [x] 3.3 `lifespan`: logging configurado antes de registrar `config_invalid`; warning `llm_fallback_disabled` cuando corresponda; logs propios con `structlog.get_logger` (D9)
- [x] 3.4 Verificar: `ruff` sin errores y `/openapi.json` con los campos nuevos

## 4. Interfaz (frontend-engineer)

- [x] 4.1 Barra lateral: modelo primario y de respaldo (o "sin respaldo") desde `Settings`; `load_config` emite `llm_fallback_disabled` (D10)
- [x] 4.2 `CallMetrics` con `fallback_used` y `cache_hit`; el panel muestra "Respaldo: sí/no" y "Desde caché: sí/no" junto al modelo y proveedor efectivos (D10)
- [x] 4.3 Logs de la app con `structlog.get_logger` (`empty_llm_reply`) (D9)
- [x] 4.4 Verificar: `ruff` sin errores y la app arranca (`AppTest` de humo)

## 5. Tests (qa-engineer)

- [x] 5.1 `conftest.py`: variables nuevas en el fixture de entorno, `REDIS_URL=""` autouse, `FakeProvider` con los campos nuevos opcionales; eliminar tests de los adaptadores de SDK
- [x] 5.2 `app-config`: valores por defecto, rangos inválidos, variables retiradas ignoradas, modelo no admitido, respaldo igual al primario, falta key del primario (sin valores de keys), respaldo vacío o sin key → sin fallback y warning
- [x] 5.3 `LiteLLMProvider` con un doble de Router: kwargs iguales en ambos modos, fragmentos, uso ausente, truncado, `model_id` primario/respaldo/ausente, errores al abrir y a mitad de stream, `close()`, segunda iteración
- [x] 5.4 Contrato con un `Router` real y `mock_response` (sin red): primario primero, fallback ante error, sin fallback tras el primer fragmento, `model_id` en `generate` y `stream`, chunk de uso y `finish_reason`; el mapa de precios de LiteLLM se carga en local
- [x] 5.5 Caché con `fakeredis`: acierto/fallo, clave sensible a prompt/mensajes/modelo, TTL, no guarda truncadas/vacías/interrumpidas/fallidas, acierto en streaming con un fragmento, compartida entre `generate` y `stream`, Redis caído sin error para el cliente
- [x] 5.6 Logs con `capture_logs`: eventos y campos de `llm_call_*` y `cache_*`; JSON en producción; ninguna línea contiene la transcripción centinela ni una key ficticia (incluido un error cuyo mensaje contiene la key)
- [x] 5.7 API y Streamlit: campos nuevos en `/estimate` y `/openapi.json`, `/health` con Redis caído y sin respaldo, barra lateral y métricas con respaldo y caché
- [x] 5.8 Verificar: `uv run pytest -q` en verde sin red, sin Redis y sin keys reales, y tabla de cobertura de los escenarios del change

## 6. Documentación y cierre

- [x] 6.1 README: configuración nueva (modelos, fallback, límites y peor caso de espera, Redis y `docker run` provisional), contrato de respuesta y `/health`, logs y qué no se registra; CLAUDE.md (stack y variables) (orquestador)
- [x] 6.2 Verificación manual con consentimiento del usuario (llamadas reales con coste): ambos modelos como primario, fallback forzado con una key inválida, acierto de caché por la API y en el chat, Redis parado, logs en consola y con `APP_ENV=production`. Resultado: A–G correctas; la F detectó que `openai._base_client` volcaba el payload en DEBUG (corregido en `NOISY_LOGGERS` con test de regresión). Coste ≈ 0,02 USD
- [x] 6.3 `openspec validate --all --strict`, `ruff` y `pytest` en verde en local
- [x] 6.4 Archivar el change (`/opsx:archive`) tras la aprobación del usuario
- [x] 6.5 Commit en `pre-session-04` (con la aprobación del usuario)
