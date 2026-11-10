## Context

Tercer change de puesta al día en `pre-session-04`. Parte del estado que dejan `s03-add-llm-wrapper-cache-logging` (`LiteLLMProvider` sobre un `Router` con grupos `primary`/`fallback`, `CachedProvider` con clave construida desde un dict, `Settings.primary/fallback` como `Deployment`, `MAX_TOKENS = 2048`, structlog) y `s03-add-sse-streaming-and-docker` (SSE multi-turno y endpoint de solo lectura con el contexto CAG para la barra lateral). Las decisiones del usuario que recoge `proposal.md` no se reabren: parámetros opcionales solo en `POST /api/v1/estimate`, tabla `Task | Hours | Cost (EUR)` con tarifas fijas, ejemplos estructurados con tres formateadores, evaluación estructural con `score`/`issues` y respuesta en el idioma de la transcripción.

Restricción heredada (decisiones 15/16 de S03): con gpt-4o-mini y una transcripción en inglés, **cualquier bloque añadido al final del prompt CAG** hizo caer las respuestas en inglés de 6/6 a 0–50 %. La regla de idioma en inglés abre el prompt y cierra `## Reglas`, y el primer turno del chat usa exactamente el prompt del endpoint. Todo cambio de prompt de este change se diseña para no romperlo y **se vuelve a verificar con llamadas reales** (D10).

Comportamiento de LiteLLM 1.106.4 comprobado en el código instalado:

- `thinking={"type": "enabled", "budget_tokens": N}` se reenvía tal cual a Anthropic para modelos con thinking clásico como `claude-haiku-4-5` (`llms/anthropic/chat/transformation.py`, `map_openai_params`, rama `param == "thinking"`). Si se pasa `max_tokens`, LiteLLM **no** lo ajusta (`base_llm/chat/transformation.py`, `update_optional_params_with_thinking_tokens` solo actúa sin `max_tokens`), y Anthropic exige `max_tokens > budget_tokens` y un presupuesto mínimo de 1024 (`constants.py`, `ANTHROPIC_MIN_THINKING_BUDGET_TOKENS`).
- Con OpenAI (`gpt-4o-mini`), `thinking` no está entre los parámetros soportados: `get_optional_params` lanza `UnsupportedParamsError` salvo `drop_params=True` o `additional_drop_params=["thinking"]` (`utils.py`, `_check_valid_arg`). El Router fusiona `litellm_params` del despliegue con los kwargs de la petición (`router.py`, `_completion`, `{**litellm_params, ..., **kwargs}`), así que un `additional_drop_params` por despliegue aplica solo a ese despliegue.
- `router.completion(model=<nombre desconocido>)` sin `default_deployment` ni comodines termina en `BadRequestError` "no healthy deployments" (`router.py`, `_common_checks_available_deployment`): el Router no admite modelos arbitrarios por petición.

## Goals / Non-Goals

**Goals:**

- Formato de salida con coste, verificable aritméticamente, en español o inglés según la transcripción.
- Ejemplos CAG con una única fuente estructurada y tres formas de inyectarlos en el prompt.
- Parámetros por petición que reproducen el comportamiento actual por defecto, validados antes de cualquier llamada al LLM.
- Evaluación estructural pura (sin LLM, sin red), que acepte etiquetas ES/EN.
- Capas intactas: `routers → services → context`, `config` transversal; tests sin red.

**Non-Goals:**

- Parámetros en el SSE o en Streamlit; LLM como juez, datasets o comparativas automáticas (S05+); formulario tipado y Jinja2 (S04); comprobación de la tarifa fila a fila (ver *Risks*).

## Decisions

### D1. Modelo de datos de los ejemplos: fuente estructurada y Markdown renderizado

