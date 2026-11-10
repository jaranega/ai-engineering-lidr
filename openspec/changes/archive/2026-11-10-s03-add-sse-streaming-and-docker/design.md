## Context

Este change parte del estado final de `s03-add-llm-wrapper-cache-logging` (en implementación): `EstimationService.stream_chat(messages) -> EstimationStream`, iterable síncrono de `str` cuyo `.result` es un `EstimationResult` (texto, `model`/`provider` efectivos, tokens, `cost_usd`, `truncated`, `fallback_used`, `cache_hit`, `latency_ms`); `LLMProviderError`; `CachedProvider`, que guarda en Redis **solo** después del `yield from` del stream interno; structlog; `/health` con `primary_model`, `fallback_model` y `cache`. Hoy `streamlit_app.py` importa `app.config` y `app.services` y llama al servicio en el mismo proceso (D1 de S03, ahora revocada por el usuario).

Decisiones del usuario que no se reabren (ver `proposal.md`): `POST /api/v1/estimate/stream` con el historial multi-turno, evento final `metrics`, evento `error` genérico, Streamlit como cliente HTTP configurado con `ESTIMATOR_API_BASE_URL`, compose con API y Redis (Streamlit fuera), sin página HTML ni CORS, `POST /estimate` sin cambios y sin parámetros por petición en el SSE.

Versiones comprobadas en `.venv` y `uv.lock`: **fastapi 0.157.0**, **starlette 1.9.0**, **uvicorn 0.54.0**, **httpx 0.28.1**, **streamlit 1.66.1**, **litellm 1.106.4**. `sse-starlette` no está instalado.

## Goals / Non-Goals

**Goals:**

- Un endpoint SSE fino: valida, adapta `stream_chat` a eventos y traduce errores. Sin lógica de negocio en el router.
- Streamlit sin ningún import de `app`: solo HTTP. El comportamiento del chat (historial, Stop, truncado, respuesta vacía, "Nueva conversación") no cambia.
- Ninguna conexión cortada guarda en caché ni deja el stream del LLM abierto más de lo necesario.
- Una imagen reproducible (`uv.lock`), sin secrets y sin root, y un compose que se levanta con un comando.
- Tests sin red: ni LLM, ni Redis, ni servidor HTTP real.

**Non-Goals:**

- Llamadas async extremo a extremo, reanudación de streams (`id`/`Last-Event-ID`), varios workers.
- Límite total del historial o gestión de la ventana de contexto (S05+).
- Despliegue, registry, autenticación, rate limiting y CORS.

## Decisions

### D1. SSE nativo de FastAPI con un generador síncrono

- `@router.post("/estimate/stream", response_class=EventSourceResponse)` sobre una función **generadora síncrona** que hace `yield ServerSentEvent(...)`. Comprobado en `fastapi/routing.py` (`get_request_handler`, rama `is_sse_stream`):
  - El body y las dependencias se resuelven **antes** de llamar al generador, así que un body inválido da 422 JSON sin abrir el stream.
  - Un generador síncrono se itera con `starlette.concurrency.iterate_in_threadpool` (cada `next()` en un hilo de anyio), igual que el `POST /estimate` síncrono. No hace falta migrar el servicio a async.
  - Añade `Cache-Control: no-cache` y `X-Accel-Buffering: no`, y envía el comentario `: ping` tras 15 s sin eventos (`fastapi/sse.py`, `_PING_INTERVAL`, `KEEPALIVE_COMMENT`).
- La cadena síncrona de LiteLLM puede reanudarse desde hilos distintos: `Router._completion_streaming_iterator` es un generador síncrono (`router.py`, `stream_with_fallbacks`) sobre el cliente httpx síncrono, sin event loop propio entre chunks.
- Es lo que enseñan los apuntes del curso (`04_streaming-y-manejo-de-respuestas-largas.md`: "Desde la versión 0.135.0, FastAPI tiene soporte nativo para SSE").
- El SSE nativo **no cierra** el generador del endpoint cuando el cliente se desconecta (comprobado a mano, ver D4). El cierre es explícito: una dependencia con `yield`, `stream_closer()`, devuelve una lista en la que el endpoint registra su generador `estimate_stream_events(...)`, y su teardown llama a `close()`.
- *Alternativas descartadas*: `sse-starlette` como la referencia (una dependencia más para lo que FastAPI ya trae); `StreamingResponse` con el formato SSE escrito a mano (repetir `format_sse_event`, las cabeceras y el keep-alive, y además tampoco resuelve el cierre por sí solo); endpoint async con `run_in_executor` por fragmento como la referencia (más código y `GeneratorExit` más difícil de razonar; el SSE nativo ya lo hace por nosotros).

