# Stress test del CAG: dónde rompe

Ejercicio de S06. Baseline cuantitativo del CAG del estimador (resumen acumulativo + anclas + ventana de 6 turnos + `project_metadata` + tier + transcripción + adjuntos) antes de comparar con RAG.

**Cómo se midió.** `python -m evals.stress.run` en proceso, gpt-4o-mini, sin Redis (las tasas de caché son 0 por construcción y no hay caché semántica), configuración del CAG por defecto (`MAX_CONVERSATION_TURNS=6`, `MAX_ANCHORED_TURNS=3`, `SUMMARY_MAX_CHARS=2000`, `MAX_ATTACHMENT_CHARS=60000`). Tres conversaciones de 20 turnos × 3 repeticiones (**growing**: "Nimbus" acumula requisitos; **pivot**: de React Native a Flutter en el turno 5; **contradiction**: "Quasar", presupuesto de 30.000 € en el turno 3 y de 80.000 € en el 8) y una estimación con PDF de 0, 5, 20, 50 y 100 KB × 3. Métricas: `LatencyBudgetMetric(10 000 ms)`, `CostBudgetMetric(0,003 USD)` y `MemoryDriftMetric` (los hechos declarados siguen en el resumen, las anclas o la metadata; la ventana **no** cuenta). Resultado: [`results.csv`](results.csv), 171 filas (5 errores; una conversación se abandona tras un error), 1758 s y 0,285 USD (de ellos 0,016 estimados por turnos fallidos). Las tablas se generan con `python -m evals.stress.report evals/stress/results.csv`.

## Resumen

| Escenario | Adjunto (KB) | Filas | Errores | P50 latencia (ms) | P95 latencia (ms) | Coste total (USD) | de ello estimado (USD) | Caché exacta | Caché semántica | Recall medio |
|---|---|---|---|---|---|---|---|---|---|---|
| growing | - | 45 | 1 | 12774 | 18560 | 0.0874 | 0.0039 | 0 % | 0 % | 0.92 |
| pivot | - | 51 | 1 | 8678 | 15212 | 0.0840 | 0.0124 | 0 % | 0 % | 1.00 |
| contradiction | - | 60 | 0 | 9607 | 14016 | 0.0864 | - | 0 % | 0 % | 0.80 |
| attachments | 0 | 3 | 0 | 9020 | 9689 | 0.0077 | - | 0 % | 0 % | 0.00 |
| attachments | 5 | 3 | 0 | 8285 | 9472 | 0.0040 | - | 0 % | 0 % | 0.33 |
| attachments | 20 | 3 | 0 | 8223 | 15273 | 0.0074 | - | 0 % | 0 % | 0.00 |
| attachments | 50 | 3 | 0 | 8380 | 8586 | 0.0085 | - | 0 % | 0 % | 0.33 |
| attachments | 100 | 3 | 3 | - | - | - | - | - | - | - |

Coste total: el conocido más el estimado de los turnos fallidos que pudieron llegar al LLM (`cost_estimated_usd`: intentos × el turno más caro visto hasta entonces).

Recall medio: `memory_drift` en los perfiles; marcadores del adjunto recordados / 3 en `attachments`.

## Curva 1: latencia frente a tokens de entrada

| tokens_in | Turnos | tokens_in medio | P50 latencia (ms) | P95 latencia (ms) |
|---|---|---|---|---|
| 4000-7999 | 96 | 7003 | 7927 | 12275 |
| 8000-15999 | 51 | 9268 | 12774 | 15613 |
| 16000-31999 | 19 | 17268 | 16295 | 23575 |

## Curva 2: coste acumulado frente al turno (USD, media de las repeticiones)

