## MODIFIED Requirements

### Requirement: Decisión del Boss
El sistema SHALL definir en `app/services/boss.py` una función pura `run_review(actor, critic, *, max_iterations)`, sin LLM ni E/S, donde:
- `actor(feedback: CriticFeedback | None)` devuelve un borrador;
- `critic(draft)` devuelve un `CriticFeedback`.

**Bucle.** La función MUST aplicar, en cada iteración empezando por la 1:
1. Pedir un borrador. En la primera iteración, `actor(None)`.
2. Si el borrador está fuera de alcance (confianza < 30): en la iteración 1, aceptarlo sin llamar al Critic, con `stop_reason = "out_of_scope"`; en una iteración posterior, terminar sintetizando el último borrador dentro de alcance con su último feedback, con `stop_reason = "retry_out_of_scope"` (un reintento nunca convierte en fuera de alcance un turno que tenía una estimación válida).
3. Si no, llamar al Critic y actuar según el resultado:
   - Con `accept`, aceptar con `stop_reason = "accepted"`; si el Critic falló, `stop_reason = "critic_failed"`.
   - Con `reject`, sintetizar con `stop_reason = "rejected"`.
   - Con `needs_iteration` y alguna iteración restante, pedir otro borrador con `actor(feedback)`.
   - Con `needs_iteration` en la última iteración, sintetizar con `stop_reason = "budget_exhausted"`.

**Fallos del Actor.** Un fallo del Actor en la iteración 1 MUST propagarse. Un `LLMProviderError` del Actor en una iteración posterior MUST terminar el bucle sintetizando el último borrador válido con su último feedback, con `stop_reason = "actor_failed"`.

**Resultado.** La función MUST devolver:
- el borrador final;
- `final_decision`: `accept` o `synthesize`;
- `stop_reason` (`accepted`, `out_of_scope`, `critic_failed`, `rejected`, `budget_exhausted`, `actor_failed` o `retry_out_of_scope`);
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

#### Scenario: Reintento fuera de alcance
- **GIVEN** un primer borrador válido de 800 horas, un Critic que devuelve `needs_iteration` y un segundo borrador con `confidence_pct = 10`
- **WHEN** se ejecuta `run_review` con `max_iterations = 3`
- **THEN** el resultado es la síntesis del primer borrador (800 horas, no fuera de alcance), `final_decision = "synthesize"` y `stop_reason = "retry_out_of_scope"`, y el Critic no revisa el segundo borrador

## ADDED Requirements

### Requirement: Tipos de la traza en un único sitio
Los valores admitidos de la traza (categoría, gravedad, veredicto, decisión por iteración, decisión final y motivo de parada) SHALL definirse una sola vez, en `app/services/critic.py` y `app/services/boss.py`, y `app/schemas.py` MUST reutilizarlos (vía `app.services.llm_service`) en lugar de redefinirlos. Un valor nuevo en el servicio MUST aparecer en el esquema de OpenAPI sin editar `app/schemas.py`.

#### Scenario: Motivo de parada nuevo en OpenAPI
- **WHEN** se consulta `/openapi.json`
- **THEN** el enum de `stop_reason` de `ReviewTrace` contiene `retry_out_of_scope` y coincide con los valores de `app/services/boss.py`