### D2. Contrato de eventos

| Evento | `data` | Cuándo |
|---|---|---|
| `token` | string JSON con el fragmento (`"## Estimación…"`) | cada fragmento no vacío |
| `metrics` | objeto JSON `EstimationMetrics` | una vez, al terminar bien (terminal) |
| `error` | `{"detail": "LLM provider error"}` | una vez, si falla (terminal) |

- **`token` en JSON** (`ServerSentEvent(event="token", data=fragment)`): FastAPI serializa con `json.dumps` y `ensure_ascii` por defecto (`routing.py`, `_serialize_sse_item`), así que cada fragmento ocupa **una sola línea `data:` ASCII**. Los saltos de línea del Markdown viajan como `\n` dentro del JSON y el cliente hace `json.loads`. Motivos: sin pérdidas (con `raw_data`, `_split_sse_lines` normaliza `\r\n` y `\r` a `\n`) y sin depender del troceo de líneas de httpx, que usa `str.splitlines` y parte también por ` `, `\x85`, `\x0c`… (`httpx/_decoders.py`, `LineDecoder`), algo que el SSE no hace. Coste: con `curl -N` los acentos se ven escapados (`\u00f3`). Decisión confirmada por el usuario.
- **`metrics`**: nuevo modelo `EstimationMetrics` en `schemas.py` con exactamente los campos de `EstimationResponse` menos `estimation`: `model`, `provider`, `input_tokens`, `output_tokens`, `cost_usd`, `truncated`, `fallback_used`, `cache_hit`, `latency_ms`. `EstimationResponse(EstimationMetrics)` añade `estimation`: un único sitio para los campos y sus descripciones. El JSON de `POST /estimate` mantiene los mismos campos y tipos; solo cambia el orden de las claves, que no forma parte del contrato. El mapeo `EstimationResult → campos` es una función del router compartida por los dos endpoints.
- **`error`**: el mismo `detail` que el 502 de `POST /estimate`. Nunca `str(exc)` (la referencia sí lo envía).
- **Sin evento `done`**: `metrics` y `error` son terminales y excluyentes. Un stream que termina sin ninguno de los dos es un corte, y el cliente lo trata como error. Desviación respecto a la referencia (que envía `done`), recogida en el proposal.
- Sin `id` ni `retry`: el cliente no es `EventSource`, el método es POST y no hay reanudación.

### D3. Request y validación (422 antes del stream)

```python
MessageText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TRANSCRIPTION_CHARS)
]


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: MessageText


class StreamEstimationRequest(BaseModel):
    messages: list[ConversationMessage]  # + model_validator (structure)
```

- `MessageText` es el mismo tipo que `EstimationRequest.transcription`, que pasa a usarlo (sin cambio de contrato). Así el límite de 20 000 caracteres sigue definido en un único sitio (`MAX_TRANSCRIPTION_CHARS` en `llm_service.py`).
- `model_validator(mode="after")`: lista no vacía, primer y último mensaje `user` y roles estrictamente alternos. Es justo lo que el chat garantiza hoy, y Anthropic fusionaría dos turnos `user` seguidos.
- Se recorta también el contenido de `assistant`, para usar una sola regla. En la práctica no cambia nada: las respuestas no suelen empezar ni terminar en espacios, y el chat las envía siempre igual, así que la clave de caché es estable entre turnos. Como el primer mensaje se recorta igual que `transcription`, una transcripción estimada por `POST /estimate` es un acierto de caché en el primer turno del SSE (D8 del change 1).
- El router convierte a `list[ChatMessage]` con `model_dump()`. *Alternativa descartada*: reutilizar el `TypedDict` `ChatMessage` de `services` como tipo del body (valida el rol pero no el contenido, y pondría restricciones HTTP en el dominio).
- Sin límite de número de mensajes (decisión del usuario; gestión del historial en S05+).

