## 0. Prerrequisito

- [x] 0.1 Revisión del change por el usuario (proposal, specs, design y tasks)

## 1. Servicios (ai-engineer)

- [x] 1.1 `LLMStream.__iter__` no vuelve a recorrer el generador ni toca el resultado si ya está consumido (D2)
- [x] 1.2 `_request_kwargs(system, messages)` en `OpenAIProvider` y `AnthropicProvider`, usado por `generate` y `_stream` (D5)
- [x] 1.3 `truncated: bool = False` en `LLMResult` y `EstimationResult`; los proveedores lo rellenan en `generate` y en `_stream` (`finish_reason == "length"` / `stop_reason == "max_tokens"`), manteniendo el warning; `_to_estimation_result` lo copia (D3)
- [x] 1.4 Verificar: `ruff` sin errores y suite en verde (los fallos por el campo nuevo los adapta qa en 4.1)

## 2. API (backend-engineer)

- [x] 2.1 `truncated: bool` en `EstimationResponse`, con descripción para OpenAPI, y mapeo en el router (D3)
- [x] 2.2 Verificar: `ruff` sin errores y `/openapi.json` con el campo nuevo

## 3. Interfaz (frontend-engineer)

- [x] 3.1 Respuesta sin texto: `PROVIDER_ERROR_MESSAGE`, warning en el log sin contenido, sin guardar mensajes ni métricas y sin `st.rerun()` (D1)
- [x] 3.2 `truncated_turns` en `st.session_state`: se añade el índice al guardar una respuesta truncada, `render_history` muestra `TRUNCATED_WARNING` con `st.warning` debajo de ella y `reset_conversation` lo vacía; los mensajes enviados siguen siendo solo `role`/`content` (D4)
- [x] 3.3 `CallMetrics.truncated` y "Respuesta cortada: sí/no" en el panel de métricas (D4)
- [x] 3.4 Verificar: `ruff` sin errores y la app arranca (`AppTest` de humo o `streamlit run` con una key ficticia, sin enviar mensajes)

## 4. Tests (qa-engineer)

- [x] 4.1 Adaptar dobles y tests existentes al campo `truncated` (el `FakeProvider` permite configurarlo y entregar cero fragmentos)
- [x] 4.2 `llm-provider`: segunda iteración (sin fragmentos, sin nueva llamada al SDK, resultado intacto); `truncated` verdadero y falso en `generate` y en streaming de ambos proveedores; "Mismos parámetros en ambos modos"
- [x] 4.3 `estimation-api`: `truncated` en la respuesta (`false` y `true`) y en `/openapi.json`
- [x] 4.4 `chat-ui`: respuesta sin texto (error genérico, sin traza, historial y métricas intactos); respuesta truncada guardada con aviso; aviso persistente tras otro turno y ausente en respuestas completas; mensajes enviados sin el indicador; "Nueva conversación" quita los avisos; métricas con "Respuesta cortada"
- [x] 4.5 Verificar: `uv run pytest -q` en verde sin red ni keys reales y tabla de cobertura de los escenarios del change

## 5. Documentación y cierre

- [x] 5.1 README: campo `truncated` en el contrato de respuesta y aviso de truncado en la descripción del chat (orquestador)
- [x] 5.2 `openspec validate --all --strict`, `ruff` y `pytest` en verde en local
- [ ] 5.3 Archivar el change (`/opsx:archive`) tras la aprobación del usuario
- [ ] 5.4 Commit y push a `session-03` (con la aprobación del usuario) y comprobar que la CI de la PR #2 sale en verde
