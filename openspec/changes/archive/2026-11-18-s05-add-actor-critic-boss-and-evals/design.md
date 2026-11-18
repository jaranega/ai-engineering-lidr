## Context

El directo de S05 (`session_05_live`, `estimator/`) añadió tres cosas. Las dos primeras, memoria con anclas y resumen y tier dinámico, ya están en `session-06` (change archivado `2026-11-17-s05-add-memory-compression-and-dynamic-tier`). La tercera es la que falta: **Actor-Critic-Boss** (ACB) y un **marco de evals**.

**Cómo lo resuelve el directo** (verificado con el código de la rama):

- **Critic** (`app/services/critic.py`, `app/schemas/critic.py`, `app/prompts/critic/v1/`):
  - Es una llamada estructurada (`CriticFeedback`) con un veredicto `accept | needs_iteration | reject`, un máximo de 12 problemas (`category`, `severity`, `field_path`, `description` y `suggested_fix` opcional) y `confidence_in_review` de 0 a 100.
  - Un validador exige al menos un problema `critical` o `major` para `needs_iteration`, y al menos un problema para `reject`.
  - El prompt usa siete categorías con umbrales concretos (`math_error`, `hallucination`, `scope_mismatch`, `phase_imbalance`, `missing_assumption`, `unrealistic_estimate`, `tier_mismatch`) y pide aceptar ante la duda.
  - Si la llamada falla, se trata como `accept` con confianza 0.
- **Boss** (`app/services/boss.py`):
  - Es una máquina de estados sin LLM. Recibe el Actor como callable `actor(feedback) -> EstimationResult` y el Critic como `critic(result) -> CriticFeedback`.
  - Por cada iteración decide `accept | iterate | synthesize`; `reject` sintetiza directamente.
  - Sintetizar es tomar el último borrador, anteponer los problemas al `summary` (recortado a 1200) y dejar la confianza en `max(30, confianza // 2)`.
  - El tope es `BOSS_MAX_ITERATIONS=3`. Un comentario del código explica que con 2 iteraciones el Actor no suele corregir todos los problemas.
- **Integración**:
  - Hay un endpoint aparte, `POST /sessions/{id}/estimate-acb`, con respuesta `ACBResponse = EstimationResponse + acb: BossTrace`.
  - El Actor recibe el feedback en un bloque `<critic_feedback>` del mensaje `user`.
  - En la sesión solo se guarda el resultado final.
- **Evals** (`evals/`):
  - `dataset.py` define `GoldenCase` con rangos de coste, semanas y número de fases, `expected_in_summary`, `expected_technologies_any_of`, `expected_out_of_scope` y `expected_tier`. Este último está en el dataset, pero ninguna métrica lo usa.
  - `golden_dataset.json` tiene 16 casos en inglés.
  - `metrics.py` define el `@dataclass MetricResult(name, score, passed, details)`, tres métricas y `run_all_metrics(case, result)`.
  - `run.py` es una CLI con `--mode actor|acb`, `--limit`, `--http` y `--output`. Funciona en proceso con `TestClient` y un `SessionStore` aislado. Un 400 de los guardrails en un caso que espera "fuera de alcance" se convierte en un sobre sintético que pasa las métricas.

**Verificación con el código del directo (tarea 1.2).** Se ha contrastado lo anterior con `app/services/{critic,boss}.py`, `app/schemas/{critic,acb}.py`, `app/prompts/critic/v1/`, `estimate_with_acb` y `evals/` de `session_05_live`. No hay diferencias que cambien las decisiones. Dos detalles se adoptan tal cual:
- `CRITIC_MODEL` y `BOSS_MAX_ITERATIONS` como configuración;
- el Actor como callable que re-renderiza el prompt con el feedback.

Uno se descarta: el `summary` recortado a 1200 en la síntesis (D6).

**Lo que pide S06.** El ejercicio "Stress test del CAG" amplía exactamente esta base:

- reutiliza `MetricResult` y `run_all_metrics`, a los que añade `LatencyBudgetMetric`, `CostBudgetMetric` y `MemoryDriftMetric`;
- toma el patrón de `evals/run.py` (`--http` o `TestClient`) para un runner nuevo, `evals/stress/run.py`;
- añade un evento `turn_observed`.

**Nuestro estado de partida:**

- `EstimationService.estimate_turn` no guarda estado. Renderiza, llama a `_generate`, que hace la llamada estructurada, el cálculo (`compute_estimate`), el render y la evaluación, y devuelve `TurnOutcome`.
- `SessionService.estimate` sigue este orden: lock, tier, adjuntos, `estimate_turn`, y después actualiza la sesión y comprime.
- Los importes y los totales los calcula el código, nunca el LLM. Un borrador con confianza < 30 (`OUT_OF_SCOPE_CONFIDENCE`) es un aviso de fuera de alcance, sin filas ni totales.
- La versión por defecto de las sesiones es `v5`, y está publicada.

