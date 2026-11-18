## Why

Puesta al día con el directo de la sesión 05 (rama oficial `session_05_live` de https://github.com/LIDR-academy-temp/ai-engineering, carpeta `estimator/`), en la rama `session-06`. Por decisión del usuario, el alcance es la **memoria** (compresión con anclas y resumen acumulativo) y el **tier dinámico**. Quedan fuera Actor-Critic-Boss y las evals con *golden dataset*.

El ejercicio de S05 dejó una ventana deslizante de 6 turnos más un `project_metadata` tipado. Lo que sale de la ventana se pierde: un "el cliente ha firmado un NDA" o "el presupuesto está cerrado en 40 000 €" solo sobrevive si acaba reflejado en el resumen de la estimación. El material de la sesión (`02_memoria-conversacional-vs-historial.md`) describe la estrategia **híbrida con anclas** (resumen + ventana + turnos críticos fijos) para conversaciones largas, y el consejo 5 del mentor es "usar anclas para proteger la información relevante durante la compresión". El directo añade también el **tier dinámico**: adaptar el encuadre de la estimación al interlocutor (ejecutivo, PM, desarrollador) según el contexto de la conversación.

## What Changes

- **Compresión del historial** (`app/sessions/compression/`):
  - Cuando un par `user` + `assistant` sale de la ventana, un **detector de anclas** decide si contiene un compromiso duradero (NDA o confidencialidad, contrato firmado, alcance congelado, presupuesto cerrado, normativa como RGPD/HIPAA/ISO 27001, fecha límite innegociable, compromiso explícito del cliente). El modo por defecto es **heurístico** (patrones en **español e inglés**, sin LLM); un modo LLM opcional (clasificador binario) vuelve al heurístico si falla.
  - Los pares ancla se conservan **literales** fuera de la ventana, con un tope para que el prompt no crezca sin límite.
  - Los pares que no son ancla se integran en un **resumen acumulativo** único por sesión con una llamada barata al LLM (en el idioma de la sesión). Si falla, se conserva el resumen anterior.
  - El LLM recibe: system regenerado → resumen (si hay) → anclas → ventana reciente → turno nuevo.
- **`project_metadata` ampliado** con `explicit_constraints` y `rejected_options` (los dos campos que propone el material y aún no tenemos), derivados de la salida estructurada como el resto. `rejected_options` también implementa la política de olvido 1: una tecnología que el cliente descarta sale de `mentioned_technologies`.
- **Tier dinámico** (`app/sessions/tier.py`): `executive`, `pm`, `developer` o `default`, resuelto en cada turno por una cadena de reglas puras sobre la transcripción y el metadata (NDA o normativa → `executive`; vocabulario técnico → `developer`; equipo pequeño → `pm`), con patrones en español e inglés. Un parámetro opcional `tier` del turno lo fija explícitamente. El tier y la regla que lo decidió se devuelven en la respuesta y en `GET /api/v1/sessions/{id}`, y se registran en el log.
- **Prompt `v5`**: copia de `v4` con un bloque `<audience>` según el tier y las instrucciones de los campos nuevos del metadata. `v4` no se edita (está publicada); el endpoint de sesión pasa a `v5` por defecto.
- **API**: el turno de sesión acepta `tier` opcional (Form); `GET /api/v1/sessions/{id}` añade `anchors_count`, `summary_chars`, `last_tier` y `last_tier_rule`; la respuesta del turno añade `tier` y `tier_rule`.
- **Streamlit**: selector de tier ("Automático" por defecto) en "Opciones avanzadas", y en la barra lateral el tier del último turno con la regla, el número de anclas y si hay resumen.
- **Configuración**: modo del detector de anclas, modelo de compresión, tope de anclas.

### Desviaciones respecto al directo

- **Patrones en español además de en inglés** (anclas y tier), por la pregunta de la sesión sobre guardrails multilingües y porque nuestras conversaciones son bilingües.
- **Resumen en el idioma de la sesión**, no siempre en inglés: lo aprendido en S04 sobre el idioma de la respuesta.
- **Tope de anclas**: el directo nunca las desaloja; aquí se limita su número para acotar el coste.
- **Los adjuntos siguen sustituidos por `[…]`** también en anclas y resumen.
- **`explicit_constraints` y `rejected_options` en el metadata**, que el directo no implementa pero sí describe el material de la sesión.
- **Prompt `v5` nuevo** en lugar de editar el conversacional, por la regla de no editar versiones publicadas.

## Capabilities

### New Capabilities
- `memory-compression`: detector de anclas (heurístico ES/EN y LLM opcional), política de compresión, resumen acumulativo y composición de los mensajes.
- `audience-tier`: resolución del tier por reglas, override explícito y explicabilidad (regla que decide).

### Modified Capabilities
- `conversation-sessions`: historial con anclas y resumen; metadata con `explicit_constraints` y `rejected_options` y la política de olvido; `tier` en el turno; campos nuevos en `GET /sessions/{id}` y en la respuesta.
- `structured-estimation`: campos del schema para restricciones y opciones descartadas.
- `prompt-templates`: versión `v5`; `v4` inmutable.
- `cag-prompt`: bloque `<audience>` por tier e instrucciones de los campos nuevos.
- `estimation-form-ui`: selector de tier y panel lateral ampliado.
- `app-config`: variables de la compresión.
- `observability`: eventos de anclas, compresión y tier, sin contenido.
- `ci-pipeline`: cobertura de compresión y tier.

## Impact

- **Código**: nuevos `app/sessions/compression/` y `app/sessions/tier.py`; `app/sessions/models.py`, `app/sessions/metadata.py`, `app/services/session_service.py`, `app/services/llm_service.py`, `app/estimation/schema.py`, `app/prompts/estimation/v5/`, `app/prompts/loader.py`, `app/schemas.py`, `app/routers/sessions.py`, `app/config.py`, `streamlit_app.py`.
- **API**: campos nuevos opcionales en el turno y en las respuestas; sin cambios incompatibles salvo la versión por defecto del prompt de sesión (`v5`).
- **Coste**: una llamada barata más por turno **solo** cuando hay pares desalojados que no son ancla (a partir del turno 7 con la ventana por defecto); el modo LLM del detector añadiría otra, y es opcional.
- **Sin dependencias nuevas.**

## Fuera de alcance

- Actor-Critic-Boss y evals con *golden dataset* (resto del directo de S05).
- Caché semántica (pendiente desde el directo de S04).
- Persistencia de sesiones y TTL por inactividad (política de olvido 2).
