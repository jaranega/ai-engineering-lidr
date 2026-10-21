## Context

El repositorio parte de cero: solo contiene la configuración de OpenSpec y las convenciones (`CLAUDE.md`). El enunciado de S02 define una estructura mínima (`app/{routers,services,context}`) y un flujo **recibir texto → inyectar contexto → llamar al LLM → devolver respuesta**. Las sesiones siguientes ya están definidas en el programa:

- **S03**: Streamlit con streaming (que reutiliza la lógica de llamada y muestra el system prompt, los ejemplos y las métricas), wrapper de proveedores con fallback, caché exact-match y structlog.
- **S04**: `app/schemas.py` con enums (`EstimationRequest`/`EstimationResponse`), prompts en `app/prompts/estimation/v1/*.j2` con `render_estimation_prompt(request, version) -> (system, user)` y tests de templates.

Restricciones: Python 3.12 con `uv`; tests y CI sin API keys reales; y el usuario decide cualquier punto ambiguo.

## Goals / Non-Goals

**Goals:**

- Flujo CAG funcional de punta a punta con OpenAI y Anthropic.
- Capas con dependencias en un único sentido: `routers → services → context`, con `config` transversal. Los servicios no dependen del contrato HTTP.
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
├── main.py              # create app, lifespan (fail fast), include router (prefix /api/v1), /health
├── config.py            # Settings + get_settings() (lru_cache)
├── schemas.py           # EstimationRequest / EstimationResponse (HTTP contract)
├── routers/estimations.py
├── services/
│   ├── llm_service.py   # prompt builders, LLMProvider protocol, OpenAI/Anthropic providers,
│   │                    # build_provider / get_llm_provider, EstimationService, EstimationResult
│   └── pricing.py       # MODEL_PRICES + estimate_cost()
└── context/examples.py  # ESTIMATION_EXAMPLES
```

- **Por qué `schemas.py` en la raíz de `app/` y con los nombres de S04** (`EstimationRequest`/`EstimationResponse`): es la ubicación y el naming que impone S04, así que no habrá que mover ni renombrar nada. En S04 cambian los campos, no el fichero ni las clases.
- **Por qué los proveedores van dentro de `llm_service.py`**: el enunciado sitúa la llamada al LLM en ese fichero, y con dos proveedores sigue siendo legible. En S03 (wrapper + fallback) se extraen a `services/providers/` con un movimiento mecánico.
- *Alternativa descartada*: crear ya `services/providers/{base,openai,anthropic}.py`. Es sobreingeniería para S02 y se aleja de la estructura que se revisa.

### D2. Abstracción de proveedor: `Protocol` + factoría por nombre

- `LLMProvider` es un `typing.Protocol` con `generate(system: str, user: str) -> LLMResult`. `LLMResult` es una dataclass con `text`, `model`, `provider`, `input_tokens` y `output_tokens`.
- `model` es siempre el **modelo solicitado** (`settings.effective_model`), no el id que devuelve la API (p. ej. `gpt-4o-mini-2024-07-18`). Así coincide con la configuración y con la clave de la tabla de precios.
- **Cliente SDK inyectado**: `OpenAIProvider(client: openai.OpenAI, model: str)` y `AnthropicProvider(client: anthropic.Anthropic, model: str)`. En los tests se pasa un *stub* que registra los kwargs (`system=` en Anthropic, los mensajes en OpenAI) y puede lanzar excepciones de cada SDK.
- **Factoría**:
  - `build_provider(name: str, settings: Settings) -> LLMProvider` construye cualquier proveedor por nombre, con su cliente, sin caché.
  - `get_llm_provider() -> LLMProvider` está decorada con `@lru_cache`, llama a `get_settings()` y a `build_provider(settings.llm_provider, settings)`. No recibe `Settings` como argumento porque `BaseSettings` no es *hashable*.
- **Extracción del texto**:
  - OpenAI: `choices[0].message.content or ""`.
  - Anthropic: concatenación de los bloques `type == "text"`.
  - Si `finish_reason == "length"` o `stop_reason == "max_tokens"`, se registra un *warning*, sin cambiar el contrato.
- *Alternativas descartadas*:
  - ABC en lugar de Protocol: el Protocol es tipado estructural, así que los dobles de prueba no necesitan heredar.
  - LiteLLM/LangChain: el curso construye el wrapper a mano en S03, y una librería taparía justo lo que se quiere aprender.
  - `lru_cache` sobre `get_llm_provider(settings)`: falla porque `Settings` no es *hashable*.
  - `Settings` con `frozen=True`: añade una restricción solo para poder cachear.
- **Habilita S03**:
  - `build_provider` por nombre permite un `FallbackProvider` que componga varios `LLMProvider`.
  - Con el cliente inyectado, pasar a `AsyncOpenAI`/`AsyncAnthropic` y añadir `stream()` queda localizado en los proveedores.

### D3. Llamadas síncronas (`def`) en S02

Los endpoints se declaran con `def` y los SDK se usan en su versión síncrona. FastAPI ejecuta los `def` en un threadpool, de modo que no se bloquea el event loop. Los clientes de los SDK son *thread-safe*.

- *Alternativa*: `async def` con `AsyncOpenAI`/`AsyncAnthropic`. Escala mejor, pero añade complejidad en los tests y no aporta nada al volumen de S02. **Se migra en S03**, con el streaming (confirmado por el usuario).

### D4. Configuración

`Settings(BaseSettings)` con `model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)`:

- Las keys son `SecretStr | None`.
- Un `field_validator(..., mode="before")` sobre `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` y `LLM_MODEL` normaliza `""` o solo espacios a `None`. Así, un `.env` copiado de `.env.example` con valores vacíos equivale a "no definida".
- Un `model_validator(mode="after")` exige una key no vacía para el proveedor activo. Su mensaje nombra la variable que falta y cómo resolverlo (definirla en `.env` o cambiar `LLM_PROVIDER`).
- `hide_input_in_errors=True` evita que el `ValidationError` incluya los valores en crudo de la entrada; sin esa opción, podría mostrar la key del otro proveedor.
- `LOG_LEVEL` es `Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]`.
- La propiedad `effective_model` resuelve el default según el proveedor.
- `get_settings()`, decorado con `@lru_cache`, se inyecta con `Depends` y se sobrescribe en tests con `app.dependency_overrides`.

`.env.example`: las keys aparecen vacías y las variables que no son secretas, comentadas con su default (`# LLM_PROVIDER=openai`).

