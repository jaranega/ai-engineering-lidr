## Context

Puesta al día con el directo de S04 (salida estructurada y guardrails) antes del ejercicio de S05 (`AIEngineer-notas/05_Funciones_avanzadas/00_memoria`). Material de referencia: `04_IA_avansados.md/03_extraccion-de-datos-estructurados.md` y `04_guardrails-y-validacion-de-outputs.md`. El alcance lo fija `proposal.md`.

Estado de partida (rama `pre-session-05`, tras `s04-add-typed-form-and-jinja-prompts`):

- `app/services/llm_service.py`:
  - `LLMProvider` con `generate` y `stream`, implementado por `LiteLLMProvider` (Router con primario y respaldo, Router por petición para la sobrescritura de modelo) y decorado por `CachedProvider`.
  - `EstimationService.estimate(EstimationInput, GenerationOptions) -> EstimationResult`, donde `EstimationResult` es un *dataclass* con el texto y las métricas.
- `app/prompts/loader.py` renderiza `v1`/`v2` (Markdown libre). Los ejemplos se pintan en Markdown con `examples.j2`, a partir de `app/context/examples.py`, que precalcula costes, fases y totales.
- `app/prompts/language.py` detecta `es`/`en`/`None`. `system.j2` termina con una línea que nombra el idioma (D14 del change anterior: sin ella, gpt-4o-mini respondía en español a las descripciones en inglés, 0/25).
- `app/services/evaluation.py` evalúa el Markdown con expresiones regulares. La medición de S04 mostró totales que no cuadran en 25 de 35 respuestas de gpt-4o-mini.
- `app/services/cache.py` guarda `{"text", "model", "provider", "input_tokens", "output_tokens", "fallback_used"}`.
- `streamlit_app.py` es un formulario cliente HTTP, sin imports de `app`.

**Restricciones heredadas:**

- Solo `app/services` y `app/config.py` importan LiteLLM (y, desde este change, Instructor).
- Los routers no tienen lógica.
- Nunca se registran descripciones, prompts, respuestas ni keys.
- Una versión de prompt publicada no se edita.
- D14: el idioma de la respuesta con gpt-4o-mini es frágil y **se vuelve a medir** siempre que cambia el prompt.

**Comprobado sin red:**

- `uv pip install --dry-run "instructor>=1.5"` resuelve `instructor 1.17.0` con `litellm==1.106.4` fijada.
- Instructor no estaba instalado al redactar este diseño. Las APIs de Instructor citadas aquí (`from_litellm`, `create_with_completion`, `Mode.TOOLS`, hooks `completion:response`/`parse:error`, `max_retries` como `tenacity.Retrying`, `IncompleteOutputException`, `InstructorRetryException`) son las de la serie 1.x. La primera tarea de implementación es **comprobarlas contra el código fuente de 1.17.0**. Si alguna difiere, se vuelve aquí antes de seguir.

**Siguiente sesión (S05):**

- Sesiones con `session_id`, historial con ventana deslizante (`user`/`assistant`), `project_metadata` separado (nombre, equipo, tecnologías, alcance) inyectado en el system prompt, y adjuntos.
- El enunciado parte de "una estimación estructurada validada por schema Pydantic" y de "guardrails programáticos sobre la salida".
- Este change deja esas piezas listas sin implementar nada de S05 (D15).

## Goals / Non-Goals

**Goals:**

- El LLM rellena un objeto Pydantic **mínimo** (`EstimationResult`): solo lo que requiere juicio. La aritmética, el formato y las etiquetas los pone el código.
- Costes y totales correctos por construcción: la comprobación de sumas de la evaluación deja de poder fallar.
- Texto Markdown determinista por formato e idioma, compatible con la evaluación de S04, que sigue puntuando.
- Guardrails de entrada baratos y ordenados (regex → moderación) **antes** de la caché y del LLM, con errores 400 que nunca repiten el texto del usuario.
- Una llamada estructurada genérica en el proveedor (`generate_structured`), con fallback, caché, tokens acumulados entre reintentos y una lista de mensajes, reutilizable en S05.
- Tests sin red para todo lo anterior y una medición real acotada (≤ 0,15 USD).

**Non-Goals:**

- Caché semántica, LLM-as-judge, Guardrails AI, detección de PII más allá de email, teléfono e IBAN.
- Sesiones, memoria, `project_metadata`, adjuntos (S05).
- Streaming de la salida estructurada (Instructor `Partial`). `stream` sigue en la interfaz sin consumidor.
- Exponer en la UI widgets nuevos.

## Decisions

### D1. Decisiones ya tomadas (no se reabren)

| # | Decisión | Alternativas descartadas |
|---|---|---|
| 1 | Totales y coste de cada fila calculados en el código (horas × tarifa del perfil) | Validador que exige al LLM que las sumas cuadren y reintenta (directo): gasta reintentos en aritmética |
| 2 | El servidor renderiza los 3 `output_format` de forma determinista, con etiquetas en el idioma detectado | Devolver solo el objeto (directo); pedir al LLM el Markdown además del objeto |
| 3 | La respuesta mantiene `text`, `prompt_version`, métricas y `validation`, y añade `result` | Respuesta solo con `result` + `prompt_version` (directo) |
| 4 | Guardrails de entrada ES + EN sobre `description` y `reference_projects` → **400** con `reason` y un `message` que nunca repite el texto | Solo EN (directo); 422; mensaje con el fragmento detectado (directo) |
| 5 | Moderación vía LiteLLM, *fail-open* | SDK de OpenAI directo; *fail-closed* |
| 6 | Guardrail de salida: confianza global < 30 → `out_of_scope` | Validador que exige el prefijo "Out of scope:" y reintenta (directo) |
| 7 | Nueva versión `v3` y única; `v1`/`v2` retiradas | Editar `v1`; mantener `v1`/`v2` con Markdown libre en paralelo |
| 8 | Se retira `thinking_budget` | Mantenerlo solo para la llamada no estructurada |
| 9 | La caché exact-match guarda el objeto validado | Guardar el texto renderizado |
| 10 | Sin caché semántica | redisvl + Redis Stack + embeddings |
| 11 | Instructor como dependencia | Tool calling a mano con LiteLLM; `response_format` nativo |

### D2. Forma de `EstimationResult` (lo que rellena el LLM)

Schema en `app/estimation/schema.py`. Solo Pydantic y `app.domain`/`app.context`, sin Instructor ni LiteLLM: se puede importar en los tests, en el loader y en S05 sin arrastrar el proveedor. Los campos van **en este orden**, porque es el orden en que el modelo los genera:

```python
PhaseKey = Literal[
    "discovery", "setup", "development", "qa", "deployment"
]  # = app.context.examples.PhaseKey
RateProfile = Literal["dev", "design"]
OUT_OF_SCOPE_CONFIDENCE = 30


class EstimateRow(BaseModel):  # task -> phase -> rate_profile -> hours
    task: str  # 1..200
    phase: PhaseKey
    rate_profile: RateProfile  # "design" only for UX/UI design work
    hours: int  # 1..2000; normalized to a multiple of 5 (min 5)


class PhaseAssessment(BaseModel):
    phase: PhaseKey
    confidence_pct: int  # 0..100; normalized to a multiple of 5
    assumptions: list[str] = []  # 0..3 items of 1..300; only filled when detail_level=detailed


class TeamMember(BaseModel):
    role: str  # 1..80, written to read after the count ("desarrolladores full-stack")
    count: int  # 1..20
    part_time: bool = False


class DurationRange(BaseModel):
    min_weeks: int  # 1..104
    max_weeks: int  # 1..104, >= min_weeks


class EstimationResult(BaseModel):
    language: Language  # "es" | "en": language of the free-text fields
    title: str  # 1..120: project name
    summary: str  # 1..600: 1-2 sentences of scope; reason when not estimable
    confidence_pct: int  # 0..100, overall; < 30 = cannot be estimated
    rows: list[EstimateRow]  # 0..60
    phases: list[PhaseAssessment]  # 0..5, one per phase used in rows
    team: list[TeamMember]  # 0..8
    duration_weeks: DurationRange | None
    assumptions_and_risks: list[str]  # 0..8 items of 1..300
    narrative: list[str] = []  # 0..6 paragraphs of 1..1500; only for output_format=narrative
```

