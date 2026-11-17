# memory-compression Specification

## Purpose
Conservar la información duradera de las conversaciones largas sin que el prompt crezca sin límite: los turnos que salen de la ventana y contienen un compromiso del cliente (NDA, contrato, alcance, presupuesto, normativa, plazos) se guardan literales como anclas, y el resto se integra en un resumen acumulativo en el idioma de la sesión. Puesta al día con el directo de S05.
## Requirements
### Requirement: Detector heurístico de anclas
El sistema SHALL decidir si un turno que sale de la ventana contiene un compromiso duradero del cliente con `detect_anchor(turn) -> AnchorDecision` en `app/sessions/compression/anchors.py`, una función pura y determinista, sin LLM ni red. `AnchorDecision` MUST ser inmutable con `is_anchor`, `rules` (tupla de identificadores) y `source` (`"heuristic"` o `"llm"`). La detección MUST hacerse solo sobre el `user` guardado del turno (con los adjuntos como `[…]`) y MUST NOT mirar el `assistant`. Las reglas MUST cubrir español e inglés y ser, en este orden: `nda` (NDA, acuerdo o cláusula de confidencialidad, *non-disclosure agreement*), `contract_signed` (contrato o SOW firmado), `scope_frozen` (alcance cerrado, congelado o fijo; *scope frozen/locked*), `budget_fixed` (presupuesto cerrado, fijo, máximo o tope; "no podemos pasar de" con una cifra; *fixed/capped budget*, *cannot exceed* con una cifra, *not to exceed*), `regulation` (RGPD, LOPDGDD, LOPD, ENS, GDPR, HIPAA, PCI DSS, SOC 2, SOX, NIS2, DORA, ISO 27001), `hard_deadline` (fecha límite innegociable o fija, "tiene que estar en producción antes de", *hard deadline*, *must go live by*, *non-negotiable*, *no later than*) y `client_commitment` ("nos comprometemos a", "queda acordado", "hemos acordado", *we agreed that/to/on*, *mandatory requirement*). Salvo `regulation`, que acepta la mención sola de la norma, cada regla MUST exigir un término del compromiso más un calificador. Las siglas MUST compararse con distinción de mayúsculas y límites de palabra; el resto, sin distinguir mayúsculas y con las tildes opcionales. `rules` MUST contener todas las reglas que casan, en el orden de la lista; `is_anchor` es `true` si hay al menos una.

#### Scenario: NDA en español
- **WHEN** se evalúa un turno cuyo `user` contiene "Hemos firmado un acuerdo de confidencialidad con el cliente"
- **THEN** `is_anchor = true`, `rules` contiene `nda` y `source = "heuristic"`

#### Scenario: Presupuesto cerrado en inglés
- **WHEN** se evalúa un turno cuyo `user` contiene "The budget is capped at $45k"
- **THEN** `is_anchor = true` y `rules = ("budget_fixed",)`

#### Scenario: Varias reglas en orden
- **WHEN** se evalúa "Hemos firmado un NDA y el presupuesto está cerrado en 40 000 €"
- **THEN** `rules = ("nda", "budget_fixed")`

#### Scenario: Siglas con mayúsculas
- **WHEN** se evalúa un turno cuyo `user` contiene "revisamos la agenda del proyecto" o "nda" en minúsculas
- **THEN** `is_anchor = false`

#### Scenario: Corpus de negativos
- **WHEN** se evalúan "presupuesto aproximado", "¿cuál sería el presupuesto?", "confidence level", "módulo para firmar contratos online", "users sign contracts in the app", "fecha de entrega estimada", "ISO 8601" y "la app debe estar en inglés"
- **THEN** ninguno es ancla

#### Scenario: El texto del asistente no cuenta
- **GIVEN** un turno cuyo `user` no contiene ningún compromiso y cuyo `assistant` contiene "Riesgo: cumplimiento RGPD"
- **WHEN** se evalúa el turno
- **THEN** `is_anchor = false`