`python-dotenv` se mantiene como dependencia porque lo pide el enunciado, aunque `pydantic-settings` ya lee `.env` por sí mismo. Se documentará así en el README.

### D5. Construcción del prompt (CAG)

- `build_system_prompt(examples) -> str` es una función pura. Concatena:
  - el rol;
  - las instrucciones de formato (Markdown con desglose de tareas, horas, total, equipo y duración, en el idioma de la transcripción);
  - los ejemplos, delimitados con etiquetas XML (`<example>`, `<meeting_summary>`, `<estimation>`), que ambos modelos interpretan bien.
- `_system_prompt() -> str`, decorada con `functools.cache` y sin argumentos (una lista no es *hashable*), devuelve `build_system_prompt(ESTIMATION_EXAMPLES)`. Se calcula una vez por proceso porque el contexto es estático.
- `build_estimation_prompt(transcription: str) -> tuple[str, str]` devuelve `(_system_prompt(), transcription)`. Es la única función que llama el servicio. La transcripción va sin modificar en `user` y nunca en `system`.
- **Habilita S04**:
  - `render_estimation_prompt(request, version) -> tuple[str, str]` sustituye a `build_estimation_prompt` con la misma forma de salida.
  - El cambio en quien la llama es mecánico: el argumento pasa de `str` a `EstimationRequest`.
  - S03 (Streamlit Nivel 3) puede mostrar `_system_prompt()` y `ESTIMATION_EXAMPLES` sin depender de FastAPI.