### D4. Errores, respuesta vacía, desconexión y caché

```python
def estimate_stream(request, service) -> Iterator[ServerSentEvent]:
    stream = service.stream_chat(request.chat_messages())
    sent = 0
    try:
        for fragment in stream:
            sent += 1
            yield ServerSentEvent(event="token", data=fragment)
    except GeneratorExit:
        logger.info("estimate_stream_cancelled", fragments=sent)
        raise
    except Exception as exc:  # LLMProviderError or a bug: the stream needs a terminal event
        if not isinstance(exc, LLMProviderError):
            logger.error("estimate_stream_failed", error_type=type(exc).__name__)
        yield ServerSentEvent(event="error", data={"detail": "LLM provider error"})
        return
    yield ServerSentEvent(event="metrics", data=_metrics(stream.result))
```

- **Código HTTP**: con un generador como endpoint no se puede devolver un 502 si el fallo llega antes del primer fragmento, porque el primer `next()` corre ya dentro de la respuesta (`_sse_producer_cm`). Por eso **todo** fallo del proveedor es `200` + evento `error`, antes o a mitad del stream. Para el cliente es un único camino.
- **Errores inesperados**: también se cierran con `error`, porque si una excepción escapa del generador FastAPI la propaga desde el task group de `_sse_producer_cm` y el cliente solo ve un corte. Se registra `error_type`, sin mensaje ni `exc_info`: el `ConsoleRenderer` de structlog puede mostrar variables locales, y entre ellas estarían los mensajes. `GeneratorExit` (que es `BaseException`) no se captura.
- **Respuesta vacía**: el servidor la transmite tal cual (`metrics` sin ningún `token`), igual que `POST /estimate` devuelve 200 con texto vacío. El cliente la trata como error (D6). La caché ya no guarda respuestas vacías (change 1).
- **Desconexión** (Stop en Streamlit, `curl` cortado): uvicorn 0.54 declara ASGI `spec_version` 2.3 (`httptools_impl.py`), así que `StreamingResponse` escucha `http.disconnect` en paralelo (`starlette/responses.py`, `listen_for_disconnect`). Al desconectar cancela el envío, y `_sse_producer_cm` (`fastapi/routing.py`) cancela el task group del productor mientras este espera en `send_stream.send`, **fuera** de nuestro generador. El generador queda suspendido en su `yield` y nadie lo cierra, tampoco el recolector: sin `estimate_stream_cancelled` y con el stream de LiteLLM y la conexión al proveedor abiertos (verificado con un repro en fastapi 0.157 / starlette 1.9 / uvicorn 0.54, sin LLM real; la caché sí estaba a salvo).
- **Cierre explícito** (aprobado por el usuario): `stream_closer()` es una dependencia con `yield` que entrega una lista; el endpoint registra en ella el generador `estimate_stream_events(...)` y lo devuelve. El teardown de la dependencia (exit stack de la petición) corre después de la respuesta, también al desconectar, cuando el task group del productor ya se canceló y el `next()` en curso en el hilo ya terminó (el hilo no se interrumpe: llega el siguiente chunk o salta `LLM_TIMEOUT`). Llama a `close()`: `GeneratorExit` registra `estimate_stream_cancelled` y recorre `EstimationStream` → `LLMStream` (`yield from`) → `CachedProvider` → `LiteLLMProvider`, cuyo `finally` cierra el stream de LiteLLM. El `set` de la caché va después del `yield from` y nunca se ejecuta. Cerrar un generador agotado no hace nada, así que un stream completo no registra nada. En el repro: corte a 1 s → `GeneratorExit` con 19 fragmentos; stream completo → sin log.
- Se prueba llamando directamente a la función generadora (consumir un evento, `close()`, el doble de proveedor se cerró y la caché no recibió `set`) y el teardown de `stream_closer()` (cierra lo registrado).
- *Alternativas descartadas*: confiar en el recolector (refutado: el generador no se cierra nunca); un wrapper async con `finally: close()` sobre `iterate_in_threadpool` (la cancelación cae en el productor, no dentro del wrapper, salvo que coincida con un `await` suyo, así que el `finally` no se alcanza); `StreamingResponse` a mano (repite el formato SSE, D1).

