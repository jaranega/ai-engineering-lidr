## 0. Prerrequisito

- [x] 0.1 Resolver las *Open Questions* de `design.md` con el usuario y actualizar specs/design si cambian

## 1. Setup del proyecto

- [ ] 1.1 `uv init` (tipo app) en la raíz con Python 3.12; `requires-python = ">=3.11"`
- [ ] 1.2 `uv add fastapi "uvicorn[standard]" pydantic-settings python-dotenv openai anthropic`
- [ ] 1.3 `uv add --dev pytest httpx ruff` y configurar `[tool.ruff]` y `[tool.pytest.ini_options]` en `pyproject.toml`
- [ ] 1.4 Crear la estructura `app/` con todos los `__init__.py` (routers, services, context) más `app/schemas.py`
- [ ] 1.5 Verificar: `uv sync --locked` y `uv run python -c "import app"` sin errores

## 2. Configuración

- [ ] 2.1 Implementar `Settings` en `app/config.py` (variables, `SecretStr`, `effective_model`, validador de la key del proveedor activo)
- [ ] 2.2 Implementar `get_settings()` con `lru_cache` y validar la configuración al importar `app.main`, de modo que el arranque falle con un mensaje claro (fail fast)
- [ ] 2.3 Crear `.env.example` con todas las variables sin valores y comentarios; crear `.env` local (no versionado)
- [ ] 2.4 Verificar: tests unitarios de los escenarios de `app-config` (defaults, default por proveedor, key ausente, proveedor inválido, enmascarado)

## 3. Contexto CAG

- [ ] 3.1 Redactar 2–3 ejemplos ficticios en `app/context/examples.py` con tipos de proyecto distintos (p. ej. web de gestión, app móvil, integración/datos), con desglose, total, equipo y duración
- [ ] 3.2 Revisión de los ejemplos por parte del usuario (cifras y realismo)
- [ ] 3.3 Implementar `build_system_prompt()` (rol, instrucciones de formato y ejemplos con etiquetas XML)
- [ ] 3.4 Verificar: tests de que el prompt contiene todos los ejemplos, es determinista, incluye la instrucción de idioma y no contiene la transcripción

## 4. Proveedores LLM y coste

- [ ] 4.1 Definir el `LLMResult` (dataclass), el Protocol `LLMProvider` y `LLMProviderError`
- [ ] 4.2 Implementar `OpenAIProvider` (Chat Completions; tokens desde `usage.prompt_tokens`/`completion_tokens`)
- [ ] 4.3 Implementar `AnthropicProvider` (Messages; `system=` y tokens desde `usage.input_tokens`/`output_tokens`)
- [ ] 4.4 Envolver los errores de ambos SDK en `LLMProviderError`
- [ ] 4.5 Implementar `get_llm_provider()` (factoría según `LLM_PROVIDER`, cacheada)
- [ ] 4.6 Verificar los precios vigentes de `gpt-4o-mini` y `claude-haiku-4-5` en la documentación oficial e implementar `services/pricing.py` con la fecha de verificación
- [ ] 4.7 Implementar `EstimationService.estimate()` (prompt + proveedor + coste)
- [ ] 4.8 Verificar: tests de coste (conocido/desconocido) y del servicio con un `FakeProvider`

## 5. API HTTP

- [ ] 5.1 Definir `EstimateRequest` (strip, no vacío, máx. 20 000) y `EstimateResponse` en `app/schemas.py`
- [ ] 5.2 Implementar `POST /estimate` en `app/routers/estimations.py` con `Depends`, y traducir `LLMProviderError` a 502
- [ ] 5.3 Configurar en `app/main.py` el título, la descripción, el router con prefijo `/api/v1` y `GET /health`
- [ ] 5.4 Verificar: tests de `/health` (200), `/api/v1/estimate` (200, 422 vacío, 422 largo, 502) y `/openapi.json`

## 6. Tests de estructura y conftest

- [ ] 6.1 Crear `tests/conftest.py` (variables de entorno ficticias, `FakeProvider`, `dependency_overrides`, limpieza de cachés)
- [ ] 6.2 Crear `tests/test_structure.py` con la lista de ficheros obligatorios del enunciado
- [ ] 6.3 Verificar: `uv run pytest` en verde sin red ni keys reales

## 7. CI

- [ ] 7.1 Crear `.github/workflows/ci.yml` (push/PR: setup-uv, `uv sync --locked`, `ruff check`, `ruff format --check`, `pytest`, `openspec validate --all --strict` con versión fijada)
- [ ] 7.2 Verificar: `uv run ruff check . && uv run ruff format --check .` en local; workflow en verde tras el push

## 8. Documentación y muestras

- [ ] 8.1 Añadir 1–2 transcripciones de prueba en `samples/` (incluida la del enunciado: landing + HubSpot + blog)
- [ ] 8.2 Actualizar el README: requisitos, instalación, `.env`, arranque, Swagger, ejemplo con `curl` usando `samples/`, tests, arquitectura y desviaciones respecto al enunciado
- [ ] 8.3 Verificación manual con keys reales: `curl` con cada sample en OpenAI y en Anthropic; comprobar que la estimación sigue el formato de los ejemplos

## 9. Cierre

- [ ] 9.1 Recorrer el checklist del enunciado y marcar cada punto
- [ ] 9.2 `openspec validate --all --strict` en verde y archivar el change (`/opsx:archive`) tras la aprobación del usuario
- [ ] 9.3 Push de `session-02` (con la aprobación del usuario) y preparar el texto del email de entrega
