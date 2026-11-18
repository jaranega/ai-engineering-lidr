## Context

El enunciado de S06 pide instrumentar el CAG, someterlo a tres escenarios de carga (multiturno largo, adjuntos grandes y repeticiones) y entregar `evals/stress/REPORT.md` y `evals/stress/results.csv`. Da por hecho la base del directo de S05, que ya tenemos:

- `MetricResult` y `run_all_metrics` en `evals/metrics.py`;
- un runner con `--http`/`TestClient` (`evals/run.py`);
- `GET /sessions/{id}` con `anchors_count`, `summary_chars` y `last_tier`;
- las métricas del turno sumadas (tokens, coste y latencia).

**Estado de partida relevante:**
- `SessionService.estimate` orquesta el turno: lock, tier, adjuntos, `estimate_turn`, actualización de la sesión y compresión.
- Ventana: `MAX_CONVERSATION_TURNS=6`. Anclas: tope de 3. Resumen: `SUMMARY_MAX_CHARS=2000`. Adjuntos: hasta `MAX_ATTACHMENT_CHARS=60000`.
- `GET /sessions/{id}` no expone texto (decisión de diseño), así que no basta para comprobar si un hecho sigue en la memoria.
- La caché es exacta en Redis y opcional. No hay caché semántica.

Decisiones del usuario:
- la rama es `session-06`, reabierta;
- un endpoint de depuración opcional;
- unos 195 turnos (≈0,35 USD);
- `fpdf2` en el grupo `dev`.

## Goals / Non-Goals

**Goals:**
- Un evento por turno con los 13 campos, sin texto.
- Un runner reproducible en proceso y por HTTP que deje un CSV de al menos 50 filas.
- Tres métricas deterministas.
- Un informe con las tres curvas canónicas y una lectura cuantitativa de dónde rompe el CAG.

**Non-Goals:**
- RAG, optimizar el CAG, comparar proveedores, LLM-as-judge, gráficos y persistencia.
- No se cambia ninguna constante del CAG: el ejercicio mide, no optimiza.

## Decisions

### D1. `turn_observed` en `SessionService.estimate`, tras la compresión

El enunciado lo sitúa en `estimate_conversational()`, que aquí no existe. El único sitio que conoce todo lo necesario es `SessionService.estimate`, justo antes del `return` y después de comprimir. Allí están las métricas del turno, el tamaño del historial ya comprimido y el tier.

| Campo | Origen |
|---|---|
| `turn_index` | `session.turn_count` tras el turno |
| `session_id` | identificador **corto** de la sesión (8 caracteres): el completo da acceso a la sesión y la regla vigente de observabilidad prohíbe registrarlo; el runner ya conoce el completo |
| `enriched_transcript_chars` | `len(transcript)` + caracteres del texto de los adjuntos ya enmascarado |
| `attachments_total_chars` | caracteres del texto de los adjuntos; 0 sin adjuntos |
| `messages_in_window` | `len(history.to_messages())` tras la compresión: anclas + ventana, en mensajes |
| `anchors_count` | número de anclas |
| `summary_chars` | longitud del resumen |
| `tokens_in`, `tokens_out`, `cost_usd` | métricas del turno, ya sumadas si hubo revisión |
| `latency_ms` | duración de todo `SessionService.estimate`, incluida la compresión: lo que espera quien llama |
| `cache_hit_kind` | `"exact"` si `outcome.cache_hit`, si no `"none"`. `"semantic"` está en el tipo, pero no se produce sin caché semántica |
| `last_resolved_tier` | tier aplicado al turno |

La observación se guarda como dataclass inmutable en `Session.last_observation`, para el endpoint de D2. El evento registra el mismo objeto, con los mismos nombres de campo.

*Alternativas:*
- Emitirlo en `EstimationService.estimate_turn`: no conoce la compresión ni el turno de la sesión.
- Agregarlo a partir de los cinco eventos sueltos: obliga a reconciliar timestamps, que es justo lo que el enunciado quiere evitar.

### D2. Endpoint de depuración opcional

