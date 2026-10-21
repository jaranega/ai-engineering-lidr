# Estimador de software con LLMs

Proyecto del programa **AI-Engineering 2026/10 (LIDR)**. Servicio que recibe la transcripción de una reunión con cliente y devuelve una estimación de software generada por un LLM. Evoluciona sesión a sesión.

## Entregas por sesión

| Sesión | Ejercicio | Rama |
|---|---|---|
| S02 | Scaffolding FastAPI + arquitectura CAG | `session-02` |

## Arquitectura

Usa **CAG (Context-Augmented Generation)**: el contexto estático (ejemplos de estimaciones previas) se inyecta en el `system` prompt de cada llamada, sin base de datos ni retrieval. La transcripción viaja como mensaje `user`. Se puede elegir entre **OpenAI** y **Anthropic** con `LLM_PROVIDER`.

```
app/
├── main.py          # FastAPI app, lifespan (fail fast), /health
├── config.py        # Settings (pydantic-settings) + get_settings()
├── schemas.py       # Contratos HTTP (EstimationRequest / EstimationResponse)
├── routers/         # HTTP: validación de E/S y mapeo de errores; sin lógica de negocio
│   └── estimations.py
├── services/        # Lógica de negocio: prompt, proveedores LLM, coste
│   ├── llm_service.py
│   └── pricing.py
└── context/         # Datos estáticos inyectados en el prompt (CAG)
    └── examples.py
tests/               # pytest, con el LLM simulado
samples/             # Transcripciones de prueba
```

Los routers dependen de los servicios mediante `Depends` y nunca importan los SDKs de los LLM, lo que permite sustituir el proveedor en los tests.

## Requisitos

- Python 3.11 o superior (el proyecto fija 3.12 en `.python-version`)
- [uv](https://docs.astral.sh/uv/)
- API key de OpenAI y/o de Anthropic (solo la del proveedor activo es obligatoria)

## Instalación

```bash
uv sync
```

## Configuración

```bash
cp .env.example .env
# edita .env y rellena la API key del proveedor que vayas a usar
```

| Variable | Obligatoria | Default | Descripción |
|---|---|---|---|
| `OPENAI_API_KEY` | Si `LLM_PROVIDER=openai` | — | API key de OpenAI |
| `ANTHROPIC_API_KEY` | Si `LLM_PROVIDER=anthropic` | — | API key de Anthropic |
| `LLM_PROVIDER` | No | `openai` | `openai` o `anthropic` |
| `LLM_MODEL` | No | `gpt-4o-mini` (openai) / `claude-haiku-4-5` (anthropic) | Modelo; el default depende del proveedor |
| `APP_ENV` | No | `development` | Entorno de ejecución |
| `LOG_LEVEL` | No | `DEBUG` | `DEBUG`, `INFO`, `WARNING`, `ERROR` o `CRITICAL` |

Las variables de entorno prevalecen sobre `.env`. Las keys vacías se tratan como no definidas y nunca se registran en logs ni en respuestas.

Con `LOG_LEVEL=DEBUG`, los loggers de terceros (`openai`, `anthropic`, `httpx`, `httpx2`, `httpcore`, `httpcore2`) se mantienen en `INFO` como mínimo para no volcar en el log el prompt con la transcripción del cliente.

> `python-dotenv` se incluye como dependencia porque lo pide el enunciado, aunque `pydantic-settings` ya lee `.env` por sí mismo.

## Arranque

```bash
uv run uvicorn app.main:app --reload
```

- Swagger UI: <http://localhost:8000/docs>
- Salud: `GET http://localhost:8000/health`

**Fail fast**: si falta la API key del proveedor activo, la aplicación no arranca y muestra un mensaje indicando la variable que falta y cómo resolverlo (`Application startup failed`). Con `--reload`, el supervisor de uvicorn sigue vivo esperando cambios en los ficheros: corrige `.env` o el código y se reintenta el arranque.

## Uso

Ejemplo del enunciado:

```bash
curl -X POST http://localhost:8000/api/v1/estimate \
  -H 'Content-Type: application/json' \
  -d '{
    "transcription": "En la reunión con el equipo de marketing, el cliente explicó que necesita una landing page con formulario de contacto, integración con su CRM actual (HubSpot), y una sección de blog con editor WYSIWYG. El plazo ideal sería tenerlo listo en 4 semanas. El diseño ya existe en Figma."
  }'
```

Con las transcripciones de `samples/`:

```bash
jq -Rs '{transcription: .}' samples/landing-hubspot-blog.txt \
  | curl -s -X POST http://localhost:8000/api/v1/estimate -H 'Content-Type: application/json' -d @- | jq

jq -Rs '{transcription: .}' samples/reserva-restaurantes-en.txt \
  | curl -s -X POST http://localhost:8000/api/v1/estimate -H 'Content-Type: application/json' -d @- | jq
```

### Contrato de respuesta

Ejemplo ilustrativo de la forma de la respuesta (los valores son de muestra, no de una llamada real):

```json
{
  "estimation": "## Estimación\n...",
  "model": "gpt-4o-mini",
  "provider": "openai",
  "input_tokens": 3200,
  "output_tokens": 650,
  "cost_usd": 0.00087
}
```

`cost_usd` es aproximado y vale `null` si el modelo no tiene precio conocido. `model` es el modelo solicitado según la configuración.

### Códigos de estado

| Código | Cuándo |
|---|---|
| 200 | Estimación generada |
| 422 | Entrada inválida: falta `transcription`, está vacía (tras recortar espacios) o supera los 20 000 caracteres |
| 502 | Falla el proveedor LLM (error de red, autenticación, límite de uso, etc.) |

## Tests y calidad

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
openspec validate --all --strict
```

Los tests no usan API keys reales ni hacen llamadas de red: el proveedor se sustituye por un doble mediante `dependency_overrides`. La CI (`.github/workflows/ci.yml`) ejecuta en cada push y PR: `uv sync --locked`, ruff (lint y formato), pytest y `openspec validate --all --strict`.

## Desviaciones respecto al enunciado

- **`LLM_MODEL` es opcional** y su default depende del proveedor. Con el default literal del enunciado (`gpt-4o-mini`) y `LLM_PROVIDER=anthropic`, la llamada fallaría.
- **Campos extra en la respuesta**: `input_tokens`, `output_tokens` y `cost_usd` (el enunciado lo permite).
- **Ficheros adicionales**: `app/schemas.py`, `app/services/pricing.py`, `tests/`, `samples/` y `.github/workflows/ci.yml`.
- **Naming** de los contratos HTTP: `EstimationRequest` y `EstimationResponse`.
- **502** cuando falla el proveedor LLM, además de los códigos habituales.
- **Riesgo con `claude-haiku-4-5`**: es un modelo considerado legacy y Anthropic podría retirarlo; si deja de estar disponible, se configura otro con `LLM_MODEL`.

Detalle completo de las decisiones: [`openspec/changes/s02-add-cag-estimation-service`](openspec/changes/s02-add-cag-estimation-service/).

## Metodología

Spec-Driven Development con [OpenSpec](https://github.com/Fission-AI/OpenSpec):

- `openspec/specs/`: especificaciones vigentes (fuente de verdad).
- `openspec/changes/`: cambios propuestos o en curso (proposal, design, specs delta y tasks).
- `CLAUDE.md`: contexto y convenciones del proyecto para los agentes.