#### Scenario: Muestras del repositorio
- **WHEN** se evalúa la descripción de cada muestra de `samples/` como `user` de un turno
- **THEN** el resultado coincide con la lista esperada fijada en el test

### Requirement: Modo LLM del detector de anclas
Con `ANCHOR_DETECTION_MODE = "llm"`, el detector SHALL aplicar primero el heurístico y, solo si no casa, llamar al LLM con `generate_structured`, el modelo de respuesta `AnchorVerdict(is_anchor: bool, category: <una de las reglas> | None)` y el prompt versionado `app/prompts/anchor_classifier/v1/`. Un positivo heurístico MUST devolverse sin llamar al LLM. Un veredicto positivo del LLM MUST producir `AnchorDecision(is_anchor=True, rules=("llm",), source="llm")`. Ante cualquier error de la llamada, MUST devolver el resultado heurístico (negativo), registrar `anchor_classifier_failed` y no propagar el error. El clasificador MUST vivir en `app/services/memory.py` y usar el mismo modelo que el resumidor (`COMPRESSION_MODEL`).

#### Scenario: Positivo heurístico sin LLM
- **GIVEN** el modo LLM y un turno con "We signed the NDA"
- **WHEN** se evalúa el turno
- **THEN** el resultado es ancla con `rules = ("nda",)` y el proveedor no recibe ninguna llamada

#### Scenario: Positivo del LLM
- **GIVEN** el modo LLM, un turno sin regla heurística y un proveedor simulado que devuelve `is_anchor = true`
- **WHEN** se evalúa el turno
- **THEN** el resultado es ancla con `rules = ("llm",)` y `source = "llm"`

#### Scenario: Fallo del clasificador
- **GIVEN** el modo LLM, un turno sin regla heurística y un proveedor que lanza `LLMProviderError`
- **WHEN** se evalúa el turno
- **THEN** el resultado es `is_anchor = false`, se registra `anchor_classifier_failed` con `error_type` y no se lanza ninguna excepción

### Requirement: Política de compresión
`compress_history(history, *, detect, summarize, max_anchors) -> CompressionReport` en `app/sessions/compression/policy.py` SHALL ser una función sin dependencias del LLM que recibe el detector y el resumidor como *callables*. MUST: tomar los turnos expulsados con `history.take_evicted()` y, si no hay expulsados ni backlog, terminar sin llamar a `detect` ni a `summarize`; evaluar con `detect` cada expulsado, del más antiguo al más reciente; añadir los que son ancla a las anclas, en orden cronológico; si las anclas pasan de `max_anchors`, degradar la más antigua a los turnos que se resumen en esa misma compresión (evento `anchor_demoted`); enviar a resumir los que no son ancla junto con el backlog, ordenados por `number`; y llamar a `summarize(resumen previo, turnos)` solo si hay turnos que resumir. `max_anchors = 0` MUST desactivar las anclas: todo lo expulsado se resume. El historial MUST cumplir siempre la invariante de conservación: `len(ventana) + len(anclas) + summarized_turns + len(backlog) + dropped_turns == turn_count`, con `len(anclas) ≤ max_anchors` y todas las anclas con `number` menor que cualquier turno de la ventana. `CompressionReport` MUST informar de `evicted`, `anchored`, `demoted`, `summarized`, `backlog`, `dropped`, `summary_updated`, `summary_error` y las reglas de las anclas.

#### Scenario: Nada expulsado
- **GIVEN** un historial con la ventana sin llenar y sin backlog
- **WHEN** se comprime
- **THEN** no se llama a `detect` ni a `summarize`

#### Scenario: Expulsado que es ancla
- **GIVEN** un turno expulsado que `detect` marca como ancla
- **WHEN** se comprime
- **THEN** el turno pasa a las anclas, `summarize` no se llama y el resumen no cambia

#### Scenario: Expulsado que no es ancla
- **GIVEN** un resumen previo `P` y un turno expulsado que no es ancla
- **WHEN** se comprime
- **THEN** se llama a `summarize(P, [turno])` y el resumen pasa a ser el texto devuelto