### D5. Endpoint de solo lectura para la barra lateral

`GET /api/v1/context?message_count=N` (`N ≥ 1`, por defecto 1) → `ContextResponse`:

| Campo | Tipo |
|---|---|
| `system_prompt` | `string`: el que usará una petición SSE con `N` mensajes |
| `examples` | lista de `{meeting_summary: string, estimation: string}` |
| `max_message_chars` | `integer` (20 000) |

- La regla de qué prompt toca (`len == 1` → prompt del endpoint) sigue en el servidor. La UI pide `message_count = len(historial) + 1`, como hace hoy `next_turn_system_prompt`.
- Para respetar `routers → services → context`, `llm_service.py` expone `prompt_context(message_count) -> PromptContext(system_prompt, examples)`, y `system_prompt_for(messages)` delega en la misma regla. El router no importa `app.context`.
- `max_message_chars` mantiene el requisito "mismo límite que la API, definido en un único sitio" sin que la UI importe `app`.
- Modelos y estado de la caché salen de `GET /health` (contrato del change 1), sin duplicarlos aquí.
- *Alternativas descartadas*: que la UI importe los constructores de prompt (arrastra `app/__init__`, `config` y LiteLLM al proceso de Streamlit y rompe "sin imports de `app/services`"); devolver los dos prompts y que la UI elija (duplica la regla de turno); meter el contexto en `/health` (lo mezcla con la sonda de vida del contenedor).

### D6. Streamlit como cliente HTTP

- **Configuración**: `ESTIMATOR_API_BASE_URL` desde `os.environ`; si no está, desde `.env` con `dotenv_values(".env")`, leyendo **solo** esa clave (no se cargan las API keys en el proceso). Por defecto `http://localhost:8000`. `streamlit_app.py` no importa nada de `app` ni `litellm`/`redis`/`openai`/`anthropic`; un test de imports (AST) lo comprueba.
- **Cliente**: siempre `httpx.Client(base_url=..., timeout=...)` en un `with` (nunca las funciones de módulo `httpx.get`/`httpx.stream`, que crean `Client` directamente desde `httpx._client` y saltarían el doble de los tests, ver D7). Timeouts: GET `httpx.Timeout(3.0)`; stream `httpx.Timeout(connect=3.0, read=45.0, write=10.0, pool=3.0)`. El `read` es mayor que los 15 s del ping, así que solo salta si el servidor muere, no por una generación lenta.
- **Parser SSE** (unas 25 líneas, en el propio fichero) sobre `response.iter_lines()`: acumula `event:` y `data:` (quita un único espacio inicial), ignora comentarios (`:`, el `: ping`) y despacha en línea vacía con `json.loads("\n".join(data))`. `token` → `yield` del texto. `metrics` → se guarda en un dict de resultado y termina. `error` → `StreamFailed`. Fin sin evento terminal → `StreamFailed`. Eventos desconocidos → se ignoran (compatibilidad hacia delante).
- **Errores en la interfaz**:
  - `httpx.TransportError` antes del primer evento (conexión rechazada, DNS, timeout de conexión) → `API_UNAVAILABLE_MESSAGE`: "No se puede conectar con el servicio de estimación (`<url>`). Arráncalo con `uv run uvicorn app.main:app` o `docker compose up`."
  - Respuesta no 200, `StreamFailed`, error de transporte a mitad o respuesta sin texto → el `PROVIDER_ERROR_MESSAGE` actual.
  - En todos los casos se mantiene la regla de S03: no se guarda nada y las métricas anteriores se conservan.
