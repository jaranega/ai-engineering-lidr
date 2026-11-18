## MODIFIED Requirements

### Requirement: Validación de la estructura del proyecto
La suite SHALL incluir un test que verifique la existencia de la estructura del enunciado:

- **Aplicación:**
  - `app/__init__.py`, `app/main.py` y `app/config.py`;
  - `app/routers/{__init__,estimations,sessions}.py`;
  - `app/services/{__init__,llm_service,session_service,memory,critic,boss}.py`;
  - `app/context/{__init__,examples}.py`;
  - `app/sessions/{__init__,models,store,metadata,tier}.py` y `app/sessions/compression/{__init__,anchors,policy}.py`;
  - `app/attachments/{__init__,extractor}.py`.
- **Prompts:**
  - `app/prompts/loader.py`;
  - `app/prompts/estimation/v3/{system,user,examples}.j2`;
  - `app/prompts/estimation/v4/{system,user,examples,project_metadata}.j2`;
  - `app/prompts/estimation/v5/{system,user,examples,project_metadata,conversation_summary,audience}.j2`;
  - `app/prompts/estimation/v6/{system,user,examples,project_metadata,conversation_summary,audience}.j2`;
  - `app/prompts/conversation_summary/v1/{system,user}.j2`, `app/prompts/anchor_classifier/v1/{system,user}.j2` y `app/prompts/critic/v1/{system,user}.j2`.
- **Evals:** `evals/{__init__,dataset,metrics,run}.py` y `evals/golden_dataset.json`.
- **Raíz:** `streamlit_app.py`, `.env.example`, `.gitignore`, `pyproject.toml`, `README.md`, `Dockerfile`, `.dockerignore` y `docker-compose.yml`.

El test MUST comprobar también:
- que `app/sessions` (incluidos `compression/` y `tier.py`) y `app/attachments` no importan `app.services`, `app.prompts`, LiteLLM ni Instructor;
- que ningún módulo de `app/` importa `evals`;
- que `app/services/boss.py` no importa LiteLLM ni Instructor.

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
- **WHEN** se elimina `app/prompts/estimation/v6/user.j2` o `app/prompts/critic/v1/system.j2`
- **THEN** el test de estructura falla indicando el fichero ausente

#### Scenario: Falta el golden dataset
- **WHEN** se elimina `evals/golden_dataset.json`
- **THEN** el test de estructura falla indicando el fichero ausente

#### Scenario: Capas sin dependencias del LLM
- **WHEN** un módulo de `app/sessions` (incluido `app/sessions/compression`) o `app/attachments` importa `app.services`, `app.prompts` o LiteLLM
- **THEN** el test de estructura falla

#### Scenario: La aplicación no depende de los evals
- **WHEN** un módulo de `app/` importa `evals`
- **THEN** el test de estructura falla

## ADDED Requirements

### Requirement: Cobertura del Actor-Critic-Boss y de los evals
La suite SHALL cubrir, sin red ni coste:
- **El Boss:** todas las ramas de `run_review`: aceptación, reintento, rechazo, presupuesto agotado, fuera de alcance, Critic caído, fallo del Actor en el reintento y una sola iteración.
- **La síntesis:** avisos en los dos idiomas, suelo de confianza y tope de supuestos.
- **Los validadores** de `CriticFeedback`.
- **El render:** el de `v6` con y sin feedback y su igualdad con `v5`; el golden de `v5`; el prompt del Critic.
- **El turno con `review=true`** por HTTP con un proveedor simulado: traza, métricas sumadas, historial guardado una vez, 422 con `v5` y eventos sin contenido.
- **Los guardrails** de las etiquetas nuevas.
- **La configuración nueva.**
- **La casilla y la traza de Streamlit.**
- **El dataset, las métricas y el runner de evals** con un proveedor simulado.

CI MUST ejecutar `ruff check` y `ruff format --check` también sobre `evals/`, y MUST NOT ejecutar la CLI de evals.

#### Scenario: Suite verde sin claves
- **WHEN** se ejecuta `uv run pytest` sin API keys ni red
- **THEN** pasan los tests del Actor-Critic-Boss y de los evals sin ninguna llamada externa

#### Scenario: Ruff sobre los evals
- **WHEN** CI ejecuta `ruff check .` con un error de estilo en `evals/metrics.py`
- **THEN** el job falla