| Turno | growing turno | growing acumulado | pivot turno | pivot acumulado | contradiction turno | contradiction acumulado |
|---|---|---|---|---|---|---|
| 1 | 0.0012 | 0.0012 | 0.0011 | 0.0011 | 0.0015 | 0.0015 |
| 2 | 0.0012 | 0.0024 | 0.0011 | 0.0022 | 0.0012 | 0.0027 |
| 3 | 0.0012 | 0.0036 | 0.0012 | 0.0034 | 0.0013 | 0.0040 |
| 4 | 0.0013 | 0.0049 | 0.0012 | 0.0046 | 0.0013 | 0.0052 |
| 5 | 0.0020 | 0.0069 | 0.0013 | 0.0059 | 0.0013 | 0.0066 |
| 6 | 0.0014 | 0.0082 | 0.0013 | 0.0072 | 0.0014 | 0.0079 |
| 7 | 0.0021 | 0.0103 | 0.0014 | 0.0086 | 0.0014 | 0.0093 |
| 8 | 0.0021 | 0.0125 | 0.0014 | 0.0099 | 0.0015 | 0.0108 |
| 9 | 0.0029 | 0.0154 | 0.0013 | 0.0113 | 0.0014 | 0.0122 |
| 10 | 0.0021 | 0.0175 | 0.0027 | 0.0140 | 0.0015 | 0.0137 |
| 11 | 0.0022 | 0.0197 | 0.0013 | 0.0153 | 0.0015 | 0.0152 |
| 12 | 0.0031 | 0.0228 | 0.0020 | 0.0173 | 0.0015 | 0.0166 |
| 13 | 0.0022 | 0.0250 | 0.0014 | 0.0187 | 0.0015 | 0.0181 |
| 14 | 0.0029 | 0.0279 | 0.0014 | 0.0200 | 0.0015 | 0.0196 |
| 15 | 0.0031 | 0.0309 | 0.0020 | 0.0221 | 0.0015 | 0.0212 |
| 16 | 0.0022 | 0.0332 | 0.0013 | 0.0234 | 0.0015 | 0.0227 |
| 17 | 0.0015 | 0.0347 | 0.0013 | 0.0247 | 0.0015 | 0.0242 |
| 18 | 0.0015 | 0.0362 | 0.0013 | 0.0260 | 0.0015 | 0.0257 |
| 19 | 0.0015 | 0.0377 | 0.0014 | 0.0274 | 0.0015 | 0.0272 |
| 20 | 0.0016 | 0.0393 | 0.0014 | 0.0288 | 0.0015 | 0.0288 |

## Curva 3: `memory_drift` medio frente a N

| N | growing | pivot | contradiction |
|---|---|---|---|
| 1 | 1.00 | 1.00 | 0.00 |
| 3 | 1.00 | 1.00 | 0.50 |
| 6 | 0.79 | 1.00 | 0.50 |
| 10 | 0.89 | 1.00 | 1.00 |
| 20 | 1.00 | 1.00 | 1.00 |

## Columnas de escenario: pivote y contradicción (% de repeticiones)

| Turno | pivot: React Native en tecnologías | contradiction: 30.000 en memoria | contradiction: 80.000 en memoria | Fuentes de 30.000 | Fuentes de 80.000 |
|---|---|---|---|---|---|
| 1 | 100 % | 0 % | 0 % | - | - |
| 2 | 100 % | 0 % | 0 % | - | - |
| 3 | 100 % | 100 % | 0 % | metadata ×3 | - |
| 4 | 100 % | 100 % | 0 % | metadata ×3 | - |
| 5 | 0 % | 100 % | 0 % | metadata ×3 | - |
| 6 | 0 % | 100 % | 0 % | metadata ×3 | - |
| 7 | 0 % | 100 % | 0 % | metadata ×3 | - |
| 8 | 0 % | 100 % | 100 % | metadata ×3 | metadata ×3 |
| 9 | 0 % | 100 % | 100 % | anchors|metadata ×3 | metadata ×3 |
| 10 | 0 % | 100 % | 100 % | anchors|metadata ×3 | metadata ×3 |
| 11 | 0 % | 100 % | 100 % | anchors|metadata ×3 | metadata ×3 |
| 12 | 0 % | 100 % | 100 % | anchors|metadata ×3 | metadata ×3 |
| 13 | 0 % | 100 % | 100 % | anchors|metadata ×3 | metadata ×3 |
| 14 | 0 % | 100 % | 100 % | summary|anchors|metadata ×3 | summary|metadata ×3 |
| 15 | 0 % | 100 % | 100 % | summary|anchors|metadata ×3 | summary|metadata ×3 |
| 16 | 0 % | 100 % | 100 % | summary|anchors|metadata ×3 | summary|metadata ×3 |
| 17 | 0 % | 100 % | 100 % | summary|anchors|metadata ×3 | summary|metadata ×3 |
| 18 | 0 % | 100 % | 100 % | summary|anchors|metadata ×3 | summary|metadata ×3 |
| 19 | 0 % | 100 % | 100 % | summary|anchors|metadata ×3 | summary|metadata ×3 |
| 20 | 0 % | 100 % | 100 % | summary|anchors|metadata ×3 | summary|metadata ×3 |

## Columnas de escenario: adjuntos por tamaño

| Adjunto (KB) | Filas | Errores | Caracteres del adjunto | tokens_in medio | P50 latencia (ms) | Coste medio (USD) | Marcadores (de 3) | Error |
|---|---|---|---|---|---|---|---|---|
| 0 | 3 | 0 | 0 | 15760 | 9020 | 0.0026 | 0.00 | - |
| 5 | 3 | 0 | 5040 | 6840 | 8285 | 0.0013 | 1.00 | - |
| 20 | 3 | 0 | 20123 | 13797 | 8223 | 0.0025 | 0.00 | - |
| 50 | 3 | 0 | 50036 | 16812 | 8380 | 0.0028 | 1.00 | - |
| 100 | 3 | 3 | - | - | - | - | - | rechazado (413 too_much_text) |

