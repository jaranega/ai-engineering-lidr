## MODIFIED Requirements

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
La suite SHALL incluir un test que verifique la existencia de la estructura del enunciado: `app/__init__.py`, `app/main.py`, `app/config.py`, `app/routers/{__init__,estimations}.py`, `app/services/{__init__,llm_service}.py`, `app/context/{__init__,examples}.py`, `streamlit_app.py`, `.env.example`, `.gitignore`, `pyproject.toml`, `README.md`, `Dockerfile`, `.dockerignore` y `docker-compose.yml`.

#### Scenario: Falta un fichero obligatorio
- **WHEN** se elimina `app/context/examples.py`
- **THEN** el test de estructura falla indicando el fichero ausente

#### Scenario: Falta la app Streamlit
- **WHEN** se elimina `streamlit_app.py`
- **THEN** el test de estructura falla indicando el fichero ausente

#### Scenario: Falta un fichero de Docker
- **WHEN** se elimina `docker-compose.yml`
- **THEN** el test de estructura falla indicando el fichero ausente

### Requirement: Tests de comportamiento del servicio
La suite SHALL cubrir, como mínimo: `GET /health` (200, también con Redis caído), `POST /api/v1/estimate` correcto con el proveedor simulado, el 422 por transcripción vacía, el 502 por fallo del proveedor, la inclusión de los ejemplos CAG en el system prompt, el proveedor LiteLLM (generación, streaming, truncado, fallback, errores) con LiteLLM simulado, la caché (acierto, fallo, qué no se guarda, streaming, Redis caído) con `fakeredis`, los eventos de log y la ausencia de datos sensibles en ellos, el endpoint SSE (orden y formato de los eventos, payload de `metrics`, `error` antes y a mitad del stream, respuesta vacía, validación 422, prompt por turno y desconexión sin guardar en caché), `GET /api/v1/context`, y la app Streamlit ejecutada sin navegador (`streamlit.testing.v1.AppTest`) con la API simulada por httpx: respuesta en el historial, conversación multi-turno, petición enviada, keep-alive, evento `error`, stream sin evento terminal, estado HTTP de error, API no disponible, panel lateral y que la app no importa `app`.

#### Scenario: Cobertura mínima
- **WHEN** se ejecuta `uv run pytest`
- **THEN** existen y pasan tests para cada uno de los casos listados