- `app/context/examples.py` define `RATES_EUR_PER_HOUR = {"dev": 62.5, "design": 50.0}` (la usan el prompt y los ejemplos) y reescribe los **3 ejemplos actuales** (*Open Questions* 5) con la misma calibración de horas.
- `EstimationExample` sigue siendo un `TypedDict` con `meeting_summary` y `estimation` (Markdown) y añade `title`, `rows: list[ExampleRow]` (`task`, `hours`, `role: "dev" | "design"`), `team`, `duration_weeks: tuple[int, int]` y `risks: list[str]`. Un helper `_example(...)` calcula el coste de cada fila (`hours × RATES[role]`), los totales y renderiza `estimation` **una vez al importar**. Solo el diseño UX/UI usa la tarifa `design`; el resto, `dev`.
- El rol **no** se pinta en ningún formato (la tabla es `Tarea | Horas | Coste (EUR)`, decisión del usuario): los tres formatos muestran la misma información, así que comparar formatos mide el formato y no el contenido.
- Tests: horas múltiplo de 5; coste de fila = horas × tarifa; totales = suma; y **cada `estimation` renderizada obtiene `score == 1.0` en la evaluación de D6** (los ejemplos son el caso de referencia del evaluador).
- Barra lateral, endpoint de contexto del change 2 y README siguen leyendo `ex["meeting_summary"]` y `ex["estimation"]` sin cambios; el README actualiza el ejemplo de salida.
- *Alternativas descartadas*: `dataclass` congelada como la referencia (más limpia, pero rompe el acceso por clave que usan Streamlit y el endpoint de contexto que se implementa en paralelo); Markdown literal precalculado más test de coherencia (dos fuentes que mantener; el test solo detecta la divergencia); columna `Perfil` en la tabla (contradice la decisión del usuario).

### D2. Formateadores de ejemplos

- `format_examples(examples, fmt) -> str` en `app/context/examples.py`, puro. El bloque siempre va dentro de `<examples>…</examples>`:
  - `markdown` (por defecto): el formato actual, `<example><meeting_summary>…</meeting_summary><estimation>…</estimation></example>`, con la `estimation` renderizada.
  - `json`: `json.dumps(..., ensure_ascii=False, indent=2)` de una lista con `meeting_summary`, `title`, `breakdown` (`task`, `hours`, `cost_eur`), `totals` (`hours`, `cost_eur`), `team`, `duration_weeks` (`[min, max]`) y `risks`. Claves en inglés (identificadores), valores en español.
  - `narrative`: un párrafo en español por ejemplo ("En un proyecto anterior el cliente pedía: … Propusimos «título»: N horas y X EUR en A-B semanas con …. Tareas: tarea (N h, X EUR); … Riesgos: …"). Incluye **todas** las tareas con horas y coste y los riesgos, para que los tres formatos lleven la misma información (D1; *Open Questions* 7).
- `_HOW_TO_USE_EXAMPLES` deja de citar las etiquetas `<example>`/`<meeting_summary>` y pasa a una redacción neutra válida para los tres formatos.
- Selección: los `num_examples` primeros en el orden fijo del fichero (determinista; el orden se elige para que los primeros sean los más distintos entre sí). Con 0 ejemplos (`num_examples=0` o `use_examples=false`) el prompt omite tanto el bloque `<examples>` como `_HOW_TO_USE_EXAMPLES`, para no referirse a ejemplos inexistentes (*Open Questions* 6).
- *Alternativa descartada*: ejemplos JSON o narrativos en inglés como la referencia (añaden texto en inglés al prompt y cambian a la vez dos variables del experimento: formato e idioma).

### D3. Formato de salida en el prompt

`_OUTPUT_FORMAT` se reescribe con la estructura (etiquetas en español y, entre paréntesis, en inglés, como hoy):

```
## Estimación: <proyecto>                (## Estimate: <project>)
### Desglose de tareas                   (### Task breakdown)
| Tarea | Horas | Coste (EUR) |          (| Task | Hours | Cost (EUR) |)
### Totales                              (### Totals)
- **Total de horas:** N                  (- **Total hours:** N)
- **Coste total:** X EUR                 (- **Total cost:** X EUR)
- **Equipo recomendado:** …              (- **Recommended team:** …)
- **Duración estimada:** A-B semanas     (- **Estimated duration:** A-B weeks)
### Supuestos y riesgos                  (### Assumptions and risks)
```