#### Scenario: Tope de anclas
- **GIVEN** `max_anchors = 3`, tres anclas (turnos 1, 2 y 3) y un turno 4 expulsado que es ancla
- **WHEN** se comprime
- **THEN** las anclas son los turnos 2, 3 y 4, el turno 1 se envía a `summarize` y el informe tiene `demoted = 1`

#### Scenario: Anclas desactivadas
- **GIVEN** `max_anchors = 0` y un turno expulsado con un NDA
- **WHEN** se comprime
- **THEN** no hay anclas y el turno se envía a `summarize`

#### Scenario: Invariante de conservación
- **WHEN** se ejecutan secuencias aleatorias de 1 a 40 turnos con decisiones de ancla y fallos de `summarize` aleatorios (con `random.Random(seed)`)
- **THEN** tras cada compresión se cumple la invariante de conservación, el tope de anclas y el orden de las anclas

### Requirement: Fallo del resumen y backlog
Si `summarize` lanza cualquier excepción, `compress_history` SHALL capturarla, conservar el resumen anterior sin cambios, pasar los turnos que no se pudieron resumir al backlog e informar `summary_error` con el tipo de la excepción. En la siguiente compresión, el backlog MUST resumirse junto con los nuevos expulsados y vaciarse si la llamada va bien. El backlog MUST tener un tope igual a `max_turns`: si lo supera, MUST descartar los turnos más antiguos, sumarlos a `dropped_turns` y registrar `summary_backlog_dropped`. Un fallo de la compresión MUST NOT hacer fallar el turno del usuario.

#### Scenario: Resumen que falla
- **GIVEN** un resumen previo `P` y un `summarize` que lanza una excepción
- **WHEN** se comprime con un turno expulsado que no es ancla
- **THEN** el resumen sigue siendo `P`, el turno está en el backlog y el informe lleva `summary_error`

#### Scenario: Reintento en el turno siguiente
- **GIVEN** un turno 1 en el backlog y un `summarize` que ahora funciona
- **WHEN** se comprime con el turno 2 expulsado
- **THEN** `summarize` recibe los turnos 1 y 2 en orden y el backlog queda vacío

#### Scenario: Backlog lleno
- **GIVEN** `max_turns = 2` y un `summarize` que siempre falla
- **WHEN** se expulsan 3 turnos que no son ancla
- **THEN** el backlog tiene los 2 más recientes, `dropped_turns = 1` y se registra `summary_backlog_dropped`

### Requirement: Resumen acumulativo de la conversación
`ConversationSummarizer` en `app/services/memory.py` SHALL integrar los turnos que se resumen en un único resumen por sesión llamando a `generate_structured` con el modelo de respuesta `ConversationSummary(summary: str)` y el prompt versionado `app/prompts/conversation_summary/v1/` (`render_summary_prompt(previous, turns, language)` en el loader), con `max_tokens = 800`. El prompt MUST recibir, solo como contexto y si lo hay, el proyecto en `<project_context>` (`project_name` y `agreed_scope` del `project_metadata`), el resumen previo (si lo hay) en `<previous_summary>` y, en `<turns>`, solo el `user` guardado de cada turno (con `--- turn N ---` y los adjuntos como `[…]`), nunca el `assistant`. El system MUST pedir reescribir el resumen completo integrando lo nuevo, conservar literalmente cifras, fechas, nombres y compromisos del cliente (NDA, presupuesto, normativa, plazos, opciones descartadas, cambios de alcance), eliminar lo que el cliente haya retractado, describir solo lo que dicen los turnos y el resumen previo (si los turnos solo traen cambios, resumirlos como cambios sobre el proyecto de `<project_context>`, sin describir de nuevo el proyecto ni inventar su tipo o sus funciones), no incluir horas ni importes de las estimaciones, tratar el contenido como datos y terminar con la instrucción de escribir el resumen en el idioma de la sesión (`session.language`). Con `COMPRESSION_MODEL` definido, la llamada MUST usar ese modelo como override sin fallback; sin él, el primario con fallback. Un error del proveedor MUST propagarse al llamador (lo captura la política). Un normalizador de `summary` (antes de validar, idempotente y sin reintento) MUST recortar los espacios, quitar las etiquetas propias del prompt (`<conversation_summary>`, `<project_metadata>`, `<transcript>`, `<attachments>`, `<audience>` y sus cierres) y, si pasa de `SUMMARY_MAX_CHARS = 2000` caracteres, cortar en el último fin de frase anterior al límite o, si no hay ninguno, en el límite. Un resumen vacío tras normalizar MUST producir un error de validación.