- **Stop**: el generador del parser abre el `Client` y el `stream` en bloques `with`, y `handle_user_message` lo consume dentro de `contextlib.closing(...)`. La excepción de control de Streamlit cierra el generador, el `with` cierra la respuesta (httpx cierra la conexión si el body no se ha leído entero) y el servidor recibe `http.disconnect` (D4). No se depende del recolector en el cliente.
- **Métricas**: `CallMetrics` se construye a partir del evento `metrics` más los tiempos del cliente (`ttft_s` hasta el primer `token` y `total_s` hasta el evento terminal, con `timed_fragments` sin cambios). `latency_ms` del servidor no se muestra: el tiempo total del cliente ya lo cubre.
- **Barra lateral**: se sigue pintando primero, y después de cada respuesta correcta se hace `st.rerun()`. En cada ejecución hace `GET /health` y `GET /api/v1/context?message_count=…`, sin `st.cache_data`: son dos peticiones locales de milisegundos, y una caché de Streamlit persistiría entre tests de `AppTest`. Muestra el primario, el respaldo (o "sin respaldo"), "Caché: disponible/no disponible", el prompt del siguiente turno y los ejemplos.
- **API caída**: la app **no** hace `st.stop()`. La barra lateral muestra `API_UNAVAILABLE_MESSAGE` en lugar de modelos y contexto, el historial y `st.chat_input` siguen visibles, y un envío muestra el mismo aviso. Así, un reinicio de uvicorn (`--reload`) no obliga a recargar la página, que perdería `st.session_state`. Sin `max_message_chars`, la UI solo rechaza los mensajes vacíos y deja el límite al 422 del servidor, que muestra el error genérico.
- **Error de configuración de la API**: la UI ya no puede detectarlo. La API no arranca (`lifespan`, fail-fast) y la UI muestra "no se puede conectar". Habrá que modificar el requisito "Configuración y API key desde el entorno" de `chat-ui` (ver informe).
- **Logs**: `structlog.get_logger` con la configuración por defecto (la app ya no llama a `configure_logging`, que vive en `app.config`). Eventos `api_unreachable` (`error_type`), `api_stream_failed` (`reason`) y `empty_llm_reply` (`model`, `provider`). Nunca contenido de mensajes.

### D7. Tests sin red

- **API SSE** con `TestClient` y `dependency_overrides[get_llm_provider]` (`FakeProvider`): se parsea el cuerpo `text/event-stream` con un helper de test. Cubren: orden de los eventos `token`* → `metrics`; un fragmento multilínea que llega íntegro; el payload de `metrics` (campos exactos); `error` antes y a mitad del stream sin detalles; respuesta vacía; los 422 (lista vacía, empieza o termina en `assistant`, roles no alternos, contenido en blanco, más de 20 000 caracteres) sin llamar al proveedor; el prompt por turno (`system_prompt_for`); y que OpenAPI documenta la ruta.
- **Desconexión** a nivel de función (D4): generador cerrado → `close` del doble registrado → la caché (`fakeredis`) sigue vacía; y teardown de `stream_closer()` → cierra el generador registrado.
- **`/api/v1/context`**: prompt para `message_count = 1` y `3`, ejemplos, límite y 422 con `0`.
- **Streamlit** con `AppTest` y `monkeypatch.setattr(httpx, "Client", factory)`. El script hace `import httpx` en cada ejecución y ve el atributo parcheado. Dos fábricas:
  - `httpx.Client(transport=httpx.MockTransport(handler))` para los casos del protocolo: comentarios `: ping`, stream sin terminal, evento `error`, 500, `httpx.ConnectError` lanzado por el handler, `/health` caído (barra lateral degradada y chat visible) y la petición enviada (cuerpo con `messages`).
  - `TestClient(app)` (es un `httpx.Client` con transporte ASGI) con `FakeProvider`, para uno o dos tests de extremo a extremo UI → API → servicio.
  - La fábrica se guarda antes de parchear (`real_client = httpx.Client`) para no llamarse a sí misma.
- El test de estructura añade `Dockerfile`, `.dockerignore` y `docker-compose.yml`.

### D8. Dependencias

- Sin librería SSE nueva (D1). `httpx` se usa en el código de la app (Streamlit) y sale del grupo `dev`. LiteLLM ya lo trae como transitiva, pero un import directo exige una dependencia declarada.
- **Imagen ligera** (decisión del usuario): `streamlit` y `httpx` pasan a un grupo `ui` (`[dependency-groups] ui = [...]`) incluido en `[tool.uv] default-groups = ["dev", "ui"]`. `uv sync` y la CI siguen instalándolo todo, y la imagen usa `uv sync --locked --no-default-groups`, que no instala `streamlit` (ni pandas, pyarrow…) en un contenedor que solo sirve la API. Revisa la D8 de S03 ("sin grupo opcional porque no hay despliegue"): ahora hay imagen. Comprobar las opciones con `uv sync --help` de la versión fijada.
- *Alternativa descartada*: `httpx` y `streamlit` en runtime (más simple, pero la imagen crece en cientos de MB).