`GET /api/v1/sessions/{id}/debug` solo está montado con `DEBUG_ENDPOINTS=true`; por defecto es `false`. Sin la variable, la ruta no existe y responde **404**. El endpoint devuelve `SessionDebug`:
- `session_id`;
- `last_observation`, con los 13 campos o `null`;
- `summary`, el texto o `null`;
- `anchors`, la lista del `user` guardado de cada ancla, con los adjuntos como `[…]`;
- `project_metadata`.

Expone texto del cliente, así que **nunca** debe activarse en producción. El README y `.env.example` lo advierten, y el arranque registra un `warning` `debug_endpoints_enabled` cuando está activo.

*Alternativas:*
- Parsear `turn_observed` del stdout del servidor. Sirve para la observación, pero `MemoryDriftMetric` necesita el texto del resumen y de las anclas, que no puede ir en los logs.
- Leer el `SessionStore` en proceso. No cumple el criterio "`--http http://localhost:8000` corre de punta a punta".
- Añadir el texto a `GET /sessions/{id}`. Rompe la decisión de no exponer texto en el endpoint normal.

### D3. Escenarios y *fact-tracker* (`evals/stress/scenarios.py`)

Un `Scenario` tiene `name`, `language`, `project_type` y `turns: list[ScenarioTurn]`, donde `ScenarioTurn(index, transcript, facts: tuple[str, ...])`. Cada hecho se declara en el turno donde el cliente lo dice y debe seguir recordándose en todos los turnos posteriores. La comparación es una subcadena que no distingue mayúsculas ni tildes, como en el enunciado.

- **`growing`** (español, `web_saas`, 20 turnos). El turno 1 presenta el proyecto "Nimbus", con React y Postgres. Del 2 al 20 se añaden requisitos coherentes: SSO, multi-tenant, audit log, exportación CSV, roles, notificaciones, API pública, etc. Hechos: `Nimbus`, `React`, `Postgres` y algunos requisitos con nombre propio (`SSO`, `multi-tenant`, `audit log`).
- **`pivot`** (inglés, `mobile_app`, 20 turnos). El turno 1 pide React Native. El turno 5 cambia a Flutter y descarta React Native. Hechos: el nombre del proyecto y, desde el turno 5, `Flutter`. Además se mide si `React Native` sigue en `mentioned_technologies` (columna `stale_tech`), lo que comprueba la política de olvido del change de memoria.
- **`contradiction`** (español, `web_saas`, 20 turnos). El turno 3 fija el presupuesto en "30.000 €" y el 8 lo sube a "80.000 €". Hechos: el nombre del proyecto y el presupuesto vigente, que es `30.000` en los turnos 3–7 y `80.000` desde el 8. Se mide cuál se conserva (columnas `budget_30k_present` y `budget_80k_present`) y dónde: resumen, ancla o metadata.
- **`attachments`** (español, `web_saas`, 1 turno). Una transcripción corta y fija, más un PDF de 0, 5, 20, 50 o 100 KB. El PDF lleva unos marcadores únicos, como "Código de proyecto ATLAS-7" o "SLA 99,9 %", repartidos al principio, a la mitad y al final del documento. El recall de los marcadores en la respuesta (resumen, tareas y supuestos) mide si el contenido del adjunto llega a la estimación, y en el de 100 KB qué pasa al superar el límite de 60 000 caracteres. **Hallazgo al implementar:** nuestro extractor no trunca, rechaza el turno con 413 `too_much_text` (falla duro). El enunciado espera un corte; no se cambia el extractor (el ejercicio mide, no optimiza) y el informe lo recoge como fila de error.

Los textos de los turnos son fijos y están en el código: el runner es reproducible.

### D4. Métricas (`evals/stress/metrics.py`)

Viven en un módulo aparte, porque dependen de la forma de `turn_observed` y del snapshot de depuración, no de `GoldenCase` ni de `EstimationResult`. Reutilizan `MetricResult` de `evals.metrics`.

