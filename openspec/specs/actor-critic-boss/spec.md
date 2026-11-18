# actor-critic-boss Specification

## Purpose
Revisar opcionalmente cada turno de sesión con el patrón Actor-Critic-Boss: un Critic estructurado audita el borrador con criterios explícitos y un Boss determinista decide aceptar, pedir otro borrador o sintetizar con avisos, con un presupuesto de iteraciones y la traza en la respuesta. Puesta al día con el directo de S05.
## Requirements
### Requirement: Feedback estructurado del Critic
El sistema SHALL definir en `app/services/critic.py` los modelos Pydantic `CriticIssue` y `CriticFeedback`. `CriticIssue` MUST tener:
- `category`, uno de `hallucination`, `scope_mismatch`, `constraint_conflict`, `phase_imbalance`, `missing_assumption`, `unrealistic_estimate` o `tier_mismatch`;
- `severity`, uno de `critical`, `major` o `minor`;
- `field_path`, de 1 a 120 caracteres;
- `description`, de 5 a 300 caracteres;
- `suggested_fix`, opcional, de hasta 200 caracteres.

`CriticFeedback` MUST tener `verdict` (`accept`, `needs_iteration` o `reject`), `issues` (lista de 0 a 12 `CriticIssue`) y `confidence_in_review` (entero de 0 a 100). Un validador MUST rechazar `needs_iteration` sin ningún problema `critical` o `major`, y `reject` sin ningún problema. El rechazo de un validador MUST provocar los reintentos de validación de Instructor, como en la estimación.

#### Scenario: Reintento solo con problemas menores
- **WHEN** se valida un `CriticFeedback` con `verdict = "needs_iteration"` y un único problema `minor`
- **THEN** la validación falla

#### Scenario: Rechazo sin problemas
- **WHEN** se valida un `CriticFeedback` con `verdict = "reject"` e `issues = []`
- **THEN** la validación falla

#### Scenario: Aceptación limpia
- **WHEN** se valida un `CriticFeedback` con `verdict = "accept"`, `issues = []` y `confidence_in_review = 80`
- **THEN** la validación pasa

### Requirement: Llamada al Critic
El sistema SHALL revisar cada borrador con una llamada estructurada que devuelve `CriticFeedback`.

**Prompt.** La llamada usa el prompt `critic/v1` (capability `prompt-templates`), renderizado con:
- la transcripción del turno;
- el `project_metadata` de la sesión, incluidos `explicit_constraints` y `rejected_options`;
- el resumen de la conversación, si lo hay;
- el tier resuelto;
- el idioma efectivo del turno;
- el borrador ya calculado (`ComputedEstimate`).

El Critic MUST NOT recibir el texto de los adjuntos ni los mensajes del historial.

**Modelo.** La llamada MUST usar `CRITIC_MODEL` si está configurado y, si no, el modelo primario con fallback. Sus eventos de proveedor MUST llevar `llm_purpose = "critic"`.

**Fallos (fail-open).** Cualquier `LLMProviderError`, incluido `StructuredOutputError`, MUST registrarse como `critic_failed` y tratarse como un `accept` con `confidence_in_review = 0` y `critic_failed = true` en la iteración. Un fallo del Critic MUST NOT hacer fallar el turno ni cambiar su código de respuesta.

#### Scenario: Critic caído
- **GIVEN** un proveedor que responde al borrador y falla en la llamada del Critic
- **WHEN** se envía un turno con `review=true`
- **THEN** la respuesta es 200 con el borrador, `acb.final_decision = "accept"`, `acb.stop_reason = "critic_failed"` y `acb.iterations[0].critic_failed = true`

#### Scenario: Modelo del Critic
- **GIVEN** `CRITIC_MODEL=claude-haiku-4-5` con su API key configurada
- **WHEN** se revisa un borrador
- **THEN** la llamada del Critic se hace con `claude-haiku-4-5` y la del borrador con el modelo primario

#### Scenario: Sin adjuntos en el Critic
- **GIVEN** un turno con un PDF cuyo texto contiene `MARCADOR-PDF`
- **WHEN** se revisa el borrador
- **THEN** ningún mensaje de la llamada del Critic contiene `MARCADOR-PDF`

### Requirement: Decisión del Boss
El sistema SHALL definir en `app/services/boss.py` una función pura `run_review(actor, critic, *, max_iterations)`, sin LLM ni E/S, donde:
- `actor(feedback: CriticFeedback | None)` devuelve un borrador;
- `critic(draft)` devuelve un `CriticFeedback`.

**Bucle.** La función MUST aplicar, en cada iteración empezando por la 1:
1. Pedir un borrador. En la primera iteración, `actor(None)`.
2. Si el borrador está fuera de alcance (confianza < 30), aceptarlo sin llamar al Critic, con `stop_reason = "out_of_scope"`.
3. Si no, llamar al Critic y actuar según el resultado:
   - Con `accept`, aceptar con `stop_reason = "accepted"`; si el Critic falló, `stop_reason = "critic_failed"`.
   - Con `reject`, sintetizar con `stop_reason = "rejected"`.
   - Con `needs_iteration` y alguna iteración restante, pedir otro borrador con `actor(feedback)`.
   - Con `needs_iteration` en la última iteración, sintetizar con `stop_reason = "budget_exhausted"`.

**Fallos del Actor.** Un fallo del Actor en la iteración 1 MUST propagarse. Un `LLMProviderError` del Actor en una iteración posterior MUST terminar el bucle sintetizando el último borrador válido con su último feedback, con `stop_reason = "actor_failed"`.

