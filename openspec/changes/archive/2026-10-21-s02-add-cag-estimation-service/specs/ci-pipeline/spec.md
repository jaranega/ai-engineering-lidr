## ADDED Requirements

### Requirement: Pipeline de integración continua
El repositorio SHALL incluir un workflow de GitHub Actions que se ejecute en cada push y pull request, en este orden: instalación con `uv sync --locked`, `ruff check`, `ruff format --check`, `pytest` y `openspec validate --all --strict`. Si cualquier paso falla, el workflow MUST fallar.

#### Scenario: Cambio correcto
- **WHEN** se hace push de código que cumple lint, formato, tests y specs
- **THEN** el workflow termina en verde

#### Scenario: Test roto
- **WHEN** un test falla
- **THEN** el workflow termina en rojo e indica el paso de pytest

### Requirement: Tests sin coste ni red
La suite de tests MUST ejecutarse sin API keys reales y sin llamadas de red a proveedores LLM, sustituyendo el proveedor por un doble de prueba mediante la inyección de dependencias de FastAPI.

#### Scenario: Ejecución en CI
- **GIVEN** un entorno sin API keys reales
- **WHEN** se ejecuta `uv run pytest`
- **THEN** todos los tests pasan y no se realiza ninguna llamada a OpenAI ni a Anthropic

### Requirement: Validación de la estructura del proyecto
La suite SHALL incluir un test que verifique la existencia de la estructura del enunciado: `app/__init__.py`, `app/main.py`, `app/config.py`, `app/routers/{__init__,estimations}.py`, `app/services/{__init__,llm_service}.py`, `app/context/{__init__,examples}.py`, `.env.example`, `.gitignore`, `pyproject.toml` y `README.md`.

#### Scenario: Falta un fichero obligatorio
- **WHEN** se elimina `app/context/examples.py`
- **THEN** el test de estructura falla indicando el fichero ausente

### Requirement: Tests de comportamiento del servicio
La suite SHALL cubrir, como mínimo: `GET /health` (200), `POST /api/v1/estimate` correcto con el proveedor simulado, el 422 por transcripción vacía, el 502 por fallo del proveedor y la inclusión de los ejemplos CAG en el system prompt.

#### Scenario: Cobertura mínima
- **WHEN** se ejecuta `uv run pytest`
- **THEN** existen y pasan tests para cada uno de los casos listados
