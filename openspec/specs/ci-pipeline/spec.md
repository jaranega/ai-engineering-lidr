# ci-pipeline Specification

## Purpose
Validar automáticamente, en cada push y pull request, la estructura del proyecto, la calidad del código, el comportamiento del servicio (sin llamadas reales a LLMs) y la coherencia de las specs.
## Requirements
### Requirement: Pipeline de integración continua
El repositorio SHALL incluir un workflow de GitHub Actions que se ejecute en cada push y pull request con dos jobs en paralelo:
- `checks`, en este orden: instalación con `uv sync --locked`, `ruff check`, `ruff format --check`, `pytest` y `openspec validate --all --strict`;
- `docker`: `docker build .` (sin publicar la imagen) y `docker compose config --quiet` (con un `.env` vacío creado en el propio job).

Si cualquier paso falla, el workflow MUST fallar.

#### Scenario: Cambio correcto
- **WHEN** se hace push de código que cumple lint, formato, tests y specs, y cuya imagen se construye
- **THEN** el workflow termina en verde

#### Scenario: Test roto
- **WHEN** un test falla
- **THEN** el workflow termina en rojo e indica el paso de pytest

#### Scenario: Imagen rota
- **WHEN** el `Dockerfile` no se puede construir (p. ej. `uv.lock` desincronizado o un fichero necesario fuera del contexto de build)
- **THEN** el job `docker` termina en rojo

### Requirement: Tests sin coste ni red
La suite de tests MUST ejecutarse sin API keys reales, sin servidor Redis, sin servidor HTTP real y sin ninguna llamada de red (ni a proveedores LLM ni a recursos que LiteLLM pueda descargar, como su mapa de precios), sustituyendo el proveedor por un doble de prueba mediante la inyección de dependencias de FastAPI en la API, la llamada de LiteLLM por un doble en los tests del proveedor, Redis por `fakeredis`, y el cliente HTTP de la app Streamlit por un `httpx.Client` con transporte simulado (`httpx.MockTransport` o el transporte ASGI de la propia app).

#### Scenario: Ejecución en CI
- **GIVEN** un entorno sin API keys reales ni Redis
- **WHEN** se ejecuta `uv run pytest`
- **THEN** todos los tests pasan y no se realiza ninguna llamada de red

#### Scenario: Mapa de precios de LiteLLM local
- **WHEN** se importa la aplicación
- **THEN** LiteLLM carga su mapa de modelos desde la copia local incluida en el paquete, sin descargarlo

#### Scenario: Streamlit sin API real
- **WHEN** se ejecutan los tests de la app Streamlit
- **THEN** todas sus peticiones HTTP las atiende el transporte simulado

### Requirement: Validación de la estructura del proyecto
La suite SHALL incluir un test que verifique la existencia de la estructura del enunciado: `app/__init__.py`, `app/main.py`, `app/config.py`, `app/routers/{__init__,estimations}.py`, `app/services/{__init__,llm_service}.py`, `app/context/{__init__,examples}.py`, `app/prompts/loader.py`, `app/prompts/estimation/v1/{system,user,examples}.j2`, `streamlit_app.py`, `.env.example`, `.gitignore`, `pyproject.toml`, `README.md`, `Dockerfile`, `.dockerignore` y `docker-compose.yml`.

#### Scenario: Falta un fichero obligatorio
- **WHEN** se elimina `app/context/examples.py`
- **THEN** el test de estructura falla indicando el fichero ausente

#### Scenario: Falta la app Streamlit
- **WHEN** se elimina `streamlit_app.py`
- **THEN** el test de estructura falla indicando el fichero ausente

#### Scenario: Falta un fichero de Docker
- **WHEN** se elimina `docker-compose.yml`
- **THEN** el test de estructura falla indicando el fichero ausente

#### Scenario: Falta un template
- **WHEN** se elimina `app/prompts/estimation/v1/examples.j2`
- **THEN** el test de estructura falla indicando el fichero ausente

### Requirement: Tests de comportamiento del servicio
La suite SHALL cubrir, como mínimo: `GET /health` (200, también con Redis caído), `POST /api/v1/estimate` correcto con el proveedor simulado, el 422 por `description` fuera de límites o enums inválidos, el 422 por `prompt_version` desconocida, el 502 por fallo del proveedor, los templates de prompt (los tres tests del enunciado en `tests/prompts/test_estimation_v1.py`: descripción dentro de `<project_description>`, palabra clave de `phases_table` presente con ese formato y ausente con `narrative`, e instrucción de asunciones por fase presente con `detailed` y ausente con `summary`; además el loader, `v2` y `reference_projects`), la inclusión de los ejemplos CAG en el system prompt, la evaluación por formato, el proveedor LiteLLM (generación, streaming, truncado, fallback, errores) con LiteLLM simulado, la caché (acierto, fallo, qué no se guarda, streaming, Redis caído) con `fakeredis`, los eventos de log y la ausencia de datos sensibles en ellos, y la app Streamlit ejecutada sin navegador (`streamlit.testing.v1.AppTest`) con la API simulada por httpx: envío del formulario con el JSON tipado, validación en cliente, resultado con métricas y validación, estado HTTP de error, API no disponible y que la app no importa `app`. Los tests de templates MUST ejecutarse sin LLM ni red.

#### Scenario: Cobertura mínima
- **WHEN** se ejecuta `uv run pytest`
- **THEN** existen y pasan tests para cada uno de los casos listados

#### Scenario: Tests de template rápidos
- **WHEN** se ejecuta `uv run pytest tests/prompts`
- **THEN** los tests pasan sin proveedor LLM, sin Redis y sin red