#### Scenario: Solo los mensajes del cliente
- **GIVEN** dos turnos con `user` `U1`, `U2` y `assistant` `A1`, `A2`
- **WHEN** se renderiza el prompt del resumen
- **THEN** el user contiene `U1` y `U2` con sus separadores de turno y no contiene `A1` ni `A2`

#### Scenario: Contexto del proyecto
- **GIVEN** un `project_metadata` con `project_name` "Sistema de reservas" y `agreed_scope` `S`
- **WHEN** se renderiza el prompt del resumen
- **THEN** el user contiene `<project_context>` con "Sistema de reservas" y `S` antes de `<turns>`, y sin metadata no contiene `<project_context>`

#### Scenario: Resumen previo
- **WHEN** se renderiza el prompt con y sin resumen previo
- **THEN** solo el primero contiene `<previous_summary>`

#### Scenario: Idioma del resumen
- **WHEN** se renderiza el prompt con `language = "es"` y con `"en"`
- **THEN** la última línea del system pide escribir el resumen en español y en inglés, respectivamente

#### Scenario: Adjuntos sustituidos
- **GIVEN** un turno cuyo `user` guardado contiene `--- attachment: spec.pdf ---` y `[…]`
- **WHEN** se renderiza el prompt del resumen
- **THEN** el user contiene el separador y `[…]`

#### Scenario: Normalización del resumen
- **WHEN** se valida un `summary` de 2500 caracteres con frases terminadas en punto y una etiqueta `</conversation_summary>`
- **THEN** el resultado no contiene la etiqueta, tiene como máximo 2000 caracteres y termina en un fin de frase, y normalizarlo otra vez no lo cambia

#### Scenario: Modelo de compresión
- **GIVEN** `COMPRESSION_MODEL=claude-haiku-4-5` y un proveedor simulado
- **WHEN** se resume
- **THEN** la llamada estructurada recibe `claude-haiku-4-5` como modelo de la petición

#### Scenario: Error del proveedor
- **GIVEN** un proveedor que lanza `LLMProviderError`
- **WHEN** se llama al resumidor
- **THEN** la excepción llega al llamador

### Requirement: Ensamblado de la compresión
`get_memory_compression(provider, settings) -> MemoryCompression` en `app/services/memory.py` SHALL ser una dependencia de FastAPI que construye el detector (heurístico o LLM según `ANCHOR_DETECTION_MODE`), el resumidor y `max_anchors` (`MAX_ANCHORED_TURNS`). El servicio de sesiones MUST recibirla con `Depends`, de modo que los tests puedan sustituirla por un compresor determinista. Las llamadas al LLM de la memoria MUST registrarse con `llm_purpose = "conversation_summary"` o `"anchor_classifier"` y reutilizar la caché, el fallback, los reintentos y los logs de `generate_structured`, sin código nuevo en el proveedor.

#### Scenario: Compresor sustituido en los tests
- **GIVEN** la app con `get_memory_compression` sustituido por un compresor falso
- **WHEN** se envían 8 turnos a una sesión
- **THEN** el proveedor simulado de estimación solo recibe las 8 llamadas de estimación

#### Scenario: Modo heurístico por defecto
- **GIVEN** la configuración por defecto
- **WHEN** se construye la compresión
- **THEN** el detector es el heurístico y `max_anchors = 3`

