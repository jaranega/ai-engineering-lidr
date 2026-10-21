## Context

El repositorio parte de cero: solo contiene la configuración de OpenSpec y las convenciones (`CLAUDE.md`). El enunciado de S02 define una estructura mínima (`app/{routers,services,context}`) y un flujo **recibir texto → inyectar contexto → llamar al LLM → devolver respuesta**. Las sesiones siguientes ya están definidas en el programa:

- **S03**: Streamlit con streaming, wrapper de proveedores con fallback, caché exact-match y structlog.
- **S04**: `app/schemas.py` con enums, prompts en `app/prompts/estimation/v1/*.j2` con un loader y tests de templates.

Restricciones: Python 3.12 con `uv`; tests y CI sin API keys reales; y el usuario decide cualquier punto ambiguo.

## Goals / Non-Goals

**Goals:**

- Flujo CAG funcional de punta a punta con OpenAI y Anthropic.
- Capas con dependencias en un único sentido: `routers → services → context`, con `config` transversal.
- Que el proveedor se pueda sustituir en tests mediante `Depends` (sin red).
- Una CI que demuestre automáticamente que la estructura y el servicio funcionan.
- Que S03 y S04 sean cambios aditivos o mecánicos, sin reescrituras.

**Non-Goals:**

- Streaming, fallback, caché, structlog, Jinja2, salida JSON estructurada (ver *Fuera de alcance* en `proposal.md`).
- Contenedores, despliegue, autenticación y persistencia.

## Decisions

### D1. Estructura de módulos

```
app/
├── main.py              # create app, include router (prefix /api/v1), /health
├── config.py            # Settings + get_settings() (lru_cache)
├── schemas.py           # EstimateRequest / EstimateResponse (HTTP contract)
├── routers/estimations.py
├── services/
│   ├── llm_service.py   # build_system_prompt, LLMProvider protocol, OpenAI/Anthropic, get_llm_provider, EstimationService
│   └── pricing.py       # MODEL_PRICES + estimate_cost()
└── context/examples.py  # ESTIMATION_EXAMPLES
```

- **Por qué `schemas.py` en la raíz de `app/`**: es la ubicación que impone S04. Así evitamos moverlo más adelante.
- **Por qué los proveedores van dentro de `llm_service.py`**: el enunciado sitúa la llamada al LLM en ese fichero, y con dos proveedores sigue siendo legible. En S03 (wrapper + fallback) se extraen a `services/providers/` con un movimiento mecánico.
- *Alternativa descartada*: crear ya `services/providers/{base,openai,anthropic}.py`. Es sobreingeniería para S02 y se aleja de la estructura que se revisa.

### D2. Abstracción de proveedor: `Protocol` + factoría

`LLMProvider` es un `typing.Protocol` con `generate(system: str, user: str) -> LLMResult` (dataclass con `text`, `model`, `provider`, `input_tokens` y `output_tokens`). `get_llm_provider(settings)` devuelve `OpenAIProvider` o `AnthropicProvider`. Los SDK se instancian una sola vez (cacheados por configuración).

- *Por qué Protocol en vez de ABC*: es tipado estructural, así que los dobles de prueba no necesitan heredar.
- *Por qué no LiteLLM/LangChain*: el curso construye el wrapper a mano en S03 con fines didácticos. Una librería taparía justo lo que se quiere aprender.
- **Habilita S03**: el wrapper con fallback será otro `LLMProvider` que componga los anteriores, y streaming consistirá en añadir `stream()` al Protocol.

### D3. Llamadas síncronas (`def`) en S02

Los endpoints se declaran con `def` y los SDK se usan en su versión síncrona. FastAPI ejecuta los `def` en un threadpool, de modo que no se bloquea el event loop.

- *Alternativa*: `async def` con `AsyncOpenAI`/`AsyncAnthropic`. Escala mejor, pero añade complejidad en los tests y no aporta nada al volumen de S02. **Se reevalúa en S03**, cuando llega el streaming (confirmado por el usuario).

### D4. Configuración

`Settings(BaseSettings)` con `model_config = SettingsConfigDict(env_file=".env", extra="ignore")`, keys como `SecretStr | None` y un `model_validator(mode="after")` que exige la key del proveedor activo. La propiedad `effective_model` resuelve el default según el proveedor. Se expone con `get_settings()` decorado con `@lru_cache` y se inyecta con `Depends(get_settings)`, lo que permite sobrescribirlo en tests con `app.dependency_overrides`.

- `python-dotenv` se mantiene como dependencia porque lo pide el enunciado, aunque `pydantic-settings` ya lee `.env` por sí mismo. Se documentará así en el README.