- **Esqueleto en inglés** (*Open Questions* 8): `_OUTPUT_FORMAT` termina con "If the transcript is in English… with this skeleton" y el esqueleto con las etiquetas inglesas. Es una desviación medida de "etiquetas en español con el inglés entre paréntesis": sin él, D10 dio EN 4/6 por defecto y 0/3 con `inline_cleaning`/`two_phase`. La regla de cierre de `## Reglas` enumera las etiquetas inglesas.
- El párrafo del rol no cita las nuevas etiquetas en español (añadían texto español cerca de la regla de idioma).
- Tarifas en la misma sección (no en un bloque nuevo), en **una línea compacta bilingüe**: 62,50 EUR/h desarrollo, QA, despliegue y gestión; 50 EUR/h diseño UX/UI. Instrucciones: calcular primero el coste de cada fila (horas × tarifa), después los totales como suma exacta de las filas; **sin fila de total dentro de la tabla**; importes sin separador de miles y con decimales solo si no son enteros (`937,50` / `937.50`).
- Se mantienen: horas múltiplo de 5 (los costes de desarrollo pueden acabar en ,50; ver *Open Questions* 4), unas 30 h productivas por semana y persona, 2–4 riesgos, sin introducciones.
- **Orden y regla de idioma intactos**: rol (abre con la regla en inglés) → limpieza opcional (D5) → cómo usar los ejemplos (solo si hay) → formato → `<examples>` → `## Reglas` (cierra con la regla en inglés). Ningún bloque nuevo va después de `## Reglas`; el chat sigue añadiendo `_CONVERSATION` solo desde el segundo turno.
- *Alternativa descartada*: copiar el bloque en inglés de la referencia (`PROMPT_OUTPUT_STRUCTURED`): más texto en inglés en un prompt español cambia el equilibrio de idioma medido en S03 y no traduce etiquetas.

### D4. `GenerationOptions`: campos, validación y flujo

| Campo (request) | Tipo | Por defecto | Validación (422) |
|---|---|---|---|
| `preprocessing` | `"none" \| "inline_cleaning" \| "two_phase"` | `"none"` | enum |
| `example_format` | `"markdown" \| "json" \| "narrative"` | `"markdown"` | enum |
| `num_examples` | `int` | `len(ESTIMATION_EXAMPLES)` | `0..len(ESTIMATION_EXAMPLES)` |
| `use_examples` | `bool` | `true` | `false` equivale a `num_examples = 0` |
| `model` | `string \| null` | `null` | 1..100 caracteres; resolución abajo |
| `max_tokens` | `int` | `2048` | `1..16384` (salida máxima de gpt-4o-mini) |
| `thinking_budget` | `int \| null` | `null` | `1024..16384` (mínimo de Anthropic) |
| `evaluate` | `bool` | `true` | — |

- Flujo: el router mapea el request a `GenerationOptions` (dataclass congelada en `llm_service`) y llama `EstimationService.estimate(transcription, options)`. El servicio resuelve el modelo, construye el prompt con `build_system_prompt(examples, example_format=…, inline_cleaning=…)` (pura, cacheada por combinación) y llama al proveedor con `LLMCallOptions(model: Deployment | None, max_tokens, thinking_budget)`. `schemas.py` importa los límites de `llm_service` (como ya hace con `MAX_TRANSCRIPTION_CHARS`), así que no depende de `context`.
- **Protocol**: `generate(system, messages, options: LLMCallOptions | None = None)` y `stream(...)` igual. `None` equivale a los valores por defecto, así que el chat, el SSE, `FakeProvider` y los stubs del change 2 siguen funcionando sin cambios.
- **`model`**: `Settings.resolve_request_model(name) -> Deployment` (en `config.py`, reutiliza la resolución de D7 de S03 con `litellm.get_llm_provider`, sin red). Modelo no reconocido, proveedor fuera de `{openai, anthropic}` o sin key configurada → `ValueError` con mensaje sin secretos. El servicio lo convierte en `InvalidOptionError(field, message)` y el router responde **422** con la misma forma que los errores de Pydantic (`detail: [{"loc": ["body", "model"], "msg": …, "type": "value_error"}]`). Se valida **antes** de cualquier llamada, también antes de la fase previa de `two_phase`.
- **Override sin fallback** (*Open Questions* 1): `LiteLLMProvider` recibe un `router_factory(deployment) -> Router` (con `lru_cache(maxsize=8)` por `Deployment`, que es hashable) que construye un Router de un solo grupo `primary` con ese despliegue, los mismos `timeout`/`num_retries` y sin `fallbacks`. Así se reutiliza el mismo camino (`_hidden_params["model_id"]`, reintentos, errores, logs) y `model`/`provider` efectivos son los del override con `fallback_used = false`. `cost_usd` sigue siendo `null` si el modelo no está en `pricing.py`.
- **`thinking_budget`** lo decide `LiteLLMProvider`, que conoce el proveedor del despliegue destino (override o primario):
  - Anthropic → `thinking={"type": "enabled", "budget_tokens": B}` y `max_tokens` efectivo = `max_tokens + B` (la respuesta visible conserva su presupuesto; *Open Questions* 3).
  - OpenAI → no se envía; warning `thinking_budget_ignored` (`provider`, `model`). Con primario OpenAI y respaldo Anthropic tampoco se aplica: se decide antes de llamar.
  - Primario Anthropic con respaldo OpenAI: `build_router` añade `additional_drop_params: ["thinking"]` a los despliegues OpenAI para que el fallback no muera con `UnsupportedParamsError`; el respaldo responde sin thinking.