### D6. Servicio de estimación y errores

- `EstimationService(provider: LLMProvider)` expone `estimate(transcription) -> EstimationResult`. Construye los mensajes con `build_estimation_prompt`, llama a `provider.generate` y calcula `cost_usd`.
- `EstimationResult` es una dataclass de dominio (`text`, `model`, `provider`, `input_tokens`, `output_tokens`, `cost_usd`). El router la mapea a `EstimationResponse` (el campo `text` pasa a `estimation`).
  - *Por qué*: si el servicio devolviera el DTO HTTP, `services` dependería de `schemas.py`, cuyo contrato cambia en S04. Además, S03 (Streamlit) puede usar el servicio sin FastAPI.
- **Errores**: la envoltura la hace **cada proveedor**, no el servicio. El proveedor captura `openai.APIError` o `anthropic.APIError` (que incluyen las subclases de conexión, timeout, rate limit y autenticación) y lanza `raise LLMProviderError(provider=...) from exc`. El servicio solo conoce `LLMProviderError`. Al conservar `__cause__`, el fallback por tipo de error de S03 puede decidir sin rediseño.
- El router traduce `LLMProviderError` a **502** con el `detail` `"LLM provider error"`, y lo documenta en OpenAPI con `responses={502: ...}`.
- En el log solo se registran el tipo de excepción y el *status* HTTP, nunca el mensaje completo, que en los errores de autenticación incluye fragmentos de la key.

### D7. Parámetros de generación

Son constantes en `llm_service.py`, no variables de entorno, para no ampliar el contrato de configuración del enunciado. Están pensadas para ajustarse en la sesión en vivo ("explorar parámetros del modelo"):

- `MAX_TOKENS = 2048`: Anthropic lo exige; en OpenAI se usa el mismo valor por coherencia.
- `LLM_TIMEOUT_S = 60` y `LLM_MAX_RETRIES = 2`: se pasan al constructor del cliente. El default del SDK, 600 s, ocuparía un hilo del threadpool hasta 10 minutos.
- `temperature`: se usa el valor por defecto del proveedor.

### D8. Precios

- `MODEL_PRICES: dict[str, tuple[float, float]]` (USD por millón de tokens de input y output) vive en `services/pricing.py`.
- Incluye los alias y los *snapshots* conocidos: `gpt-4o-mini`, `gpt-4o-mini-2024-07-18`, `claude-haiku-4-5` y `claude-haiku-4-5-20251001`.
- La búsqueda es exacta por el modelo solicitado.
- Los valores se verifican contra las páginas oficiales en la tarea de implementación, con un comentario que indica la fecha de verificación. Si el modelo no está en la tabla, el coste es `None`.
- *Alternativa descartada*: búsqueda por prefijo. Es menos explícita y podría asignar el precio de un modelo a otro distinto.

### D9. Calidad y CI

- `ruff` con `line-length = 100` y las reglas `E, F, I, UP, B, SIM`. Formateo con `ruff format`.
  - Las dependencias se declaran con `Annotated[T, Depends(...)]`, lo que evita el aviso B008 de bugbear sin excepciones de configuración.
  - *Alternativa descartada*: `extend-immutable-calls`.
- `pytest` con `fastapi.testclient.TestClient` usado como context manager (`with TestClient(app)`), para que se ejecute el `lifespan`.
- `tests/conftest.py`:
  - Fija con `monkeypatch` variables de entorno ficticias (`OPENAI_API_KEY=test`, `LLM_PROVIDER=openai`) que pisan cualquier valor real.
  - Sobrescribe `get_llm_provider` con un `FakeProvider` que cuenta las llamadas.
  - Limpia las cachés `get_settings`, `get_llm_provider` y `_system_prompt` antes y después de cada test.
  - El `lifespan` construye el proveedor real con la key ficticia; construirlo no hace red y las peticiones usan el override.