## Goals / Non-Goals

**Goals:**

- Que los turnos de sesión, cuando lo pida quien llama, pasen por la revisión Actor-Critic-Boss del directo, adaptada a nuestro esquema y bilingüe, sin cambiar nada de los turnos sin revisión.
- Que el Boss sea comprobable sin LLM: lógica pura, testeable con dobles y con la traza en la respuesta.
- Que haya un marco de evals deterministas con la API que S06 amplía (`MetricResult`, `run_all_metrics` y el runner con `--mode` y `--http`), un golden dataset bilingüe y tests sin red.
- Medir con llamadas reales, con permiso, cuánto aporta y cuánto cuesta la revisión.

**Non-Goals:**

- El ejercicio de S06 (`turn_observed`, stress, métricas de presupuesto y deriva, `REPORT.md`).
- LLM-as-judge (DeepEval), los tests de consistencia con N ejecuciones y los evals en CI.
- La revisión en `POST /api/v1/estimate` y en el streaming.
- Optimizar el prompt de estimación con lo que encuentre el Critic. Eso sería un change aparte, a partir de los datos.

## Decisions

### D1. Activación: `review` en el formulario del turno (decisión del usuario)

`POST /api/v1/sessions/{id}/estimate` acepta `review: bool = False` (Form).

- Con `true`, el turno pasa por el ACB y la respuesta incluye `acb`.
- Con `false`, el turno es idéntico al actual y `acb` es `null`.
- `review=true` exige un prompt con `<critic_feedback>` (D4). Con `v4` o `v5`, responde **422** en `["body", "review"]` (`"requires prompt_version v6"`), igual que `tier` con `v4`.

*Alternativas:* un endpoint `/estimate-acb`, como en el directo, que duplica la ruta, el manejo de errores y la documentación de OpenAPI; o un modo global por configuración, que no deja comparar `actor` y `acb` en el mismo servidor, cosa que necesitan los evals.

### D2. Dónde vive el bucle: en `EstimationService`, entre la llamada estructurada y el cálculo

El Boss envuelve el paso que produce el `EstimationResult`:

- **Actor**: el render del prompt con el feedback más `_call_structured`.
- **Critic**: recibe cada borrador ya calculado con `compute_estimate`, porque necesita horas e importes para juzgar si la estimación es realista.
- Al terminar el bucle, se calcula, renderiza y evalúa **una sola vez** el resultado final, con el `_generate` de siempre, partido en dos funciones: generar y finalizar.

`estimate_turn` recibe `review: ReviewConfig | None`, con `max_iterations` y el destino del Critic, y sigue sin estado. `SessionService` no cambia de forma: pasa el flag y guarda el resultado final como cualquier turno. Así, el historial, el `project_metadata`, `last_tier` y la compresión se actualizan una sola vez, y los borradores intermedios no dejan rastro.

*Alternativas:*

- El bucle en `SessionService`, llamando a `estimate_turn` varias veces. Repetiría los guardrails y la moderación en cada iteración, duplicaría coste y eventos, y obligaría a sacar el render del feedback fuera de `estimate_turn`.
- Un servicio aparte que llame a `estimate_turn`. Mismo problema.

### D3. Critic: contrato del directo con categorías de nuestro dominio

`app/services/critic.py` define:

- `CriticIssue`:
  - `category` es un `Literal` de siete valores: `hallucination`, `scope_mismatch`, `constraint_conflict`, `phase_imbalance`, `missing_assumption`, `unrealistic_estimate` y `tier_mismatch`.
  - `severity` ∈ `critical | major | minor`.
  - `field_path` tiene de 1 a 120 caracteres.
  - `description` tiene de 5 a 300 caracteres.
  - `suggested_fix` es opcional, de hasta 200 caracteres.
- `CriticFeedback`:
  - `verdict` ∈ `accept | needs_iteration | reject`;
  - un máximo de 12 `issues`;
  - `confidence_in_review` de 0 a 100;
  - los validadores del directo: `needs_iteration` exige al menos un problema `critical` o `major`, y `reject` exige al menos un problema.

Cuando un validador falla, Instructor reintenta como con la estimación. Las categorías cambian respecto al directo:

- **Sin `math_error`**: los totales los calcula el código. Un error aritmético sería un bug nuestro, y para eso están los tests y la evaluación estructural.
- **`constraint_conflict`, nueva**: la estimación contradice una `explicit_constraint` (excede un presupuesto cerrado o un plazo innegociable, o ignora un NDA o una normativa) o usa una opción de `rejected_options`. Aprovecha los dos campos que añadió el change anterior.
- **Umbrales de `unrealistic_estimate` y `phase_imbalance` en horas**: una fase con más del 75 % de las horas sin justificar es `major`. Una desviación de más del 50 % respecto a lo razonable para el alcance y el equipo es `major`; entre el 30 % y el 50 %, `minor`; por debajo, se ignora.
- **`field_path` con nuestro esquema**: `summary`, `rows[3].hours`, `phases[1]`, `team`, `duration_weeks`, `assumptions_and_risks`, `technologies`.