**Por qué este orden.** En una salida autorregresiva, lo que se escribe antes condiciona lo que viene después:

- `language` va primero: el modelo se compromete con el idioma antes de escribir texto libre. Es el token más barato a favor de D14.
- `summary` y `confidence_pct` van antes del desglose. El modelo decide pronto si el proyecto es estimable; si no lo es, deja vacío el resto (D7) y no gasta tokens inventando filas. Es el orden del directo (`summary`, `confidence_pct`, `phases`, totales).
- `rows` va antes que `phases`: la confianza por fase se decide viendo sus tareas.
- `team` y `duration_weeks` van después de las horas, como en el prompt actual ("coherente con el total y el equipo").
- `narrative` va al final: resume lo ya estructurado.

**Qué calcula el código y no está en el schema**: coste por fila, horas y coste por fase, totales, tarifas y `out_of_scope` (D3). Tampoco hay ningún importe en el schema: el LLM no ve tarifas en euros.

**Cómo se hacen cumplir los requisitos.** Hay tres niveles, de más barato a más caro:

| Nivel | Qué | Cómo |
|---|---|---|
| Normalización (sin reintento) | horas a múltiplo de 5 (`max(5, 5 * round(h / 5))`); confianzas a múltiplo de 5; recorte de espacios; se quitan los strings vacíos de las listas | `field_validator(mode="after")`, idempotente (se puede volver a validar un acierto de caché) |
| Restricción de campo (reintento) | longitudes, rangos y `Literal` (`phase`, `rate_profile`, `language`) | `Field(...)`/`Literal`: Pydantic falla e Instructor reintenta |
| Coherencia (reintento) | (a) `phases` sin duplicados y con el mismo conjunto de fases que `rows`; (b) si `confidence_pct ≥ 30`: `rows`, `team` y `assumptions_and_risks` no vacíos y `duration_weeks` no nulo; (c) `min_weeks ≤ max_weeks`; (d) según el formato y el nivel pedidos (abajo) | `model_validator(mode="after")` |
| Solo prompt (sin validación) | número de filas por nivel, viñetas, párrafos por nivel, que la narrativa no lleve cifras, granularidad | instrucción en `system.j2` + medición D13 |

- **Requisitos por formato y nivel (d).** Con `narrative` y en alcance, `narrative` debe tener entre 1 y 6 párrafos. Con `detailed` y en alcance, cada fase debe tener entre 1 y 3 supuestos.
- **Cómo llegan el formato y el nivel al validador.** Se crea una **subclase por requisito**, cacheada: `response_model_for(output_format, detail_level) -> type[EstimationResult]` (`functools.cache`, 9 combinaciones). Cada subclase lleva `ClassVar`s (`require_narrative: ClassVar[bool]`, `require_phase_assumptions: ClassVar[bool]`) que lee el `model_validator`. La subclase se crea con el **mismo nombre** `EstimationResult`, así que el JSON Schema (y el nombre de la herramienta) es idéntico en las 9. El `EstimationResult` base no exige nada condicional, que es lo que necesitan los tests, los ejemplos y S05.
- *Alternativas descartadas para (d)*:
  - `context=`/`validation_context=` de Instructor: Instructor usa ese mismo `context` para **renderizar como plantillas Jinja el contenido de los mensajes**. La descripción del usuario se interpretaría como plantilla (`{{ … }}`), lo que es un vector de inyección y altera el texto. **Regla: nunca se pasa `context` ni `validation_context` a Instructor** (comprobar en 1.17.0).
  - Una `ContextVar`: estado global implícito.
  - Un schema distinto por formato (`create_model` con campos diferentes): rompe la caché por schema, y S05 tendría 9 schemas.
  - Todo opcional y solo prompt: una narrativa vacía se renderizaría sin prosa.
- **Normalizar en vez de `multiple_of=5`**: redondear es un cálculo determinista (principio del D1-1). Un `multiple_of` provocaría reintentos de pago por un 12 en lugar de un 10. La descripción del campo sigue diciendo "multiple of 5".
- **`language` en el schema**: decide las etiquetas solo cuando la detección devuelve `None` (D5). Cuesta unos 3 tokens.
- **`team` estructurado** en lugar del texto libre de S04: el render pone `(part-time)` y los separadores (`+`) en el código, y S05 obtiene `assumed_team_size = sum(count)` sin parsear. Obliga a convertir el `team` de los 3 ejemplos de datos.
- **Sin `technologies`**: no es gratis. Añade tokens en cada respuesta y datos nuevos en los ejemplos (si un ejemplo lo deja vacío, enseña a dejarlo vacío), y S05 lo extrae de la conversación, no de la estimación. S05 puede añadirlo con un extractor (D15).
- **Un solo `assumptions_and_risks`**: es la sección única "Supuestos y riesgos" de S04 y el campo `risks` de los datos de ejemplo. Dos listas obligarían a reescribir los ejemplos y el render las volvería a juntar.
- *Alternativas descartadas (forma general)*:
  - Schema del directo (fases con `duration_weeks` y `cost_eur` del LLM, totales del LLM): devuelve al LLM la aritmética.
  - Filas con el coste incluido: el mismo problema.
  - Solo fases, sin filas: no se puede costear una fase que mezcla diseño y desarrollo, ni pintar `line_items`.
  - `computed_field` de coste dentro de `EstimationResult`: el schema del LLM y el de la respuesta quedarían mezclados, el valor de caché llevaría importes, y el filtro de fuera de alcance no podría vaciar los importes sin mutar el objeto.

### D3. Lado de la respuesta: `ComputedEstimate` y el guardrail de salida

En `app/estimation/compute.py`, una función pura:

```python
def compute_estimate(result: EstimationResult, language: Language) -> ComputedEstimate
```

```python
class CostedRow(BaseModel):        # EstimateRow + rate_eur_per_hour, cost_eur
class PhaseBreakdown(BaseModel):   # phase, hours, cost_eur, confidence_pct, assumptions
class ComputedEstimate(BaseModel):
    language: Language             # effective language of the labels (D5)
    title: str
    summary: str
    confidence_pct: int
    out_of_scope: bool
    rows: list[CostedRow]
    phases: list[PhaseBreakdown]   # canonical order, only phases with rows
    team: list[TeamMember]
    duration_weeks: DurationRange | None
    assumptions_and_risks: list[str]
    narrative: list[str]
    total_hours: int
    total_cost_eur: float
```

- **Cálculo:**
  - `cost_eur = round(hours * RATES_EUR_PER_HOUR[rate_profile], 2)`.
  - Las fases se agregan en el orden canónico de `PHASE_KEYS`, con la confianza y los supuestos de su `PhaseAssessment`.
  - Los totales son sumas, con el coste redondeado a 2 decimales.
  - Las tarifas siguen en `app/context/examples.py` (`RATES_EUR_PER_HOUR`).
- **Guardrail de salida** (política *filter*): `out_of_scope = result.confidence_pct < OUT_OF_SCOPE_CONFIDENCE`.
  - Con `out_of_scope`, `rows`, `phases`, `team` y `narrative` quedan vacíos, `duration_weeks = None` y los totales a `0`.
  - Se conservan `title`, `summary` (el motivo), `confidence_pct` y `assumptions_and_risks` (suele listar lo que falta).
  - Se registra `estimation_out_of_scope` con `confidence_pct`.
  - Es la forma de la política *filter* del material del curso (`phases=[]`, importes a 0).
- `ComputedEstimate` es directamente el schema del campo `result` de la respuesta HTTP (`app/schemas.py` lo importa), así que no hay un modelo espejo. OpenAPI lo documenta entero.
- *Alternativas descartadas*:
  - Devolver con `out_of_scope = true` las filas y los importes inventados: el cliente podría pintarlos.
  - Totales `null` cuando está fuera de alcance: un tipo `int | None` en todo el contrato por un caso que ya marca `out_of_scope`.
  - Un *dataclass* más un modelo Pydantic espejo en `schemas.py`: dos definiciones del mismo contrato.

### D4. Ubicación, capas y render

