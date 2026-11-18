## Why

La revisión de código de la PR #6 (rama `session-06`, puesta al día con el directo de S05) encontró defectos en lo entregado por `s05-add-actor-critic-boss-and-evals` y `s05-add-memory-compression-and-dynamic-tier`. El más grave: si un reintento del Actor sale fuera de alcance, el Boss lo acepta y el usuario recibe 0 horas en lugar del borrador válido anterior. Los demás afectan a la medición del gasto (llamadas fallidas no contadas, tope de coste que no salta) y a la interfaz. El usuario pidió aplicar los arreglos antes del merge.

## What Changes

- **Boss**: un borrador fuera de alcance en un reintento termina sintetizando el último borrador válido (`stop_reason = "retry_out_of_scope"`); solo el primero se acepta fuera de alcance.
- **Coste del turno revisado**: los intentos fallidos (Critic que agota reintentos, reintento del Actor cortado) suman sus tokens; consumo desconocido → `cost_usd = null`.
- **Runner de evals**: un coste desconocido detiene la ejecución cuando hay `--max-cost-usd`, en lugar de contar como 0.
- **Streamlit**: audiencia y revisión según la versión seleccionada, con aviso si no se enviarán; ayuda sin "3" fijo.
- **Resumen**: el normalizador reutiliza el patrón de etiquetas del guardrail (incluye `<critic_feedback>`).
- **Tipos de la traza** definidos una sola vez (servicio) y reutilizados por `app/schemas.py`.
- **OpenAPI**: la descripción de `tier` menciona `v5` y `v6`.

## Capabilities

### New Capabilities

### Modified Capabilities
- `actor-critic-boss`: reintento fuera de alcance; tipos de la traza en un único sitio.
- `conversation-sessions`: coste de las llamadas fallidas; descripción de `tier`.
- `offline-evals`: tope de coste con coste desconocido.
- `estimation-form-ui`: controles según la versión seleccionada.
- `memory-compression`: limpieza de etiquetas del resumen.

## Impact

- Código: `app/services/{boss,critic,llm_service,memory}.py`, `app/guardrails/input.py` (exponer el patrón), `app/schemas.py`, `app/routers/sessions.py`, `streamlit_app.py`, `evals/run.py` y sus tests. Sin dependencias nuevas ni cambios incompatibles (un valor nuevo en `stop_reason`).
- Sin llamadas reales: los arreglos se verifican con dobles.

## Fuera de alcance

- Sacar la compresión del historial del camino de la petición (decisión D4 de `s05-add-memory-compression-and-dynamic-tier`; cambio de diseño para otro change).
- Unificar la comprobación de versión de `tier`/`review` en un helper (refactor sin fallo actual).