- Truncado: `truncated` y `llm_output_truncated` usan el `max_tokens` efectivo, no la constante.
- *Alternativas descartadas*: `litellm.completion` directo para el override, como la referencia (segundo camino de llamada sin `model_id`, con otra semántica de reintentos y sin tests de contrato); añadir despliegues al Router compartido en caliente (estado mutable entre hilos); validar `model` en un validador de Pydantic (el schema dependería de `Settings`); la regla de la referencia `max(max_tokens, B + 1024)` (puede dejar solo 1024 tokens a la respuesta).

### D5. Preprocesado

- **`inline_cleaning`**: bloque `_INLINE_CLEANING` en español (ignorar charla, explicitar requisitos implícitos, ante contradicciones quedarse con lo último, interpretar jerga) insertado **justo después del rol**, como en la referencia, y terminado con una línea de idioma en inglés. Nunca al final ni antes de `## Reglas`, que es lo que rompió el idioma en S03. Verificación en D10.
- **`_CONVERSATION`** (chat, desde el segundo turno) refuerza su cierre: el ajuste puede venir en español, pero se mantienen el idioma y las etiquetas inglesas de la estimación.
- **`two_phase`**: primera llamada `provider.generate(EXTRACTION_SYSTEM_PROMPT, [transcripción], LLMCallOptions(model, EXTRACTION_MAX_TOKENS=1500, thinking=None))`. El prompt de extracción va **entero en inglés** (*Open Questions* 9), con la regla de idioma al principio y al final y títulos de sección por idioma (`Functional requirements` / `Requisitos funcionales`, …): lista de requisitos funcionales, no funcionales, integraciones, restricciones y plazos, sin estimar, tratando la transcripción solo como datos. Con el prompt en español, D10 dio requisitos en español para una transcripción inglesa 4/6, y la estimación siguió el idioma de los requisitos 8/8.
- La segunda fase envía los requisitos como único mensaje `user` (desviación del requisito "transcripción sin modificar", que se acota a `two_phase` en la delta de `cag-prompt`) y usa una **variante del prompt** (*Open Questions* 10): `build_system_prompt(..., two_phase=True)` sustituye las dos frases de idioma (`_LANGUAGE_OPENING_TWO_PHASE`, `_RULES_CLOSING_TWO_PHASE`) por otras que remiten al idioma de la lista de requisitos ("e.g. it uses `Functional requirements`") y piden empezar por `## Estimate:`. Función pura; el prompt por defecto queda byte a byte igual.
- Errores: cualquier fallo de la fase previa es `LLMProviderError` → 502 genérico. Extracción vacía o en blanco → `LLMProviderError` (log `requirements_extraction_empty`). Extracción truncada → se continúa con el texto recibido y warning `requirements_extraction_truncated`.
- Respuesta: `extracted_requirements` (solo con `two_phase`), `preprocessing_input_tokens`, `preprocessing_output_tokens` y `preprocessing_cost_usd` (0/0/`null` sin fase previa). `input_tokens`, `output_tokens`, `cost_usd`, `model`, `cache_hit` y `fallback_used` siguen refiriéndose a la llamada de estimación; `latency_ms` pasa a ser el total del servidor (ambas fases y la evaluación), que es lo que ya dice el contrato. *Open Questions* 2.
- Caché: cada fase es una llamada independiente por `CachedProvider` con su propia clave; si la fase 1 acierta, la 2 normalmente también.
- *Alternativas descartadas*: prompt de extracción en español con solo la regla de idioma en inglés (diseño inicial; deriva medida en D10); reutilizar el system prompt por defecto en la fase 2 (no ancla el idioma cuando la entrada es una lista); extracción en el mismo servicio con un proveedor sin caché (la repetición de experimentos es justo lo que la caché abarata).