- **Aislamiento de `.env`**: los tests unitarios de configuración instancian `Settings(_env_file=None)` tras `monkeypatch.delenv`. El escenario "la variable de entorno prevalece" usa `_env_file=tmp_path / ".env"`. Así, el `.env` local del desarrollador no altera los resultados.
- Proveedores concretos: se testean con un cliente *stub* inyectado (kwargs enviados, extracción de texto y tokens, envoltura de errores). La factoría se testea por nombre.
- Workflow `.github/workflows/ci.yml`:
  - `astral-sh/setup-uv` con Python 3.12 y `uv sync --locked`. `uv.lock` se versiona y no se excluye en `.gitignore`.
  - `ruff check`, `ruff format --check` y `pytest`.
  - `actions/setup-node` con Node fijado (22) y `npx --yes @fission-ai/openspec@<versión local exacta> validate --all --strict`.
- Las dependencias de desarrollo van en `[dependency-groups] dev` de `uv`. El proyecto se inicializa con `uv init --bare` (o `--app --no-readme`) para no generar un `main.py` ni un README en la raíz. `.python-version` se fija a 3.12.

### D10. Fail fast en el `lifespan`

- El `lifespan` de FastAPI:
  1. Configura `logging.basicConfig(level=settings.log_level)`. structlog lo sustituirá en S03.
  2. Llama a `get_settings()` y a `get_llm_provider()`.
  3. Si hay un `ValidationError`, registra un mensaje construido a mano (variable que falta y cómo resolverlo, sin valores) y lanza `RuntimeError(msg) from None`.
- Uvicorn aborta con "Application startup failed. Exiting." sin servir peticiones.
- `import app.main` **no** depende del entorno.
- Con `--reload`, el supervisor de uvicorn sigue vivo esperando cambios aunque el worker falle. Es un comportamiento aceptado y se documenta en el README.
- Testable con `pytest.raises` alrededor de `with TestClient(app)` sin la key.
- *Alternativa descartada*: validar al importar `app.main` y relanzar `SystemExit`. El entorno tendría que fijarse antes de cualquier import en los tests, `get_settings()` quedaría cacheado con el entorno del import, y el test de estructura y `/openapi.json` dependerían de la configuración.

### D11. Grafo de dependencias

```
get_settings ──► get_llm_provider ──► get_estimation_service ──► POST /api/v1/estimate
get_settings ──► GET /health
```

- Los routers declaran `Annotated[EstimationService, Depends(get_estimation_service)]` y `Annotated[Settings, Depends(get_settings)]`.
- Los tests sobrescriben `get_llm_provider`, que FastAPI resuelve también como subdependencia, y opcionalmente `get_settings`, para comprobar `provider`/`model` en las respuestas.
- `EstimationService` y `build_provider` se pueden construir sin FastAPI, que es lo que necesita S03 (Streamlit).

## Risks / Trade-offs

- [Los precios de los modelos cambian] → Tabla con la fecha de verificación y `cost_usd` documentado como estimación orientativa.
- [El alias `claude-haiku-4-5` podría apuntar a otra snapshot en el futuro] → Se puede fijar mediante `LLM_MODEL=claude-haiku-4-5-20251001` sin tocar código. Ese snapshot está en `MODEL_PRICES`, así que el coste sigue calculándose.
- [Snapshot nuevo no listado en la tabla] → `cost_usd = null` y la estimación se devuelve con normalidad. Se añade a la tabla cuando se use.
- [Las llamadas síncronas limitan la concurrencia] → Es aceptable en S02. El timeout de 60 s acota cuánto ocupa un hilo, y se migra a async en S03.
- [Salida truncada por `MAX_TOKENS`] → *Warning* en el log. Se ajusta en la sesión en vivo.
- [Fuga de secrets en errores de validación o del SDK] → `hide_input_in_errors=True`, mensaje de arranque construido a mano y log de errores del SDK limitado a tipo y *status*.
- [La CI no detecta incompatibilidades reales con las APIs de los proveedores, porque usa mocks] → Hay una verificación manual con `curl` y las transcripciones de `samples/` antes de entregar (tarea explícita).
- [La versión de OpenSpec o de Node difiere entre local y CI] → Ambas se fijan en el workflow.
- [Fallo en el arranque si falta la key] → Es un comportamiento intencionado (fail fast, D10). Los tests inyectan una key ficticia.

