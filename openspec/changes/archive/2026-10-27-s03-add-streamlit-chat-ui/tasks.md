## 0. Prerrequisito

- [x] 0.1 Resolver las *Open Questions* de `design.md` con el usuario (async, dependencia `streamlit`, botón "Nueva conversación", historial tras un error, tiempo hasta el primer fragmento) y actualizar specs/design si cambian
- [x] 0.2 Revisión del design por `architect` e incorporación de los hallazgos aprobados por el usuario (decisiones confirmadas 6–12)

## 1. Harness (orquestador)

- [x] 1.1 Crear `.claude/agents/frontend-engineer.md` (sonnet; rutas `streamlit_app.py` y `.streamlit/`; sin lógica de negocio ni SDKs de LLM en la UI; nunca mostrar ni registrar keys; textos de la interfaz en español)
- [x] 1.2 Actualizar en `CLAUDE.md` la tabla de agentes y el roadmap del harness, y el hito de S03 (rama `session-03`)
- [x] 1.3 Verificar: el agente aparece entre los disponibles y su frontmatter sigue el formato de los existentes

## 2. Setup y configuración (backend-engineer)

- [x] 2.1 `uv add streamlit` (dependencia de runtime) y comprobar que `uv sync --locked` funciona
- [x] 2.2 Extraer `configure_logging(settings)` y `NOISY_LOGGERS` del `lifespan` a `app/config.py`; el `lifespan` pasa a llamarla sin cambiar su comportamiento
- [x] 2.3 `app/schemas.py` importa `MAX_TRANSCRIPTION_CHARS` de `app/services/llm_service.py` (después de la tarea 3.1)
- [x] 2.4 Verificar: `uv run pytest` en verde (incluidos los tests de logging, una vez que qa actualice el import de `NOISY_LOGGERS` en la tarea 5.1) y `ruff` sin errores

## 3. Proveedores, prompt y servicio (ai-engineer)

- [x] 3.1 Mover `MAX_TRANSCRIPTION_CHARS` a `app/services/llm_service.py`; definir `ChatMessage` (`TypedDict` con `role: Literal["user", "assistant"]` y `content`) y cambiar `LLMProvider.generate` a `(system, messages)` en el Protocol y en `OpenAIProvider`/`AnthropicProvider`
- [x] 3.2 Adaptar `build_estimation_prompt(transcription)` para devolver `(system, [{"role": "user", "content": transcription}])` y `EstimationService.estimate()` a la nueva firma, sin cambios de comportamiento
- [x] 3.3 Implementar `LLMStream` (iterable de `str`; internamente `self._result = yield from gen` sobre un `Generator[str, None, LLMResult]`; `.result` tras consumirlo, `RuntimeError` si no se ha consumido) y añadir `stream` al Protocol
- [x] 3.4 Implementar `OpenAIProvider.stream`: `create(stream=True, stream_options={"include_usage": True}, max_completion_tokens=MAX_TOKENS)`, deltas de `choices[0].delta.content` (comprobando que `choices` no está vacío y emitiendo solo fragmentos no vacíos), uso del último chunk (0 si falta), warning si `finish_reason == "length"`; errores del SDK al crear y **cualquier `Exception`** al iterar envueltos en `LLMProviderError`; la llamada se hace al empezar a iterar
- [x] 3.5 Implementar `AnthropicProvider.stream`: `create(stream=True, max_tokens=MAX_TOKENS)` con `system=`, texto de `content_block_delta`/`text_delta` (solo fragmentos no vacíos), tokens de `message_start` y `message_delta` (`output_tokens` acumulado: se asigna), warning si `stop_reason == "max_tokens"`, mismo tratamiento de errores
- [x] 3.6 Añadir `_CONVERSATION`, `build_chat_system_prompt(examples)`, `_chat_system_prompt()` cacheado y `system_prompt_for(messages)` (prompt del endpoint en el primer turno; con el bloque de conversación desde el segundo, decisión 16), con el bloque de conversación de D5 al final del prompt del endpoint (ajustes en el idioma de la transcripción)
- [x] 3.7 Revisión por el usuario de la redacción del bloque de conversación
- [x] 3.8 Extraer la conversión `LLMResult → EstimationResult` a una función privada e implementar `EstimationService.stream_chat(messages) -> EstimationStream` (iterable de `str`; `.result` con `cost_usd`)
- [x] 3.9 Verificar: `ruff` sin errores y la suite existente en verde tras adaptar los dobles de prueba a la nueva firma (en coordinación con qa-engineer, tarea 5.1)