### D9. Imagen Docker

- Dos etapas sobre `python:3.12-slim` (misma etiqueta exacta en ambas, porque los shebangs del venv apuntan a `/app/.venv` y a `/usr/local/bin/python`). El builder copia `uv` de `ghcr.io/astral-sh/uv:<versión>` con la versión exacta usada en local (nunca `latest`, que usa la referencia). Primero `COPY pyproject.toml uv.lock`, después `uv sync --locked --no-default-groups --no-install-project` (la capa de dependencias se cachea), y luego `COPY app/`. Con `UV_COMPILE_BYTECODE=1`, `UV_LINK_MODE=copy` y `UV_PYTHON_DOWNLOADS=0`.
- Runtime: solo `/app/.venv` y `/app/app`, propiedad de root y solo lectura para el usuario `app` (UID fijo 10001, sin shell), con `USER app`. `PATH` al venv, `PYTHONUNBUFFERED=1`, `PYTHONDONTWRITEBYTECODE=1`, `EXPOSE 8000` y `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`, con un solo worker.
- `HEALTHCHECK` con `python -c "urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"` (la imagen slim no trae `curl`). `/health` responde 200 aunque Redis esté caído: la sonda indica que el proceso está vivo, no que la caché funcione.
- Ningún secret en la imagen: `Settings` lee el entorno y en el contenedor no hay `.env`. `LITELLM_LOCAL_MODEL_COST_MAP` lo fija `app/__init__.py`, así que el arranque no descarga nada.
- **`.dockerignore` en modo lista blanca**: `*`, `!pyproject.toml`, `!uv.lock`, `!app/` y `**/__pycache__`. Un fichero nuevo (un `.env.local`, volcados, `samples/`) queda fuera por defecto. *Alternativa descartada*: la lista negra de la referencia (olvidar una entrada filtra un secret a la imagen).

### D10. docker-compose

- Servicio `api`: `build: .`, `env_file: .env`, `environment: REDIS_URL: redis://redis:6379/0` (que tiene precedencia sobre `env_file`, porque el `.env` local apunta a `localhost`), `depends_on: redis: condition: service_healthy`, y `ports: "127.0.0.1:8000:8000"`. El healthcheck es el de la imagen.
- Servicio `redis`: `redis:7.4-alpine` (etiqueta de versión menor), `command: redis-server --save "" --appendonly no` y sin volumen, porque es una caché con texto derivado de reuniones de clientes y no conviene que persista en disco. Healthcheck `redis-cli ping`, `ports: "127.0.0.1:6379:6379"` para el modo híbrido (`docker compose up redis` + uvicorn local con `--reload`).
- Puertos ligados a `127.0.0.1`: Streamlit corre en el host y no hace falta exponerlos a la red local.
- **Sin bind-mount ni `--reload`** (la referencia los tiene): el bucle de desarrollo es uvicorn local contra el Redis del compose. Montar `app/` sobre una imagen de solo lectura y usuario no root mezcla dos modos y oculta errores de build.

### D11. CI

- Un job `docker` en paralelo al actual: `docker build .` (sin push) y `docker compose config --quiet` (valida la sintaxis y las variables del compose). Cubre lo que más se rompe: un `uv.lock` desincronizado o un fichero que falta en la lista blanca. Para `compose config` en CI se crea un `.env` vacío antes, porque `env_file` lo exige. *Alternativas*: sin job de Docker (un Dockerfile roto pasaría desapercibido hasta la entrega); arrancar el contenedor y sondear `/health` con una key ficticia (más fiable, pero con más pasos y más tiempo de CI).

### D12. Cómo habilita los siguientes changes

- **Change 3 (parámetros y evaluación, solo en `POST /estimate`)**: `StreamEstimationRequest` es un schema separado, así que añadir campos a `EstimationRequest` no toca el SSE. `EstimationResponse` puede añadir `validation` sin afectar a `EstimationMetrics`. El SSE sigue llamando a `stream_chat` con los valores por defecto.
- **S04 (formulario y Jinja2)**: el cliente HTTP de Streamlit (URL base, `httpx.Client`, mapeo de errores, barra lateral desde `/health`) se reutiliza tal cual. El formulario añadirá un `POST /estimate` junto al stream. `prompt_context` es el punto donde se renderizará el template, y el contrato de `/api/v1/context` (un `system_prompt` string) no cambia. Si el formulario sustituye al chat, el endpoint SSE multi-turno puede retirarse o reducirse a un único mensaje sin tocar el resto.

