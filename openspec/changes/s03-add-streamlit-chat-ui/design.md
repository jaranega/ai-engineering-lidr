## Context

Punto de partida: la rama `session-02`, ya mergeada en `main`. El servicio FastAPI expone `POST /api/v1/estimate` y toda la lógica vive en `app/services/llm_service.py`:

- `build_system_prompt(examples)` + `_system_prompt()` (cacheado) + `build_estimation_prompt(transcription) -> (system, user)`.
- `LLMProvider` (Protocol) con `generate(system, user) -> LLMResult`, implementado por `OpenAIProvider` y `AnthropicProvider` con el cliente del SDK inyectado y los errores envueltos en `LLMProviderError`.
- `build_provider(name, settings)` y `get_llm_provider()` (`lru_cache`), construibles sin FastAPI.
- `EstimationService.estimate(transcription) -> EstimationResult` (con `cost_usd`).
- La configuración de logging (incluido subir a INFO los loggers de los SDK) está dentro del `lifespan` de `app/main.py`.

El diseño de S02 ya preveía S03: el servicio no depende de FastAPI ni de `schemas.py` (D6 de S02), el system prompt y los ejemplos se pueden mostrar sin FastAPI (D5 de S02) y añadir `stream()` queda localizado en los proveedores (D2 de S02).

Decisiones del usuario previas a este diseño (2026-10-27):

1. Integración **in-process**: Streamlit importa `app/services`; no pasa por HTTP.
2. **Conversación multi-turno**: el historial se envía al LLM.
3. Se incluye el **Nivel 3** (sidebar con contexto y métricas).
4. Se crea el subagente **`frontend-engineer`**.
5. **System prompt del chat = prompt del endpoint + bloque de conversación**; el endpoint no cambia.

La nota del enunciado deja para la sesión en vivo el wrapper con fallback, la caché y el logging/trazabilidad: el diseño tiene que **habilitarlos** sin implementarlos.

## Goals / Non-Goals

**Goals:**

- Los niveles 1, 2 y 3 del enunciado funcionando con OpenAI y Anthropic.
- Una única fuente de verdad para el prompt, los proveedores, la configuración y el coste: la UI no duplica lógica de `app/services`.
- Streaming en la capa de proveedores, con el uso de tokens al final, de forma que la sesión en vivo pueda envolverlo (fallback, caché, trazas) sin rediseñar la interfaz.
- El contrato HTTP de la API, intacto.
- La app Streamlit testeada sin navegador ni red.

**Non-Goals:**

- Fallback entre proveedores, caché, structlog (sesión en vivo).
- Endpoint SSE de streaming y migración a clientes async.
- Gestión de la ventana de contexto (recorte o resumen del historial), persistencia de conversaciones, autenticación y despliegue.

## Decisions

### D1. Integración in-process

`streamlit_app.py` importa de `app.config` y `app.services.llm_service` y llama al servicio en el mismo proceso.

- *Por qué*: el enunciado pide "reutilizar la lógica de llamada que ya tienes". No obliga a arrancar uvicorn para usar la UI, y no añade un endpoint SSE ni un cliente HTTP.
- *Alternativa descartada*: UI → HTTP → FastAPI. Más desacoplada (la UI podría desplegarse aparte), pero requiere un endpoint de streaming nuevo, un cliente HTTP en la UI y tener dos procesos levantados. Si algún día se quiere, el servicio de streaming de D4 es lo que expondría ese endpoint.
- La app **no** importa los SDKs `openai`/`anthropic`, igual que los routers.

### D2. Interfaz de proveedor con lista de mensajes

```python
class ChatMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str


class LLMProvider(Protocol):
    def generate(self, system: str, messages: Sequence[ChatMessage]) -> LLMResult: ...
    def stream(self, system: str, messages: Sequence[ChatMessage]) -> LLMStream: ...
```

