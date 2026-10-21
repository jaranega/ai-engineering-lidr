## 0. Prerrequisito

- [x] 0.1 Resolver las *Open Questions* de `design.md` con el usuario y actualizar specs/design si cambian
- [x] 0.2 Revisión del design por `architect` e incorporación de los hallazgos aprobados por el usuario

## 1. Setup del proyecto

- [x] 1.1 `uv init --bare` en la raíz (sin `main.py` ni README generados); `requires-python = ">=3.11"`; mantener `.python-version` en 3.12
- [x] 1.2 `uv add fastapi "uvicorn[standard]" pydantic-settings python-dotenv openai anthropic`
- [x] 1.3 `uv add --dev pytest httpx ruff` y configurar `[tool.ruff]` (line-length 100; reglas E, F, I, UP, B, SIM) y `[tool.pytest.ini_options]` en `pyproject.toml`
- [x] 1.4 Crear la estructura `app/` con todos los `__init__.py` (routers, services, context), más `app/schemas.py` y `app/services/pricing.py`
- [x] 1.5 Verificar: `uv sync --locked` y `uv run python -c "import app"` sin errores; `uv.lock` no está excluido por `.gitignore` y se versiona

## 2. Configuración

- [x] 2.1 Implementar `Settings` en `app/config.py`: variables, `SecretStr`, `hide_input_in_errors=True`, `field_validator(mode="before")` que normaliza `""`/espacios a `None` (keys y `LLM_MODEL`), validador de key no vacía del proveedor activo y `effective_model`
- [x] 2.2 Implementar `get_settings()` con `lru_cache`, más una función que traduce el error de validación a un mensaje claro escrito a mano (variable que falta y cómo resolverlo), sin valores de keys
- [x] 2.3 Crear `.env.example` (keys vacías; variables no secretas comentadas con su default) y un `.env` local, que no se versiona
- [x] 2.4 Verificar: tests unitarios de todos los escenarios de `app-config`, aislados del `.env` local (`Settings(_env_file=None)` + `monkeypatch.delenv`; `_env_file=tmp_path/".env"` para el escenario "la variable de entorno prevalece sobre .env"), incluidos los de key vacía, `LLM_MODEL` vacío y el error que no muestra valores de keys

## 3. Contexto CAG y prompt

- [x] 3.1 Redactar 2–3 ejemplos ficticios en `app/context/examples.py` con tipos de proyecto distintos (p. ej. web de gestión, app móvil, integración/datos), con desglose, total, equipo y duración
- [x] 3.2 Revisión de los ejemplos por parte del usuario (cifras y realismo)
- [x] 3.3 Implementar `build_system_prompt(examples)` como función pura (rol, formato, instrucción de responder en el idioma de la transcripción, ejemplos con etiquetas XML), `_system_prompt()` cacheado y `build_estimation_prompt(transcription) -> tuple[str, str]`
- [x] 3.4 Verificar: tests de que el prompt contiene todos los ejemplos, es determinista, incluye la instrucción de idioma y la de tratar la transcripción como datos, y no contiene la transcripción, y de que el mensaje `user` es exactamente la transcripción

## 4. Proveedores LLM y coste

- [x] 4.1 Definir `LLMResult` (dataclass), el Protocol `LLMProvider`, `LLMProviderError` y las constantes `MAX_TOKENS = 2048`, `LLM_TIMEOUT_S = 60` y `LLM_MAX_RETRIES = 2`
- [x] 4.2 Implementar `OpenAIProvider(client, model)`: Chat Completions; texto con `content` posiblemente `None`; tokens desde `usage.prompt_tokens`/`completion_tokens`; warning si `finish_reason == "length"`
- [x] 4.3 Implementar `AnthropicProvider(client, model)`: Messages con `system=`; concatenación de bloques `text`; tokens desde `usage.input_tokens`/`output_tokens`; warning si `stop_reason == "max_tokens"`
- [x] 4.4 Que cada proveedor envuelva los errores de su SDK en `LLMProviderError(provider=...) from exc` y que el log registre solo el tipo de error y el status
- [x] 4.5 Implementar `build_provider(name, settings)` (sin caché; construye el cliente del SDK con timeout y reintentos) y `get_llm_provider()` cacheado, que usa `get_settings()`
- [x] 4.6 Verificar los precios vigentes de `gpt-4o-mini` y `claude-haiku-4-5` en la documentación oficial e implementar `services/pricing.py` (alias y snapshots conocidos, con la fecha de verificación)
- [x] 4.7 Implementar `EstimationService.estimate()`, que devuelve un `EstimationResult` de dominio (texto, modelo solicitado, proveedor, tokens y coste), sin depender de `app/schemas.py`
- [x] 4.8 Verificar los tests de coste (conocido, snapshot y desconocido), del servicio con `FakeProvider`, de cada proveedor con un stub de cliente del SDK (kwargs enviados, extracción del texto y los tokens, envoltura de errores, warning por truncado) y de la factoría según `LLM_PROVIDER`

