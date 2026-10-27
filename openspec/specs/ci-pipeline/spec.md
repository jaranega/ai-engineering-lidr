# ci-pipeline Specification

## Purpose
Validar automáticamente, en cada push y pull request, la estructura del proyecto, la calidad del código, el comportamiento del servicio (sin llamadas reales a LLMs) y la coherencia de las specs.
## Requirements
### Requirement: Pipeline de integración continua
El repositorio SHALL incluir un workflow de GitHub Actions que se ejecute en cada push y pull request, en este orden: instalación con `uv sync --locked`, `ruff check`, `ruff format --check`, `pytest` y `openspec validate --all --strict`. Si cualquier paso falla, el workflow MUST fallar.

#### Scenario: Cambio correcto
- **WHEN** se hace push de código que cumple lint, formato, tests y specs
- **THEN** el workflow termina en verde

#### Scenario: Test roto
- **WHEN** un test falla
- **THEN** el workflow termina en rojo e indica el paso de pytest

### Requirement: Tests sin coste ni red
La suite de tests MUST ejecutarse sin API keys reales y sin llamadas de red a proveedores LLM, sustituyendo el proveedor por un doble de prueba mediante la inyección de dependencias de FastAPI en la API y mediante la sustitución del proveedor en la app Streamlit.

#### Scenario: Ejecución en CI
- **GIVEN** un entorno sin API keys reales
- **WHEN** se ejecuta `uv run pytest`
- **THEN** todos los tests pasan y no se realiza ninguna llamada a OpenAI ni a Anthropic

### Requirement: Validación de la estructura del proyecto
La suite SHALL incluir un test que verifique la existencia de la estructura del enunciado: `app/__init__.py`, `app/main.py`, `app/config.py`, `app/routers/{__init__,estimations}.py`, `app/services/{__init__,llm_service}.py`, `app/context/{__init__,examples}.py`, `streamlit_app.py`, `.env.example`, `.gitignore`, `pyproject.toml` y `README.md`.

#### Scenario: Falta un fichero obligatorio
- **WHEN** se elimina `app/context/examples.py`
- **THEN** el test de estructura falla indicando el fichero ausente

#### Scenario: Falta la app Streamlit
- **WHEN** se elimina `streamlit_app.py`
- **THEN** el test de estructura falla indicando el fichero ausente

### Requirement: Tests de comportamiento del servicio
La suite SHALL cubrir, como mínimo: `GET /health` (200), `POST /api/v1/estimate` correcto con el proveedor simulado, el 422 por transcripción vacía, el 502 por fallo del proveedor, la inclusión de los ejemplos CAG en el system prompt, el streaming de ambos proveedores con un cliente de SDK simulado y la app Streamlit ejecutada sin navegador (`streamlit.testing.v1.AppTest`) con el proveedor simulado: respuesta en el historial, conversación multi-turno, error del proveedor, error de configuración y panel lateral.

#### Scenario: Cobertura mínima
- **WHEN** se ejecuta `uv run pytest`
- **THEN** existen y pasan tests para cada uno de los casos listados