- OpenAI: `messages=[{"role": "system", "content": system}, *messages]`. Anthropic: `system=system, messages=list(messages)`.
- Los mensajes se pasan sin modificar; el proveedor no valida la alternancia (es responsabilidad de quien construye la conversación, ver D7).
- `build_estimation_prompt(transcription)` pasa a devolver `(system, [{"role": "user", "content": transcription}])`, así que `EstimationService.estimate()` y el endpoint mantienen el comportamiento y el contrato.
- *Alternativas descartadas*:
  - Mantener `generate(system, user)` y añadir `generate_chat(system, messages)`: dos métodos que hacen lo mismo, y el doble de superficie para el wrapper de fallback de la sesión en vivo.
  - Mantener `(system, user)` y aplanar el historial en un único `user`: rompe el patrón de roles que pide la spec `cag-prompt` y mezcla las estimaciones previas del asistente con la entrada del usuario.
- `ChatMessage` como `TypedDict` (y no dataclass) porque coincide con la forma que esperan ambos SDK y con cómo se guarda el historial en `st.session_state`, sin conversiones.

### D3. Streaming en los proveedores: `LLMStream`

`stream()` devuelve un `LLMStream`: un objeto **iterable de `str`** (los fragmentos de texto) que, cuando se ha consumido por completo, expone `.result: LLMResult` con el texto concatenado y los tokens.

```python
class LLMStream:
    def __iter__(self) -> Iterator[str]: ...
    @property
    def result(self) -> LLMResult: ...  # RuntimeError if the stream was not fully consumed
```

- Implementación: cada proveedor tiene un generador privado `Generator[str, None, LLMResult]` que hace la llamada con `stream=True`, emite los deltas de texto, acumula el uso y **devuelve** el `LLMResult` con `return`. `LLMStream` lo envuelve y captura ese valor con `self._result = yield from gen` dentro de su `__iter__`. Así la interfaz pública sigue siendo "iterar + `.result`", y las piezas de la sesión en vivo (fallback, caché) pueden encadenar generadores con `yield from` sin acceder a internos.
- **OpenAI**: `chat.completions.create(..., stream=True, stream_options={"include_usage": True})`. Los chunks con `choices[0].delta.content` aportan texto; el último chunk trae `usage` y `choices` vacío (hay que comprobar `chunk.choices` antes de indexar). Sin chunk de uso → 0 tokens, como en `generate` (decisión 20 de S02). `finish_reason == "length"` → warning.
- **Anthropic**: `messages.create(..., stream=True)` con eventos crudos: `message_start` (`message.usage.input_tokens`), `content_block_delta` con `delta.type == "text_delta"` (texto) y `message_delta` (`usage.output_tokens`, `delta.stop_reason`). `stop_reason == "max_tokens"` → warning. El `usage.output_tokens` de `message_delta` es **acumulado**: se asigna, no se suma.
- Ambos proveedores emiten **solo fragmentos no vacíos**: el primer chunk de OpenAI suele traer `delta.role` con `content` vacío o `None`, y emitirlo falsearía el tiempo hasta el primer fragmento que mide la UI.
- **Errores**: la llamada inicial convierte `openai.OpenAIError`/`anthropic.AnthropicError` en `LLMProviderError(provider) from exc`, con el mismo log de tipo + status. Durante la **iteración** se envuelve **cualquier `Exception`** en `LLMProviderError` (decisión confirmada 6), por el motivo que sigue.
  - Comprobado en los SDK instalados: en **openai 3.35.1**, `Stream._iter_events` convierte los errores de transporte a mitad del stream en `APITimeoutError`/`APIConnectionError` (subclases de `OpenAIError`), y un evento de error del servidor en `APIError`. En **anthropic 1.17.0**, un evento `error` se convierte en `APIStatusError`, pero `Stream._iter_events` **no** envuelve los errores de transporte (`httpx2.ReadTimeout`, `httpx2.RemoteProtocolError`…) ni un JSON malformado: llegan al consumidor sin envolver, así que `except anthropic.AnthropicError` no los captura. *Alternativas descartadas*: capturar además `httpx2.TransportError` (acopla el código a una dependencia transitiva y no cubre el JSON malformado) y dejarlo así (un corte de red mostraría la traza en la UI).