### D6. Evaluación estructural

- `app/services/evaluation.py`: `evaluate_estimation(text: str, truncated: bool) -> StructureCheck` (dataclass congelada), pura, sin LLM ni red. La ejecuta `EstimationService` cuando `evaluate=true` (también en aciertos de caché); el router solo mapea a Pydantic. `validation = null` si `evaluate=false`.
- Etiquetas aceptadas siempre en ambos idiomas, sin detectar el idioma, insensibles a mayúsculas, a `*`/`_` y a espacios:
  - título: `^#{1,3}\s*(Estimación|Estimacion|Estimate)\s*:`;
  - tabla: una fila de cabecera con celdas `Tarea|Task`, `Horas|Hours` y `Coste|Costo|Cost` seguida de una fila separadora `|---|`. Los índices de columna salen de la cabecera (tolera columnas extra o en otro orden); se leen filas hasta la primera línea que no empiece por `|`; se ignoran las filas cuya tarea empiece por `Total`;
  - totales: `(Total de horas|Total horas|Total hours|Hours total|Total estimado)` y `(Coste total|Costo total|Total cost|Cost total)` seguidos (también `Hours total`: variantes observadas en gpt-4o-mini en D10) de un número;
  - equipo: `Equipo recomendado|Recommended team`; duración: `Duración estimada|Estimated duration` con un número y `semana(s)|week(s)`.
- Números: se eliminan `EUR`, `€`, `h`, espacios (incluido NBSP) y `*`. Si hay `.` y `,`, el último es el decimal; si solo hay uno y le siguen exactamente 3 dígitos, es de miles; si no, es decimal. Así `19500`, `19.500`, `19,500`, `937,50` y `937.5` se leen bien en ES y EN.
- Comprobaciones (8): título, tabla, totales (ambos), equipo, duración, `hours_match` (|suma − total| ≤ 1 h), `cost_match` (|suma − total| ≤ 2 % del total, como la referencia) y `not truncated`. `score = aprobadas / 8` redondeado a 3 decimales; una comprobación aritmética sin datos (`None`) cuenta como no aprobada. `issues`: frases fijas en inglés con números, nunca texto de la estimación (pueden ir al log).
- Campos: `has_title`, `has_breakdown_table`, `has_totals`, `has_team`, `has_duration`, `rows_parsed`, `declared_total_hours`, `sum_row_hours`, `hours_match`, `declared_total_cost`, `sum_row_cost`, `cost_match`, `finish_ok`, `score`, `issues`.
- `example_format` no afecta a la evaluación: la salida pedida es siempre Markdown; medir cómo cambia el `score` con el formato de los ejemplos es precisamente el experimento.
- Log `estimation_evaluated` (`score`, `issues` en número). *Alternativa descartada*: evaluar en el router (lógica de negocio en la capa HTTP); detectar el idioma y exigir sus etiquetas (falsos negativos con respuestas mixtas, que ya se registran como deriva de idioma en la verificación manual).

### D7. Clave de caché

- `CachedProvider._key` añade al dict de S03 los campos que cambian la petición: `{"system", "messages", "model": <override o primario>, "max_tokens": <solicitado>, "thinking_budget": <o null>, "model_override": <bool>}`. `model_override` distingue el mismo modelo pedido por override (sin fallback) del primario configurado (con fallback).
- Las opciones del prompt (`preprocessing`, `example_format`, `num_examples`, `use_examples`) ya entran a través de `system`/`messages`; la fase previa tiene su propio system prompt. `evaluate` no entra (posproceso).
- Todas las claves cambian una vez respecto a S03 (el prefijo `v1` versiona el valor guardado, que no cambia): las entradas antiguas caducan por TTL.
- *Alternativa descartada*: añadir campos solo cuando no son por defecto para conservar las claves de S03 (dos formas de clave para el mismo dict; ahorra como mucho 24 h de caché local).

### D8. Contrato HTTP y compatibilidad