**Resultado.** La función MUST devolver:
- el borrador final;
- `final_decision`: `accept` o `synthesize`;
- `stop_reason`;
- una entrada por iteración con `iteration`, `verdict` (`null` sin Critic), `critic_confidence`, `critic_failed`, `decision` (`accept`, `iterate` o `synthesize`), `draft_confidence_pct`, `draft_total_hours` y los problemas.

`max_iterations` MUST ser al menos 1.

#### Scenario: Aceptación a la primera
- **GIVEN** un Critic que devuelve `accept`
- **WHEN** se ejecuta `run_review` con `max_iterations = 3`
- **THEN** el Actor se llama una vez, `final_decision = "accept"`, `stop_reason = "accepted"` y hay una iteración con `decision = "accept"`

#### Scenario: Reintento y aceptación
- **GIVEN** un Critic que devuelve `needs_iteration` y después `accept`
- **WHEN** se ejecuta `run_review` con `max_iterations = 3`
- **THEN** el Actor se llama dos veces, la segunda con el feedback de la primera revisión, y `final_decision = "accept"` con dos iteraciones (`iterate`, `accept`)

#### Scenario: Presupuesto agotado
- **GIVEN** un Critic que siempre devuelve `needs_iteration`
- **WHEN** se ejecuta `run_review` con `max_iterations = 3`
- **THEN** el Actor se llama tres veces, el Critic tres veces, `final_decision = "synthesize"` y `stop_reason = "budget_exhausted"`

#### Scenario: Rechazo
- **GIVEN** un Critic que devuelve `reject` en la primera revisión
- **WHEN** se ejecuta `run_review`
- **THEN** el Actor se llama una vez, `final_decision = "synthesize"` y `stop_reason = "rejected"`

#### Scenario: Borrador fuera de alcance
- **GIVEN** un Actor cuyo borrador tiene `confidence_pct = 10`
- **WHEN** se ejecuta `run_review`
- **THEN** el Critic no se llama, `final_decision = "accept"`, `stop_reason = "out_of_scope"` y la iteración tiene `verdict = null`

#### Scenario: Fallo del Actor en el reintento
- **GIVEN** un Critic que pide un reintento y un Actor que lanza `LLMProviderError` en el segundo borrador
- **WHEN** se ejecuta `run_review`
- **THEN** el resultado es la síntesis del primer borrador con `stop_reason = "actor_failed"`

#### Scenario: Una sola iteración
- **GIVEN** `max_iterations = 1` y un Critic que devuelve `needs_iteration`
- **WHEN** se ejecuta `run_review`
- **THEN** el Actor se llama una vez y `stop_reason = "budget_exhausted"`

### Requirement: Síntesis del resultado
Sintetizar SHALL devolver el último borrador con dos cambios, sin llamar al LLM:

**Avisos.** Los avisos se añaden al principio de `assumptions_and_risks`, uno por cada problema `critical` o `major` del último feedback. Si no hay ninguno de esos, uno por cada problema `minor`.
- Cada aviso MUST tener la forma `"<etiqueta>: <description>"`, recortada a 300 caracteres.
- La etiqueta MUST ser "Revisión independiente" si el idioma efectivo del turno es `es` e "Independent review" si es `en`.
- La lista resultante MUST tener como máximo 8 elementos: primero los avisos y después los supuestos del borrador que quepan.

**Confianza.** La confianza global pasa a `max(30, múltiplo de 5 inferior o igual a confidence_pct / 2)`. Un resultado sintetizado MUST NOT quedar fuera de alcance.

Sin problemas en el feedback, sintetizar MUST devolver el borrador solo con la confianza reducida.

#### Scenario: Avisos en español
- **GIVEN** un último borrador en español con `confidence_pct = 80`, dos supuestos y un feedback con un problema `major` "No incluye la integración con el TPV"
- **WHEN** se sintetiza
- **THEN** `assumptions_and_risks[0] = "Revisión independiente: No incluye la integración con el TPV"`, los dos supuestos originales siguen detrás y `confidence_pct = 40`

#### Scenario: Suelo de confianza
- **GIVEN** un último borrador con `confidence_pct = 45`
- **WHEN** se sintetiza
- **THEN** `confidence_pct = 30` y `out_of_scope = false`

#### Scenario: Tope de supuestos
- **GIVEN** un borrador con 7 supuestos y un feedback con 3 problemas `major`
- **WHEN** se sintetiza
- **THEN** `assumptions_and_risks` tiene 8 elementos: los 3 avisos y los 5 primeros supuestos

### Requirement: Traza de la revisión en la respuesta
Con `review=true`, la respuesta del turno SHALL incluir `acb` con:
- `final_decision`: `accept` o `synthesize`;
- `stop_reason`;
- `iterations_run`;
- `max_iterations`;
- `iterations`, una entrada por iteración.

Cada iteración MUST incluir:
- `iteration`, empezando en 1;
- `verdict`: `accept`, `needs_iteration`, `reject` o `null`;
- `critic_confidence`: entero o `null`;
- `critic_failed`;
- `decision`: `accept`, `iterate` o `synthesize`;
- `draft_confidence_pct`;
- `draft_total_hours`;
- `issues`, cada uno con `category`, `severity`, `field_path` y `description`.

Los esquemas de la traza MUST definirse en `app/schemas.py` y documentarse en OpenAPI.

#### Scenario: Traza con reintento
- **GIVEN** un Critic que devuelve `needs_iteration` con un problema `major` `scope_mismatch` en `rows[2].hours` y después `accept`
- **WHEN** se envía un turno con `review=true`
- **THEN** `acb.iterations_run = 2`, `acb.iterations[0].decision = "iterate"`, `acb.iterations[0].issues[0].field_path = "rows[2].hours"` y `acb.iterations[1].decision = "accept"`

