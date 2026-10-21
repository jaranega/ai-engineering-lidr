## Why

Sesión 02 del programa AI Engineering (LIDR): construir la base del Proyecto 1, un servicio que convierte la transcripción de una reunión con cliente en una estimación de software generada por un LLM con arquitectura **CAG** (el contexto estático viaja en cada llamada, sin base de datos ni retrieval). Enunciado de referencia: `AIEngineer-notas/02_CAG/00_ejercicio-scaffolding-fastapi.md`. Es la base sobre la que se construyen las sesiones S03 (Streamlit, streaming, wrapper de proveedores) y S04 (prompts versionados con Jinja2), así que la separación por capas debe quedar bien resuelta desde el principio.

## What Changes

- Proyecto Python gestionado con `uv` (`pyproject.toml`, `.python-version`), con la estructura de carpetas del enunciado: `app/{main,config}.py`, `app/routers/`, `app/services/`, `app/context/`.
- Configuración tipada con `pydantic-settings`, que se carga desde `.env`. Se acompaña de `.env.example`, y `.env` queda en `.gitignore`.
- Contexto CAG estático: 2–3 ejemplos ficticios de estimaciones previas (resumen de la reunión y estimación desglosada) en `app/context/examples.py`.
- Servicio LLM que construye el system prompt (rol + ejemplos inyectados), envía la transcripción como mensaje `user` y devuelve la estimación, el uso de tokens y el coste estimado.
- Soporte para **OpenAI y Anthropic** detrás de una interfaz mínima de proveedor, que se elige con `LLM_PROVIDER`.
- Endpoint `POST /api/v1/estimate` y endpoint `GET /health`, documentados en Swagger (`/docs`).
- Transcripciones de prueba en `samples/`, referenciadas desde el README.
- Calidad: `ruff` (lint + format), `pytest` (estructura, `/health` y `/estimate` con el LLM mockeado) y un pipeline de **GitHub Actions** que lo ejecuta todo junto con `openspec validate`.

### Desviaciones respecto al enunciado

- **`LLM_MODEL` es opcional y su default depende del proveedor** (`gpt-4o-mini` con OpenAI, `claude-haiku-4-5` con Anthropic). Con el default literal del enunciado (`gpt-4o-mini`) y `LLM_PROVIDER=anthropic`, la llamada fallaría.
- **Campos extra en la respuesta**: `input_tokens`, `output_tokens` y `cost_usd`. El enunciado lo permite de forma explícita.
- **Ficheros adicionales** a la estructura mínima: `app/schemas.py` (contratos HTTP), `app/services/pricing.py`, `tests/`, `samples/` y `.github/workflows/ci.yml`.

## Capabilities

### New Capabilities

- `app-config`: carga y validación de la configuración desde las variables de entorno y `.env`, y resolución del modelo por defecto según el proveedor.
- `cag-prompt`: contexto estático de ejemplos y construcción de los mensajes `system`/`user` según el patrón CAG.
- `llm-provider`: abstracción mínima sobre OpenAI y Anthropic (selección, llamada, uso de tokens, coste estimado y errores).
- `estimation-api`: contrato HTTP de `POST /api/v1/estimate` y `GET /health`, más la documentación OpenAPI.
- `ci-pipeline`: validación automática de la estructura, la calidad y el funcionamiento del servicio en cada push o PR.

### Modified Capabilities

<!-- Ninguna: es el primer change del proyecto. -->

## Impact

- **Código nuevo**: `app/`, `tests/`, `samples/`, `.github/workflows/ci.yml`, `pyproject.toml`, `.env.example`, y el README actualizado con las instrucciones de instalación y uso.
- **Dependencias de runtime**: `fastapi`, `uvicorn[standard]`, `pydantic-settings`, `python-dotenv`, `openai`, `anthropic`.
- **Dependencias de desarrollo**: `pytest`, `httpx` (TestClient), `ruff`.
- **Sistemas externos**: las APIs de OpenAI y Anthropic, que requieren API keys reales solo para el uso manual, nunca en tests ni en la CI.
- **Git**: rama `session-02`, que es la que se entrega.

## Fuera de alcance

Queda para sesiones posteriores, de acuerdo con el programa:

- Streaming de respuestas e interfaz Streamlit (S03).
- Fallback entre proveedores, caché de respuestas y logging estructurado con structlog (S03).
- Formulario tipado de entrada y prompts en templates Jinja2 versionados (S04).
- Salida JSON estructurada del LLM, guardrails y caché semántica (S04+).
- Persistencia, autenticación, rate limiting y despliegue.
