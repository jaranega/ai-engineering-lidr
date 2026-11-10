## Why

Ejercicio de la sesión 05 (`AIEngineer-notas/05_Funciones_avanzadas/00_memoria`: "memoria conversacional y contexto enriquecido"). Hasta ahora el estimador es transaccional: una descripción entra y una estimación sale. En una empresa la estimación es una conversación: el cliente refina el alcance, añade información y adjunta documentos, y espera que el sistema recuerde de qué proyecto se habla. El ejercicio pide memoria conversacional con ventana deslizante, un `project_metadata` separado del historial e inyectado en el system prompt, y adjuntos PDF y Word. Es la base del directo de S05 (compresión de memoria con anclas, tier dinámico y Actor-Critic-Boss).

## What Changes

- **Sesiones en memoria del proceso** (`app/sessions/`): `Session` con `ConversationHistory` (ventana deslizante de `MAX_CONVERSATION_TURNS` turnos, por defecto 6; un turno es un par `user` + `assistant`) y `ProjectMetadata` (`project_name`, `assumed_team_size`, `mentioned_technologies`, `agreed_scope`). Un diccionario por `session_id` (UUID v4), sin base de datos ni Redis, con un tope de sesiones para que la memoria no crezca sin límite.
- **Endpoints nuevos**:
  - `POST /api/v1/sessions` crea una sesión vacía y devuelve `{"session_id"}` con 201.
  - `GET /api/v1/sessions/{session_id}` devuelve el `project_metadata` y el número de turnos (para el panel de la UI).
  - `POST /api/v1/sessions/{session_id}/estimate` recibe `multipart/form-data` con `transcript`, los tres enums tipados y una lista opcional de `attachments`, y devuelve el mismo contrato que `POST /api/v1/estimate` más `session_id`, `turn` y `project_metadata`. 404 si la sesión no existe.
- **Adjuntos, camino B (extracción local)**: pypdf para PDF y python-docx para Word (`.docx`). El texto extraído se concatena a la transcripción con un separador `--- attachment: <nombre> ---`. Hay límites de número, tamaño y caracteres. Un tipo no admitido da 415; un fichero que no se puede leer o sin texto, 422.
- **Guardrails sobre la entrada de la sesión**: la transcripción pasa por los mismos guardrails que la descripción (400). En los adjuntos, la prompt injection también bloquea (400), pero los datos personales **se enmascaran** (`[EMAIL]`, `[PHONE]`, `[IBAN]`) en lugar de bloquear, porque los documentos reales suelen llevar contactos.
- **`project_metadata` sin una segunda llamada al LLM**: se deriva de forma determinista del `EstimationResult` estructurado de cada turno (`title` → `project_name`, suma de `team` → `assumed_team_size`, `summary` → `agreed_scope`) más un campo nuevo `technologies` en el schema. Los escalares se sobrescriben y las tecnologías se acumulan sin duplicados.
- **Prompt `v4`**: copia de `v3` con el bloque `<project_metadata>` en el system prompt (vacío en el primer turno) e instrucciones multi-turno: el historial va en los mensajes y cada respuesta es la estimación completa y actualizada. El system se regenera en cada turno desde el `project_metadata` actual y no forma parte del historial. El turno `assistant` que se guarda es el Markdown que vio el usuario. `v3` sigue disponible para `POST /api/v1/estimate`.
- **Streamlit**: crea una sesión al cargar la página y conserva la conversación. Muestra los turnos, un área de transcripción, el selector múltiple de ficheros, el panel lateral con el `project_metadata` y el botón "Nueva conversación".
- Dependencias nuevas: `python-multipart`, `pypdf`, `python-docx`.

### Desviaciones respecto al enunciado

- **`project_metadata` derivado de la salida estructurada** en lugar de una heurística sobre texto libre o un LLM extractor. Con salida estructurada (puesta al día de S04), los hechos ya vienen tipados en la respuesta. Es la opción "heurística" del enunciado, pero determinista y sin coste extra; el extractor costaría una llamada por turno. Se justifica en el README.
- **Ruta con prefijo `/api/v1`** (`/api/v1/sessions`), como el resto de la API.
- **Los turnos fallidos no entran en el historial**: un 400, 415, 422 o 502 no añade turno ni toca el `project_metadata`, para no contaminar la memoria con respuestas que el usuario no recibió.
- **PII en adjuntos enmascarada**, no bloqueada (en la transcripción sigue bloqueando, como la descripción).
- **Tests de integración con `httpx.AsyncClient`**, como pide el enunciado, además de los unitarios.

## Capabilities

### New Capabilities
- `conversation-sessions`: almacén de sesiones en memoria, ventana deslizante del historial, `ProjectMetadata` y su actualización tras cada turno, y los endpoints de sesión.
- `attachments`: extracción local de texto de PDF y DOCX, límites, separador, errores 415/422 y enmascarado de datos personales.

### Modified Capabilities
- `structured-estimation`: el schema gana `technologies`.
- `prompt-templates`: versión `v4` con `<project_metadata>`; `v3` se mantiene.
- `cag-prompt`: bloque de `project_metadata` e instrucciones multi-turno en `v4`.
- `input-guardrails`: transcripción y adjuntos (bloqueo por inyección, enmascarado de PII en adjuntos).
- `estimation-form-ui`: la interfaz pasa a ser conversacional con sesión, adjuntos y panel de metadata.
- `app-config`: `MAX_CONVERSATION_TURNS`, `MAX_SESSIONS` y los límites de adjuntos.
- `observability`: eventos de sesión y de adjuntos, sin contenido.
- `ci-pipeline`: los tres tests de integración del enunciado y la cobertura de sesiones y adjuntos.

## Impact

- **Código**: nuevos `app/sessions/` y `app/attachments/`; `app/routers/sessions.py`; `app/services/llm_service.py` (turno de sesión); `app/estimation/schema.py` (`technologies`); `app/prompts/estimation/v4/`; `app/guardrails/input.py` (enmascarado); `app/config.py`; `app/main.py`; `streamlit_app.py`.
- **API**: tres endpoints nuevos; `POST /api/v1/estimate` no cambia salvo el campo `technologies` dentro de `result`.
- **Dependencias**: `python-multipart`, `pypdf`, `python-docx`.
- **Memoria del proceso**: las sesiones se pierden al reiniciar y no se comparten entre workers. Es aceptable en esta fase y se documenta.
- **Coste**: cada turno reenvía el historial de la ventana (hasta 6 turnos), así que el coste por turno crece con la conversación hasta el límite de la ventana. Una medición final con llamadas reales (conversación de 3 turnos y un PDF).

## Fuera de alcance

- Resumen acumulativo, memoria híbrida con anclas y tier dinámico (directo de S05).
- Persistencia de las sesiones entre reinicios.
- Patrón Actor-Critic-Boss (directo de S05).
- Búsqueda web y function calling.
- OCR de PDF escaneados, `.doc` antiguo y otros formatos.
- Caché semántica.