`description` y `suggested_fix` se escriben **en el idioma de la sesión**, porque pueden acabar como avisos al usuario (D6). El Critic se llama con `llm_purpose="critic"` y respeta `CRITIC_MODEL` (D9).

**Fail-open.** Cualquier `LLMProviderError`, incluido `StructuredOutputError`, se registra como `critic_failed` y se trata como `accept` con `confidence_in_review = 0`, marcando la iteración con `critic_failed = true`. Un Critic caído nunca hace fallar el turno.

*Alternativas:* conservar `math_error` como red de seguridad, que es ruido porque el modelo no ve un total que pueda descuadrar; o dejar el texto libre del Critic, que es el antipatrón 2 del material.

### D4. Actor: prompt `v6` con `<critic_feedback>` en el mensaje `user`

`app/prompts/estimation/v6/` es una copia literal de `v5`. Solo cambia `user.j2`, que añade al final, **después** de `</transcript>`, un bloque opcional:

```
<critic_feedback>
A previous draft of this estimate was reviewed by an independent auditor.
Produce a new estimate that fixes these issues and keeps everything else consistent:
- [major] scope_mismatch at rows[2].hours: <description> (suggested fix: <fix>)
</critic_feedback>
```

- Va al final porque es la instrucción más reciente; el criterio es el mismo de poner los documentos largos primero y la petición al final.
- El texto fijo del bloque está en inglés, como el resto de las instrucciones.
- Solo se listan los problemas `critical` y `major` del último feedback. Los `minor` no justifican un reintento, como dice el validador.

`render_session_prompt(..., critic_feedback=None)` pinta el bloque solo en `v6`. `history_user` **nunca** lo incluye: el historial guarda el turno como lo escribió el cliente.

- **Sin feedback, `v6` renderiza byte a byte lo mismo que `v5`.** Un test lo comprueba y otro test dorado congela `v5`.
- `v6` pasa a ser la versión por defecto de las sesiones: `SESSION_PROMPT_VERSIONS = ("v4", "v5", "v6")`.
- `MEMORY_PROMPT_VERSIONS = {"v5", "v6"}`, porque el tier y el resumen funcionan igual.
- `REVIEW_PROMPT_VERSIONS = {"v6"}`.

*Alternativas:*

- Editar `v5`. Lo prohíbe la regla de versiones publicadas.
- Mandar el feedback como un par extra `assistant` (el borrador) + `user` (el feedback). Duplica tokens con el borrador entero y mezcla borradores en una conversación que luego no se guarda.
- Ponerlo en el system. El system se regenera por turno con datos de la sesión; el feedback es de esta petición.

### D5. Boss: lógica pura con presupuesto y motivos de parada

`app/services/boss.py` define `run_review(actor, critic, *, max_iterations) -> ReviewRun`, sin LLM ni E/S:

1. **Iteración 1**: `draft = actor(None)`. Un fallo del Actor aquí se propaga: es el mismo 502 de hoy.
2. **Borrador fuera de alcance** (`out_of_scope`): **acepta sin llamar al Critic**, con `stop_reason = "out_of_scope"`. No hay estimación que auditar.
3. **`review = critic(draft)`**, y después:
   - Veredicto `accept`, o Critic caído: acepta (`accepted` o `critic_failed`).
   - Veredicto `reject`: sintetiza (`rejected`).
   - Veredicto `needs_iteration` con iteraciones restantes: `draft = actor(review)` y vuelve al paso 2.
   - Veredicto `needs_iteration` sin iteraciones restantes: sintetiza (`budget_exhausted`).
4. **Fallo del Actor en un reintento** (`LLMProviderError`): sintetiza con el último borrador válido y el último feedback (`actor_failed`). El turno no falla por intentar mejorar.

`ReviewRun` contiene:

- `final` (el `EstimationResult`);
- `final_decision`, que es `accept` o `synthesize`;
- `stop_reason` ∈ `accepted | out_of_scope | critic_failed | rejected | budget_exhausted | actor_failed`;
- `iterations`, con una entrada por iteración: `iteration`, que empieza en 1, `verdict` (o `None` si no hubo Critic), `critic_confidence`, `critic_failed`, `decision` (`accept | iterate | synthesize`), los problemas, la confianza del borrador y el total de horas del borrador.

El número de llamadas al LLM del turno se deriva de ahí: borradores más llamadas al Critic.