```
app/estimation/          # new; no LLM/LiteLLM/Instructor imports
├── __init__.py
├── schema.py            # EstimationResult & nested models, normalizers, validators, response_model_for
├── compute.py           # ComputedEstimate, compute_estimate (costs, totals, out-of-scope filter)
├── render.py            # LABELS[es|en], format_amount(value, language), render_markdown(...)
└── examples.py          # example_result(EstimationExample) -> EstimationResult; example_payloads(...)
app/guardrails/          # new; pure regex, no network
├── __init__.py
└── input.py             # screen_texts, InputGuardrailViolation, patterns
app/services/moderation.py   # Moderator protocol, LiteLLMModerator, NullModerator (LiteLLM lives here)
```

**Dependencias** (en un solo sentido, sin ciclos):

- `estimation → {domain, context}` y `guardrails → {domain}`.
- `prompts → {estimation, context, domain}`.
- `services → {estimation, guardrails, prompts, config, …}`.
- `schemas → {domain, estimation, services}` y `routers → {schemas, services}`.

**Reglas:**

- `domain` sigue sin imports de `app`.
- `estimation` y `guardrails` no importan `services` ni `prompts`.
- El router solo importa de `services` (que reexporta `InputGuardrailViolation`, como ya hace con `PromptVersion`) y de `schemas`.

**Ajustes en módulos existentes:**

- `Language = Literal["es", "en"]` pasa a `app/domain.py`. `app/prompts/language.py` lo importa como `ResponseLanguage = Language`, porque `estimation` no puede depender de `prompts`.
- La moderación usa LiteLLM, así que vive en `app/services/` y no en `app/guardrails/`. Es una desviación menor respecto al apartado *Impact* de la propuesta, que ponía toda la entrada en `app/guardrails/`.

**Render en Python, no en Jinja:**

```python
render_markdown(estimate: ComputedEstimate, output_format: OutputFormat, detail_level: DetailLevel) -> str
```

- El idioma sale de `estimate.language`.
- `LABELS: dict[Language, Labels]`, donde `Labels` es un `TypedDict` con:
  - título (`Estimación`/`Estimate`), encabezados de sección y cabeceras de tabla;
  - las cuatro etiquetas de totales, `semanas`/`weeks` y las etiquetas de fase (`PHASES[*].label_es/label_en`);
  - el aviso de fuera de alcance;
  - la plantilla de la frase de fases de `narrative`;
  - el prefijo "Riesgos y supuestos principales"/"Main risks and assumptions".
- Importes: en `es`, `format_amount` actual (`937,50`); en `en`, `937.50`; sin separador de miles en los dos. `format_amount` se mueve a `render.py` con un parámetro de idioma.
- La estructura de cada formato es la de S04 D5, para que la evaluación de S04 siga valiendo sin cambios:
  - **`line_items`**: `## Estimación: …` → `### Desglose de tareas` con `| Tarea | Horas | Coste (EUR) |` → `### Totales` (cuatro viñetas) → `### Supuestos y riesgos`.
  - **`phases_table`**: igual, con `### Desglose por fases` y la columna `Confianza (%)`.
  - **`narrative`**: título → párrafos del LLM → un párrafo **generado por el código** con las fases, sus horas y su coste ("El trabajo se organiza en N fases: …") → "Riesgos y supuestos principales: …" → `### Totales`. El prompt prohíbe cifras en los párrafos del LLM; las cifras las pone siempre el código.
  - **`detailed`**: añade `### Supuestos por fase` con una viñeta por fase (etiqueta localizada) y los supuestos anidados.
  - **Fuera de alcance**, en todos los formatos: título + `**Fuera de alcance:**` con un aviso fijo y la confianza ("No se puede estimar este proyecto con la información disponible (confianza 20 %).") + `summary` + la lista de `assumptions_and_risks` si no está vacía. Sin tablas ni importes.
  - `summary` solo se pinta fuera de alcance: dentro, S04 D5 no tiene introducción, y el resumen ya va en `result`.
- *Alternativas descartadas*:
  - Una plantilla Jinja de salida con un `Environment` propio: más maquinaria, el control de espacios en tablas es delicado, y el render no es un prompt versionado.
  - Reutilizar las macros de `examples.j2`: las versiones de prompt son inmutables y el render cambiaría con cada versión nueva.

**Ejemplos (`examples.j2` de `v3`) como objetos.**

- `app/context/examples.py` queda como **solo datos**. Se retiran `cost_eur`, `phases`, `total_hours` y `total_cost_eur` precalculados, porque ahora calcula `compute_estimate`. Ganan:
  - `summary`, `confidence_pct` global (coherente con la confianza por fase);
  - `team` estructurado;
  - 2 párrafos de `narrative` en español, sin cifras.
  
  Los datos los escribe `ai-engineer`. Siguen sin supuestos por fase (granularidad `medium`, S04 D5).
- `example_payloads(examples, output_format)` en `app/estimation/examples.py` devuelve, por ejemplo, `{project_type, meeting_summary, output_json}`:
  - `output_json = json.dumps(example_result(ex).model_dump(mode="json", exclude_defaults=True), ensure_ascii=False)`, con lo que se omiten la `narrative` y los supuestos por fase vacíos;
  - fuera de `narrative`, además, `exclude={"narrative"}`.
- La serialización se hace en Python porque el filtro `tojson` de Jinja escapa los acentos (`ó`): más tokens y peor lectura.
- `examples.j2` pinta `<example><example_request>…</example_request><example_output>{{ example.output_json }}</example_output></example>`.
- **Invariante de referencia** (el de S04 D5, trasladado): para cada ejemplo × formato × nivel × idioma, `render_markdown(compute_estimate(example_result(ex), lang), fmt, level)` obtiene `score = 1.0`. Un test fija además los totales conocidos del ejemplo 1: 320 h y 19 500 EUR.

### D5. Idioma

- **Idioma de las etiquetas**: `detect_language(request.description) or llm_result.language`. Se registra `language_source = "detected" | "llm"` en `estimation_rendered`.
  - El idioma detectado manda: es el que nombra el prompt (D14) y el del usuario.
  - Si la detección es ambigua, el campo `language` del modelo hace que las etiquetas coincidan con el texto libre que escribió.
- `v3` mantiene la regla de idioma al principio y al final del system y la línea final que nombra el idioma detectado, ahora referida a "all text fields (title, summary, tasks, roles, assumptions, narrative)". El esqueleto de etiquetas en inglés desaparece: las etiquetas ya no las escribe el modelo.
- *Alternativas descartadas*:
  - Español por defecto cuando la detección da `None`: etiquetas en español sobre un texto libre que puede estar en inglés.
  - Solo el campo del LLM, ignorando la detección: reabre el fallo de D14 (texto y etiquetas en español con una descripción en inglés).
  - Detectar el idioma sobre el texto generado: más heurística sobre textos cortos (títulos de tarea).
- **Riesgo nuevo**: con `Mode.TOOLS`, las descripciones de los campos (en inglés) viajan en el parámetro `tools`, y los ejemplos muestran `"language": "es"`. Puede empujar hacia el inglés con descripciones en español, o a copiar `"es"`. Lo comprueba la medición D13 en los dos idiomas.

### D6. Instructor sobre `LiteLLMProvider`

**Interfaz** (en `LLMProvider`; `generate` y `stream` no cambian):

```python
T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class StructuredResult(Generic[T]):
    value: T
    model: str
    provider: str
    input_tokens: int  # summed over every attempt
    output_tokens: int  # summed over every attempt
    attempts: int = 1
    fallback_used: bool = False
    cache_hit: bool = False
    latency_ms: int = 0


def generate_structured(
    self,
    system: str,
    messages: Sequence[ChatMessage],
    response_model: type[T],
    options: LLMCallOptions | None = None,
) -> StructuredResult[T]: ...
```

**Implementación en `LiteLLMProvider`:**

