## ADDED Requirements

### Requirement: Endpoint de estimación
El sistema SHALL exponer `POST /api/v1/estimate`, que recibe una transcripción y devuelve la estimación generada por el LLM.

Request (`application/json`):
| Campo | Tipo | Reglas |
|---|---|---|
| `transcription` | `string` | obligatorio, no vacío tras eliminar espacios, máximo 20 000 caracteres |

Response `200` (`application/json`):
| Campo | Tipo |
|---|---|
| `estimation` | `string` (Markdown) |
| `model` | `string` |
| `provider` | `"openai" \| "anthropic"` |
| `input_tokens` | `integer` |
| `output_tokens` | `integer` |
| `cost_usd` | `number \| null` |

#### Scenario: Estimación correcta
- **GIVEN** un proveedor LLM disponible
- **WHEN** se hace POST a `/api/v1/estimate` con `{"transcription": "<texto de reunión>"}`
- **THEN** la respuesta es 200 con todos los campos del contrato y `provider`/`model` coinciden con la configuración efectiva

#### Scenario: Transcripción ausente o vacía
- **WHEN** el body no incluye `transcription` o esta solo contiene espacios
- **THEN** la respuesta es 422 y no se llama al LLM

#### Scenario: Transcripción demasiado larga
- **WHEN** `transcription` supera los 20 000 caracteres
- **THEN** la respuesta es 422 y no se llama al LLM

#### Scenario: Fallo del proveedor
- **WHEN** el proveedor LLM devuelve un error
- **THEN** la respuesta es 502 con un `detail` genérico, sin traza ni datos sensibles

### Requirement: Health check
El sistema SHALL exponer `GET /health`, que responde 200 con `{"status": "ok", "env": <APP_ENV>, "provider": <LLM_PROVIDER>, "model": <modelo efectivo>}`, sin llamar al LLM.

#### Scenario: Servicio levantado
- **WHEN** se hace GET a `/health`
- **THEN** la respuesta es 200 con `status = "ok"` y no se realiza ninguna llamada al proveedor

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
