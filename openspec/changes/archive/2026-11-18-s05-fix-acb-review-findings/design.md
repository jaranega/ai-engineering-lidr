## Context

Hallazgos de `/code-review` sobre la PR #6, verificados contra el código. Este change corrige los que el usuario aprobó (1–8); quedan fuera la compresión en el camino de la petición (decisión D4 previa) y el helper de versión.

## Goals / Non-Goals

**Goals:** corregir los defectos sin cambiar contratos más allá de un valor nuevo en `stop_reason`. **Non-Goals:** rediseñar la compresión o el bucle del Boss.

## Decisions

### D1. Reintento fuera de alcance → síntesis del último borrador válido
Un borrador con confianza < 30 en la iteración ≥ 2 no se acepta: se sintetiza el último borrador dentro de alcance con el último feedback, con `stop_reason = "retry_out_of_scope"`. *Alternativas:* reutilizar `actor_failed` (mezcla dos causas y confunde la traza) o pedir otro borrador (gasta presupuesto en una dirección que el Actor ya rechazó).

### D2. Tokens de los intentos fallidos
Las excepciones estructuradas del proveedor ya conocen los intentos; se amplían `StructuredOutputError`/`LLMProviderError` con el consumo acumulado (`input_tokens`, `output_tokens`, `model`, o `None` si no se conoce) para que el bucle de revisión lo sume. Si un intento fallido no tiene consumo conocido, el coste del turno es `null`, como ya ocurre con un precio desconocido. *Alternativa:* estimar los tokens (inexacto) o ignorarlos (el defecto actual).

### D3. Tope de coste con coste desconocido
Con `--max-cost-usd`, una fila no bloqueada con `cost_usd = null` detiene el runner (`stopped_by_unknown_cost`). Más vale parar que gastar sin control; sin tope, solo se cuenta aparte.

### D4. Streamlit según la versión seleccionada
Los controles leen la versión del selector en el momento; si el usuario tiene un valor que la versión no admite, se muestra un aviso y no se envía. *Alternativa:* deshabilitar (no posible de forma reactiva dentro de un `st.form` sin reestructurarlo).

### D5. Patrón de etiquetas compartido
`app/guardrails/input.py` expone la expresión de etiquetas propias (`PROMPT_TAG_PATTERN`) y `memory.normalize_summary` la usa para eliminarlas. Una sola lista.

### D6. Tipos de la traza
`schemas.py` importa los `Literal` desde `app.services.llm_service` (que reexporta los de `boss.py` y `critic.py`), como ya hace con `Tier`.

## Risks / Trade-offs

- [El patrón del guardrail es más amplio que la lista anterior] → solo afecta a etiquetas con forma `<tag>`; texto normal no cambia (test).
- [Excepciones con más campos] → los campos son opcionales; ningún mensaje de error ni log incluye contenido.

## Migration Plan

Aditivo. Sin datos que migrar.

## Open Questions

Ninguna.