- **`LatencyBudgetMetric(budget_ms=10_000)`**: 1.0 si `latency_ms <= budget_ms`. Por defecto, 10 s, el P95 de un turno simple medido en S05 con margen. Es un contrato, no una observación.
- **`CostBudgetMetric(budget_usd=0.003)`**: 1.0 si `cost_usd <= budget_usd`. Por defecto, el doble del coste de un turno simple. Un `cost_usd` nulo da 0.0 con `details = "unknown cost"`.
- **`MemoryDriftMetric(facts, where=("summary", "anchors", "metadata"))`**: evalúa el snapshot de depuración. `score` es la fracción de hechos encontrados en las fuentes indicadas, y `passed` es `score == 1.0`. `details` lista los que faltan y dónde se encontró cada uno. La metadata se aplana a texto (nombre, alcance, tecnologías, restricciones y descartes).

Los presupuestos se pueden cambiar con `--latency-budget-ms` y `--cost-budget-usd`.

*Alternativas:* ponerlas en `evals/metrics.py`, que mezclaría métricas por caso golden con métricas por turno; o embeddings y LLM-as-judge, que el enunciado excluye.

### D5. Runner (`evals/stress/run.py`)

```
uv run python -m evals.stress.run [--http URL] \
  --scenarios growing,pivot,contradiction --attachment-sizes 0,5,20,50,100 \
  --repeats 3 --turns 20 --output evals/stress/results.csv --max-cost-usd 0.60
```

**Modos:**
- **En proceso** (por defecto): `TestClient` con un `SessionStore` aislado y `DEBUG_ENDPOINTS` activado por override de configuración.
- **`--http`**: el servidor debe tener `DEBUG_ENDPOINTS=true`. Si no lo tiene, el primer `GET …/debug` da 404 y el runner sale con código 2 y un mensaje claro.

**Ejecución:**
- Cada repetición de cada escenario usa una sesión nueva.
- Tras cada turno se llama a `GET …/debug`: la observación y la memoria salen del mismo snapshot.
- Antes, se generan los PDF con `build_pdfs` si faltan.

**Fila del CSV:**
- Los campos de identificación: `scenario`, `repeat`, `attachment_kb` y `turn_index`.
- Los 13 campos de `turn_observed`.
- `latency_budget`, `cost_budget` y `memory_drift`, con la puntuación de cada métrica.
- `facts_total`, `facts_recalled` y las columnas de escenario: `stale_tech`, `budget_30k_present`, `budget_80k_present` y `attachment_markers_recalled`.
- `error`, vacío si no lo hay.

**Errores:**
- Una fila de error no detiene el escenario, salvo un 404 o 409 de sesión: entonces se abandona esa conversación.
- `--max-cost-usd` se comporta como en el runner de evals: un coste desconocido detiene la ejecución.

**Lo que no escribe:** el CSV no lleva transcripciones ni respuestas.

**Ajuste tras la primera ejecución real (decisión del usuario).** La ejecución se paró en la fila 27. El turno 7 de `growing` falló tres veces la validación estructural (fases incoherentes) y la API respondió 502. Con la regla general del tope, un coste desconocido detiene todo el runner.

En el stress runner, una fila de error de un turno que llegó al LLM (5xx o respuesta inválida) **no detiene** la ejecución. En su lugar suma al acumulado una estimación conservadora: (`STRUCTURED_MAX_RETRIES` + 1) × el coste máximo por turno observado hasta ese momento, o 0,005 USD si aún no hay ninguno. La estimación queda en la columna `cost_estimated_usd`.

Un turno con éxito y `cost_usd` nulo sigue deteniendo la ejecución, porque eso sería un hueco de precios y no un fallo. El runner de evals (`evals/run.py`) no cambia.

### D6. Informe (`evals/stress/report.py` + `REPORT.md`)

`python -m evals.stress.report evals/stress/results.csv` imprime en Markdown:
1. **Tabla resumen**, una fila por escenario y tamaño de adjunto: P50 y P95 de la latencia, coste total, tasa de acierto de caché exacta y semántica, y recall medio.
2. **Las tres curvas en tabla:**
   - latencia frente a `tokens_in`, por cubos de tokens;
   - coste acumulado frente a `turn_index`, por escenario;
   - `MemoryDriftMetric` frente a N ∈ {1, 3, 6, 10, 20}, por escenario.