## 5. API HTTP

- [x] 5.1 Definir `EstimationRequest` (strip, no vacío, máximo 20 000 caracteres) y `EstimationResponse` en `app/schemas.py`
- [x] 5.2 Implementar `get_estimation_service` y `POST /estimate` en `app/routers/estimations.py` con `Annotated[..., Depends(...)]`: mapear `EstimationResult` → `EstimationResponse`, traducir `LLMProviderError` a 502 y documentar `responses={502: ...}`
- [x] 5.3 En `app/main.py`: título, descripción, `lifespan` (fail fast: `get_settings()` + construcción del proveedor; `logging.basicConfig` con `LOG_LEVEL`), router con prefijo `/api/v1` y `GET /health`
- [x] 5.4 Verificar con tests: `/health` (200, sin llamar al LLM); `/api/v1/estimate` (200 con `provider`/`model` comparados con los settings sobrescritos; 422 si falta el campo, si está vacío o si es demasiado largo, comprobando que no se llama al LLM; 502); `/openapi.json` (ruta, schemas y 200/422/502); fail fast en el lifespan (`with TestClient(app)` + `pytest.raises`, mensaje claro y sin valores de keys); importar `app.main` sin keys no falla

## 6. Tests de estructura y conftest

- [x] 6.1 Crear `tests/conftest.py`: entorno ficticio, `FakeProvider` con contador de llamadas, override de `get_llm_provider` y `get_settings`, y limpieza de cachés entre tests (`get_settings`, `get_llm_provider`, `_system_prompt`)
- [x] 6.2 Crear `tests/test_structure.py` con la lista de ficheros obligatorios del enunciado
- [x] 6.3 Verificar: `uv run pytest` en verde sin red ni keys reales, también con un `.env` local presente

## 7. CI

- [x] 7.1 Crear `.github/workflows/ci.yml` (push y PR): setup-uv; `uv sync --locked`; `ruff check`; `ruff format --check`; `pytest`; `actions/setup-node` con una versión fijada; `npx --yes @fission-ai/openspec@<versión local exacta> validate --all --strict`
- [ ] 7.2 Verificar: `uv run ruff check . && uv run ruff format --check .` en local, y el workflow en verde tras el push

## 8. Documentación y muestras

- [x] 8.1 Añadir 1–2 transcripciones de prueba en `samples/` (incluida la del enunciado: landing + HubSpot + blog)
- [x] 8.2 Actualizar el README con: requisitos, instalación, `.env`, arranque (incluido el comportamiento del fail fast con `--reload`), Swagger, un ejemplo con `curl` usando `samples/`, tests, arquitectura y desviaciones respecto al enunciado
- [ ] 8.3 Verificación manual con keys reales (previa confirmación del usuario, porque tiene coste): `curl` con cada sample en OpenAI y en Anthropic, comprobando que la estimación sigue el formato de los ejemplos y está en el idioma de la transcripción

## 9. Cierre

- [ ] 9.1 Recorrer el checklist del enunciado y marcar cada punto
- [ ] 9.2 `openspec validate --all --strict` en verde y archivar el change (`/opsx:archive`) tras la aprobación del usuario
- [ ] 9.3 Commit de `uv.lock`, push de `session-02` (con la aprobación del usuario) y preparar el texto del email de entrega para sisley@lidr.co