### D5. Construcción del prompt (CAG)

`build_system_prompt(examples) -> str` es una función pura. Concatena el rol, las instrucciones de formato (Markdown con desglose de tareas, horas, total, equipo y duración, en el idioma de la transcripción) y los ejemplos delimitados con etiquetas XML (`<example>`, `<meeting_summary>`, `<estimation>`), que ambos modelos interpretan bien. El resultado se calcula una vez por proceso, porque el contexto es estático.

- **Habilita S04**: la firma `(...) -> (system, user)` se conserva al migrar a Jinja2. El loader sustituirá el cuerpo de esta función sin cambiar quién la llama.

### D6. Servicio de estimación y errores

`EstimationService.estimate(transcription) -> EstimateResponse`: construye los mensajes, llama a `provider.generate`, calcula `cost_usd` y devuelve el resultado. Los errores del SDK (`openai.APIError`, `anthropic.APIError` y sus subclases de conexión, timeout, rate limit y autenticación) se envuelven en `LLMProviderError`. El router los traduce a **502** con el `detail` `"LLM provider error"`. El detalle se registra con `logging` estándar, nunca en la respuesta.

### D7. Parámetros de generación

`max_tokens = 2048` (Anthropic lo exige; con OpenAI se usa el mismo valor por coherencia) y `temperature` con el valor por defecto del proveedor. Son constantes en `llm_service.py`, no variables de entorno, para no ampliar el contrato de configuración del enunciado. Están pensadas para ajustarse en la sesión en vivo ("explorar parámetros del modelo").

### D8. Precios

`MODEL_PRICES: dict[str, tuple[float, float]]` (USD por millón de tokens de input y output) en `services/pricing.py`, con `gpt-4o-mini` y `claude-haiku-4-5` como mínimo. Los valores se verifican contra las páginas oficiales de precios en la tarea de implementación, con un comentario que indica la fecha de verificación. Si el modelo no está en la tabla, el coste es `None`.

### D9. Calidad y CI

- `ruff` con `line-length = 100` y las reglas `E, F, I, UP, B, SIM`. Formateo con `ruff format`.
- `pytest` con `fastapi.testclient.TestClient`. El fichero `tests/conftest.py` fija variables de entorno ficticias (`OPENAI_API_KEY=test`) y sobrescribe `get_llm_provider` con un `FakeProvider`.
- Workflow `.github/workflows/ci.yml`: `astral-sh/setup-uv`, Python 3.12, `uv sync --locked`, ruff, pytest, `npx @fission-ai/openspec validate --all --strict` (con la versión de OpenSpec fijada).
- Las dependencias de desarrollo van en `[dependency-groups] dev` de `uv`.

## Risks / Trade-offs

- [Los precios de los modelos cambian] → Tabla con la fecha de verificación y `cost_usd` documentado como estimación orientativa.
- [El alias `claude-haiku-4-5` podría apuntar a otra snapshot en el futuro] → Se puede fijar mediante `LLM_MODEL=claude-haiku-4-5-20251001` sin tocar código.
- [Las llamadas síncronas limitan la concurrencia] → Es aceptable en S02 y se revisa en S03.
- [La CI no detecta incompatibilidades reales con las APIs de los proveedores, porque usa mocks] → Hay una verificación manual con `curl` y las transcripciones de `samples/` antes de entregar (tarea explícita).
- [Fallo en el arranque si falta la key] → Es un comportamiento intencionado (fail fast). Los tests inyectan una key ficticia.

## Migration Plan

No aplica, porque es el primer change. La entrega consiste en hacer push de la rama `session-02` y validar en verde con la CI. Si hubiera que volver atrás, basta con revertir la rama: `main` no se toca hasta después de la entrega.

## Decisiones confirmadas por el usuario (2026-10-21)

Las preguntas abiertas de la primera versión de este diseño quedan resueltas así:

1. `transcription` admite como máximo **20 000 caracteres**. Por encima, la respuesta es 422.
2. **Fail fast**: si falta la API key del proveedor activo, el servicio **no arranca** y muestra un mensaje claro que nombra la variable que falta y cómo resolverlo (definirla en `.env` o cambiar `LLM_PROVIDER`).
3. Los errores del proveedor LLM se responden con **502**.
4. **`max_tokens = 2048`** es una constante en el código, no una variable de entorno.
5. **Llamadas síncronas** en S02. Se migrará a async en S03, con el streaming.
6. La estimación se genera **en el idioma de la transcripción**.

## Open Questions

Ninguna pendiente.