3. **Columnas propias de cada escenario:**
   - `stale_tech` en `pivot`;
   - presupuestos presentes en `contradiction`;
   - marcadores del adjunto por tamaño.

`REPORT.md` = esas tablas más los **dos párrafos de lectura**, escritos a mano a partir de los datos, con al menos una afirmación cuantitativa como "el coste del turno 20 multiplica por X el del turno 1". El generador no inventa la lectura.

### D7. PDFs (`evals/stress/fixtures/build_pdfs.py`)

`fpdf2` se añade al grupo `dev`, no a la imagen de la API. El texto es determinista: párrafos de un pliego técnico sintético, con una semilla fija y los marcadores de D3. Se ajusta hasta que el texto extraído por nuestro extractor (pypdf) ronde 5, 20, 50 y 100 KB. Los ficheros se escriben en `evals/stress/fixtures/` y se ignoran en git (`*.pdf`).

### D8. Ejecución real

- **Condiciones:** `REDIS_URL` vacío, gpt-4o-mini, la configuración por defecto del CAG y `--max-cost-usd 0.60`. En proceso, para no depender de Docker; el modo `--http` se prueba con un caso corto.
- **Volumen:** 3 perfiles × 20 turnos × 3 repeticiones + 5 tamaños × 3 repeticiones = 195 filas.
- **Duración y coste:** unos 35 minutos y unos 0,35 USD. Los turnos largos cuestan más por el historial, y la compresión añade llamadas baratas.

## Risks / Trade-offs

- **[El endpoint de depuración expone texto del cliente]** → Desactivado por defecto, 404 si no está activo, `warning` al arrancar y advertencias en `.env.example` y en el README. Nunca se activa en Docker por defecto.
- **[Variabilidad del LLM en 3 repeticiones]** → Se reportan P50 y P95 y las medias por N. Las afirmaciones del informe se apoyan en tendencias, no en una sola fila.
- **[Coincidencias espurias en la deriva]** → Los hechos usan nombres propios poco comunes ("Nimbus", "ATLAS-7") y cifras con formato fijo.
- **[Tiempo de ejecución]** → Unos 35 minutos en segundo plano. El CSV se escribe fila a fila, así que un corte no pierde lo medido.

## Migration Plan

Aditivo. El endpoint nuevo está desactivado por defecto y no hay cambios en los contratos existentes.

## Open Questions

Ninguna pendiente. Las cuatro decisiones de alcance las tomó el usuario. Los presupuestos de las métricas (10 s y 0,003 USD) son valores por defecto que se pueden cambiar con flags.

## Resultado de la ejecución real (2026-11-18)

**Volumen y coste.** 171 filas en 1758 s y 0,285 USD, de los que 0,016 son estimados. Hubo 5 errores: dos 502 por salida estructurada inválida (una conversación de `growing` y otra de `pivot` se abandonan tras su error) y los tres PDF de 100 KB, rechazados con 413. Hubo una primera ejecución que se paró en la fila 27 por la regla del coste desconocido; tras el ajuste de D5 se repitió desde cero. Una prueba corta con `--http` contra uvicorn y `DEBUG_ENDPOINTS=true` dio 3 filas por 0,004 USD.

**Hallazgos** (detalle en `evals/stress/REPORT.md`):
- **Coste acotado:** el turno 20 cuesta 1,3 veces el turno 1.
- **Latencia, la dimensión dominante:** el 43 % de los turnos supera los 10 s, y el P50 pasa de 7,9 s a 16,3 s con más tokens de entrada.
- **Memoria fuera de la ventana:** el recall cae a 0,57 en el turno 7 de `growing`, y "Quasar" no llega a la memoria duradera hasta el turno 7.
- **Resumen saturado** en el turno 20: 1958 de 2000 caracteres.
- **Presupuestos contradictorios:** los dos conviven en la memoria desde el turno 8.
- **Olvido de React Native:** funciona en el 100 % de las repeticiones.
- **Fallos duros:** 2 de 156 turnos de conversación acaban en 502, y el PDF de 100 KB siempre da 413.
