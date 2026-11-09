## Why

Sesión 03 (correcciones posteriores a la entrega, antes de mergear la PR #2 de `session-03`). Enunciado de referencia: `AIEngineer-notas/03_Patrones_diseno/00_ejercicio-wrapper-de-interfaz-conversacional.md`; change de origen: `openspec/changes/archive/2026-10-27-s03-add-streamlit-chat-ui/`.

La revisión de la implementación de S03 (recorrido guiado + `/code-review` sobre `main...session-03`, 2026-11-09) encontró dos bugs en el flujo del chat y un hueco de información:

- Si el modelo termina **sin devolver texto**, la app falla con `KeyError: 'ttft_s'` y muestra la traza, y además guardaría un turno `assistant` vacío que Anthropic rechaza en la llamada siguiente.
- **Iterar dos veces un `LLMStream`** borra su resultado. Hoy nadie lo hace, pero el fallback y la caché de la sesión en vivo componen streams y podrían hacerlo.
- Una respuesta **cortada por `max_tokens`** se presenta como si estuviera completa: solo queda un warning en el log del servidor, ni el usuario del chat ni el cliente de la API lo saben.

También señaló código duplicado en los proveedores (los argumentos de la llamada al SDK se repiten en `generate` y en `_stream`), que conviene unificar antes de que la sesión en vivo añada parámetros.

## What Changes

- **Respuesta vacía en el chat**: si el stream termina sin ningún fragmento de texto, la UI lo trata como un error del proveedor: mensaje genérico, sin traza, y no se guardan ni los mensajes ni las métricas.
- **`LLMStream` reutilizable**: una segunda iteración no entrega fragmentos y conserva el resultado.
- **Indicador de truncado**:
  - `LLMResult` y `EstimationResult` ganan `truncated: bool` (`finish_reason == "length"` en OpenAI, `stop_reason == "max_tokens"` en Anthropic), en generación normal y en streaming. Se mantiene el warning en el log.
  - **API**: `EstimationResponse` gana el campo `truncated: bool`. Es un cambio **aditivo** del contrato de `POST /api/v1/estimate`: ningún campo existente cambia.
  - **Chat**: la respuesta truncada se guarda en el historial como cualquier otra y se muestra debajo un aviso persistente; las métricas de la última llamada también lo indican.
- **Limpieza sin cambio de comportamiento**: un helper por proveedor construye los argumentos comunes de la llamada al SDK, compartido por `generate` y `_stream`.

### Desviaciones respecto al enunciado

Ninguna nueva. El campo `truncated` amplía la respuesta de la API, igual que los campos de tokens y coste de S02.

## Capabilities

### New Capabilities

<!-- Ninguna. -->

### Modified Capabilities

- `llm-provider`: el resultado de la generación (normal y en streaming) incluye `truncated`, y el stream conserva su resultado si se itera más de una vez.
- `estimation-api`: la respuesta de `POST /api/v1/estimate` incluye `truncated`.
- `chat-ui`: una respuesta sin texto se trata como error; una respuesta truncada se guarda con un aviso persistente y se refleja en las métricas.

## Impact

- **Código**: `app/services/llm_service.py` (resultado, proveedores, `LLMStream`, helper de petición), `app/schemas.py` y `app/routers/estimations.py` (campo `truncated`), `streamlit_app.py` (respuesta vacía, aviso de truncado, métricas), `tests/` y el README (contrato de respuesta).
- **API HTTP**: campo nuevo `truncated` en la respuesta 200 de `POST /api/v1/estimate`; los demás campos y códigos de estado no cambian.
- **Dependencias**: ninguna.
- **Git**: rama `session-03`; los commits actualizan la PR #2.

## Fuera de alcance

Hallazgos de la revisión que se aplazan o descartan (motivo en `design.md`):

- Límite del tamaño total del historial (memoria y gestión del contexto, S05+).
- Mover `MAX_TRANSCRIPTION_CHARS` fuera de `services` para que `schemas.py` no dependa de esa capa.
- Reutilizar `EstimationRequest` para validar la entrada del chat.
- Tratar la respuesta vacía como error de dominio en el servicio (y 502 en la API).
- Wrapper con fallback, caché y structlog (sesión en vivo).
