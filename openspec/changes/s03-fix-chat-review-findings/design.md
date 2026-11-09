## Context

Correcciones de la revisión de S03 (2026-11-09), antes de mergear la PR #2. Se revisó con un recorrido guiado del orquestador y con `/code-review` sobre `main...session-03`. El código afectado es el que dejó el change archivado `2026-10-27-s03-add-streamlit-chat-ui`:

- `streamlit_app.py`: `timed_fragments` solo registra `ttft_s` al llegar el primer fragmento, y `build_metrics` lo lee sin comprobarlo.
- `app/services/llm_service.py`: `LLMStream.__iter__` hace `self._result = yield from self._gen` en cada iteración. Los proveedores detectan el truncado, pero solo lo registran en el log. `generate` y `_stream` repiten los argumentos de la llamada al SDK.

El usuario decidió los cuatro arreglos y su comportamiento (ver *Decisiones confirmadas*). Este change no debe adelantar nada de la sesión en vivo (fallback, caché, structlog), aunque sí dejar `LLMStream` seguro para que esa sesión lo componga.

## Goals / Non-Goals

**Goals:**

- Que ninguna respuesta del modelo provoque una traza en la UI ni deje un turno vacío en el historial.
- Que el resultado de un `LLMStream` no se pierda al recorrerlo de nuevo.
- Que el truncado por `max_tokens` llegue al usuario del chat y al cliente de la API.
- Que la llamada al SDK se construya en un solo sitio por proveedor.

**Non-Goals:**

- Gestionar el tamaño total del historial, reorganizar dónde vive `MAX_TRANSCRIPTION_CHARS` o reutilizar `EstimationRequest` en la UI (ver D6).
- Reintentar o continuar automáticamente una respuesta truncada.
- Cambiar los códigos de estado de la API.

## Decisions

### D1. Respuesta sin texto: error en la UI

- En `handle_user_message`, tras consumir el stream, si no ha llegado ningún fragmento (no hay `ttft_s` en los tiempos, o el texto devuelto por `st.write_stream` está vacío) se muestra `PROVIDER_ERROR_MESSAGE` y se sale sin guardar mensajes ni métricas y sin `st.rerun()`. Es el mismo camino que un `LLMProviderError`, de modo que el usuario ve un único tipo de fallo.
- `build_metrics` ya no puede recibir unos tiempos sin `ttft_s`, porque solo se llama con respuestas con texto.
- La app registra un warning en el log (`empty LLM reply: provider=… model=…`, sin contenido de la conversación) para que la causa no se pierda.
- Motivos: un turno `assistant` vacío no aporta nada al usuario y rompe la conversación con Anthropic, que rechaza contenido vacío en la llamada siguiente.
- *Alternativa descartada*: que el servicio lance un error de dominio con texto vacío (y la API responda 502). Afecta también a la API, cuyo comportamiento con respuestas vacías no se ha revisado, y el usuario prefirió limitar el arreglo a la UI.

### D2. `LLMStream` conserva el resultado

```python
def __iter__(self) -> Iterator[str]:
    if self._result is not None:
        return
    self._result = yield from self._gen
```

- Si el stream ya está consumido, recorrerlo otra vez no entrega nada y no toca el resultado. No hay una segunda llamada al SDK, porque el generador ya está agotado.
- `EstimationStream` delega en `LLMStream`, así que hereda el comportamiento.
- *Alternativa descartada*: lanzar un error en la segunda iteración. Sorprendería a quien componga streams (fallback, caché), y en Python lo habitual es que recorrer un iterador agotado no produzca elementos.

### D3. Indicador `truncated`

- `LLMResult` y `EstimationResult` ganan `truncated: bool = False`. Con el valor por defecto, los dobles de prueba y cualquier proveedor futuro siguen siendo válidos sin cambios.
- OpenAI: `truncated = finish_reason == "length"`, en `generate` y en `_stream`. Anthropic: `truncated = stop_reason == "max_tokens"` (en streaming, el de `message_delta`). El warning del log se mantiene.
- `_to_estimation_result` copia el campo, de modo que sigue siendo el único sitio donde se construye el resultado de dominio.
- **API**: `EstimationResponse.truncated: bool` con su descripción en OpenAPI, y el router lo mapea. Es un cambio aditivo: los clientes que no lo lean no se ven afectados.
- *Alternativa descartada*: exponerlo solo en el chat. El servicio ya tiene el dato y la API es la otra interfaz del mismo caso de uso (decisión del usuario).