## Risks / Trade-offs

- [Tras una desconexión, el cierre del stream del LLM es explícito (teardown de `stream_closer()`), pero espera a que termine el `next()` en curso: hasta el siguiente chunk o `LLM_TIMEOUT` sin chunks] → El texto ya generado se descarta y la caché no guarda nada. Se prueba a nivel de función y de dependencia, y se verifica a mano con el log `estimate_stream_cancelled`. Depende de un detalle interno de FastAPI (el productor SSE no cierra el generador); si una versión futura lo cierra, `close()` sobre un generador cerrado no hace nada.
- [Cada stream activo ocupa un hilo del pool de anyio (40 por defecto), compartido con `POST /estimate`] → Suficiente para uso local con un solo usuario. Async queda fuera de alcance.
- [Sin límite de mensajes, un body puede ser grande (N × 20 000 caracteres)] → Uso local, sin exposición pública (puertos en `127.0.0.1`). El proveedor rechaza el exceso de contexto, que llega como `error`.
- [`/api/v1/context` expone el system prompt sin autenticación] → Hoy ya se ve en la UI. Antes de cualquier despliegue habría que revisarlo (fuera de alcance).
- [`env_file` deja las API keys en el entorno del contenedor (visibles con `docker inspect`)] → Aceptable en local. Docker secrets o un gestor de secretos quedan para un despliegue real.
- [Uso local con dos procesos (**BREAKING**)] → README con los dos modos (uvicorn + streamlit, o compose + streamlit) y un aviso claro en la UI si la API no responde.
- [Usuario no root con el código en solo lectura: una librería que escriba en `HOME` o en `site-packages` fallaría] → La verificación manual incluye una estimación real dentro del contenedor.
- [Doble parcheo de `httpx.Client` en los tests: frágil si el script usa las funciones de módulo de httpx] → Regla en D6 y test que falla si se hace una petición sin el doble (el `MockTransport` registra todas las peticiones).

## Migration Plan

1. Requiere `s03-add-llm-wrapper-cache-logging` implementado y archivado (contratos de `EstimationResult` y `/health`).
2. Orden de implementación: dependencias (D8) → `prompt_context` en servicios (`ai-engineer`) → schemas, router SSE y `/api/v1/context` (`backend-engineer`) → `streamlit_app.py` (`frontend-engineer`) → `Dockerfile`, `.dockerignore`, `docker-compose.yml` y el job de CI (`backend-engineer`) → tests (`qa-engineer`) → README y `.env.example` (`ESTIMATOR_API_BASE_URL`, nota sobre `REDIS_URL` en compose).
3. Verificación manual: `curl -N` contra el SSE (fragmentos, `metrics`, `error` con una key inválida y sin respaldo); Stop en Streamlit, con `estimate_stream_cancelled` en el log y la caché sin la entrada; API parada con la UI abierta; `docker compose up` con `/health` (`cache: "ok"`), una estimación y un acierto de caché desde Streamlit en el host.
4. Rollback: revertir los commits del change. `POST /estimate` no cambia para sus clientes.

## Open Questions

Resueltas por el usuario el 2026-11-09 (todas con la opción recomendada):

1. **Grupo `ui`**: `streamlit` y `httpx` pasan a un dependency group `ui` incluido en `[tool.uv] default-groups`. La imagen usa `uv sync --locked --no-default-groups` y no los instala (D8). Revisa la D8 de S03.
2. **Número de mensajes del SSE**: sin límite en este change; la gestión del historial queda para S05+ (D3).
3. **Docker en CI**: job paralelo con `docker build` y `docker compose config --quiet`, sin arrancar contenedores (D11).
4. **Evento `token`**: string JSON (D2).
5. **Endpoint de contexto**: `GET /api/v1/context?message_count=N` con `system_prompt`, `examples` y `max_message_chars` (D5).