1. `plan = self._plan(system, messages, options)`. `_plan` reutiliza el modelo destino, el Router (el principal o el de la sobrescritura, sin fallback) y `max_tokens`; desaparece todo lo de `thinking`.
2. Por llamada, se crea `client = instructor.from_litellm(plan.router.completion, mode=instructor.Mode.TOOLS)` y se registran hooks con un acumulador local:
   - `completion:response` suma `usage.prompt_tokens`/`completion_tokens`, cuenta los intentos y guarda el `_model_id` y el `finish_reason` de la última respuesta;
   - `parse:error` registra `structured_output_retry` con `attempt` y `error_count` (nunca el mensaje de Pydantic, que incluye valores).
   
   El cliente es **por llamada** porque FastAPI ejecuta los endpoints síncronos en un *threadpool*: un cliente global con hooks mezclaría contadores entre peticiones. Construirlo cuesta muy poco (envuelve un callable).
3. Llamada:

   ```python
   value, completion = client.chat.completions.create_with_completion(
       model=PRIMARY_GROUP,
       messages=plan.kwargs["messages"],
       max_tokens=plan.max_tokens,
       response_model=response_model,
       max_retries=self._structured_max_retries,
   )
   ```

   - **Verificado en el código de Instructor 1.17.0** (`instructor/v2/core/retry.py`): con `max_retries` entero, Instructor crea `Retrying(stop=stop_after_attempt(max_retries + 1), retry=retry_if_exception_type((ValidationError, JSONDecodeError, AsyncValidationError, ResponseParsingError)))`, así que **solo reintenta errores de validación o de parseo**. Los errores de transporte se propagan sin reintentar y los sigue gestionando el Router (`LLM_RETRIES` y fallback). No hace falta un `tenacity.Retrying` propio, y `tenacity` no pasa a ser dependencia directa.
   - No se pasa `timeout` en los kwargs de Instructor: con un `timeout` numérico añadiría `stop_after_delay(timeout)` al presupuesto total de reintentos. El timeout por llamada sigue en el despliegue del Router.
4. El despliegue efectivo y `fallback_used` salen del `_model_id` de la **última** respuesta (la que produjo el objeto válido), con el mismo `_effective` de hoy.
   - **Coste**: tokens sumados de todos los intentos y precio del modelo efectivo final.
   - Si en un mismo `generate_structured` un intento lo sirvió el primario y otro el respaldo, el coste es aproximado (documentado en *Risks*).
5. **Errores**: nuevo `StructuredOutputError(LLMProviderError)` con `reason: Literal["truncated", "invalid"]` y `attempts`.
   - `IncompleteOutputException` (`finish_reason == "length"`) → `reason="truncated"`.
   - `InstructorRetryException` o `ValidationError`/`JSONDecodeError` al agotar los intentos (según cómo propague 1.17 con `reraise=True`) → `reason="invalid"`.
   - Cualquier otra excepción → `LLMProviderError`, como hoy.
   - Se registra `llm_call_failed` (`error_type`, `status_code`, `mode="structured"`, `attempts`) y, si aplica, `structured_output_failed` (`reason`, `attempts`).
   - El router mapea todo a **502** con el `detail` genérico de siempre.
6. `llm_call_started`/`llm_call_completed` con `mode="structured"` y `attempts`.

**Modo `TOOLS` para los dos proveedores.**

- Instructor genera `tools` + `tool_choice` forzado en formato OpenAI. LiteLLM lo traduce a `tool_use` + `tool_choice: {"type": "tool"}` en Anthropic.
- Como el Router puede pasar del primario de un proveedor al respaldo del otro **dentro de la misma llamada**, los kwargs tienen que ser neutros respecto al proveedor, y `TOOLS` lo es.
- *Alternativas descartadas*:
  - `ANTHROPIC_TOOLS`: kwargs de Anthropic, que fallarían al pasar a un respaldo de OpenAI.
  - `JSON`/`MD_JSON`: Instructor inserta el schema como texto en el mensaje `system`, lo que cambia el equilibrio de idioma medido en D14 y no restringe la salida.
  - `TOOLS_STRICT`/`response_format` estricto de OpenAI: exige todos los campos obligatorios y prohíbe los valores por defecto, y no tiene equivalente en Anthropic vía Router.

**Configuración:**

- `STRUCTURED_MAX_RETRIES` (`int`, `0..5`, por defecto **2**) son los reintentos por validación: como máximo 3 intentos. Mismo significado que `LLM_RETRIES` ("reintentos", no "intentos"). Se pasa a `LiteLLMProvider` en `build_provider`.
- Con el valor por defecto, el peor caso en latencia son 3 intentos × la ruta del Router.
- Por qué no el 6 del directo: cada reintento es una llamada completa (unos 8 k tokens de entrada).

**`truncated`.**

- Un JSON cortado no se puede usar, así que un truncado es un error 502 y no un 200 parcial.
- El campo `truncated` se mantiene en el contrato y en una respuesta 200 siempre vale `false`. Por eso `validation.finish_ok` siempre es `true`.
- `max_tokens` por defecto sigue en 2048. La medición D13 comprueba `detailed` + `line_items`, el caso más largo. Ver Open Questions.

**`stream` y `generate`**: se mantienen en la interfaz y en `CachedProvider` con sus tests, sin consumidor, como decidió S04 D10. `LLMCallOptions` pierde `thinking_budget`. `_router_deployment` pierde `additional_drop_params: ["thinking"]`.

*Alternativas descartadas*:

- Un `StructuredProvider` separado del `LLMProvider`: duplica el Router, la caché y los logs.
- Llamar a Instructor desde `EstimationService`: el servicio tendría que conocer el Router y los ids de despliegue.
- Leer `completion.usage` suponiendo que Instructor acumula el uso de los intentos: es un comportamiento interno que depende del tipo de `usage`. El hook lo hace explícito y un test lo fija.

**Nota de implementación (ai-engineer, 2026-11-10).**

- En Instructor 1.17.0, `retry_sync_v2` envuelve **todo** fallo final en `InstructorRetryException` (también un error de transporte, que se propaga sin reintento). El proveedor clasifica por `__cause__`: `ValidationError`/`JSONDecodeError`/`ResponseParsingError` → `invalid`; `IncompleteOutputException` (que se relanza sin envolver) → `truncated`; cualquier otra causa → `LLMProviderError`. `llm_call_failed` registra el tipo de la causa.
- Instructor registra cada intento fallido con `logging` a nivel ERROR incluyendo el texto de la excepción (valores de Pydantic o cuerpo de LiteLLM). `llm_service` sube el logger `instructor` a `CRITICAL` al importarse. Recomendado: moverlo a `configure_logging` junto a `LITELLM_LOGGERS` (`app/config.py`, backend-engineer).
- Instructor asigna `_raw_response` a la instancia devuelta, así que `value == otro` puede ser falso con los mismos datos: los tests comparan `model_dump()`.
- `StructuredOutputError(provider, reason, attempts)`; `LiteLLMProvider(..., structured_max_retries=2)`.

### D7. Caché

`CachedProvider.generate_structured`:

- **Clave**: `cache_key({...})` con:
  - `kind: "structured"`, `system`, `messages` (todos, en orden), `model`, `model_override` y `max_tokens`;
  - `schema: f"{response_model.__name__}:{sha256(json.dumps(response_model.model_json_schema(), sort_keys=True))[:12]}"`;
  - `requirements: {"narrative": bool, "phase_assumptions": bool}` (las `ClassVar` de D2).

  Por qué cada pieza:
  - El hash del schema invalida solo las entradas cuando cambia un campo o una descripción, sin acordarse de subir una versión manual.
  - Los requisitos entran en la clave porque cambian qué respuestas son válidas.
  - `thinking_budget` sale de la clave.
  - `STRUCTURED_MAX_RETRIES` no entra: no cambia qué es un resultado válido.
- **Valor**: `{"value": value.model_dump(mode="json"), "model", "provider", "input_tokens", "output_tokens", "fallback_used", "attempts"}`. Es el objeto del LLM, **antes** de `compute_estimate` y del filtro de fuera de alcance. Así:
  - el cálculo, el filtro y el render se aplican en cada petición (son puros y baratos);
  - cambiar etiquetas, tarifas o el umbral no exige invalidar la caché;
  - un resultado fuera de alcance se cachea como cualquier otro.
