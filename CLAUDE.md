# CLAUDE.md — Estimador de software con LLMs (AI Engineering 2026/09, LIDR)

Contexto persistente para todas las sesiones de Claude Code en este repositorio.

## Qué es este proyecto

Servicio que recibe la **transcripción de una reunión con cliente** y devuelve una **estimación de software** generada por un LLM.
Es el "Proyecto 1" del programa AI Engineering de LIDR y **evoluciona sesión a sesión** en este mismo repo:

| Sesión | Hito | Rama |
|---|---|---|
| S02 | Scaffolding FastAPI + arquitectura **CAG** (contexto estático en el prompt), `POST /api/v1/estimate`, `GET /health`, CI | `session-02` |
| S03 | Cliente Streamlit (chat + streaming), wrapper de proveedores con fallback, caché exact-match, logging con structlog | `session-03` |
| S04 | Formulario tipado (Pydantic + enums), prompts Jinja2 versionados (`prompts/estimation/v1/`), tests de templates | `pre-session-04` / `session-04` |
| S05+ | Contexto dinámico, memoria, prompts adaptativos, evaluación, RAG… | — |

Enunciados y material de referencia: `/Users/javiaranega/claude/AIEngineer-notas/` (carpeta por módulo, `00_ejercicio-*.md` es el enunciado de cada sesión).
Solución oficial (solo si hay bloqueo total): https://github.com/LIDR-academy/ai-engineering (ramas `session_N`).

## Reglas de colaboración (IMPORTANTE)

- **El criterio del usuario es el último.** No des nada por hecho: ante cualquier ambigüedad de alcance, diseño, naming o dependencia, **pregunta** antes de decidir.
- Trabajamos con **Spec-Driven Development usando OpenSpec**: ningún cambio de comportamiento se implementa sin un *change* aprobado en `openspec/changes/`.
- Cada decisión de diseño no trivial queda registrada en el `design.md` del change correspondiente (con alternativas descartadas).
- Lo que se diseñe hoy debe **habilitar** la siguiente sesión sin sobreingeniería: abstracciones mínimas, sin implementar adelantado lo que el curso trabajará en directo.

## Flujo SDD con OpenSpec

1. `/opsx:explore` — pensar/aclarar antes de proponer (opcional).
2. `/opsx:propose <idea>` — crea `openspec/changes/<change>/` con `proposal.md`, `design.md`, `specs/` (deltas) y `tasks.md`.
3. **Revisión humana** del change. No se implementa hasta que el usuario lo apruebe.
4. `/opsx:apply` — implementar tareas marcándolas en `tasks.md`.
5. `openspec validate --strict` + tests + ruff en verde.
6. `/opsx:archive` — mueve el change a `archive/` y actualiza `openspec/specs/` (la fuente de verdad).

Naming de changes: `sNN-<verbo>-<qué>` en kebab-case (p. ej. `s02-add-cag-estimation-service`), para trazar change ↔ sesión.

## Git y entregas

- Repo único **público** en GitHub con todos los ejercicios. `main` contiene la base y el último estado entregado.
- **Una rama por sesión** con ejercicio (`session-02`, `session-03`, … o el nombre que exija el enunciado). Cada rama parte de la anterior; tras la entrega se mergea a `main`.
- Entrega: email con la sesión y el enlace completo a la rama (destinatario indicado en cada enunciado / programa; Sisley: sisley@lidr.co, mismo hilo para todas las entregas).
- Commits pequeños con **Conventional Commits en inglés** (`feat:`, `fix:`, `chore:`, `docs:`, `test:`, `ci:`). Nunca commitear `.env`.
- No hacer commit/push/PR sin que el usuario lo pida.

## Idioma

- Specs de OpenSpec, README y documentación: **español**.
- Código, identificadores, comentarios, docstrings y mensajes de commit: **inglés**.

## Stack y convenciones técnicas

- **Python 3.12** (mínimo 3.11), gestor **uv** (`uv add`, `uv run`; nunca `pip install`).
- **FastAPI** + **uvicorn**, **Pydantic v2** y **pydantic-settings** para configuración desde `.env`.
- Proveedores LLM: **OpenAI** (`gpt-4o-mini`) y **Anthropic** (`claude-haiku-4-5`), seleccionables con `LLM_PROVIDER`. Usar SDKs oficiales (`openai`, `anthropic`).
- Calidad: **ruff** (lint + format) y **pytest**. Los tests **nunca** llaman a APIs reales (LLM mockeado).
- CI: **GitHub Actions** en cada push/PR → `uv sync`, `ruff check`, `ruff format --check`, `pytest`, `openspec validate --all --strict`.

### Arquitectura (capas)

```
app/
├── main.py          # FastAPI app, routers, /health
├── config.py        # Settings (BaseSettings) + get_settings()
├── routers/         # HTTP: validación de entrada/salida, sin lógica de negocio
├── services/        # Lógica de negocio: construcción de prompt y llamada al LLM
└── context/         # Datos estáticos inyectados en el prompt (CAG)
```

- Routers **no** llaman a SDKs de LLM directamente; dependen de `services/` vía `Depends` (facilita mocks en tests).
- Secrets solo vía variables de entorno; nunca en código, logs ni respuestas.
- Patrón de mensajes: `system` (instrucciones + ejemplos CAG) → `user` (transcripción). En Anthropic el system va en el parámetro `system`, no como mensaje.

## Comandos habituales

```bash
uv sync                                   # instalar dependencias
uv run uvicorn app.main:app --reload      # arrancar servidor (Swagger en /docs)
uv run pytest                             # tests
uv run ruff check . && uv run ruff format --check .
openspec list / openspec validate --all --strict
```