## 4. Interfaz Streamlit (frontend-engineer)

- [x] 4.0 Crear `.streamlit/config.toml` con `[browser] gatherUsageStats = false`
- [x] 4.1 Crear `streamlit_app.py`: `set_page_config`, título y arranque con `get_settings()`/`get_llm_provider()`; si hay `ValidationError`, `st.error(format_config_error(exc))` y `st.stop()`; después `configure_logging(settings)`
- [x] 4.2 Estado en `st.session_state` (`messages`, `last_metrics`) y repintado del historial con `st.chat_message`
- [x] 4.3 Entrada con `st.chat_input`: recorte de espacios y validación (en blanco y > `MAX_TRANSCRIPTION_CHARS`, importado de `app.services.llm_service`) con aviso y sin llamar al LLM ni tocar el historial
- [x] 4.4 Respuesta en streaming: pintar el mensaje `user` desde una variable local (sin guardarlo aún); `st.write_stream` sobre un generador que itera `EstimationService.stream_chat([*historial, mensaje])` y mide el tiempo hasta el primer fragmento y el total; al terminar bien, guardar juntos el mensaje `user`, el `assistant` y `CallMetrics`
- [x] 4.5 Error del proveedor: `st.error` genérico; no se guarda nada (ni mensaje, ni respuesta parcial, ni métricas)
- [x] 4.6 Sidebar (renderizado al principio del script, con `st.rerun()` tras una respuesta correcta; decisión 17): proveedor y modelo activos, system prompt del chat de solo lectura, ejemplos CAG en expanders, métricas de la última llamada o "Aún no hay métricas" y botón "Nueva conversación" (vacía historial y métricas)
- [x] 4.7 Verificar: `uv run streamlit run streamlit_app.py` arranca sin errores con una key ficticia (se muestra la UI; sin enviar mensajes), revisar el ruido de logs con `LOG_LEVEL=DEBUG` (`watchdog`/`tornado`; si molesta, avisar para añadirlos a `NOISY_LOGGERS`) y `ruff` sin errores

## 5. Tests (qa-engineer)

- [x] 5.1 Adaptar a la nueva firma los tests y dobles existentes (`FakeProvider`, stubs de proveedores, servicio, prompt); importar `NOISY_LOGGERS` de `app.config` en `tests/test_logging.py`; limpiar también `_chat_system_prompt` en `_clear_caches()`; y añadir `stream()` al `FakeProvider` (fragmentos configurables, error opcional tras N fragmentos, registro de los mensajes recibidos)
- [x] 5.2 Tests de prompt: el del chat empieza por el del endpoint y contiene la sección de conversación (con la regla de idioma de los ajustes), es determinista y el del endpoint no la contiene; conversación `user`/`assistant`/`user` sin mensajes en el system prompt
- [x] 5.3 Tests de proveedores en `generate` con varios turnos (mensajes en orden; `system` como primer mensaje en OpenAI y como parámetro en Anthropic)
- [x] 5.4 Tests de streaming de OpenAI y Anthropic con stubs de chunks/eventos: kwargs (incluidos `stream=True` y `max_completion_tokens`/`max_tokens = MAX_TOKENS`), fragmentos en orden (sin fragmentos vacíos), `.result` (texto y tokens), `.result` antes de terminar, uso ausente → 0 tokens, error del SDK al crear y a mitad de iteración y error no-SDK a mitad (`LLMProviderError` con `__cause__`), warning por truncado
- [x] 5.5 Tests de `EstimationService.stream_chat`: prompt del chat, mensajes sin modificar y `cost_usd` en `.result`; `estimate` sigue enviando un único mensaje `user`
- [x] 5.6 Tests de la app con `AppTest.from_file(..., default_timeout=10)`, parcheando `llm_service.build_provider` y definiendo la key ficticia con `monkeypatch.setenv`: respuesta en el historial, segundo turno con los tres mensajes enviados, mensaje recortado, mensaje en blanco y demasiado largo, error del proveedor al crear y a mitad (historial intacto y métricas anteriores conservadas), configuración inválida (error con la variable, sin `chat_input`), sidebar con prompt, ejemplos y métricas (antes y después de una llamada), botón "Nueva conversación", ninguna key en la interfaz, `streamlit_app.py` sin imports de `openai`/`anthropic`
- [x] 5.7 Añadir `streamlit_app.py` a `tests/test_structure.py`
- [x] 5.8 Verificar: `uv run pytest` en verde sin red ni keys reales, también con un `.env` local presente; recorrer cada escenario de las specs del change y anotar el test que lo cubre ("Arranque de la interfaz", "Texto incremental" y "Generación interrumpida" se verifican a mano en la 7.2)