*Alternativas:* que el Boss sea un LLM (orchestrator), que encarece el turno y lo hace no determinista sin ventaja con criterios tan claros; o un Critic que también decide, que el material descarta por bucles infinitos y sesgo de confirmación.

### D6. Síntesis: el borrador con avisos en `assumptions_and_risks`

Sintetizar devuelve el último borrador con dos cambios, hechos con `model_copy` y sin LLM:

- **Avisos.** Por cada problema `critical` o `major` del último feedback se añade un aviso al **principio** de `assumptions_and_risks`, con el formato `"<etiqueta>: <description>"`. La etiqueta va en el idioma efectivo del turno: "Revisión independiente" o "Independent review". Si no hay problemas de ese nivel, se usan los `minor`.
  - Cada aviso se recorta a 300 caracteres (`ShortItem`).
  - El total de la lista no pasa de `MAX_ASSUMPTIONS = 8`: entran primero los avisos y después los supuestos del Actor que quepan.
- **Confianza.** `confidence_pct = max(30, múltiplo de 5 inferior a confidence_pct / 2)`. El suelo de 30 coincide con `OUT_OF_SCOPE_CONFIDENCE`, así que un borrador sintetizado nunca pasa a fuera de alcance.

Los avisos aparecen en el render de `line_items` y `phases_table` (sección de supuestos y riesgos) y en `narrative`.

*Alternativas:* anteponerlos al `summary`, como el directo, que con nuestro límite de 600 caracteres se comería el resumen; o devolver un sobre vacío "fuera de alcance", que esconde la mejor respuesta disponible y es lo que el directo descarta con un comentario.

### D7. Entrada del Critic: prompt `critic/v1`

`render_critic_prompt(*, transcript, project_metadata, conversation_summary, tier, language, estimate: ComputedEstimate) -> tuple[str, str]` usa el mismo `Environment` de Jinja2.

- **System** (en inglés): el papel de auditor independiente, las siete categorías con sus umbrales, las reglas del veredicto ("ante la duda, `accept` y `minor`"), el formato de `field_path` y el idioma de `description` y `suggested_fix`.
- **User**: los bloques `<transcript>` (la transcripción del turno, literal), `<project_metadata>` (incluidos `explicit_constraints` y `rejected_options`), `<conversation_summary>` (si hay), `<resolved_tier>` y `<estimation_under_review>`. Este último lleva un resumen compacto del `ComputedEstimate`: título, resumen, confianza, filas con índice, fase, perfil y horas, totales de horas y coste, fases, equipo, duración, supuestos y tecnologías.

Al Critic **no** se le mandan los adjuntos ni el historial literal. Su prompt indica que puede haber hechos de turnos anteriores o de adjuntos que no ve, y que solo marque `hallucination` cuando un dato contradiga lo que sí ve. Así su coste queda acotado: el turno 10 cuesta lo mismo que el 1.

*Alternativas:* pasarle el mismo contexto que al Actor, que duplica el coste de entrada en cada iteración y repite el sesgo; o solo la transcripción, como el directo, que en un turno "añade Stripe" no tiene contexto y dispara falsos `hallucination`.

**Ajustes al implementar (D3, D4, D5, D7).** Se aplican las opciones recomendadas:

- **`CriticFeedback`:** declara `issues` antes de `verdict`, para que el modelo liste lo que encuentra antes de decidir. Es el mismo criterio que `confidence_pct` antes de `rows` en `schema.py`. La spec no fija el orden.
- **Prompt del Critic:**
  - Dice que los importes los calcula el código ("never report arithmetic errors").
  - Toma una capacidad de unas 30–35 h por persona a jornada completa y semana para `unrealistic_estimate`.
  - Usa `critical` en `constraint_conflict` solo si la solución depende del conflicto.
  - Nunca marca `tier_mismatch` con el tier `default`.
  - Marca `missing_assumption` como `major` solo si la omisión mueve las horas más de un 20 %.
- **`<estimation_under_review>`:** incluye el texto de cada tarea, que el Critic necesita para juzgar el alcance. No incluye la narrativa ni las restricciones del propio borrador.
- **`phases[i]` del Critic:** el índice sigue el orden canónico de las fases calculadas. Es informativo.
- **Bloque `<critic_feedback>`:** va justo después de `</transcript>`, sin línea en blanco, siguiendo el estilo de `user.j2`.
- **Errores del proveedor:** `LLMProviderError`, `StructuredOutputError` y `StructuredFailure` se mueven a `app/services/errors.py`, y `llm_service` los reexporta. Así, `boss.py` y `critic.py` no importan `llm_service` (ni LiteLLM o Instructor) y se evita un ciclo de imports.

### D8. Respuesta, `GET` y métricas del turno