## Migration Plan

No aplica, porque es el primer change. La entrega consiste en hacer push de la rama `session-02` y validar en verde con la CI. Si hubiera que volver atrás, basta con revertir la rama: `main` no se toca hasta después de la entrega.

## Decisiones confirmadas por el usuario (2026-10-21)

1. `transcription` admite como máximo **20 000 caracteres**. Por encima, la respuesta es 422.
2. **Fail fast**: si falta la API key del proveedor activo, el servicio **no arranca** y muestra un mensaje claro que nombra la variable que falta y cómo resolverlo (definirla en `.env` o cambiar `LLM_PROVIDER`).
3. Los errores del proveedor LLM se responden con **502**.
4. **`max_tokens = 2048`** es una constante en el código, no una variable de entorno.
5. **Llamadas síncronas** en S02. Se migrará a async en S03, con el streaming.
6. La estimación se genera **en el idioma de la transcripción**.
7. El fail fast se materializa en el **`lifespan`** de FastAPI (D10), con un mensaje construido a mano. `import app.main` no depende del entorno. Con `--reload`, el supervisor de uvicorn sigue vivo esperando cambios: aceptado y documentado.
8. `model` en la respuesta es el **modelo solicitado** (`effective_model`) y el coste se calcula con él. `MODEL_PRICES` incluye alias y snapshots conocidos. No hay campo de modelo servido.
9. Los schemas usan ya el naming de S04: **`EstimationRequest` / `EstimationResponse`**.
10. **`LLM_TIMEOUT_S = 60`** y **`LLM_MAX_RETRIES = 2`** son constantes en el código, junto a `MAX_TOKENS`.
11. Si la salida se trunca (`finish_reason=length` / `stop_reason=max_tokens`), **solo se emite un *warning* en el log**, sin cambio de contrato.
12. `.env.example`: keys vacías y variables no secretas comentadas con su default. `""` se normaliza a `None`.
13. Se añaden a `specs/app-config` los escenarios: key vacía equivale a ausente, `LLM_MODEL` vacío usa el default y el error no muestra valores de keys.
14. Ejemplos CAG aprobados tal cual (3 ejemplos: web de gestión 320 h, app móvil 615 h e integración de datos 125 h). Las instrucciones del prompt van en español.
15. Calibración en el prompt: unas **30 h productivas por semana y persona** a tiempo completo, y horas **redondeadas a múltiplos de 5**.
16. **Solo horas, sin coste en euros** en S02.
17. Se añade una **línea básica contra prompt injection**: la transcripción se trata solo como datos, y se ignoran las instrucciones que contenga. Los guardrails completos llegan en S04 o después.
18. Se **mantiene `claude-haiku-4-5`** como modelo por defecto de Anthropic, aunque desde octubre de 2026 figura como *Legacy* (su sustituto es Claude Haiku 5.5). Es un riesgo aceptado, se comprueba en la tarea 8.3 y, si deja de funcionar, se cambia con `LLM_MODEL` sin tocar código.
19. **Todo fallo del SDK** (excepción base `openai.OpenAIError` / `anthropic.AnthropicError`, no solo `APIError`) se envuelve en `LLMProviderError` y se responde con **502**.
20. Si OpenAI devuelve `usage=None`, se informan **0 tokens** (y por tanto un coste de 0). Es un caso raro y queda documentado.
21. Detalles de implementación verificados contra los SDK instalados (openai 3.35.1, anthropic 1.17.0): OpenAI usa `max_completion_tokens` (`max_tokens` está obsoleto en Chat Completions); `build_provider(name, settings)` usa `effective_model` para el proveedor activo y `DEFAULT_MODELS[name]` para cualquier otro (pensando en el fallback de S03).

## Open Questions

Ninguna pendiente.
