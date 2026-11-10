## ADDED Requirements

### Requirement: Imagen Docker de la API
El repositorio SHALL incluir un `Dockerfile` multi-stage que construya la API con `uv` a partir de `uv.lock` (`uv sync --locked`), con la versión de la imagen de `uv` fijada (nunca `latest`). La imagen final MUST contener solo el entorno virtual y `app/`, ejecutarse con un usuario no root, exponer el puerto 8000, arrancar `uvicorn app.main:app` y declarar un `HEALTHCHECK` contra `/health`. MUST NOT incluir las dependencias de la interfaz (grupo `ui`: `streamlit`, `httpx`), ni las de desarrollo, ni ningún secret ni fichero `.env`.

#### Scenario: Construcción
- **WHEN** se ejecuta `docker build .` en la raíz del repositorio
- **THEN** la imagen se construye sin errores con las versiones de `uv.lock`

#### Scenario: Usuario no root
- **WHEN** se inspecciona la imagen
- **THEN** el usuario configurado no es `root`

#### Scenario: Sin dependencias de la interfaz
- **WHEN** se ejecuta `python -c "import streamlit"` dentro de la imagen
- **THEN** falla porque `streamlit` no está instalado

### Requirement: Contexto de build en lista blanca
El repositorio SHALL incluir un `.dockerignore` que excluya todo por defecto y solo permita `pyproject.toml`, `uv.lock` y `app/` (sin `__pycache__`), de modo que `.env` y cualquier fichero nuevo no entren en el contexto de build.

#### Scenario: .env fuera de la imagen
- **GIVEN** un `.env` con API keys en la raíz del repositorio
- **WHEN** se construye la imagen
- **THEN** el fichero `.env` no existe dentro de la imagen

### Requirement: docker-compose con Redis
El repositorio SHALL incluir un `docker-compose.yml` con dos servicios: `api` (construida con el `Dockerfile`, configuración desde `env_file: .env` y `REDIS_URL` apuntando al servicio `redis`, que prevalece sobre `.env`) y `redis` (imagen con versión fijada, sin persistencia en disco, con healthcheck). `api` MUST esperar a que `redis` esté sano. Los puertos publicados MUST estar ligados a `127.0.0.1`. Streamlit MUST NOT formar parte del compose.

#### Scenario: Arranque completo
- **GIVEN** un `.env` con la API key del modelo primario
- **WHEN** se ejecuta `docker compose up`
- **THEN** `GET http://localhost:8000/health` responde 200 con `cache = "ok"`

#### Scenario: Caché sin persistencia
- **GIVEN** respuestas guardadas en la caché del compose
- **WHEN** se reinicia el servicio `redis`
- **THEN** la caché está vacía

#### Scenario: Puertos locales
- **WHEN** se inspecciona `docker compose config`
- **THEN** los puertos publicados de `api` (8000) y `redis` (6379) están ligados a `127.0.0.1`