- `EstimationRequest` gana los 8 campos opcionales de D4; un body solo con `transcription` produce la misma llamada que hoy (mismo modelo, `max_tokens`, sin thinking), con el nuevo formato de salida.
- `EstimationResponse` gana `validation: StructureCheck | null`, `preprocessing`, `extracted_requirements: string | null`, `preprocessing_input_tokens`, `preprocessing_output_tokens` y `preprocessing_cost_usd: number | null`. Todo aditivo y plano, como los campos existentes (la referencia anida `usage`).
- OpenAPI documenta 200, 422 (incluido `model` inválido) y 502. El SSE y el chat llaman sin opciones (valores por defecto); su contrato no cambia.
- Logs: `estimation_requested` con las opciones (sin contenido) y los eventos de D5/D6. Nunca transcripción, requisitos extraídos ni prompt.

### D9. Reparto por agente

| Capa | Agente |
|---|---|
| `app/context/examples.py`, `app/services/{llm_service,evaluation}.py` | `ai-engineer` |
| `app/config.py` (`resolve_request_model`), `app/schemas.py`, `app/routers/estimations.py` | `backend-engineer` |
| `tests/` | `qa-engineer` |
| `README.md` | orquestador |

### D10. Verificación del idioma y del formato (manual, con llamadas reales)

Requiere **consentimiento del usuario** (coste estimado: céntimos con gpt-4o-mini y Haiku). Matriz mínima, con las transcripciones de prueba de S03 en inglés y en español:

- Prompt por defecto, gpt-4o-mini, EN: 6/6 en inglés (el umbral de S03). ES: 2/2.
- gpt-4o-mini EN con `inline_cleaning`, `two_phase`, `example_format=json`, `narrative` y `num_examples=0`: 3/3 cada uno.
- Haiku EN y ES con valores por defecto: 2/2; Haiku con `thinking_budget=2048`: 1.
- Chat en Streamlit: primer turno EN y un ajuste en español (4/4 en inglés, como S03).
- Se registra el `score` de cada respuesta. Si alguna variante deriva de idioma, se vuelve al usuario con los datos antes de tocar el prompt.

#### Resultados (2026-11-10, transcripción EN `samples/reserva-restaurantes-en.txt`)

Prompt inicial (gpt-4o-mini, respuestas en inglés):

| Variante | Resultado |
|---|---|
| Por defecto EN | 4/6 |
| `inline_cleaning` | 0/3 |
| `two_phase` | 0/3 |
| `json` | 2/3 |
| `narrative` | 2/3 |
| 0 ejemplos | 0/3 |
| Chat, turno 1 | 1/2 |
| Chat, ajuste en español | 1/2 |
| ES y Haiku | correctos |

Causa: más texto en español (prosa de tarifas y cálculo, lista de etiquetas españolas en el rol). Correcciones aprobadas por el usuario: las de D3 y D5 (*Open Questions* 8–10).

Prompt final:

| Variante | Resultado |
|---|---|
| Por defecto gpt-4o-mini EN / ES | 6/6 / 2/2 |
| `inline_cleaning`, `json`, `narrative`, 0 ejemplos | 3/3 cada uno |
| `two_phase` (requisitos y estimación) | 3/3 |
| Chat, turno 1 / ajuste | 2/2 / 4/4 |
| Haiku EN/ES; Haiku con thinking | correctos (thinking, a la primera) |

Gasto total de la verificación: ≈ 0,12 USD. Aritmética: gpt-4o-mini declara totales que no cuadran con la suma de filas en la mayoría de respuestas (desvío de 5–100 h); Haiku obtiene `score` 1.0. Ver *Risks*.

### D11. Cómo habilita S04

- `GenerationOptions` es independiente de HTTP: el formulario tipado de S04 la poblará desde sus "opciones avanzadas" sin tocar el servicio.
- `example_format` (cómo se **inyectan los ejemplos**, lado de entrada) no es el `output_format` de S04 (`phases_table | line_items | narrative`: cómo se **formatea la respuesta**, lado de salida). Coexistirán; la evaluación de D6 aplica a la salida en tabla, y en S04 habrá que decidir si se evalúa por formato de salida o solo la tabla.
- Las secciones del prompt (`_ROLE_AND_TASK`, `_INLINE_CLEANING`, `_HOW_TO_USE_EXAMPLES`, `_OUTPUT_FORMAT`, ejemplos, `_RULES`, `_CONVERSATION`) pasan 1:1 a templates Jinja2 en `prompts/estimation/v1/`; los formateadores pueden quedarse en Python y alimentar `examples.j2`. Como el system prompt entra entero en la clave de caché, cambiar de versión de template invalida sola.