## 6. CI

- [x] 6.1 Comprobar que el workflow existente cubre el change sin cambios (`uv sync --locked` instala streamlit; `ruff` incluye `streamlit_app.py`) y ajustarlo solo si hace falta (backend-engineer)
- [x] 6.2 Verificar: `uv run ruff check . && uv run ruff format --check .` y `openspec validate --all --strict` en local; el workflow en verde tras el push. Resultado: CI en verde tras el push (run 40584877486)

## 7. Documentación y verificación manual

- [x] 7.1 Actualizar el README: sesión S03 en la tabla de entregas, arranque de la interfaz (`uv run streamlit run streamlit_app.py` desde la raíz), qué muestra el sidebar, arquitectura (UI in-process, streaming), desviaciones respecto al enunciado y nuevo agente
- [x] 7.2 Verificación manual con keys reales (previa confirmación del usuario, porque tiene coste): con OpenAI y con Anthropic, pegar un sample, ver el streaming, pedir un ajuste ("quita el blog") y comprobar que llega la estimación completa actualizada, también con un ajuste escrito en otro idioma; pulsar *Stop* a mitad de una respuesta; revisar las métricas del sidebar y un error forzado (key inválida). Anotar resultados y coste aproximado. Resultado (2026-10-27, usuario en la interfaz y orquestador con llamadas directas): streaming, ajustes, métricas, mensaje en blanco y key inválida correctos con ambos proveedores. Dos fallos encontrados y corregidos: (1) gpt-4o-mini respondía en español a una transcripción en inglés en el chat (endpoint 6/6 en inglés; con cualquier bloque añadido al prompt, 0–50 %) → prompt por turno, decisión 16 (tras el arreglo, 3/3 OpenAI y 1/1 Anthropic en inglés en los dos turnos, con un ajuste pedido en español); (2) el *Stop* hacía desaparecer el panel lateral → panel al principio del script + `st.rerun()`, decisión 17. Revalidado por el usuario tras ambos arreglos. Coste de las llamadas directas: ~0,055 USD

## 8. Cierre

- [x] 8.1 Recorrer el checklist de verificación del enunciado y marcar cada punto. Resultado: los 5 puntos se cumplen (interfaz con `streamlit run`, estimación de una transcripción, conversación persistente, streaming y API key desde `.env`), más el Nivel 3 opcional
- [x] 8.2 `openspec validate --all --strict` en verde y archivar el change (`/opsx:archive`) tras la aprobación del usuario
- [x] 8.3 Commit y push de `session-03` (con la aprobación del usuario) y preparar el texto del email de entrega para sisley@lidr.co con el enlace a la rama (el envío lo gestiona el usuario) (texto del email preparado; el envío lo gestiona el usuario)