- La llamada al SDK se hace al **empezar a iterar**, no al llamar a `stream()`: construir el objeto no hace red, lo que simplifica los tests y permite medir el tiempo hasta el primer fragmento desde la UI.
- **Cierre del stream**: un `finally` llama a `close()` sobre la respuesta del SDK (si existe) al terminar la iteración por cualquier motivo. Así se libera la conexión HTTP cuando el consumidor corta antes de tiempo (el *Stop* de Streamlit cierra el generador con `GeneratorExit`, que no es `Exception` y no se envuelve). Añadido durante la implementación y aprobado por el usuario (decisión confirmada 13).
- *Alternativas descartadas*:
  - Exponer directamente como API pública el generador que devuelve el `LLMResult` con `return` (valor en `StopIteration`): `st.write_stream` descarta ese valor y cada consumidor tendría que recuperarlo a mano. Se usa como mecanismo interno de `LLMStream`, no como interfaz.
  - Callback `on_complete(result)`: invierte el control y complica la composición (fallback, caché).
  - Helper `client.messages.stream()` de Anthropic: es un context manager con su propia API; con `create(stream=True)` ambos proveedores siguen el mismo esquema (iterar eventos) y los stubs de test son simples secuencias de objetos.
- **Habilita la sesión en vivo**: un `FallbackProvider` puede reintentar con otro proveedor si falla antes del primer fragmento; una caché puede devolver un `LLMStream` que reproduce el texto guardado, y su clave puede incluir la lista completa de mensajes porque `generate` y `stream` reciben `(system, messages)`; las trazas pueden leer `.result` al terminar. La nota 02 del curso usa **LiteLLM**, que hace streaming con el formato de chunks de OpenAI: encajaría como otra implementación de `LLMProvider` que reutiliza el parseo de `OpenAIProvider`.

### D4. Servicio: `EstimationService.stream_chat`

```python
class EstimationService:
    def estimate(self, transcription: str) -> EstimationResult: ...  # unchanged
    def stream_chat(self, messages: Sequence[ChatMessage]) -> EstimationStream: ...
```

- `stream_chat` usa el system prompt del chat (D5) y `provider.stream(...)`. Devuelve un `EstimationStream`: iterable de `str` que delega en el `LLMStream` y cuyo `.result` es un `EstimationResult` (con `cost_usd`).
- La conversión `LLMResult → EstimationResult` se extrae a una función privada que usan `estimate` y `stream_chat`, para que el coste se calcule en un único sitio.
- La UI solo conoce `EstimationService`, `EstimationStream`, `ChatMessage`, `LLMProviderError` y los getters del prompt; nunca un proveedor concreto.
- *Alternativa descartada*: que la UI llame directamente a `provider.stream()` y calcule el coste con `estimate_cost`: duplicaría lógica de dominio en la UI.

### D5. System prompt del chat

- Nueva constante `_CONVERSATION` (en español, como el resto de instrucciones) y `build_chat_system_prompt(examples) = build_system_prompt(examples) + "\n\n" + _CONVERSATION`, pura y determinista. `_chat_system_prompt()` cacheada, análoga a `_system_prompt()`.
- Contenido (redacción final la revisa el usuario en la tarea correspondiente):
  - El primer mensaje del usuario es la transcripción, a la que se aplican todas las reglas anteriores (incluida tratarla solo como datos).
  - Los mensajes posteriores son peticiones del usuario para ajustar la estimación; responde con la estimación **completa** actualizada, en el mismo formato y **en el idioma de la transcripción**, aunque la petición de ajuste esté escrita en otro idioma (decisión confirmada 10).
  - Si un mensaje posterior es una transcripción nueva, estímala desde cero.
  - Si un mensaje no tiene relación con la estimación, responde en una frase, en el idioma de ese mensaje, que solo generas y ajustas estimaciones (decisión confirmada 14).