## Lectura: dónde empieza a romperse mi CAG y por qué

**Hasta 20 turnos el CAG no colapsa, pero ya enseña las dos grietas que lo harán romper.** El coste está acotado por construcción: la ventana y el resumen mantienen `tokens_in` entre ~6k y ~12k, y el turno 20 cuesta 1,3 veces el turno 1 en *growing* (0,0016 frente a 0,0012 USD) y lo mismo en *contradiction*; solo los turnos que además resumen llegan a 2,6× (0,0031 USD). 159 de 166 turnos cumplen el presupuesto de 0,003 USD. La primera grieta es la **memoria fuera de la ventana**: lo que el cliente dice vive solo en la ventana hasta que sale de ella y se resume, y mientras tanto no está en la memoria duradera. En *growing* el recall cae a 0,57 en el turno 7 (5 de 7 hechos), justo cuando el turno 1 sale de la ventana y el resumen aún tiene 526 caracteres, y en *contradiction* el nombre "Quasar" no aparece en la memoria hasta el turno 7 (recall 0 en N=1 y 0,5 en N=6): el `project_name` lo pone el título que elige el modelo, no el nombre que dio el cliente. La segunda grieta es la **saturación del resumen**: en el turno 20 mide 1938–1958 caracteres de un máximo de 2000, así que a partir de ahí cada turno nuevo obliga a recortar y la deriva dejará de recuperarse (hoy vuelve a 1,0 en N=20 porque todavía cabe todo). Y hay **degradación silenciosa** en las contradicciones: desde el turno 8, el 100 % de las repeticiones conserva a la vez 30.000 € (ancla + metadata + resumen) y 80.000 € (metadata + resumen); nada falla, pero el contexto dice dos presupuestos. El olvido explícito sí funciona: tras el pivote, React Native sale de `mentioned_technologies` en el 100 % de las repeticiones.

## Lectura: qué dimensión domina y qué justificaría saltar a RAG

**Domina la latencia, y los fallos duros aparecen en los bordes.** El 43 % de los turnos (71 de 166) supera el presupuesto de 10 s: el P50 sube de 7,9 s con 4–8k tokens de entrada a 16,3 s con 16–32k (P95 23,6 s), y la latencia del turno 20 en *growing* (15,6 s) es 1,7 veces la del turno 1 (9,0 s) aunque tenga casi los mismos tokens, porque la compresión (una llamada más al LLM dentro del turno) se suma a la espera. El coste es secundario y la memoria degrada sin avisar; lo que sí avisa son los fallos duros: 2 de 156 turnos de conversación (1,3 %) terminan en 502 porque el modelo no produce un objeto válido en 3 intentos, el 13 % de las llamadas necesita reintentos de validación (24 de 166, que encarecen el turno: los turnos de 0 KB con reintentos gastan 11–18k tokens frente a 6,8k del de 5 KB), y el PDF de 100 KB (99 820 caracteres) se **rechaza** siempre con 413 `too_much_text`: el CAG no trunca, falla. El caso límite que justifica RAG es claro con estos datos: un documento de más de ~60 000 caracteres (≈ 50 KB de texto, ya 16,8k tokens de entrada en un solo turno) no entra, y las conversaciones de más de ~20 turnos no caben en un resumen de 2000 caracteres sin perder hechos; con RAG, el documento y la conversación se trocean e indexan y en cada turno solo se recuperan los fragmentos relevantes, con un coste de entrada que no crece con el tamaño del documento ni con la longitud de la conversación. Un detalle que RAG también mejoraría: los códigos del PDF (ATLAS-7, el SLA de 99,9 %, Kestrel) aparecen como mucho 1 de 3 en la estimación aunque el documento entre entero, porque el modelo resume y no cita.

## Notas

- La matriz no es completa (decisión del usuario): los perfiles multiturno no se cruzan con los tamaños de adjunto; N ∈ {1, 3, 6, 10, 20} se lee en el turno N de cada conversación de 20.
- `MemoryDriftMetric` busca en resumen, anclas y metadata como pide el enunciado; un hecho que solo está en la ventana cuenta como olvidado, lo que explica las caídas antes de la primera compresión.
- `turn_observed` lleva el `session_id` corto (8 caracteres); el completo da acceso a la sesión y no se registra.
- El coste de los turnos que fallan con 502 se estima (`cost_estimated_usd`: intentos × el turno más caro visto), porque el proveedor no lo devuelve en el error.
