# estimation-api Specification

## Purpose
Exponer por HTTP la generación de estimaciones a partir de transcripciones de reuniones, junto con el health check y la documentación OpenAPI del servicio.
## Requirements
### Requirement: Endpoint de estimación
El sistema SHALL exponer `POST /api/v1/estimate`, que recibe una transcripción y devuelve la estimación generada por el LLM.

Request (`application/json`, schema `EstimationRequest`):
| Campo | Tipo | Reglas |
|---|---|---|
| `transcription` | `string` | obligatorio, no vacío tras eliminar espacios, máximo 20 000 caracteres |

Response `200` (`application/json`, schema `EstimationResponse`):
| Campo | Tipo |
|---|---|
| `estimation` | `string` (Markdown) |
| `model` | `string` (modelo efectivo: el que respondió, primario o respaldo) |
| `provider` | `string` (proveedor del modelo efectivo: `openai` o `anthropic`) |
| `input_tokens` | `integer` |
| `output_tokens` | `integer` |
| `cost_usd` | `number \| null` |
| `truncated` | `boolean` (`true` si la estimación se cortó por el límite de tokens) |
| `fallback_used` | `boolean` (`true` si respondió el modelo de respaldo) |
| `cache_hit` | `boolean` (`true` si la respuesta vino de la caché) |
| `latency_ms` | `integer` (tiempo en el servidor desde que se recibe la petición hasta que se tiene la estimación, en milisegundos) |

#### Scenario: Estimación correcta
- **GIVEN** un modelo primario disponible y la caché vacía
- **WHEN** se hace POST a `/api/v1/estimate` con `{"transcription": "<texto de reunión>"}`
- **THEN** la respuesta es 200 con todos los campos del contrato, `model`/`provider` son los del modelo primario, `truncated`, `fallback_used` y `cache_hit` son `false` y `latency_ms` es mayor o igual que 0

#### Scenario: Estimación servida por el respaldo
- **GIVEN** un modelo primario que falla y un respaldo disponible
- **WHEN** se hace POST a `/api/v1/estimate` con una transcripción válida
- **THEN** la respuesta es 200 con `fallback_used = true` y el `model`/`provider` del respaldo

#### Scenario: Estimación desde la caché
- **GIVEN** que la misma transcripción ya se estimó y la caché está disponible
- **WHEN** se hace POST a `/api/v1/estimate` con esa transcripción
- **THEN** la respuesta es 200 con el mismo `estimation` y `cache_hit = true`, sin llamar al LLM

#### Scenario: Estimación truncada
- **GIVEN** un proveedor que corta la generación por el límite de tokens
- **WHEN** se hace POST a `/api/v1/estimate` con una transcripción válida
- **THEN** la respuesta es 200 con el texto recibido y `truncated` es `true`

#### Scenario: Transcripción ausente o vacía
- **WHEN** el body no incluye `transcription` o esta solo contiene espacios
- **THEN** la respuesta es 422 y no se llama al LLM

#### Scenario: Transcripción demasiado larga
- **WHEN** `transcription` supera los 20 000 caracteres
- **THEN** la respuesta es 422 y no se llama al LLM

#### Scenario: Fallo del proveedor
- **WHEN** el primario y el respaldo fallan
- **THEN** la respuesta es 502 con un `detail` genérico, sin traza ni datos sensibles

#### Scenario: Error 502 documentado
- **WHEN** se consulta `/openapi.json`
- **THEN** la operación `POST /api/v1/estimate` documenta las respuestas 200, 422 y 502

### Requirement: Health check
El sistema SHALL exponer `GET /health`, que responde 200 con `{"status": "ok", "env": <APP_ENV>, "primary_model": <PRIMARY_MODEL>, "fallback_model": <FALLBACK_MODEL, o null si está vacío o no tiene key>, "cache": "ok" | "unavailable"}`, sin llamar al LLM. El estado de la caché MUST comprobarse con una operación ligera de Redis con timeout corto, y un Redis caído MUST NOT cambiar el código 200.

#### Scenario: Servicio levantado
- **WHEN** se hace GET a `/health`
- **THEN** la respuesta es 200 con `status = "ok"`, los modelos configurados y no se realiza ninguna llamada al LLM

#### Scenario: Redis caído
- **GIVEN** que Redis no responde
- **WHEN** se hace GET a `/health`
- **THEN** la respuesta es 200 con `cache = "unavailable"`

#### Scenario: Sin fallback
- **GIVEN** que falta la API key del proveedor del respaldo o `FALLBACK_MODEL` está vacío
- **WHEN** se hace GET a `/health`
- **THEN** `fallback_model` es `null`

### Requirement: Documentación OpenAPI
La aplicación SHALL configurar un título y una descripción y servir Swagger UI en `/docs`, con los schemas de request y response de `/api/v1/estimate`.

#### Scenario: Swagger accesible
- **WHEN** se hace GET a `/docs`
- **THEN** la respuesta es 200, y `/openapi.json` incluye la ruta `/api/v1/estimate` con sus schemas

### Requirement: Arranque estándar
El servicio MUST arrancar con `uv run uvicorn app.main:app --reload` desde la raíz del repositorio.

#### Scenario: Arranque local
- **GIVEN** un `.env` válido
- **WHEN** se ejecuta el comando de arranque
- **THEN** el servidor escucha en `http://localhost:8000` sin errores