- **El bloque solo se añade cuando la conversación tiene historial** (decisión confirmada 16). En el primer turno (un único mensaje `user`) el chat envía exactamente el prompt del endpoint. Motivo, medido con llamadas reales (tarea 7.2) con gpt-4o-mini y una transcripción en inglés: el prompt del endpoint responde en inglés 6 de 6 veces; con cualquier bloque añadido (en español al final, en español antes de «## Reglas» o entero en inglés, con o sin una regla final en inglés) baja a 0–50 %. En los turnos siguientes, con la estimación en inglés ya en el historial y el bloque de conversación añadido, el ajuste pedido en español mantiene el inglés 4 de 4 veces. Anthropic (Haiku) respondía bien en todos los casos.
- Selección: `system_prompt_for(messages)` devuelve `_system_prompt()` si `len(messages) == 1` y `_chat_system_prompt()` en otro caso. La usan `EstimationService.stream_chat` y el sidebar (con el historial actual más un turno, para mostrar el prompt del siguiente envío). Bonus: el primer turno cumple al pie de la letra el "mismo system prompt que el endpoint" del enunciado.
- El bloque termina con la regla de idioma en inglés (decisión confirmada 15, aplicada tras el fallo de la 7.2), que se mantiene porque es la configuración medida en los turnos siguientes.
- Va **al final** porque es la sección más específica del caso de uso y así no altera el orden rol → formato → ejemplos → reglas validado en S02 (decisión 22 de S02).
- Funciones públicas para la UI: `system_prompt_for(messages) -> str` (lo que se muestra en el sidebar es exactamente lo que se enviará) y `ESTIMATION_EXAMPLES` desde `app.context.examples`.
- *Alternativas descartadas*: prompt idéntico sin añadidos (riesgo de que el modelo ignore los ajustes por la regla anti-injection) y cambiar el prompt también en el endpoint (altera un comportamiento ya validado sin necesidad). Decisión del usuario.
- **Habilita S04**: cuando el prompt pase a Jinja2 versionado, el bloque de conversación será un template más (`conversation.j2`) incluido solo por el chat.

### D6. Logging compartido

- Se extrae del `lifespan` una función `configure_logging(settings)` en `app/config.py` (`basicConfig(level=...)` + loggers de SDK y HTTP a `max(INFO, LOG_LEVEL)`), y la constante `NOISY_LOGGERS` se mueve con ella.
- El `lifespan` y `streamlit_app.py` la llaman. Streamlit re-ejecuta el script en cada interacción: `basicConfig` no hace nada si ya hay handlers y `setLevel` es idempotente, así que repetirla es inocuo.
- *Alternativa descartada*: duplicar la configuración en la app Streamlit: dos sitios que mantener justo antes de que la sesión en vivo los sustituya por structlog.
- **Habilita la sesión en vivo**: structlog se configurará en esa misma función.

### D7. Estructura de `streamlit_app.py`

Un único fichero, como pide el enunciado, organizado en funciones pequeñas:

- `st.set_page_config(...)` y título.
- **Arranque**: `get_settings()` y `get_llm_provider()` dentro de un `try`; si hay `ValidationError`, `st.error(format_config_error(exc))` y `st.stop()`. Después, `configure_logging(settings)`. El proveedor se obtiene con `get_llm_provider()` (`lru_cache` por proceso, compartido entre sesiones: los clientes de los SDK son thread-safe). *Alternativa*: `st.cache_resource`; equivalente, pero `lru_cache` ya existe y los tests lo sustituyen igual que en la API.
- **Estado** (`st.session_state`): `messages: list[ChatMessage]` y `last_metrics: CallMetrics | None` (dataclass pequeña en el propio fichero: modelo, proveedor, tokens, coste, `ttft_s`, `total_s`).
- **Sidebar**: proveedor y modelo activos (de `Settings`: `llm_provider` y `effective_model`); botón "Nueva conversación" (vacía `messages` y `last_metrics`) **con `on_click`**: como el sidebar se renderiza al final del script, comprobar `if st.button(...)` allí dejaría pintado el historial antiguo en esa misma ejecución, mientras que el callback se ejecuta antes del rerun y el chat ya sale vacío; system prompt del chat en `st.text_area(..., disabled=True)` dentro de un `st.expander`; un expander por ejemplo CAG con `meeting_summary` y `estimation`; métricas de la última llamada con `st.metric` o "Aún no hay métricas".
- **Chat**: se repinta el historial; `st.chat_input` recibe el mensaje; se **recorta** (`strip`, como la API) y se valida (en blanco o > `MAX_TRANSCRIPTION_CHARS`, importado de `app.services.llm_service`); el mensaje recortado se pinta como `user` desde una **variable local**, sin añadirlo todavía a `st.session_state.messages`; dentro de `st.chat_message("assistant")`, `st.write_stream(...)` consume un generador que mide el tiempo hasta el primer fragmento y el total (`time.perf_counter()`) mientras itera `EstimationService.stream_chat([*historial, mensaje_nuevo])`.
- **Fin correcto**: solo entonces se añaden al historial, juntos, el mensaje `user` y el texto completo como `assistant`, y se guardan las métricas a partir de `.result` y los tiempos. Después se llama a `st.rerun()` para que el sidebar muestre las métricas y el prompt nuevos.
- **El sidebar se renderiza al principio del script** (decisión confirmada 17). En la verificación manual (7.2) se vio que, con el sidebar al final, el *Stop* cortaba el script antes de pintarlo y Streamlit lo borraba de la pantalla. Pintado al principio, una interrupción ya no lo afecta; el `st.rerun()` tras una respuesta correcta lo actualiza (una re-ejecución sin llamadas al LLM). *Alternativa descartada*: sidebar al principio con huecos (`st.empty`) que se rellenan al terminar; evita el rerun pero complica el código.
- **Error** (`LLMProviderError`): `st.error("No se ha podido generar la estimación. Inténtalo de nuevo.")`; como el historial no se ha tocado, no hay nada que retirar y no se guardan ni el mensaje, ni la respuesta parcial, ni las métricas.
- **Interrupción** (botón *Stop* o nuevo envío durante el streaming): Streamlit corta la ejecución con su excepción de control, que no pasa por la rama de error; como el historial solo se actualiza al terminar bien, tampoco queda un mensaje `user` sin respuesta (decisión confirmada 7).
- Con este esquema el historial siempre alterna `user`/`assistant` y el usuario puede reenviar el mensaje sin duplicarlo (la API de Anthropic combina en un único turno los mensajes consecutivos del mismo rol, así que el mensaje fallido y el reenviado llegarían fusionados).
- Textos de la interfaz en español (idioma del producto); código y comentarios en inglés.
- *Por qué medir los tiempos en la UI y no en el servicio*: es una métrica de presentación del Nivel 3 y la observabilidad de verdad (latencia, trazas) es contenido de la sesión en vivo; medir en el servicio ahora sería adelantarla.
- *Por qué tiempo hasta el primer fragmento*: es la métrica que justifica el streaming (latencia percibida) y cuesta una línea.

### D8. Dependencia `streamlit`

- `uv add streamlit` como dependencia de **runtime**: `streamlit_app.py` es un entregable del proyecto, igual que la API.
- *Alternativa descartada*: grupo opcional (`[dependency-groups] ui` o extra) para no instalarlo en un despliegue solo-API. Hoy no hay despliegue y complicaría el arranque (`uv sync --group ui`).
- `.streamlit/config.toml` con `[browser] gatherUsageStats = false`, para que Streamlit no envíe estadísticas de uso (decisión confirmada 11). `client.showErrorDetails` se deja con su valor por defecto: los errores esperados ya se muestran sin traza (`LLMProviderError` y configuración), y ver la traza de un error inesperado ayuda en desarrollo.

### D9. Tests

- **Proveedores**: stubs de cliente que devuelven secuencias de objetos (`SimpleNamespace`) con la forma de los chunks de OpenAI y los eventos de Anthropic. Se verifica: kwargs enviados (`stream=True`, `stream_options` en OpenAI, `system=` en Anthropic, mensajes en orden), fragmentos en orden, `.result` (texto y tokens), `.result` antes de terminar → `RuntimeError`, uso ausente → 0 tokens, error del SDK al crear y a mitad de iteración → `LLMProviderError` con `__cause__`, warning por truncado.
- **Prompt**: el del chat empieza por el del endpoint, contiene la sección de conversación, es determinista, y el del endpoint no la contiene.
- **Servicio**: `stream_chat` usa el prompt del chat, pasa los mensajes sin modificar y su `.result` incluye `cost_usd`; `estimate` sigue enviando un único mensaje `user`.
- **App Streamlit** con `streamlit.testing.v1.AppTest.from_file(<ruta absoluta>)`:
  - El `FakeProvider` de `conftest.py` gana `stream()` (fragmentos configurables, error opcional tras N fragmentos) y registra los mensajes recibidos.
  - `AppTest` ejecuta el script en el mismo proceso (en un hilo propio) y cada ejecución vuelve a hacer `from app.services.llm_service import ...`, así que lee los atributos parcheados del módulo. Se parchea **`llm_service.build_provider`** (`lambda name, settings: fake`) y no `get_llm_provider`: la fixture autouse `_clear_cached_state` llama a `get_llm_provider.cache_clear()` en su teardown, que pytest ejecuta **antes** de deshacer el `monkeypatch` (este se monta primero, vía `_isolated_env`), y una `lambda` no tiene `cache_clear` → error en el teardown. Parcheando `build_provider`, el `get_llm_provider` real (con su `lru_cache`, que se limpia entre tests) devuelve el doble.
  - La fixture autouse `_isolated_env` **borra** las variables de configuración y cambia el cwd a un directorio vacío, así que cada test de la app define las suyas (p. ej. `monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-ui")`) para que `Settings` sea válido; el test de configuración inválida simplemente no las define. `_clear_caches()` debe limpiar también `_chat_system_prompt`.
  - Casos: respuesta añadida al historial, segundo turno con los 3 mensajes enviados, mensaje recortado antes de enviarlo, mensaje vacío y demasiado largo, error del proveedor al crear y a mitad (historial intacto, `st.error` y métricas anteriores conservadas), configuración inválida (`st.error` con la variable y sin `chat_input`), sidebar con prompt, ejemplos y métricas, botón "Nueva conversación", ninguna key en la interfaz y `streamlit_app.py` sin imports de `openai`/`anthropic`.
  - `AppTest.from_file(..., default_timeout=10)`: el timeout por defecto (3 s) puede quedarse corto en CI.
  - `AppTest` solo ve el texto final de `st.write_stream`; "Texto incremental" y "Arranque de la interfaz" se verifican a mano.
- **Proveedores (errores)**: además del error del SDK, un error que no es del SDK a mitad del stream (p. ej. `RuntimeError` desde el stub) acaba en `LLMProviderError`.
- Los tests existentes que usan `generate(system, user)` se adaptan a la nueva firma, y `tests/test_logging.py` importa `NOISY_LOGGERS` de `app.config`.

### D10. Harness: subagente `frontend-engineer`

- `.claude/agents/frontend-engineer.md`, modelo **sonnet**, con la misma estructura que los demás agentes. Rutas propias: `streamlit_app.py` y `.streamlit/`. Fuera de su alcance: `app/` (backend/ai), `tests/` (qa).
- Reglas: la UI no contiene lógica de negocio ni importa SDKs de LLM; solo consume `app/services`; nunca muestra ni registra keys; textos de interfaz en español.
- Se actualizan la tabla de agentes y el roadmap del harness en `CLAUDE.md` (el `frontend-engineer` deja de ser "S03 pendiente").

### D11. Reparto de tareas por agente

| Capa | Agente |
|---|---|
| `pyproject.toml`/`uv.lock` (streamlit), `app/config.py` y `app/main.py` (logging), `app/schemas.py` (importar `MAX_TRANSCRIPTION_CHARS`) | `backend-engineer` |
| `app/services/llm_service.py` (mensajes, streaming, prompt del chat, servicio, `MAX_TRANSCRIPTION_CHARS`) | `ai-engineer` |
| `streamlit_app.py`, `.streamlit/config.toml` | `frontend-engineer` |
| `tests/` | `qa-engineer` |
| `README.md`, `CLAUDE.md`, `.claude/agents/frontend-engineer.md` | orquestador |

### D12. Grafo de dependencias

```
streamlit_app.py ──► app.config (get_settings, format_config_error, configure_logging)
                 ──► app.services.llm_service (get_llm_provider, EstimationService,
                                               ChatMessage, LLMProviderError, system_prompt_for,
                                               MAX_TRANSCRIPTION_CHARS)
                 ──► app.context.examples (ESTIMATION_EXAMPLES)
routers ──► services ──► context        (sin cambios)
schemas ──► services                    (MAX_TRANSCRIPTION_CHARS)
```

- `MAX_TRANSCRIPTION_CHARS` se mueve de `app/schemas.py` a `app/services/llm_service.py` como límite de dominio, y `schemas.py` lo importa de allí (decisión confirmada 9). La UI no depende del contrato HTTP, que cambia en S04. `tests/test_api.py` lo sigue importando de `app.schemas` sin cambios.

## Risks / Trade-offs

- [El historial crece sin límite y cada turno reenvía transcripción + estimaciones: más tokens, más coste y, en conversaciones largas, la ventana de contexto] → Límite de 20 000 caracteres por mensaje, coste visible en el sidebar y botón "Nueva conversación". La gestión de memoria es contenido de S05+.
- [El bloque de conversación debilita la defensa básica contra prompt injection: los mensajes posteriores sí se tratan como instrucciones] → La regla "solo datos" se mantiene para la transcripción; los seguimientos los escribe el mismo usuario de la sesión, que ya controla la entrada. Los guardrails llegan en S04+.
- [El modelo puede no respetar el bloque de conversación (p. ej. responder solo el delta en lugar de la estimación completa)] → Verificación manual con ambos proveedores y una conversación de ajuste antes de entregar.
- [`st.write_stream` y `AppTest` cambian entre versiones de Streamlit] → Versión fijada en `uv.lock`; la tarea de tests verifica la API de `AppTest` contra la versión instalada.
- [Cambio de firma de `generate`: rompe cualquier código o test que use `(system, user)`] → Es interno (no hay consumidores externos); se adaptan todos los usos en el mismo change y la CI lo comprueba.
- [`.env` se resuelve contra el directorio actual] → El README indica ejecutar `streamlit run` desde la raíz del repo, igual que uvicorn.
- [Respuesta parcial visible en pantalla si falla a mitad] → Al error se muestra el aviso; en el siguiente rerun el texto parcial desaparece porque no está en el historial.
- [Envolver cualquier `Exception` durante la iteración puede ocultar un bug propio como "error del proveedor"] → El log conserva el tipo de excepción y la original queda como `__cause__`.
- [Con `LOG_LEVEL=DEBUG`, `basicConfig` en Streamlit puede llenar la terminal con logs de `watchdog`/`tornado`] → Se comprueba en la tarea 4.7 y, si molesta, se añaden a `NOISY_LOGGERS`.
- [`format_config_error` devuelve el mensaje en inglés dentro de una UI en español] → Aceptado: la spec pide el mismo mensaje que la API.
- [Llamadas síncronas: cada sesión de Streamlit ocupa su hilo durante la generación] → Aceptable para uso local (decisión confirmada 1).

## Migration Plan

- Rama `session-03` desde `main`. El contrato HTTP no cambia, así que no hay migración para consumidores de la API.
- Entrega: push de `session-03`, CI en verde, verificación manual (`streamlit run` con ambos proveedores) y email con el enlace a la rama. Rollback: revertir la rama; `main` no se toca hasta después de la entrega.

## Decisiones confirmadas por el usuario (2026-10-27)

1. **Llamadas síncronas** en este change. Se revisa la decisión 5 de S02 ("migrar a async en S03"): con la UI in-process y `st.write_stream`, lo síncrono es más simple y suficiente. La migración a async queda para cuando haya un endpoint SSE o lo pida la sesión en vivo.
2. **`streamlit` como dependencia de runtime** (D8).
3. **Botón "Nueva conversación"** en el sidebar, que vacía el historial y las métricas. No lo pide el enunciado, pero con multi-turno es la única forma de empezar de cero sin recargar la página.
4. **Error del proveedor**: el mensaje del usuario no queda en el historial (D7), para mantener la alternancia `user`/`assistant` (Anthropic fusionaría dos turnos `user` seguidos) y permitir reenviarlo. Con la decisión 7 se consigue sin "retirar" nada.
5. **Tiempo hasta el primer fragmento** como métrica, además del tiempo total.

Tras la revisión del `architect` (tarea 0.2):

6. **Durante la iteración del stream se envuelve cualquier `Exception`** en `LLMProviderError` (D3), porque anthropic 1.17.0 no envuelve los errores de transporte a mitad del stream.
7. **El historial solo se actualiza cuando el stream termina bien** (D7): el mensaje `user` se pinta desde una variable local y se guarda junto con la respuesta. Cubre el error del proveedor y la interrupción (*Stop* o nuevo envío).
8. **El chat recorta los espacios** del mensaje antes de validarlo y enviarlo, como la API.
9. **`MAX_TRANSCRIPTION_CHARS` se mueve a `app/services/llm_service.py`** y `schemas.py` lo importa de allí (D12).
10. **Los ajustes se responden en el idioma de la transcripción**, aunque la petición esté en otro idioma (D5).
11. **`.streamlit/config.toml` solo con `gatherUsageStats = false`**; `showErrorDetails` queda por defecto (D8).
12. **`LLMStream` usa internamente un generador que devuelve el resultado** (`yield from`), para poder componerlo en la sesión en vivo (D3).

Durante la implementación (tarea 3.7, revisión del bloque de conversación):

13. **Se cierra el stream del SDK en un `finally`** para liberar la conexión si la lectura se corta (D3).
14. **Redacción del bloque de conversación aprobada**; la frase para mensajes no relacionados va en el idioma de ese mensaje (D5).
15. **Sin refuerzo en inglés** de la regla de idioma de los ajustes por ahora: se añade solo si falla en la verificación manual (7.2). *Actualización*: falló (gpt-4o-mini respondía en español a una transcripción en inglés) y se añadió la regla en inglés al final del bloque, pero no bastó (2/4).

Tras la verificación manual (7.2):

16. **Prompt por turno**: el primer turno del chat usa exactamente el prompt del endpoint, y el bloque de conversación solo se añade cuando hay historial (D5). El sidebar muestra el prompt que se enviará en el siguiente turno.
17. **Sidebar al principio del script + `st.rerun()` tras una respuesta correcta** (D7), porque con el sidebar al final el *Stop* lo hacía desaparecer.

## Open Questions

Ninguna pendiente.