- **Acierto**: `response_model.model_validate(stored["value"])` con la subclase de la petición. Si falla, se registra `cache_entry_invalid` y se trata como fallo de caché. `cache_hit = true`, con los tokens y el coste originales y la latencia actual.
- **Nada se guarda si falla**: `StructuredOutputError`, `LLMProviderError` y cualquier excepción salen antes del `set`.
- `app/services/cache.py`:
  - `_VALUE_FIELDS` se divide en campos comunes (`model`, `provider`, `input_tokens`, `output_tokens`, `fallback_used`) y una carga, que es **o** `text: str` **o** `value: dict` (y `attempts: int` opcional);
  - `_decode` acepta las dos formas;
  - `KEY_PREFIX` no cambia, porque las claves estructuradas no colisionan con las de texto (`kind` está en el hash).
- *Alternativas descartadas*:
  - Cachear el `ComputedEstimate` o el texto: congela las tarifas y las etiquetas, y el filtro no se volvería a aplicar.
  - Versión manual del schema: se olvida.
  - No volver a validar al leer: una entrada corrupta llegaría al render.

### D8. Guardrails de entrada

**Orden en `EstimationService.estimate`:**

1. Validación Pydantic (FastAPI: 422) y `_check_options`.
2. **Regex de inyección** → 3. **Regex de PII**: los dos con `screen_texts(texts)`.
4. **Moderación**.
5. Render, caché y LLM.

Lo barato va primero. La PII se comprueba **antes** de la moderación para no enviar datos personales a OpenAI. Todo va antes de la caché: una caché no sirve respuestas a entradas bloqueadas (material del curso).

`texts` = `[("description", description)] + [("reference_projects", name), ("reference_projects", summary) …]`.

**`app/guardrails/input.py`:**

```python
GuardrailReason = Literal["prompt_injection", "pii", "moderation"]

class InputGuardrailViolation(Exception):
    reason: GuardrailReason
    message: str            # fixed English phrase per reason; never user text

@dataclass(frozen=True)
class GuardrailHit:
    reason: GuardrailReason
    field: str              # "description" | "reference_projects"
    rule: str               # rule id, e.g. "en_ignore_previous", "iban"

def screen_texts(texts: Sequence[tuple[str, str]]) -> GuardrailHit | None
```

- **Normalización** antes de comparar: `casefold`, se quitan las tildes (NFKD) y se colapsan los espacios. "Ignora las instrucciones" e "ignorá" caen en la misma regla. Cada regla es un `re.Pattern` con un id, en una tupla `(rule_id, pattern)`.
- **Inyección**, EN (con límites de palabra):
  - `ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts?|rules|messages)`;
  - `disregard (all |the )?(previous|prior|above) …`;
  - `forget (all |everything|your) (previous )?(instructions|rules)`;
  - `you are now (a|an|the|in)\b`;
  - `(reveal|show|print|repeat|output) (me )?(your|the) (system )?(prompt|instructions)`;
  - `new instructions:`;
  - `(developer|jailbreak) mode`.
- **Inyección**, ES:
  - `ignora (todas )?(las )?(instrucciones|indicaciones|reglas) (anteriores|previas)`;
  - `olvida (todas )?(las |tus )?(instrucciones|reglas)`;
  - `haz caso omiso de`;
  - `(a partir de ahora|ahora) (eres|actua como)`;
  - `(muestra|revela|repite|imprime) (el|tu) (prompt|mensaje|instrucciones) (del|de) sistema`;
  - `nuevas instrucciones:`;
  - `no sigas (las|tus) instrucciones`.
- **Inyección** en los dos idiomas: etiquetas propias del prompt, abiertas o cerradas: `</?(project_description|project_type|reference_projects?|examples?|example_output|system|assistant)>`.
- **No** se bloquea "system prompt" ni "act as" sueltos. Descripciones legítimas de productos con IA ("chatbot con system prompt configurable") o de arquitectura ("el servicio actúa como pasarela") darían falsos positivos. Ver Open Questions.
- **PII** (sobre el texto sin normalizar):
  - **email**: `[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}`.
  - **IBAN**: `\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b` **y** comprobación mod-97. Sin el checksum, códigos como "ES2025…" de un texto darían falsos positivos.
  - **teléfono**:
    - internacional: `\+\d{1,3}[ .-]?\(?\d{1,4}\)?(?:[ .-]?\d){6,10}`;
    - español: `\b[6789]\d{2}(?:[ .-]?\d{3}){2}\b` y `\b[6789]\d{2}(?:[ .-]?\d{2}){3}\b`;
    - EE. UU.: `\(\d{3}\) ?\d{3}-\d{4}` y `\b\d{3}-\d{3}-\d{4}\b`.

    Exigen 9 o más dígitos con forma de teléfono. Por eso no saltan con "10-12 semanas", "20000 EUR", "2025-2026", "300 pedidos", "+20 %", "1.000.000 de usuarios", "ISO 27001" ni "v2.3.1", que forman el **corpus de negativos** de los tests junto con las dos muestras de `samples/`.
- **Mensajes** (constantes en inglés, como `PROVIDER_ERROR_DETAIL`):
  - `prompt_injection`: "The request contains instructions addressed to the assistant. Describe only the project."
  - `pii`: "The request contains personal data (email, phone number or IBAN). Remove it and try again."
  - `moderation`: "The request content is not allowed."

**Moderación (`app/services/moderation.py`):**

```python
class Moderator(Protocol):
    def flagged(self, texts: Sequence[str]) -> bool | None: ...   # None = could not check (fail-open)

class LiteLLMModerator:   # litellm.moderation(input=list(texts), model="omni-moderation-latest", api_key=...)
class NullModerator:      # always None; logs nothing per request
def get_moderator() -> Moderator   # lru_cache; FastAPI dependency
```

- Una sola llamada con todos los textos. `flagged = any(r.flagged for r in response.results)`.
- Se activa si `MODERATION_ENABLED=true` (por defecto) **y** hay `OPENAI_API_KEY`, aunque el primario sea Anthropic. Si no, se usa `NullModerator`, y al construirlo se registra una vez `moderation_disabled` con `reason` (`config` o `no_openai_key`).
- **Fail-open**: cualquier excepción → `None`, se registra `moderation_failed` (`error_type`, `status_code`) y la petición sigue.
- Timeout: `MODERATION_TIMEOUT_S = 5`, si `litellm.moderation` lo acepta (verificar). Si no, cliente con timeout en la llamada.
- `moderation_checked`: `flagged`, `texts` (número), `latency_ms` y, si se marca, `categories` (solo nombres de categoría).
- `EstimationService(provider, resolve_model=None, moderator: Moderator | None = None)`. Con `None`, `NullModerator`: los tests existentes no cambian.

**Logs** (S03, sin contenido):

- `input_guardrail_blocked` con `reason`, `field` y `rule`: nunca el fragmento ni la descripción.
- Un test con un marcador secreto en la descripción y en un proyecto de referencia comprueba que no aparece en ningún evento.

**HTTP**: el router hace `except InputGuardrailViolation as exc: raise HTTPException(400, detail={"reason": exc.reason, "message": exc.message})`. El cuerpo es `{"detail": {"reason": "...", "message": "..."}}`.

*Alternativas descartadas*:

- Guardrails como dependencia de FastAPI en el router: es lógica de negocio fuera del servicio, y S05 tendría que repetirlos en el endpoint de sesiones.
- Moderación antes de las regex: llamada de red y PII enviada a terceros para casos que una regex rechaza en microsegundos.
- Un validador Pydantic en `EstimationInput`: daría 422 en vez de 400 y metería red en la validación de entrada.
- Modo "solo log" previo al bloqueo (recomendado por el curso): ver Open Questions.
- Presidio u otros detectores: dependencia pesada para tres patrones.

**Nota de implementación (ai-engineer, 2026-11-10).**

- `litellm.moderation` (1.106.4) no acepta `timeout`, pero sí `client`. `LiteLLMModerator` le pasa un `openai.OpenAI(timeout=5, max_retries=0)` creado una vez (el SDK es dependencia transitiva de LiteLLM e Instructor). Es la alternativa prevista en este apartado.
- `en_reveal_prompt` exige `your` o `system` ("reveal your prompt", "show the system prompt"): con `(your|the) (system )?` bloqueaba "the app should show the instructions". La regla de etiquetas añade `example_request` y `client_request`, y admite espacios dentro de `< / tag >`.
- Un bloqueo por moderación se registra con `field="input"` y `rule="moderation"`.