`SessionEstimateResponse` añade `acb: ReviewTrace | null`, con `final_decision`, `stop_reason`, `iterations_run`, `max_iterations` e `iterations`. Cada iteración incluye `iteration`, `verdict`, `critic_confidence`, `critic_failed`, `decision`, `draft_confidence_pct`, `draft_total_hours` e `issues`, y cada problema incluye `category`, `severity`, `field_path` y `description`. El texto de los problemas es para quien llama; en los logs nunca aparece (D10).

`GET /api/v1/sessions/{id}` añade `last_review`, con `final_decision`, `stop_reason` e `iterations_run`, o `null` si el último turno con éxito no pidió revisión.

Con revisión, las métricas del turno (`input_tokens`, `output_tokens` y `cost_usd`) **suman todas las llamadas** (borradores y Critic), porque son lo que cuesta el turno.

- `cost_usd` es `null` si alguna llamada no tiene precio conocido.
- `cache_hit` es `true` solo si todas las llamadas fueron aciertos de caché.
- `model` y `provider` son los del borrador final.
- `latency_ms` cubre el turno entero.

Esto es lo que necesitan los evals y S06 para comparar coste y latencia entre los modos `actor` y `acb`.

### D9. Configuración

| Variable | Tipo | Default |
|---|---|---|
| `CRITIC_MODEL` | `str \| None`; vacío = `None` | `None`: el modelo primario con fallback |
| `BOSS_MAX_ITERATIONS` | `int`, `1..5` | `3` (decisión del usuario, como el directo) |

`CRITIC_MODEL` se valida al arrancar con `resolve_request_model`, como `COMPRESSION_MODEL`.

*Alternativas:* fijar `gpt-4o-mini` como en el directo, que ya es nuestro primario y rompería si alguien configura otro proveedor como primario; o permitir `0` iteraciones, que no tiene sentido porque para eso está `review=false`.

### D10. Observabilidad

Los eventos llevan el `session` corto por contextvars, como el resto del turno, y nunca texto del cliente, del Critic ni de la estimación:

- `critic_completed`: `iteration`, `verdict`, `issues`, `critical`, `major`, `minor`, `categories` (lista ordenada sin repetidos), `confidence` y `latency_ms`.
- `critic_failed`: `iteration` y `error_type`.
- `boss_iteration`: `iteration`, `decision`, `verdict` (o `None`), `issues` y `draft_out_of_scope`.
- `acb_completed`: `final_decision`, `stop_reason`, `iterations`, `llm_calls`, `latency_ms` e `input_tokens`/`output_tokens` acumulados.

Las llamadas del Critic emiten `llm_call_completed` con `llm_purpose="critic"`. Los reintentos del Actor emiten `llm_purpose="estimation"` y `review_iteration`. `session_turn_completed` añade `review` (bool) y `review_decision`.

### D11. Guardrails

El bloqueo de etiquetas propias del prompt añade `<critic_feedback>`, `<estimation_under_review>`, `<resolved_tier>` y `<response_language>`, con sus cierres. Así, un cliente no puede fingir un feedback del auditor. `<transcript>`, `<project_metadata>` y `<conversation_summary>` ya están bloqueadas. Las palabras "critic", "feedback" o "auditor" sin forma de etiqueta no se bloquean.

### D12. Evals: paquete `evals/` con la API del directo y nuestro dominio

El paquete está en la raíz del repo, fuera de `app/`. Lo importa la CLI y lo prueban los tests; `app/` nunca lo importa.

- **`evals/dataset.py`: `GoldenCase`.**
  - Entrada: `id` (`case-NNN-slug`), `language` (`es | en`), `transcript` (20–20 000 caracteres), `project_type`, `detail_level = medium`, `output_format = line_items`, `tier = "auto"` o un `Tier`.
  - Expectativas, todas opcionales salvo los booleanos:
    - `expected_blocked`: se espera un 400 de los guardrails;
    - `expected_out_of_scope`;
    - `expected_in_summary`: términos que deben aparecer en el título, el resumen o las tareas;
    - `expected_technologies_any_of`: contra `result.technologies`;
    - `expected_hours_range`, `expected_cost_range_eur` y `expected_duration_weeks_range`: este último se cumple si se solapa con `[min_weeks, max_weeks]`;
    - `expected_phase_count_range`;
    - `expected_tier`.
  - Validadores: rangos con `lo <= hi`, y ni un caso bloqueado ni uno fuera de alcance llevan rangos. `load_dataset(path=None)`.
- **`evals/golden_dataset.json`: 16 casos, 8 en español y 8 en inglés.**
  - Proyecto simple, medio y grande con integraciones externas en los cuatro `project_type`.
  - NDA → `executive`, normativa (RGPD/HIPAA) → `executive`, vocabulario técnico → `developer`, equipo de 2 → `pm`.
  - Uno con presupuesto y plazo cerrados, uno con contradicciones internas, uno vago → fuera de alcance, y uno con inyección → bloqueado.
  - Los rangos son **generosos**, del orden de ±50 % sobre el juicio experto, como recomienda el material: se trata de detectar fallos catastróficos, no de fijar cifras.