### D4. Aviso de truncado en el chat

- Los mensajes de `st.session_state.messages` se envían tal cual a los SDK, así que el indicador **no** puede ir como clave extra dentro de `ChatMessage`: OpenAI rechaza claves desconocidas en los mensajes y Anthropic podría rechazarlas.
- Se guarda aparte: `st.session_state.truncated_turns: set[int]` con los índices, dentro de `messages`, de las respuestas `assistant` truncadas.
- `render_history` pinta, dentro del `st.chat_message` de esa respuesta, un `st.warning` con el texto `TRUNCATED_WARNING`: "La respuesta se ha cortado por el límite de longitud. Pide un ajuste o una versión más breve." Al ser parte del historial, sobrevive a los reruns.
- `reset_conversation` vacía también `truncated_turns`.
- `CallMetrics` gana `truncated: bool` y el panel de métricas muestra "Respuesta cortada: sí/no".
- *Alternativa descartada*: guardar mensajes enriquecidos (`{"role", "content", "truncated"}`) y quitar la clave antes de enviarlos. Exige una conversión en cada envío, y olvidarla filtraría la clave al SDK.

### D5. Helper de petición por proveedor

- Cada proveedor tiene un método privado `_request_kwargs(system, messages) -> dict` con el modelo, los mensajes (con el system en la forma de cada SDK) y el límite de tokens (`max_completion_tokens` en OpenAI, `max_tokens` en Anthropic).
- `generate` llama a `create(**kwargs)`. `_stream` llama a `create(**kwargs, stream=True, ...)` y añade `stream_options` en OpenAI.
- No cambia el comportamiento. Los tests de kwargs existentes lo verifican y se añade uno que compara las dos llamadas (escenario "Mismos parámetros en ambos modos").
- **Habilita la sesión en vivo**: un parámetro nuevo, como `temperature`, se añade en un solo sitio por proveedor.

### D6. Hallazgos de la revisión que no se arreglan

| Hallazgo | Decisión | Motivo |
|---|---|---|
| El historial no tiene límite total; solo cada mensaje está limitado a 20 000 caracteres | Aplazado | Riesgo ya asumido en el design de S03. La gestión del contexto y la memoria es contenido de S05+. El coste es visible en las métricas y "Nueva conversación" permite empezar de cero. |
| `schemas.py` importa `MAX_TRANSCRIPTION_CHARS` de `services`, lo que carga los SDK | Descartado | Decisión 9 de S03, validada por el `architect`. Los routers ya importan `llm_service`, así que el coste real es nulo. |
| `validate_input` duplica las reglas de `EstimationRequest` | Descartado | Reutilizar el schema HTTP acoplaría la UI al contrato que cambia en S04. Ambos comparten el límite, que vive en un solo sitio. |

## Risks / Trade-offs

- [El campo nuevo `truncated` cambia el contrato de la API] → Es aditivo. Se documenta en OpenAPI y en el README, y los tests de contrato lo cubren.
- [Índices de `truncated_turns` desalineados con `messages`] → Solo se añaden índices al guardar una respuesta y se vacían con "Nueva conversación"; el historial no se edita de ninguna otra forma.
- [Tratar la respuesta vacía como error oculta la causa (filtro de contenido, rechazo)] → Se registra un warning en el log con el proveedor. El usuario ve el mensaje genérico y puede reformular.
- [El warning de truncado ocupa espacio en conversaciones largas] → Solo aparece en respuestas cortadas, que son poco probables con las estimaciones actuales (unos 650 tokens frente a un límite de 2048).

## Migration Plan

- Rama `session-03`. Los commits actualizan la PR #2, que se mergea después.
- Los clientes de la API reciben un campo adicional y no necesitan migrar. Rollback: revertir los commits del change.

## Decisiones confirmadas por el usuario (2026-11-09)

1. Se arreglan los cuatro hallazgos: respuesta vacía, doble iteración, aviso de truncado y helper de petición.
2. Respuesta vacía: el **mismo error genérico** que un fallo del proveedor, sin guardar nada; solo en la UI (D1).
3. Respuesta truncada en el chat: **se guarda y se avisa**, con un aviso persistente y en las métricas (D4).
4. **`truncated` también en la API**, como campo aditivo de `EstimationResponse` (D3).
5. Se aplazan o descartan el límite total del historial, la ubicación del límite de caracteres y la reutilización de `EstimationRequest` (D6).

## Open Questions

Ninguna pendiente. El texto del aviso de truncado (D4) se puede ajustar al revisar la implementación.