### D9. Fuera de alcance: texto, evaluación y caché

- `result.out_of_scope = true` con la forma de D3, y un `text` con el aviso localizado (D4).
- **Evaluación**: con `out_of_scope`, `validation = null` y se registra `estimation_evaluated` con `skipped="out_of_scope"`. Puntuar un aviso contra comprobaciones de tablas y totales daría un `score` engañoso. `result.out_of_scope` distingue este `null` del de `evaluate=false`.
- **Caché**: se guarda el objeto del LLM (D7). El filtro se vuelve a aplicar en cada acierto.
- **Prompt (capa 3 del curso)**: un bloque en `system.j2` antes de los ejemplos. Si la descripción es demasiado vaga, no es un proyecto de software o no permite estimar, `confidence_pct` < 30, el motivo va en `summary`, `rows`/`phases`/`team`/`narrative` quedan vacíos y `duration_weeks` a null. Nada de inventar detalles para llenar el schema.
- *Alternativas descartadas*:
  - Un `StructureCheck` específico de fuera de alcance: un formato más en el contrato de `validation` para un caso sin estructura que comprobar.
  - Evaluar igualmente: el `score` bajo parecería un fallo del LLM.

### D10. Contrato de la API

- **Query** `prompt_version: Literal["v3"] = "v3"` (`PromptVersion` del loader). `?prompt_version=v1`/`v2` → 422 con `loc = ["query", "prompt_version"]`, por FastAPI y sin llamar al LLM.
- **Body**: `thinking_budget` se retira de `EstimationRequest`. Sigue la política vigente de campos desconocidos: se ignora (200 sin efecto), como `preprocessing`/`example_format` en S04. Se retiran `MIN_THINKING_BUDGET`/`MAX_THINKING_BUDGET`.
- **Response 200** (`EstimationResponse`): los campos actuales + `result: ComputedEstimate` (siempre presente en un 200). `text` es el render de D4. `truncated` siempre es `false` (D6).
- **Response 400**: `GuardrailErrorResponse` en `schemas.py` (`detail: GuardrailError{reason: GuardrailReason, message: str}`), declarada en `responses={400: {"model": GuardrailErrorResponse, …}}`.
- **Responses 422 y 502**: como hoy. 502 también cubre `StructuredOutputError` (`truncated`/`invalid`) con el mismo `detail` genérico.
- OpenAPI documenta 200, 400, 422 y 502, el enum de `prompt_version` (solo `v3`) y el schema completo de `result`.
- **El servicio** pasa a devolver `EstimationOutcome`, el *dataclass* que hoy se llama `EstimationResult` y se renombra para liberar el nombre del schema de la propuesta, con `result: ComputedEstimate` y los demás campos como hoy.

  El router construye la respuesta igual que ahora.

### D11. Streamlit (mínimo, sin widgets nuevos)

- `PROMPT_VERSIONS = ("v3",)` y `DEFAULT_PROMPT_VERSION = "v3"`. Se mantiene el selector en "Opciones avanzadas" con una sola opción (S04 D12): una versión futura solo añade un valor. El test de paridad con `app` sigue cubriéndolo.
- **400**: nueva excepción `GuardrailRejected(reason)`. `GUARDRAIL_MESSAGES` en español por `reason`:
  - `prompt_injection`: "La descripción contiene instrucciones dirigidas al asistente. Describe solo el proyecto."
  - `pii`: "La petición contiene datos personales (email, teléfono o IBAN). Elimínalos y vuelve a intentarlo."
  - `moderation`: "El contenido de la petición no está permitido."
  - Una `reason` desconocida o un cuerpo ilegible muestran el genérico.
  - Se registra `api_request_failed` con `reason="guardrail"` y `guardrail_reason`.
  - No se usa el `message` de la API (inglés): la UI es española.
- **Fuera de alcance**: si `data["result"]["out_of_scope"]`, se muestra `st.info(OUT_OF_SCOPE_NOTICE)` antes del `text`. La validación muestra "Sin validación" (ya existe).
- La comprobación de carga (`text` es `str`) no cambia. La UI no pinta desde `result`: el `text` ya está renderizado.

### D12. Tests (sin red)

- `tests/estimation/`:
  - **schema**: normalizaciones, conjunto de fases, requisitos en alcance y fuera de alcance, `min ≤ max`, `response_model_for` con el mismo `model_json_schema()` en las 9 combinaciones;
  - **compute**: costes, orden canónico, totales y filtro de fuera de alcance;
  - **render**: cada formato × nivel × idioma, separadores de importe y ninguna etiqueta del otro idioma;
  - **ejemplos**: `example_result` valida e invariante `score = 1.0` (D4).
- `tests/guardrails/`:
  - positivos por regla en ES y EN, con tildes y mayúsculas;
  - corpus de negativos, incluidas las muestras;
  - IBAN válido e inválido por checksum;
  - `reference_projects` también se revisa.
- **Moderación**: `litellm.moderation` con *monkeypatch* (marcado, no marcado, excepción → `None` + log), `MODERATION_ENABLED=false` y sin `OPENAI_API_KEY` → `NullModerator`.
- **Proveedor estructurado**: un Router doble cuyo `.completion(**kw)` devuelve `litellm.ModelResponse` construidos en local, con `tool_calls` y `usage`. Casos:
  - inválido y después válido → `attempts=2` y tokens sumados;
  - `finish_reason="length"` → `StructuredOutputError("truncated")` con **una** llamada;
  - excepción del Router → `LLMProviderError` con una llamada;
  - inválido siempre → `invalid` tras `1 + STRUCTURED_MAX_RETRIES` llamadas;
  - `_model_id="fallback"` en la última → `fallback_used`;
  - los kwargs contienen `tools`/`tool_choice` y **ningún** mensaje alterado por plantillas (descripción con `{{ x }}` intacta).
- **Caché estructurada**: acierto revalidado, entrada inválida → fallo de caché, fallo no guardado, la clave cambia con el schema, los requisitos y el modelo, y el valor guardado es el objeto antes del filtro.
- **Servicio** con un `FakeProvider` que implementa `generate_structured`:
  - con un guardrail que salta no se llama ni a la moderación, ni a la caché, ni al proveedor;
  - idioma detectado frente al del LLM;
  - fuera de alcance → `validation=None`.
- **API**:
  - 400 con la forma exacta;
  - 422 para `v1`/`v2`;
  - `thinking_budget` ignorado;
  - `result` en el 200;
  - OpenAPI con el 400.
- **Prompt `v3`**:
  - regla de idioma al principio y al final;
  - solo existe la carpeta `v3`;
  - el JSON de cada ejemplo en el prompt valida contra `EstimationResult`;
  - el system no contiene datos del usuario;
  - bloque de fuera de alcance presente;
  - solo el bloque del formato pedido;
  - instrucción de `detailed`.
- **Logs**: marcador secreto ausente en los eventos de guardrail, moderación y salida estructurada.

### D13. Medición con llamadas reales (plan para `tasks.md`)

Requiere **autorización del usuario**. Sin Redis (`REDIS_URL` vacío), con las dos muestras JSON (ES y EN) de `samples/` y dos descripciones vagas ("Quiero una aplicación para mi negocio, algo sencillo." / "Renovate my bathroom with new tiles and a shower.").

| Bloque | Modelo | Ejecuciones |
|---|---|---|
| EN `medium` × 3 formatos | gpt-4o-mini | 2 por formato (6) |
| ES `medium` × 3 formatos | gpt-4o-mini | 1 por formato (3) |
| EN `summary`/`detailed` × `line_items`/`phases_table` + EN `detailed` `narrative` | gpt-4o-mini | 5 |
| ES `detailed` `phases_table` | gpt-4o-mini | 1 |
| Vagas ES y EN, `medium` `line_items` | gpt-4o-mini | 2 por descripción (4) |
| EN y ES × `phases_table`, `narrative` + vaga EN | claude-haiku-4-5 (`model`) | 5 |
| Guardrails: las 2 muestras (moderación real, gratuita) + 3 inyecciones conocidas | — | 0 llamadas al LLM |