- **`evals/metrics.py`**: `@dataclass(frozen=True) MetricResult(name, score, passed, details)`, igual que el directo. Cada métrica tiene `name` y `evaluate(case, outcome) -> MetricResult`. `CaseOutcome` contiene `blocked_reason` (o `None`) y `response` (`SessionEstimateResponse` o `None`). Las métricas son:
  - **`SchemaAdherenceMetric`**:
    - un bloqueo pasa solo si se esperaba;
    - un 200 cuando se esperaba bloqueo falla;
    - en un 200, la respuesta valida, la suma de horas de las filas coincide con `total_hours`, `validation.score == 1.0` (si hay evaluación) y el número de fases está en rango.
  - **`CostBoundsMetric`**: con fuera de alcance esperado, `out_of_scope = true` y totales 0; sin él, `out_of_scope = false` y las horas, el coste y la duración en rango.
  - **`ContentRecallMetric`**: términos y tecnologías sin distinguir mayúsculas ni tildes, y `result.language == case.language`. Se salta en los casos bloqueados o fuera de alcance.
  - **`TierMatchMetric`**: `response.tier == expected_tier`. Se salta si no hay expectativa o el caso está bloqueado.

  "Se salta" significa `passed = true`, `score = 1.0` y `details = "skipped: …"`, como el directo.

  `run_all_metrics(case, outcome) -> list[MetricResult]` aplica las cuatro en orden.
- **`evals/run.py`**: `python -m evals.run`, con estas opciones:
  - `--mode actor|acb`, por defecto `actor`;
  - `--limit N`;
  - `--case ID`, repetible;
  - `--http URL`, sin él se ejecuta en proceso;
  - `--output PATH`, informe JSON;
  - `--max-cost-usd X`: para cuando el gasto acumulado lo supera, como `STOP_USD` en nuestras mediciones.

  **Funcionamiento:**
  - En proceso usa `TestClient(app)` con `get_session_store` sobrescrito por un `SessionStore` aislado. Proveedor, moderación y memoria son los reales.
  - Cada caso crea su propia sesión y hace un turno, con `review=true` en el modo `acb`.
  - Un 400 de guardrails es un bloqueo. Cualquier otro error HTTP o excepción es una fila de error, que cuenta como caso fallido, y el runner sigue.

  **Salida:**
  - Una línea por caso: id, idioma, latencia, coste, PASS/FAIL por métrica y la decisión del ACB con sus iteraciones.
  - Un resumen: casos que pasan todo, aciertos por métrica, coste total, latencia media y P50/máx, `cache_hit` y la distribución de decisiones y la media de iteraciones del ACB.

  **Códigos de salida:** 0 si todos los casos pasan, 1 si no, y 2 por un error de uso o de dataset.

  El núcleo `run_cases(cases, mode, client, *, max_cost_usd=None) -> EvalReport` se separa de la CLI para probarlo con un proveedor falso.

**Ajustes al implementar.**

- **`--dataset PATH`:** la CLI admite esta opción, que la spec no lista. El escenario "Dataset inválido" la necesita para probar un fichero con un id repetido.
- **Tope de coste:** si la ejecución se detiene por `--max-cost-usd`, termina con código 1.
- **Tecnologías esperadas:** también casan si están contenidas en un nombre más largo ("Stripe" con "Stripe Connect").
- **Caso `pm` (003):** se envía con `tier="pm"` explícito. En un único turno de una sesión nueva la regla `small_team` no puede dispararse, porque `assumed_team_size` se rellena a partir del primer turno. Por eso este caso comprueba que se respeta el tier explícito. Los casos de varios turnos quedan para S06.

*Alternativas:* DeepEval + pytest parametrizado. El usuario lo descartó: añade una dependencia pesada, y el material deja el LLM-as-judge para la cúspide de la pirámide. Otra opción eran los evals como tests con un marcador `slow`: llamarían a APIs reales desde pytest, que nuestra regla prohíbe.

**Cómo habilita S06 sin adelantarlo:**

- `MetricResult`, `run_all_metrics` y el runner con `--http`/`TestClient` son los puntos de extensión que nombra el enunciado.
- Las métricas del turno sumadas en D8 son las que `turn_observed` necesitará.
- No se añaden `turn_observed`, escenarios multi-turno, PDFs sintéticos ni métricas de presupuesto.

### D13. Interfaz

En "Opciones avanzadas", la casilla "Revisión Actor-Critic-Boss" está desmarcada por defecto. Al marcarla, se envía `review=true` y aparece un aviso de que el turno puede tardar hasta tres veces más y cuesta más.

Si la respuesta trae `acb`, se muestra un desplegable "Revisión Actor-Critic-Boss":
- la decisión final y el motivo, en español;
- las iteraciones;
- una tabla por iteración con el veredicto, la gravedad, la categoría, el campo y la descripción de cada problema.