## Risks / Trade-offs

- [El nuevo formato o los bloques opcionales hacen que gpt-4o-mini responda en español a transcripciones en inglés] → Orden y reglas de idioma de S03 intactos, nada tras `## Reglas`, extracción con la regla de idioma al principio y verificación D10 antes de cerrar.
- [La aritmética con ,50 EUR induce errores del modelo] → Instrucción de parciales antes de totales; la evaluación lo mide. *Open Questions* 4.
- [gpt-4o-mini declara totales que no cuadran con las filas (medido en D10: 5–100 h de desvío en la mayoría de respuestas)] → Limitación documentada (decisión del usuario): el `score` la hace visible y sirve de base a la evaluación de S05. No se calculan totales en el servidor (cambiaría el contrato).
- [La tolerancia del 2 % oculta errores de coste de cientos de euros en proyectos grandes] → Es la de la referencia, como constante; se puede endurecer tras ver datos. La tarifa por fila no se comprueba (la tabla no lleva el rol).
- [El evaluador depende de etiquetas concretas: una variante válida pero distinta puntúa bajo] → Regex tolerantes, tests con variantes reales de la verificación D10 y los ejemplos como caso de `score = 1.0`.
- [Un override a un modelo con salida máxima menor que `max_tokens`, o thinking con un modelo que no lo soporta, devuelve 400 del proveedor] → Sin fallback en el override: 502 genérico y `status_code` en el log.
- [`additional_drop_params` es un detalle de LiteLLM] → Test de contrato sin red con `litellm.utils.get_optional_params` (con y sin `additional_drop_params`) que fija el comportamiento de la 1.106.4.
- [Cambiar la firma del Protocol mientras otros agentes implementan el change 2] → Parámetro opcional al final con `None` por defecto: ningún llamador existente cambia.

## Migration Plan

1. Requisito: `s03-add-llm-wrapper-cache-logging` archivado (la delta de `response-cache` modifica su spec). Orden: `context` (D1, D2) → `config` → `llm_service` (D3–D5, D7) → `evaluation` (D6) → schema y router (D8) → tests → README → verificación D10.
2. Sin cambios de `.env`. Las entradas de caché de S03 dejan de acertar una vez (D7).
3. Rollback: revertir los commits del change; los clientes solo pierden campos opcionales.

## Open Questions

Resueltas por el usuario el 2026-11-09 (todas con la opción recomendada); el resto del documento ya las refleja:

1. **Override de `model`**: sin fallback, como la referencia. Si el modelo pedido falla, 502 genérico.
2. **Coste de `two_phase`**: por separado (`preprocessing_input_tokens`, `preprocessing_output_tokens`, `preprocessing_cost_usd`), sin total sumado; `cost_usd` es solo el de la estimación.
3. **Thinking**: `max_tokens` efectivo = `max_tokens + thinking_budget`; con OpenAI el presupuesto se ignora y solo se avisa en el log (`thinking_budget_ignored`), sin campo en la respuesta.
4. **Redondeo**: horas múltiplo de 5 (los costes de desarrollo pueden acabar en ,50 EUR).
5. **Ejemplos**: se mantienen los 3 ejemplos actuales, reescritos con coste; `num_examples` va de 0 a 3.

Resueltas por el usuario el 2026-11-10:

6. **0 ejemplos**: con `num_examples=0` o `use_examples=false` se omiten el bloque `<examples>` y la sección "cómo usar los ejemplos" (D2, D3).
7. **Formato `narrative`**: incluye todas las tareas con horas y coste y los riesgos, para que los tres formatos lleven la misma información (D1, D2).
8. **Esqueleto en inglés** al final de `_OUTPUT_FORMAT`: aceptado como desviación medida de "etiquetas en español con el inglés entre paréntesis" (D3, D10).
9. **Prompt de extracción**: entero en inglés, con la regla de idioma al principio y al final y títulos por idioma (D5).
10. **Fase 2 de `two_phase`**: se mantiene la variante de las frases de idioma que remite a la lista de requisitos (D5).