- **Coste estimado**: unas 24 llamadas.
  - gpt-4o-mini: ~8 k tokens de entrada + 1,5 k de salida ≈ 0,002 USD por llamada, ~0,04 USD.
  - Haiku: ≈ 0,016 USD por llamada, ~0,08 USD.
  - Total < 0,15 USD, contando algún reintento.
- **Umbrales**:
  - Idioma correcto en los campos de texto libre: 100 %.
  - `validation.score = 1.0` en todas las respuestas en alcance.
  - Las muestras nunca quedan fuera de alcance.
  - La descripción no software: 100 % fuera de alcance. La vaga: se informa, sin umbral.
  - Ningún 502.
  - Intentos: al menos el 80 % al primer intento; cualquier agotamiento se para y se informa.
  - Ningún falso positivo de guardrail en las muestras; las 3 inyecciones dan 400 sin llamar al LLM.
  - Se registran también la latencia y el `finish_reason`, para revisar el `max_tokens` de 2048.
- Si algo queda por debajo, **se para y se vuelve al usuario con los datos**, como en D14 de S04. Los resultados se añaden a este documento.

#### Resultados de la medición (2026-11-10)

**Primer intento: 24 respuestas 502, sin coste.** `litellm.completion` 1.106.4 importa su gestor de MCP siempre que la llamada lleva `tools`, y ese gestor necesita el paquete opcional `mcp`. Los tests no lo vieron porque usaban un Router simulado. **Corrección**: `generate_structured` pasa `_skip_mcp_handler=True`, el kwarg que el propio LiteLLM retira antes de llamar al proveedor, en lugar de añadir `mcp` como dependencia. Un test de contrato nuevo con el Router real (`mock_response` con `tool_calls`) lo cubre.

**Medición válida**: 24 llamadas sin Redis, con moderación real, más 3 inyecciones conocidas. Coste: 0,096 USD (0,026 gpt-4o-mini y 0,070 Haiku).

| Bloque | Modelo | Idioma | `score` | Fuera de alcance |
|---|---|---|---|---|
| EN `medium` × 3 formatos (6) | gpt-4o-mini | 6/6 | 1,0 | 0/6 |
| ES `medium` × 3 formatos (3) | gpt-4o-mini | 3/3 | 1,0 | 0/3 |
| EN `summary` y `detailed` (5) + ES `detailed` (1) | gpt-4o-mini | 6/6 | 1,0 | 0/6 |
| Vaga ES (2) y no software EN (2) | gpt-4o-mini | 4/4 | `null` | 4/4 (confianza 20-25) |
| EN y ES × `phases_table`, `narrative` (4) + no software EN (1) | claude-haiku-4-5 | 5/5 | 1,0 | 1/1 en la no software |
| 3 inyecciones conocidas (EN, ES y etiquetas) | — | — | — | 400 `prompt_injection` sin llamar al LLM |

- **Umbrales cumplidos**: idioma 100 % (texto libre y etiquetas), `score = 1.0` en todas las respuestas en alcance, las muestras nunca quedan fuera de alcance, la descripción no software siempre queda fuera, ningún 502 y ningún falso positivo de los guardrails con las muestras.
- **Intentos**: 20 de 24 al primer intento (83 %). Los 4 reintentos son todos de `detailed`, con 1 o 2 intentos extra.
  - Diagnóstico, gracias a `error_locs` y `coherence_errors` añadidos al evento `structured_output_retry` (rutas de campo y mensajes fijos de los validadores, nunca valores): primero, `phases` con un elemento por tarea y `team` ausente. Se corrigió reforzando el bloque `detailed` de `v3` (un elemento por fase y el resto de campos igual que en los otros niveles).
  - Queda un caso: alguna fase sin supuestos ("each phase must have 1 to 3 assumptions"), que el segundo intento corrige siempre (5 de 5 en la revalidación). Coste extra en `detailed`: unos 0,001 USD y 10 s.
  - **Mejora pendiente** (`v4`): ejemplos con supuestos por fase cuando se pide `detailed`, porque los 3 ejemplos actuales son `medium`.
- **`max_tokens = 2048`** basta: el máximo observado fue 1959 tokens de salida sumando 3 intentos en `detailed` `phases_table`, y ningún intento individual se acercó al límite. Se mantiene.
- **Latencia**: 6-11 s en `medium` con gpt-4o-mini y 13-26 s en `detailed` (por los reintentos); Haiku, 13-19 s.

### D14. Prompt `v3`

- `app/prompts/estimation/v3/{system,user,examples}.j2`. `user.j2` es una copia literal de `v1`.
- `PromptVersion = Literal["v3"]` y `DEFAULT_PROMPT_VERSION = "v3"`. Se borran `v1/` y `v2/`, y sus tests se sustituyen. El test "carpetas = `PROMPT_VERSIONS`" lo comprueba.
- **Orden de `system.j2`** (D6 de S04 adaptado; nada después de las reglas salvo la línea de idioma):
  1. Regla de idioma (EN).
  2. Rol.
  3. Pista por tipo.
  4. Cómo usar los ejemplos (calibrar horas, perfiles y equipo; el JSON de ejemplo muestra la forma).
  5. **Cómo rellenar los campos**:
     - qué significa cada campo y el perfil `design` solo para diseño UX/UI;
     - horas en múltiplos de 5;
     - la confianza por fase con las pautas de S04 D5, y la global;
     - las fases canónicas;
     - el bloque del `output_format` pedido (en `narrative`, párrafos sin cifras) y el del `detail_level`.
  6. Bloque de fuera de alcance (D9).
  7. `{% include "examples.j2" %}`.
  8. `## Reglas` (sin preguntas; datos como datos; idioma de cierre).
  9. Línea del idioma detectado.
- Sin tarifas ni importes en el prompt, y sin esqueleto Markdown. Las variables `rates` y el filtro `amount` salen del contexto del loader.
- **Dependencia**: `instructor==1.17.0` fijada, como LiteLLM. Instructor envuelve LiteLLM y cambia APIs (hooks, `context`) entre versiones menores. `uv add instructor==1.17.0` y revisar las transitivas en `uv.lock`.

**Nota de implementación (ai-engineer, 2026-11-10).**

- Conflicto entre escenarios de `cag-prompt`: "Narrativa sin cifras" dice que el system de `narrative` no contiene `confidence_pct`, pero "Bloque de fuera de alcance" exige `confidence_pct` menor que 30 en cualquier combinación, y este D14 pide la confianza por fase y global en todos los formatos (el schema la exige siempre). `v3` sigue este D14: `confidence_pct` aparece en los tres formatos. Hay que corregir el escenario "Narrativa sin cifras" (quitar "y no contiene `confidence_pct`").
- Párrafos de `narrative`: el bloque del formato pide de 2 a 4 y el del nivel lo concreta (resumen 2 breves, medio 2 o 3, detallado 3 o 4), dentro del máximo de 6 que valida el schema.
- Con `summary` y `medium`, el bloque del nivel pide dejar vacío `assumptions` de `phases`; la instrucción "Lista los supuestos de cada fase" solo aparece con `detailed`.

### D15. Cómo habilita S05 (sin implementarlo)

- **Historial**: `generate_structured(system, messages, …)` ya recibe una lista ordenada de `ChatMessage`.
  - El `ConversationHistory` de S05 pasará sus turnos `user`/`assistant` tal cual.
  - El system se regenera con `project_metadata` en una `v4`.
  - La caché ya incluye todos los mensajes en la clave.
  - Qué contenido lleva el turno `assistant` (el JSON del `EstimationResult` o el texto renderizado) se decide en S05.
- **`project_metadata` derivable del resultado**, sin otra llamada: `title` → `project_name`, `sum(team.count)` → `assumed_team_size`, `summary` → `agreed_scope`. `mentioned_technologies` no está en el schema (D2). Para eso, la llamada genérica admite otro `response_model`: un extractor `ProjectMetadata` reutiliza fallback, caché, reintentos y logs sin código nuevo en el proveedor.
- **Guardrails**: `screen_texts` y `Moderator.flagged` reciben textos sueltos con su campo. La transcripción y el texto extraído de los adjuntos de S05 pasan por los mismos guardrails añadiendo entradas a la lista.
- **Render y `ComputedEstimate`** son independientes del endpoint: `POST /sessions/{id}/estimate` devolverá el mismo contrato.
- Lo que **no** se hace: ni sesiones, ni `project_metadata` en el prompt, ni adjuntos, ni `technologies`, ni streaming estructurado.