La barra lateral no cambia.

### D14. Medición con llamadas reales (requiere permiso del usuario)

**Ejecución.**
- Con `REDIS_URL` vacío, para que no haya aciertos de caché.
- `uv run python -m evals.run --mode actor --output …` y `--mode acb`, los 16 casos en cada modo, con `--max-cost-usd 0.25`.

**Datos que se apuntan aquí:**
- casos que pasan por métrica y por modo;
- coste y latencia, media y P50/máx, por modo;
- la distribución de `stop_reason` y la media de iteraciones;
- los problemas más frecuentes del Critic, por categoría.

**Umbrales:**

| Comprobación | Umbral |
|---|---|
| Errores de ejecución | 0 en los dos modos |
| Caso de inyección | bloqueado |
| Caso vago | fuera de alcance |
| Pasan todas las métricas en `actor` | ≥ 12/16 |
| Pasan todas las métricas en `acb` | no menos que en `actor` - 1 |
| Coste medio por caso en `acb` | ≤ 4 × `actor` |
| Casos de `acb` aceptados en la iteración 1 | ≥ 50 % (Critic no sobreexigente) |
| Turnos que fallan por el ACB | 0 |

**Primera medición (2026-11-17, gpt-4o-mini, sin caché, 0,090 USD).**

| | `actor` | `acb` |
|---|---|---|
| Pasan todas las métricas | 11/16 | 13/16 |
| `schema_adherence` / `cost_bounds` / `content_recall` / `tier_match` | 16 / 15 / 12 / 16 | 16 / 15 / 13 / 16 |
| Errores de ejecución | 0 | 0 |
| Coste total | 0,0173 USD | 0,0728 USD (4,2 ×) |
| Latencia media / P50 / máx | 8,1 / 8,8 / 12,2 s | 36,4 / 40,8 / 51,3 s |
| `stop_reason` | — | 12 `budget_exhausted`, 1 `accepted` (en la iteración 3), 1 `out_of_scope`, 1 `actor_failed` |

El caso de inyección se bloqueó y el vago quedó fuera de alcance en los dos modos.

**Fallos en `actor`:**
- Cuatro casos no tienen en `technologies` tecnologías que la transcripción nombra literalmente: iOS/Android, Redsys, Twilio y Slack/Google Workspace. Es un fallo real de extracción del modelo, no del dataset. Su mejora queda para un change aparte.
- El caso 008 estima 345 h frente a un mínimo esperado de 450.
- Por decisión del usuario, no se tocan ni los rangos ni las métricas.

**Fallos de umbral:**
- `actor` pasa 11/16, por debajo del mínimo de 12.
- El Critic es sobreexigente: solo acepta a la primera 1 de 14 casos (0 en realidad: el único `accepted` llegó en la iteración 3), frente a un objetivo del 50 %. De 41 revisiones, 40 son `needs_iteration`, casi siempre con problemas `major` de `scope_mismatch` y `missing_assumption`.
- `acb` cuesta 4,2 veces lo que `actor`, por encima del máximo de 4.

**Muestra de textos del Critic** (dos casos, 0,01 USD):
- Marca como ausente una funcionalidad que está en las filas.
- Cambia de criterio entre iteraciones.
- Da como `critical` un `constraint_conflict` estando por debajo del presupuesto, y se equivoca al comparar 18 375 con 20 000.
- Marca `phase_imbalance` con un 70 % de las horas, cuando el umbral es el 75 %.

En el tercer borrador, además, el Actor degrada su esquema (reintentos de validación) y uno se cortó por longitud (`actor_failed`). Tres respuestas del Critic se reintentaron por descripciones de más de 300 caracteres.

**Decisión del usuario:** calibrar `critic/v1`, que aún no está publicado, y repetir la medición de `acb`. Los cambios son estos:
- La vista del borrador lleva precalculados el porcentaje de horas por fase, los totales, el equipo y las semanas implícitas.
- Hay reglas concretas por categoría: un problema numérico solo se marca si el número calculado cruza el umbral, y `scope_mismatch` solo si falta en todas las tareas.
- Las descripciones se limitan a 250 caracteres.

**Ajustes de la calibración.** Las semanas implícitas se calculan con 32 h por persona a jornada completa y semana, como el directo; una persona a media jornada cuenta como 0,5. Ambos valores son constantes del loader. La prueba con dos casos (0,005 USD) mostró la mejora: el caso simple se acepta a la primera. En el caso de presupuesto cerrado, el Critic detecta un exceso real (21 375 € frente a 20 000 €), pero todavía produce algún problema contradictorio con gpt-4o-mini.

**Segunda medición (2026-11-17, solo `acb`, Critic calibrado, 0,046 USD).**