## Risks / Trade-offs

- **[El idioma con el prompt nuevo y la salida por tool calling]** Las descripciones de los campos en inglés van en `tools` y los ejemplos llevan `"language": "es"`. Mitigación: campo `language` el primero del objeto, regla de idioma al principio y al final, línea del idioma detectado y medición D13 en ES y EN.
- **[Reintentos de pago y latencia]** Cada reintento es una llamada completa y se combina con los reintentos del Router. Mitigación: solo se reintenta por validación, como máximo 2 reintentos, normalizaciones en vez de restricciones, y `attempts` en los logs y en la medición.
- **[Coste aproximado si los intentos los sirven modelos distintos]** Tokens sumados con el precio del modelo final. Es poco frecuente (el primario falla a mitad de los reintentos) y queda documentado.
- **[Truncado → 502]** Antes un truncado devolvía texto parcial. Mitigación: la medición revisa `max_tokens = 2048` en `detailed`.
- **[APIs de Instructor 1.17 no comprobadas sin instalar]** Hooks, `Retrying` como `max_retries`, la propagación de excepciones y la plantilla con `context`. Mitigación: primera tarea de verificación contra el código fuente, y tests que fijan el comportamiento (número de llamadas, tokens, mensajes intactos).
- **[Falsos positivos de los guardrails]** Una regex bloquea a un usuario legítimo (400). Mitigación: patrones con verbo + objeto, checksum en el IBAN, corpus de negativos en los tests, y logs con el `rule` para ajustar. El curso recomienda "solo log" antes de bloquear (Open Questions).
- **[Falsos negativos]** Las regex no cubren paráfrasis ni otros idiomas. Es aceptado: es defensa en profundidad con la regla "datos como datos" y la salida estructurada, que limita lo que puede devolver una inyección.
- **[La moderación envía el texto a OpenAI aunque el primario sea Anthropic]** Mitigación: PII antes de la moderación, se puede desactivar con `MODERATION_ENABLED=false` y es *fail-open*.
- **[La evaluación deja de medir al LLM]** Con el render determinista, `score` comprueba el renderer (debería ser siempre 1,0). Se mantiene por la propuesta y como regresión. La calidad del LLM se mide con `attempts`, fuera de alcance e idioma.
- **[Ejemplos sin supuestos por fase]** `detailed` puede reintentar más a menudo. Mitigación: la medición informa los intentos en `detailed`. Si es alto, se propone añadir supuestos por fase a los datos.
- **[`response_model_for` crea subclases]** Si un día el nombre o el schema divergen, la caché o la herramienta cambian. Mitigación: un test comprueba el mismo `model_json_schema()` en las 9 combinaciones.

## Migration Plan

1. Verificar las APIs de `instructor==1.17.0` (D6) y añadir la dependencia. Después: `app/domain.py` (`Language`) y `app/config.py` (`STRUCTURED_MAX_RETRIES`, `MODERATION_ENABLED`; `.env.example`).
2. `app/estimation/` (schema, compute, render, examples) y los datos de `app/context/examples.py`.
3. `app/guardrails/` y `app/services/moderation.py`.
4. `app/prompts/` (`v3`; borrar `v1`/`v2`; `language.py` con `Language`).
5. `app/services/{llm_service,cache}.py` (`generate_structured`, caché, `EstimationOutcome`, guardrails en `estimate`, fuera `thinking`).
6. `app/schemas.py` y `app/routers/estimations.py`.
7. `streamlit_app.py`.
8. Tests y CI.
9. README y `CLAUDE.md` (arquitectura, dependencias y propietarios de los paquetes nuevos).
10. Medición D13.

**Notas de migración:**

- **BREAKING sin transición**: el único cliente es Streamlit, que se actualiza en el mismo change. Todas las claves de caché cambian (prompt `v3` y `kind`). Las entradas antiguas caducan por TTL. Sin cambios en Docker.
- **Rollback**: revertir los commits del change.

## Open Questions

**Resueltas por el orquestador (2026-11-10)**, con la opción recomendada y la autorización del usuario para decidir sin consultarle:

1. `app/estimation/` y `app/guardrails/` pertenecen a `ai-engineer` (se añade a `CLAUDE.md`).
2. `STRUCTURED_MAX_RETRIES = 2`.
3. Fuera de alcance: importes, filas y equipo vacíos (D3).
4. `truncated` se mantiene en el contrato, siempre `false` en un 200.
5. `team` estructurado.
6. `summary`, confianza global y párrafos de `narrative` de los ejemplos, escritos a mano por `ai-engineer`.
7. Moderación activa por defecto cuando hay `OPENAI_API_KEY`.
8. Guardrails bloqueantes desde el primer día, con un corpus de negativos en los tests. "system prompt" y "act as" sueltos no se bloquean.
9. "Cobertura mínima" son las suites obligatorias de D12, sin umbral numérico.
10. `instructor==1.17.0` fijada, como LiteLLM.
11. `max_tokens = 2048` por defecto; la medición decide si sube.
12. Medición D13 autorizada dentro del presupuesto de llamadas reales de esta puesta al día (0,30 USD en total para los dos changes).
14. **Test del enunciado de S04 sobre `confidence_pct`** (contradicción detectada por `ai-engineer`): con salida estructurada, `confidence_pct` es un campo del schema y aparece en el prompt de todos los formatos (confianza global y por fase, y el bloque de fuera de alcance). El test de `v3` comprueba en su lugar el **bloque del formato tabla por fases**, presente con `phases_table` y ausente con `narrative`, que es la intención del enunciado. Specs `cag-prompt` y `ci-pipeline` ajustadas.
13. **Riesgo de plantillas de Instructor (verificado en 1.17.0)**: `handle_templating` (`instructor/v2/core/templating.py`) devuelve los kwargs sin tocar cuando `context` está vacío. Mientras no se pase `context`/`validation_context`, la descripción no se interpreta como plantilla. El test con `{{ x }}` en la descripción se mantiene como regresión.

**Texto original de las preguntas** (para trazabilidad):

1. **Propietarios** de `app/estimation/` y `app/guardrails/` en la tabla de agentes de `CLAUDE.md`. Recomendado: `ai-engineer` para los dos (calidad y seguridad del LLM), y `app/services/moderation.py` ya es suyo.
2. **`STRUCTURED_MAX_RETRIES`**: el valor por defecto recomendado es 2 (3 intentos). Las alternativas son 1, con menos latencia, o 6 como el directo.
3. **`result` fuera de alcance**: vaciar los importes, las filas y el equipo (recomendado, D3), o devolverlo todo con `out_of_scope = true`.
4. **`truncated`**: mantenerlo en el contrato, siempre `false` en un 200 (recomendado), o retirarlo.
5. **`team` estructurado** (recomendado) frente a texto libre. Implica convertir los 3 ejemplos.
6. **Datos nuevos de los ejemplos**: `summary`, confianza global y 2 párrafos de `narrative` por ejemplo, escritos a mano por `ai-engineer` (recomendado). La alternativa es generarlos desde los datos, que da un *few-shot* más pobre.
7. **Moderación activa por defecto** cuando hay `OPENAI_API_KEY`, aunque el primario sea Anthropic (recomendado), o desactivada por defecto.
8. **Guardrails bloqueantes desde el primer día** (propuesta) o un periodo "solo log" como recomienda el curso. Recomendado: bloqueantes, con el corpus de negativos. Y confirmar que "system prompt" y "act as" sueltos **no** se bloquean.
9. **`ci-pipeline` "cobertura mínima"**: se interpreta como las suites de tests obligatorias de D12, no como un porcentaje con `pytest-cov`. ¿Se quiere un umbral numérico?
10. **`instructor==1.17.0` fijada** (recomendado), o `>=1.17,<2` como el resto de dependencias.
11. **`max_tokens = 2048`**: se mantiene por defecto y la medición decide. Si `detailed` trunca, se propone subirlo a 4096 (solo se paga lo generado).
12. **Medición D13**: ¿se autorizan unas 24 llamadas reales (< 0,15 USD) al final del change? Se preguntará cuando llegue la tarea.