| | `actor` (1.ª) | `acb` (1.ª) | `acb` (2.ª) |
|---|---|---|---|
| Pasan todas las métricas | 11/16 | 13/16 | 12/16 |
| `cost_bounds` / `content_recall` | 15 / 12 | 15 / 13 | 13 / 13 |
| Errores | 0 | 0 | 0 |
| Coste total (× `actor`) | 0,0173 | 0,0728 (4,2 ×) | 0,0460 (2,7 ×) |
| Latencia P50 / máx | 8,8 / 12,2 s | 40,8 / 51,3 s | 18,0 / 95,8 s |
| Aceptados a la primera | — | 0/14 | 6/14 (43 %) |
| `stop_reason` | — | 12 agotados, 1 aceptado, 1 `actor_failed` | 10 aceptados, 4 agotados |
| Iteraciones medias | — | 2,8 | 1,9 |

Hay 27 revisiones: 10 `accept` y 17 `needs_iteration`.

Los fallos de `cost_bounds` (casos 008, 009 y 016) son estimaciones por debajo del mínimo del rango. Los mismos casos pasan o fallan según la ejecución, lo que confirma la varianza del Actor: es el motivo de los tests "soft" con N ejecuciones que el material deja para más adelante. Los fallos de `content_recall` son los de extracción de `technologies` ya descritos.

**Umbrales finales:**

| Umbral | Resultado |
|---|---|
| Errores de ejecución | ✓ 0 |
| Caso de inyección | ✓ bloqueado |
| Caso vago | ✓ fuera de alcance |
| `actor` ≥ 12/16 | ✗ 11, aceptado por el usuario como fallo real del modelo |
| `acb` ≥ `actor` - 1 | ✓ 12 ≥ 10 |
| Coste ≤ 4 × | ✓ 2,7 × |
| ≥ 50 % aceptados a la primera | ✗ 43 % (6/14), aceptado por el usuario como desviación |
| Turnos fallidos por el ACB | ✓ 0 |

Gasto total de la medición: unos 0,15 USD, por debajo del tope de 0,25.

**Líneas futuras** (fuera de este change):
- un Critic de otro modelo, más capaz o distinto del Actor (`CRITIC_MODEL`), para reducir los problemas contradictorios y el sesgo de confirmación;
- mejorar la extracción de `technologies` en una versión nueva del prompt;
- tests de consistencia con N ejecuciones.

**Si falla un umbral**, se para y se pregunta al usuario. Si falla por un rango mal calibrado del dataset, el rango se corrige anotando el motivo, nunca para "hacer pasar" un caso.

## Risks / Trade-offs

- **[Critic sobreexigente: bucles hasta agotar el presupuesto]** → El prompt dice "ante la duda, `accept`/`minor`"; el validador impide `needs_iteration` solo con `minor`; hay un tope de 3; y el umbral de D14 exige un 50 % de aceptación a la primera.
- **[El Critic marca `hallucination` con hechos de turnos anteriores que no ve]** → Le llegan el `project_metadata` y el resumen, y su prompt explica que hay contexto que no ve. La medición lo vigila por categoría.
- **[Coste y latencia: hasta 6 llamadas por turno]** → Es opcional y está desactivado por defecto. El Critic recibe una entrada compacta (D7), un borrador fuera de alcance se acepta sin Critic, y `BOSS_MAX_ITERATIONS` es configurable.
- **[El reintento empeora otra parte de la estimación]** → El feedback pide mantener el resto coherente, y el Boss se queda con el último borrador. Esto no se puede eliminar con reglas: la medición compara los aciertos de `acb` con los de `actor`.
- **[Los rangos del golden dataset envejecen o están mal calibrados]** → Son generosos, cada corrección se justifica en D14, y el material recomienda revisarlo cada trimestre.
- **[Un `field_path` inventado por el Critic]** → Es informativo: no se usa para modificar nada automáticamente, solo para orientar al Actor y mostrarse en la traza.
- **[Caché exacta en los reintentos]** → El mensaje con feedback es distinto, así que no hay acierto. El Critic con la misma entrada puede acertar en caché, y es correcto porque es determinista para la misma entrada.

## Migration Plan

Los cambios en la API son aditivos (`review`, `acb` y `last_review` opcionales). El cambio de versión por defecto de `v5` a `v6` no altera el render sin revisión, y lo garantiza un test byte a byte.

Para volver atrás basta con dejar de enviar `review` o fijar `prompt_version=v5`. No hay datos persistentes que migrar.

## Open Questions

Ninguna pendiente de decisión. Las cuatro decisiones de alcance las tomó el usuario: rama `session-06`, flag `review`, evals solo deterministas y `BOSS_MAX_ITERATIONS=3`. El resto sigue las opciones recomendadas. Si al implementar aparece una ambigüedad, se consulta antes de decidir.
