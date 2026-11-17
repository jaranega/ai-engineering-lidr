## Context

Puesta al día con el directo de la sesión 05 en la rama `session-06` (parte de `main`, que ya incluye S05). El alcance lo fija `proposal.md`: **compresión de la memoria** (anclas y resumen acumulativo) y **tier dinámico**. Actor-Critic-Boss y las evals con *golden dataset* quedan fuera.

Material de referencia:

- `AIEngineer-notas/05_Funciones_avanzadas/02_memoria-conversacional-vs-historial.md`: estrategia híbrida con anclas (§5), campos `explicit_constraints` y `rejected_options` (§2) y política de olvido 1 (§6).
- `03_prompts-adaptativos-patron-tier.md`: patrón tier y sus antipatrones (§5 "el tier que solo cambia el tono", §7.1 "el tier lo envía el frontend").
- `grabacion-y-material-sesion-5.md`, consejos del mentor:
  - 1 y 3: reglas deterministas antes que LLM;
  - 4: medir antes de resolver *edge cases*;
  - 5: anclas para proteger la información durante la compresión;
  - 6: LLM con *fallback* heurístico.

  Y la respuesta sobre prompt injection multilingüe.
- Rama oficial `session_05_live` (`estimator/`): `app/sessions/compression/{anchors,policy,summarizer}.py`, `tier_resolver.py`, `prompts/estimation/v3/system.j2` con `<audience>`. **Verificación con el código del directo (tarea 1.1)**: contrastado con `session_05_live`. Las suposiciones se confirman y ninguna diferencia cambia una decisión:
  - `AnchorDetector` usa 8 regex en inglés (`nda`, `signed_contract`, `scope_frozen`, `budget_locked`, `compliance`, `deadline_hard`, `contractual`, `explicit_commitment`) sobre el mensaje `user`; las anclas nunca se desalojan.
  - En modo `llm` el clasificador decide **siempre** (con vuelta al heurístico si falla), como suponía D5.
  - `CumulativeSummarizer` recibe `user` **y** `assistant` (`[role] content`), responde siempre en inglés (máximo 12 frases) y, si falla, conserva el resumen previo y **pierde** los turnos desalojados (D4 y D6 se desvían a propósito).
  - `to_messages()` antepone el resumen como mensaje `user` sintético `[Earlier conversation summary — the recent turns below are the live thread]` (D7 se desvía a propósito).
  - `tier_resolver.py`: reglas `nda_detected`, `regulatory_context`, `technical_audience` (≥ 2 términos distintos de `docker`, `kubernetes`, `k8s`, `microservice(s)`, `terraform`, `iac`, `helm`, `grpc`, `graphql`, `kafka`, `airflow`, `spark`, `rabbitmq`) y `low_budget_pm` (equipo ≤ 2); override `explicit_override`. NDA y normativa se buscan en la transcripción y en `agreed_scope` (y la normativa también en las tecnologías); el patrón NDA del directo acepta `confidential` suelto. Aquí se mantiene el patrón más estricto de D5 y se añaden a la lista técnica de D9 los términos del directo que faltaban (`iac`, `helm`, `grpc`, `airflow`, `spark`, `rabbitmq`).
  - El `<audience>` del directo va justo después de `<project_metadata>` y tiene texto por tier en inglés. Configuración: `ANCHOR_DETECTION_MODE` (`heuristic`) y `COMPRESSION_MODEL` (`gpt-4o-mini`), sin tope de anclas.

**Estado de partida** (`main` tras archivar `s05-add-sessions-memory-and-attachments`):

- `app/sessions/models.py`:
  - `Turn(number, user, assistant)`;
  - `ConversationHistory(max_turns)`, una `deque` de turnos. `append` descarta los más antiguos y **los pierde**. `to_messages()` devuelve los pares de la ventana;
  - `ProjectMetadata` (`frozen`) con 4 campos;
  - `Session` con `lock`, `language` y `turn_count`.
- `app/sessions/metadata.py`: `update_project_metadata` (puro) y `merge_technologies`.
- `app/services/session_service.py`: `SessionService.estimate`. Bajo el lock de la sesión: extracción → `estimate_turn` → y solo con éxito, `append`, metadata, idioma y contadores.
- `app/services/llm_service.py`:
  - `EstimationService.estimate_turn(...)`, sin estado. Llama a `render_session_prompt` y después a `_generate`;
  - `LLMProvider.generate_structured` (Instructor, fallback del Router, reintentos);
  - `CachedProvider`, cuya clave lleva system, mensajes, modelo y `_schema_id`.
- `app/estimation/schema.py`: `EstimationResult` con `technologies` tras `confidence_pct` y normalizadores sin reintento.
- `app/estimation/examples.py`: `example_payloads(..., include_technologies, include_phase_assumptions)` con `exclude_defaults=True`. Un campo nuevo con valor por defecto `[]` **no aparece** en los JSON de `v3`/`v4`.
- `app/prompts/loader.py`:
  - `SessionPromptVersion = Literal["v4"]`;
  - un `Environment` por versión con raíz en `estimation/<v>/`;
  - `render_session_prompt(turn, version, *, attachments, project_metadata, response_language, num_examples)`.
- `app/prompts/estimation/v4/`: `system.j2`, `user.j2`, `examples.j2` y el parcial `project_metadata.j2` (4 campos). El bloque dinámico va tras los ejemplos y antes de `## Reglas` (S05 D5).
- `app/guardrails/input.py`: la regla `prompt_tag` bloquea las etiquetas propias del prompt (incluidas `transcript`, `attachments` y `project_metadata`).
- `tests/conftest.py`: `FakeProvider.generate_structured` registra **todas** las llamadas en `calls` y valida un único *payload* contra el `response_model` que recibe.
- Medición de S05 (D14): unos 450 tokens de entrada más por turno, idioma 100 % y título conservado 100 %. El punto débil del metadata derivado era `technologies`.

**Restricciones heredadas:**

- `app/sessions` es puro: ni LiteLLM, ni Instructor, ni `app.services`, ni `app.prompts`. Solo `app/services` y `app/config.py` importan LiteLLM/Instructor.
- Los routers no tienen lógica.
- Nunca se registran transcripciones, prompts, respuestas, resúmenes, valores del metadata ni keys.
- Una versión publicada no se edita: `v3` y `v4` quedan igual, byte a byte, con test dorado.
- S04 D14: el idioma con gpt-4o-mini es frágil. Lo que va cerca del final del system pesa más, y se vuelve a medir siempre que cambia el prompt.

## Goals / Non-Goals

**Goals:**

- Que un compromiso duradero del cliente (NDA, presupuesto cerrado, normativa, fecha innegociable…) sobreviva cuando su turno sale de la ventana: **literal** (ancla) y además **tipado** (`explicit_constraints`).
- Que el resto de lo que sale de la ventana quede en un resumen acumulativo acotado, en el idioma de la sesión.
- Que un fallo de la compresión **nunca** haga fallar el turno del usuario.
- Política de olvido 1: una tecnología descartada sale de `mentioned_technologies` y queda en `rejected_options`.
- Tier por reglas puras, explicable (regla que decide), con override explícito, aplicado en un prompt `v5` que adapta el contenido dentro de nuestro schema.
- Coste acotado: como máximo una llamada barata extra por turno (dos en modo LLM del detector), y prompt de tamaño acotado (tope de anclas y de resumen).
- Tests sin red y una medición real de ≤ 0,10 USD con umbrales y regla de parada.

**Non-Goals:**

- Actor-Critic-Boss, evals con *golden dataset*, caché semántica, persistencia y TTL de sesiones (política de olvido 2).
- Schemas de salida distintos por tier (notas 03 §2-§5). Ver D9 y Risks.
- Tier en `POST /api/v1/estimate` (D9).
- Retirar restricciones de `explicit_constraints` cuando el cliente las levanta (ver Open Questions).
- Detectar anclas o tier en el texto de los adjuntos (D5, D9).
- Cambiar el comportamiento de `v3` o `v4`.

## Decisions

### D1. Decisiones ya tomadas (no se reabren)

| # | Decisión | Alternativas descartadas |
|---|---|---|
| 1 | Detector de anclas **heurístico ES+EN** por defecto; modo **LLM** opcional que vuelve al heurístico si falla | Solo LLM (coste y no determinista); solo EN (como el directo) |
| 2 | Las anclas se conservan **literales** (pares `user` + `assistant`) fuera de la ventana, con un **tope** | Sin tope (el directo); anclas resumidas |
| 3 | **Resumen acumulativo** único por sesión, con una llamada barata, en el **idioma de la sesión**. Si falla, se conserva el anterior | Resumen siempre en inglés (el directo); resumen por turno |
| 4 | Orden de lo que recibe el LLM: system → resumen → anclas → ventana → turno nuevo | Anclas tras la ventana |
| 5 | Los adjuntos siguen como `[…]` en anclas y resumen | Conservar el texto del adjunto en las anclas |
| 6 | `ProjectMetadata` gana `explicit_constraints` y `rejected_options`, derivados de la salida estructurada. Política de olvido 1: lo descartado sale de `mentioned_technologies` | LLM extractor aparte; heurística sobre el texto |
| 7 | Tier `executive`/`pm`/`developer`/`default` por reglas ES+EN, con override explícito; tier y regla en la respuesta, en `GET` y en el log | Tier fijo por sesión; tier inferido por el LLM |
| 8 | Prompt **`v5`** nuevo (copia de `v4` + `<audience>` + campos nuevos); `v4` inmutable; `v5` por defecto en sesiones | Editar `v4` |
| 9 | API: `tier` opcional (Form) en el turno; `tier`/`tier_rule` en la respuesta; `anchors_count`, `summary_chars`, `last_tier` y `last_tier_rule` en `SessionInfo` | Endpoint aparte para el tier |
| 10 | Streamlit: selector de tier ("Automático" por defecto) y panel lateral con tier, regla, anclas y resumen | Sin cambios en la UI |
| 11 | Configuración: modo del detector, modelo de compresión y tope de anclas. Sin dependencias nuevas | — |

### D2. Paquetes y dependencias

```
app/sessions/
├── models.py              # + Anchor, ConversationHistory with anchors/summary/backlog, Session.last_tier
├── metadata.py            # + explicit_constraints / rejected_options merge, forgetting policy 1
├── tier.py                # new, pure: Tier, TierRule, TierDecision, resolve_tier
└── compression/           # new, pure
    ├── __init__.py
    ├── anchors.py         # heuristic patterns ES+EN, detect_anchor(turn) -> AnchorDecision
    └── policy.py          # compress_history(history, *, detect, summarize, max_anchors) -> CompressionReport
app/services/memory.py     # new: LLM summarizer + LLM anchor classifier + get_memory_compression
app/prompts/
├── loader.py              # + render_summary_prompt, render_anchor_prompt; v5; tier/summary args
├── estimation/v5/         # new: system, user, examples, project_metadata, conversation_summary, audience
├── conversation_summary/v1/{system,user}.j2   # new
└── anchor_classifier/v1/{system,user}.j2      # new (LLM mode only)
```

**Dependencias:**

- `sessions.compression → sessions.models`.
- `sessions.tier → {sessions.models, sessions.compression.anchors}`: reutiliza los patrones de NDA y normativa, así que hay una sola fuente.
- `prompts → sessions.{models, tier}`.
- `services.memory → {services.llm_service, prompts, sessions}`.
- `services.session_service → services.memory`.

Nada de `app/sessions` importa `services` ni `prompts`, así que no hay ciclos.

**Inyección de lo impuro.** El detector en modo LLM y el resumidor llaman al LLM. La política (`policy.py`) los recibe como *callables* (`detect`, `summarize`), así que sigue siendo pura y se prueba con funciones locales. Es el mismo patrón que `clock` en `SessionStore`.

- *Alternativa descartada*: la política dentro de `app/services` (como el `summarizer.py` del directo bajo `sessions/compression/`). Mezclaría la regla de negocio (qué se ancla, qué se resume, el tope) con la llamada al LLM, y rompería la regla de capas si el resumidor viviera en `app/sessions`.
- *Alternativa descartada*: una clase `MemoryStrategy` con jerarquía. Hoy solo hay una estrategia; dos *callables* bastan.

### D3. Modelo de datos: anclas, resumen e invariantes

```python
AnchorSource = Literal["heuristic", "llm"]


@dataclass(frozen=True)
class AnchorDecision:
    is_anchor: bool
    rules: tuple[str, ...] = ()       # heuristic rule ids ("nda", "budget_fixed", …) or ("llm",)
    source: AnchorSource = "heuristic"


@dataclass(frozen=True)
class Anchor:
    turn: Turn
    rules: tuple[str, ...]


class ConversationHistory:
    def __init__(self, max_turns: int, max_anchors: int = 0) -> None  # ValueError: max_turns < 1, max_anchors < 0
    def append(self, *, user: str, assistant: str) -> Turn             # unchanged signature
    def take_evicted(self) -> list[Turn]                               # turns that left the window, oldest first; clears
    def to_messages(self) -> list[ChatMessage]                         # anchors' pairs, then window pairs
    # state written by the compression policy (D4)
    def set_anchors(self, anchors: Sequence[Anchor]) -> None           # replaced whole (tuple)
    def set_summary(self, text: str, *, covered_turns: int) -> None
    def set_backlog(self, turns: Sequence[Turn]) -> None
    # read-only views
    turns: tuple[Turn, ...]; anchors: tuple[Anchor, ...]; summary: str | None
    summarized_turns: int; backlog: tuple[Turn, ...]; dropped_turns: int
    max_turns: int; max_anchors: int; __len__ (window only, unchanged)
```

- **`append` no cambia de firma** (lo usan los tests de S05). Los turnos expulsados de la ventana pasan a una lista interna, que `take_evicted()` entrega y vacía. Así el recorte sigue en un único punto, y el servicio decide qué hacer con lo expulsado. *Alternativa descartada*: que `append` devuelva los expulsados. Rompe la firma y mezcla dos responsabilidades.
- **Backlog**: los turnos que no son ancla y que aún no han entrado en el resumen, porque la llamada falló (D4). Tope = `max_turns`: si lo supera, se descartan los más antiguos y se cuentan en `dropped_turns`, con el evento `summary_backlog_dropped`.
- **Invariantes** (un test de propiedad los recorre con secuencias aleatorias de turnos, decisiones y fallos):
  1. **Conservación**: `len(window) + len(anchors) + summarized_turns + len(backlog) + dropped_turns == turn_count`. Cada turno está exactamente en un sitio.
  2. `len(anchors) ≤ max_anchors`. Las anclas están en orden cronológico, y todas tienen `number` menor que cualquier turno de la ventana.
  3. `to_messages()` = pares de anclas y luego pares de la ventana. Empieza por `user`, alterna (Anthropic) y no lleva system.
  4. `summary` es `None` o un texto no vacío de ≤ `SUMMARY_MAX_CHARS` caracteres.
  5. Las anclas, el backlog y el resumen se **sustituyen enteros** (tuplas y `str`). `GET` lee sin el lock y ve el estado anterior o el nuevo (S05 D2).
- **`turn_count` y `history_turns` no cambian de significado.** `history_turns` = turnos de la ventana. Los nuevos `anchors_count` y `summary_chars` completan la foto de lo que ve el modelo (D11).
- **`Session` gana `last_tier: TierDecision | None`** (D9). Solo se escribe con un turno con éxito.
- **Tope de anclas lleno → se degrada la más antigua al resumen.** Al entrar una ancla nueva con `max_anchors` ocupado, la más antigua sale de las anclas y se añade, en orden cronológico, a los turnos que se integran en el resumen de esa misma compresión (D4).
  - El resumidor recibe la instrucción de conservar **expresamente** los compromisos (D6).
  - Además, el hecho sigue tipado en `explicit_constraints` (D8).
  - *Alternativa descartada*: rechazar las anclas nuevas con el tope lleno. Los compromisos recientes suelen corregir a los antiguos ("el presupuesto sube a 50 000 €"), y perderíamos justo esos.
  - *Alternativa descartada*: desalojar la más antigua sin resumirla. Perdería el hecho en silencio.
  - *Alternativa descartada*: sin tope (el directo). El coste crece sin límite en conversaciones largas con muchos compromisos.
- **`MAX_ANCHORED_TURNS = 0`** desactiva las anclas: todo lo expulsado va al resumen.
- **Memoria**: en el peor caso, 3 anclas × (20 000 + 8 000 caracteres) + 2 000 de resumen + backlog ≈ +90 KB por sesión, unos 260 MB con 1000 sesiones (antes ~170 MB, S05 D2). Lo normal es un orden de magnitud menos. Se acepta: la LRU sigue acotándolo.

### D4. Política de compresión: dónde, cuándo y qué pasa si falla

**Dónde.** En `SessionService.estimate`, **dentro del lock y después de mutar la sesión con el turno con éxito**:

1. `append`;
2. metadata;
3. idioma;
4. `last_tier`;
5. `turn_count`;
6. compresión.

```python
def compress_history(
    history: ConversationHistory,
    *,
    detect: Callable[[Turn], AnchorDecision],
    summarize: Callable[[str | None, Sequence[Turn]], str],   # raises on failure
    max_anchors: int,
) -> CompressionReport
```

**Pasos:**

1. `evicted = history.take_evicted()`. Si está vacío y no hay backlog, termina sin llamar a nada.
2. Por cada expulsado (del más antiguo al más reciente), `detect(turn)`. Si es ancla, se añade a las anclas. Si se pasa del tope, la más antigua se degrada a `to_summarize`. Si no es ancla, va a `to_summarize`.
3. `to_summarize = backlog + to_summarize`, ordenado por `number`.
4. Si `to_summarize` no está vacío, `summarize(history.summary, to_summarize)`:
   - si va bien: `set_summary(text, covered_turns=summarized_turns + len(to_summarize))` y el backlog se vacía;
   - **si lanza cualquier excepción**: el resumen anterior se conserva, `to_summarize` pasa al backlog (con su tope) y el informe lleva `summary_error=<tipo de la excepción>`.
5. Devuelve `CompressionReport(evicted, anchored, demoted, summarized, backlog, dropped, summary_updated, summary_error, anchor_rules)`. Lo registra el servicio (D13).

**Semántica de fallos** (el turno del usuario nunca falla por la compresión):

- La política captura **`Exception`** alrededor de `summarize`. Es un paso *best-effort* y el turno ya está guardado: devolver un 500 o un 502 contradiría "turno con éxito".
- `SessionService` envuelve además `compress_history` entero en un `try/except Exception` que registra `history_compression_failed` con `error_type`. Un fallo de `detect` (p. ej. un bug del regex) deja la sesión con el turno guardado y lo expulsado en el backlog. La respuesta sigue siendo 200.
- El modo LLM del detector captura sus propios errores y vuelve al heurístico (D5). `detect` solo lanza por un bug.
- *Alternativa descartada*: conservar el resumen anterior y **perder** los turnos que no se pudieron resumir (como hace el directo). Un 502 transitorio del modelo barato borraría un turno para siempre. El backlog cuesta ~10 líneas y se reintenta en el siguiente turno.

**Cuándo (síncrono, en la petición, después del turno).**

- **Latencia**: +1-3 s con gpt-4o-mini, **solo** en los turnos que expulsan un par que no es ancla. Con la ventana por defecto, eso es desde el turno 7 y como mucho una vez por turno.
- *Alternativa descartada*: comprimir **antes** de construir los mensajes del turno siguiente (*lazy*). La latencia es la misma, pero `GET` mostraría un estado sin comprimir entre turnos, y un fallo del resumen ocurriría en la petición del turno siguiente, mezclado con su ciclo de vida.
- *Alternativa descartada*: `BackgroundTasks` de FastAPI tras enviar la respuesta. La compresión necesita el lock de la sesión. Si lo vuelve a tomar en segundo plano, compite con el turno siguiente (409 o espera). Si no, hay carreras sobre el historial. Además, los tests con `ASGITransport` tendrían que esperar tareas de fondo. Ganar 1-3 s no compensa esa complejidad en un ejercicio con un worker.

**Coste.**

- Resumen: system ~300 tokens + resumen previo ≤ ~500 + el `user` expulsado (~300 de media, ≤ 5 k), y salida ≤ ~500. Unos 0,0003 USD por compresión con gpt-4o-mini.
- El modo LLM del detector añade ~400 tokens por par expulsado sin regla heurística.

**Caché.**

- El resumen pasa por `CachedProvider.generate_structured` (clave con system, mensajes y `_schema_id(ConversationSummary)`). Dos sesiones idénticas comparten resumen, lo cual es correcto, y un fallo nunca se guarda.
- En la estimación, el resumen entra en el **system** (D7) y por tanto en la clave. Como lo genera el LLM, a partir del turno 7 los aciertos entre sesiones serán aún más raros (ya lo eran: la clave lleva toda la conversación).
- Se acepta: la caché exact-match nunca fue útil en conversaciones largas, y la semántica está fuera de alcance.

### D5. Detector de anclas (`app/sessions/compression/anchors.py`)

**Sobre qué texto.** Sobre el **`user` guardado** del turno (el mensaje renderizado, con los adjuntos como `[…]`), nunca sobre el `assistant`:

- El Markdown del estimador escribe por su cuenta "riesgo: cumplimiento RGPD" o "presupuesto ajustado". Detectar ahí daría falsos positivos.
- Se ancla lo que se puede conservar: si el compromiso estaba solo en un PDF, el ancla guardaría `[…]` y no serviría. Ese hecho sobrevive por `explicit_constraints` (D8).

**Reglas heurísticas**, con sesgo a **pocos falsos positivos**:

- Cada regla exige un término del compromiso **más** un calificador.
- Las siglas se buscan con distinción de mayúsculas y límites de palabra.
- El resto, sin distinguir mayúsculas y con acentos opcionales (`l[ií]mite`).

Patrones iniciales (los ajusta `ai-engineer` con el corpus de D15):

| Regla | ES | EN |
|---|---|---|
| `nda` | `\bNDA\b` (mayúsculas); `acuerdo de (confidencialidad\|no divulgaci[oó]n)`; `cl[aá]usula de confidencialidad` | `\bNDA\b`; `non-disclosure( agreement)?`; `confidentiality (agreement\|clause)` |
| `contract_signed` | `(hemos\|han\|ha\|ya) firmado (el\|un\|los) contratos?`; `contrato (ya )?firmado`; `firmamos (el\|un) contrato` | `(we\|we've\|we have\|they\|they've\|client has) signed (the\|a\|an) (contract\|agreement\|SOW\|statement of work)`; `(contract\|SOW) (is\|was\|has been) signed` |
| `scope_frozen` | `alcance (cerrado\|congelado\|fijo\|definitivo)`; `congel(a\|amos\|ar) el alcance` | `scope (is \|was \|has been )?(frozen\|locked\|fixed\|final)`; `(freeze\|lock) the scope`; `scope freeze` |
| `budget_fixed` | `presupuesto (cerrado\|fijo\|m[aá]ximo\|tope\|innegociable)`; `presupuesto (es\|est[aá]) (cerrado\|limitado a)`; `no (podemos\|puede\|pueden) (pasar\|superar\|exceder) (de )?(los )?\d`; `tope de \d` | `(fixed\|capped\|hard\|maximum\|max\.?) budget`; `budget (is \|of )?(capped\|fixed\|limited)( at\| to)?`; `budget cap`; `(cannot\|can't\|must not) exceed \S*\d`; `not to exceed` |
| `regulation` | `\b(RGPD\|LOPDGDD\|LOPD\|ENS)\b` (mayúsculas) | `\b(GDPR\|HIPAA\|PCI[- ]?DSS\|SOC ?2\|SOX\|NIS ?2\|DORA)\b` (mayúsculas); `ISO[ /-]?27001` |
| `hard_deadline` | `fecha (l[ií]mite\|tope) (innegociable\|fija\|cerrada)`; `(tiene\|tenemos\|debe\|debemos) que estar (listo\|lista\|en producci[oó]n\|publicad[oa]) (antes de(l)?\|para (el\|la)) `; `\binnegociable\b` | `hard deadline`; `(must\|has to\|needs to) (go live\|launch\|be live\|ship\|be ready) (by\|before)`; `non-negotiable`; `no later than` |
| `client_commitment` | `(nos )?comprometemos a`; `queda acordado`; `(hemos\|han) acordado`; `acordamos que`; `requisito (obligatorio\|imprescindible)` | `we (have )?agreed (that\|to\|on)`; `(it's\|it is) agreed`; `we commit to`; `mandatory requirement` |

- La regla `regulation` acepta la mención sola de la norma. Una norma citada por el cliente casi siempre condiciona el proyecto, y "no aplica el RGPD" es raro. Se mide (D16).
- `detect_anchor(turn) -> AnchorDecision` devuelve **todas** las reglas que casan (para el log), en el orden de la tabla.
- **Relación con lo que ya existe**: `agreed_scope` es el `summary` de la última estimación, no un compromiso del cliente, y no sustituye a las anclas. `explicit_constraints` (D8) es la versión **tipada** del mismo hecho; el ancla es la versión **literal**, con su contexto. Son complementarias: si el modelo omite una restricción en `explicit_constraints`, el ancla la conserva, y viceversa con los adjuntos.
- **Corpus de negativos** (tests): "presupuesto aproximado", "¿cuál sería el presupuesto?", "confidence level", "AGENDA", "módulo para firmar contratos online", "users sign contracts in the app", "fecha de entrega estimada", "ISO 8601", "la app debe estar en inglés", "we agreed it would be nice" (este último sí casa con `client_commitment`: se documenta como falso positivo aceptado si el corpus lo confirma), más las muestras de `samples/` (ninguna debería anclar salvo las que contengan compromisos; se fija la lista esperada).

**Modo LLM** (`ANCHOR_DETECTION_MODE=llm`, en `app/services/memory.py`):

1. Primero el heurístico. **Si casa, es ancla sin llamar al LLM** (precisión alta y coste cero).
2. Si no casa, `generate_structured` con `AnchorVerdict(is_anchor: bool, category: Literal[<reglas>] | None)` y el prompt versionado `anchor_classifier/v1/`. Clasificador binario con la definición de "compromiso duradero" y ejemplos ES/EN breves. Resultado: `AnchorDecision(True, ("llm",), "llm")` o `False`.
3. Ante cualquier error del LLM: el resultado heurístico (negativo), `anchor_classifier_failed` con `error_type`, y el turno sigue.

- *Alternativa descartada*: que el LLM decida siempre, también contra un positivo heurístico (el directo, según la descripción). Pagaría por los casos claros y podría desanclar un "NDA firmado" evidente.
- `generate_structured` reutiliza el fallback, la caché, los reintentos y los logs. Sin código nuevo en el proveedor.

### D6. Resumidor (`app/services/memory.py` + `app/prompts/conversation_summary/v1/`)

```python
SUMMARY_MAX_CHARS = 2000     # constant, ~500 tokens


class ConversationSummary(BaseModel):
    """Cumulative summary of earlier conversation turns."""
    summary: str = Field(description=...)      # normalizer below, no retry

class ConversationSummarizer:
    def __init__(self, provider: LLMProvider, target: Deployment | None) -> None
    def __call__(self, previous: str | None, turns: Sequence[Turn], language: Language | None) -> str
```

**Ubicación.** El modelo de respuesta y la llamada viven en `app/services/memory.py`, porque llama al LLM (capas). La plantilla vive en `app/prompts/conversation_summary/v1/{system,user}.j2`, versionada e inmutable como las de estimación.

- `render_summary_prompt(previous, turns, language) -> (system, user)` en `loader.py`, con su propio `Environment` y raíz en `conversation_summary/<v>/`.
- `SUMMARY_PROMPT_VERSION = "v1"` es una constante, no un parámetro de la API.
- `PUBLISHED_PROMPT_VERSIONS` sigue refiriéndose solo a `estimation/`. Un test equivalente comprueba que `conversation_summary/` contiene solo `v1` (y lo mismo con `anchor_classifier/`).

**Salida estructurada** (Instructor, un campo) en vez de texto (`generate`):

- reutiliza la clave estructurada de la caché, el fallback y los logs `structured`;
- Instructor elimina los preámbulos ("Here is the summary:");
- es lo que anticipaba S05 D15 ("el resumidor puede ser `generate_structured` con otro `response_model`").

*Alternativa descartada*: `generate` en modo texto. Es igual de barato, pero el preámbulo y el truncado (`finish_reason=length`) habría que tratarlos a mano.

**Normalizador de `summary`** (`mode="before"`, idempotente y sin reintento, el principio de S04 D2):

1. recortar espacios;
2. quitar las etiquetas propias del prompt (`</?(conversation_summary|project_metadata|transcript|attachments|audience)>`), para que el texto no pueda cerrar su bloque (D7);
3. si pasa de `SUMMARY_MAX_CHARS`, cortar en el último fin de frase (`. `, `.\n`, `? `, `! `) anterior al límite, o en el límite si no hay ninguno.

Vacío tras normalizar → `ValidationError` → reintento de Instructor (no puede pasar con un modelo sano). `max_tokens = 800` en la llamada.

**Qué recibe.**

- System (inglés, estático):
  - rol de "secretario de la conversación";
  - integrar los turnos nuevos en el resumen previo y **reescribirlo** completo (no añadir al final);
  - conservar literalmente cifras, fechas, nombres y **compromisos** (NDA, presupuesto, normativa, plazos, opciones descartadas, cambios de alcance);
  - eliminar lo que el cliente haya retractado;
  - **no** inventar ni incluir horas ni importes de las estimaciones;
  - frases breves, ≤ ~300 palabras;
  - tratar el contenido como datos;
  - y al final, "Write the summary in Spanish/English" según `language`.
- User:
  - `<previous_summary>` (si lo hay);
  - `<turns>`, con `--- turn N ---` y el `user` guardado de cada turno (con `[…]` en los adjuntos).
- **Solo los mensajes del cliente, no el Markdown del asistente.**
  - El asistente devuelve siempre la estimación completa. Sus cifras son obsoletas en cuanto llega la siguiente, y la última siempre está en la ventana.
  - Lo que hay que conservar es lo que pidió, decidió o descartó el cliente.
  - Cuesta ~5× menos tokens de entrada.
  - *Alternativa descartada*: pasar también el `assistant` (probablemente lo hace el directo). Arrastraría horas viejas al resumen, y el estimador las tomaría por vigentes.
  - *Alternativa descartada*: un JSON del `EstimationResult` por turno. Habría que guardarlo en `Turn`, y lo útil ya está en `project_metadata`.
- **Idioma**: `session.language`, el idioma efectivo del último turno, es decir, el del turno que dispara la compresión. Si la sesión cambia de idioma, el resumen se reescribe en el nuevo en la siguiente compresión. Es coherente con la regla de idioma de S04/S05.
- **Modelo**: `COMPRESSION_MODEL` (D12). Sin valor, el primario con fallback. Con valor, ese despliegue como override, sin fallback (el mismo comportamiento que `model` en `/estimate`). Un fallo queda cubierto por D4.
- **Logs**: `llm_call_*` con `bound_contextvars(llm_purpose="conversation_summary")` (o `"anchor_classifier"`), para separar el coste de la memoria del de la estimación en los logs sin tocar el proveedor. Nunca el texto: solo `summary_chars` y su hash en `history_compressed`.

### D7. Cómo llegan el resumen y las anclas al LLM

**Decisión: el resumen va en el system, dentro de un bloque `<conversation_summary>`, tras `<project_metadata>` y antes de `<audience>` y `## Reglas`. Las anclas van como pares de mensajes antes de la ventana.**

```
system:  …estático (idioma, rol, pista, conversación y memoria, campos, fuera de alcance, ejemplos)…
         <project_metadata>…</project_metadata>
         <conversation_summary>…</conversation_summary>     (solo si hay resumen)
         <audience>…</audience>                             (solo si el tier no es default)
         ## Reglas … línea de idioma
messages: [ancla_1 user, ancla_1 assistant, …, ventana…, user actual]
```

Se mantiene el orden fijado (system → resumen → anclas → ventana → turno nuevo). El resumen es lo último del system, inmediatamente antes del primer mensaje.

**Por qué en el system y no como mensaje `user` sintético** (lo que hace el directo, `[Earlier conversation summary…]`):

1. **Alternancia**. Un `user` sintético seguido del `user` de la primera ancla o de la ventana son dos `user` seguidos. OpenAI los acepta, y Anthropic los fusiona o exige alternancia según la versión. La salida habitual es inventar un `assistant` de relleno ("Entendido"), que el modelo puede imitar. En el system no hay nada que alternar.
2. **Memoria frente a historial** (notas 02 §1 y la pregunta de clase "¿el system forma parte de la memoria?"). El resumen es memoria destilada, como `<project_metadata>`, y la memoria se inyecta regenerada en el system. Los mensajes son historial literal: anclas y ventana.
3. **Equilibrio de idioma (S04 D14)**. El resumen está en el idioma de la sesión, y cerca del final del system empuja a favor, como el metadata. Un `user` sintético con un marcador en inglés ("[Earlier conversation summary]") en una sesión en español añadiría señal en inglés justo al principio de los mensajes.
4. **Prefijo estable**. El bloque va tras los ejemplos, así que el prefijo cacheable de OpenAI (secciones estáticas y ejemplos, ~6 k tokens) no cambia al actualizarse el resumen. Como mensaje, rompería el prefijo de **mensajes**; en el system también rompe a partir de su posición, pero esa parte ya era dinámica por el metadata.
5. **Contrato de mensajes**: la fórmula de la spec `conversation-sessions` pasa a `2 × (anclas + min(k − 1, N)) + 1`. Un mensaje sintético añadiría `+1` condicional y una excepción a "empieza por `user` y alterna".

**Costes de la decisión:**

- Texto generado por el LLM a partir de entradas del usuario entra en el system. Es un vector de inyección "blanqueada": una instrucción que pasó los guardrails podría reaparecer parafraseada. Mitigaciones:
  - el regex de entrada ya bloqueó las inyecciones conocidas;
  - la regla "datos como datos" se amplía a `<conversation_summary>`;
  - el normalizador quita las etiquetas propias (D6);
  - la regla `prompt_tag` del guardrail añade `conversation_summary` y `audience` (también afecta a `/estimate`, con riesgo de falso positivo despreciable);
  - el tope de 2000 caracteres.

  Ver Risks.
- Desviación del directo: Open Questions 1.

**Anclas como mensajes** (fijado en D1-2). Son pares literales (`user` con `[…]` y el Markdown del `assistant`) en orden cronológico antes de la ventana, así que se conservan la alternancia y la cronología.

- *Alternativa descartada*: las anclas como un bloque `<anchored_turns>` en el system con solo el texto del cliente. Sería más barato (sin estimaciones viejas), pero contradice el "literal, pares" de la propuesta y metería más texto del usuario en el system.
- El Markdown viejo de un ancla puede llevar cifras obsoletas. `v5` dice que la estimación vigente es la del último turno de la ventana (D10).

**Precedencia que fija `v5`**: el mensaje actual > la ventana > las anclas y `<project_metadata>` > `<conversation_summary>`. Lo literal y lo tipado mandan sobre la paráfrasis.

**`v4` con compresión.** Una sesión que pide `prompt_version=v4` también comprime (el estado es de la sesión), pero la plantilla `v4` no pinta el resumen: solo ve las anclas, como mensajes. Se documenta en la descripción del parámetro. `v4` sigue existiendo para comparar.

- **Etiquetas de los prompts de memoria** (decidido al implementar): `prompt_tag` bloquea también `<previous_summary>`, `<turns>` y `<client_message>` (y sus cierres), para que un mensaje del cliente no pueda cerrar el bloque de datos del resumidor ni del clasificador.

### D8. Schema y metadata: `explicit_constraints` y `rejected_options`

**`EstimationResult`** (`app/estimation/schema.py`). **Orden**: `language, title, summary, confidence_pct, explicit_constraints, rejected_options, technologies, rows, …`

- Tras `confidence_pct`, para no retrasar la decisión de fuera de alcance (S04 D2).
- `explicit_constraints` antes que todo el desglose: el presupuesto, el plazo o la normativa condicionan las filas.
- `rejected_options` **antes** de `technologies`: el modelo, que escribe de arriba abajo, se compromete con lo descartado antes de listar el stack, y así no mete en `technologies` lo que acaba de descartar. El código lo garantiza igualmente (abajo).
- *Alternativa descartada*: los dos al final. Serían un resumen *a posteriori*, sin efecto sobre las filas.

```python
MAX_CONSTRAINTS = 8  # per turn
MAX_CONSTRAINT_CHARS = 200
MAX_REJECTED_OPTIONS = 10  # per turn
# names: TechName (1..60 chars), as `technologies`

explicit_constraints: list[ConstraintText] = Field(
    default=[],
    max_length=MAX_CONSTRAINTS,
    description="Binding constraints the client stated explicitly in any turn or attachment "
    "and that are still in force (fixed budget, deadline, regulation, NDA, mandated platform "
    "or hosting, team limits), including those of project_metadata; one short phrase each, "
    "in the client's language, keeping figures and dates; empty if none. Not your assumptions.",
)
rejected_options: list[TechName] = Field(
    default=[],
    max_length=MAX_REJECTED_OPTIONS,
    description="Technologies, vendors or platforms the client explicitly discarded in any "
    "turn, written exactly as in technologies or mentioned_technologies; empty if none.",
)
```

- **Normalizadores** (`mode="before"`, sin reintento e idempotentes, como `_clean_technologies`): recortar, quitar vacíos y demasiado largos, deduplicar por `casefold` conservando la primera grafía y truncar al tope. `_clean_technologies` se generaliza con parámetros de tope y longitud. Un campo secundario nunca justifica una llamada de pago (S05 D4).
- **Valores por defecto `[]`**: el JSON Schema no los marca como obligatorios.
- `ComputedEstimate` gana los dos campos, copiados tal cual (también fuera de alcance). El Markdown **no** los pinta: las plantillas de formato y la evaluación no cambian. `/estimate` los devuelve en `result`, un cambio aditivo.

**`v3` y `v4` no cambian ni un byte:**

- `example_payloads` usa `exclude_defaults=True`. Los datos de los ejemplos no traen los campos nuevos, o los traen vacíos, así que no aparecen en los JSON de `v3`/`v4`. Por seguridad, se añaden explícitamente al `exclude` salvo con `include_memory_fields=True`, que solo pasa `v5`.
- En `v5` se insertan **siempre** tras `confidence_pct`, en el orden del schema (también `[]`, que enseña a dejarlos vacíos), generalizando `_with_technologies` a `_with_list_fields`.
- `app/context/examples.py` gana `explicit_constraints` y `rejected_options` por ejemplo, escritos a mano por `ai-engineer` a partir de `meeting_summary`. Al menos uno no vacío, para que el *few-shot* enseñe el formato (p. ej. "Presupuesto máximo de 30 000 €"), y uno vacío.
- **Dorados**: el test de `v3` existente, más un **dorado nuevo de `v4`** (sha256 de system y user para 4 combinaciones, con y sin metadata y adjuntos), calculados **antes** de tocar el código (Migration 2).
- La herramienta (*tool definition*) sí cambia en todas las versiones, porque el schema es compartido. En `v3`/`v4` el modelo puede rellenar los campos sin instrucciones. En `/estimate` aparecen en `result` sin más efecto. En sesiones `v4`, se acumulan en el metadata aunque `v4` no los pinte. Se documenta.
- **Caché**: `_schema_id` cambia, y todas las entradas estructuradas se invalidan una vez. Se acepta (S04 D7, S05 D4).

**`ProjectMetadata`** gana, al final y en este orden:

```python
explicit_constraints: list[str] = []  # accumulated, max 15
rejected_options: list[str] = []  # accumulated, max 20
```

**Reglas de `update_project_metadata`** (pura; `merge_unique(current, new, limit)` es el `merge_technologies` renombrado y genérico, y el nombre antiguo queda como alias). El orden de aplicación importa y se fija:

| Paso | Campo | Regla |
|---|---|---|
| 1 | `explicit_constraints` | `merge_unique(current, new, 15)`: en orden, sin duplicados por `casefold`; si pasa de 15, fuera las más antiguas |
| 2 | `rejected_options` | `merge_unique(current, new, 20)` |
| 3 | `rejected_options` | **Readopción**: se quitan los nombres que están en `estimate.technologies` y **no** en `estimate.rejected_options` del mismo turno. El cliente ha vuelto a una opción descartada, y el turno actual manda |
| 4 | `mentioned_technologies` | `merge_unique(current, estimate.technologies, 30)` y después se quita **cualquier** nombre de `rejected_options` (comparación `casefold` exacta tras recortar). Es la **política de olvido 1** |
| — | resto | sin cambios (S05 D4) |
| — | fuera de alcance | no se toca nada (S05 D4) |

- **Comparación exacta, no por subcadena.** "app Android" descartada no debe borrar "Android" si sigue la versión tablet. La descripción del campo pide escribir el nombre tal como aparece en `technologies`. *Alternativa descartada*: comparar por tokens o subcadenas, que borra de más sin avisar.
- **Restricciones acumuladas, no sustituidas** (fijado en el brief). Consecuencia: una restricción levantada ("ya no hay fecha límite") sigue en el metadata. `v5` dice que el mensaje actual prevalece sobre `<project_metadata>`. Retirarlas es Open Questions 4.

### D9. Tier (`app/sessions/tier.py`, puro)

```python
class Tier(StrEnum):
    EXECUTIVE = "executive"; PM = "pm"; DEVELOPER = "developer"; DEFAULT = "default"

TierRule = Literal["explicit", "nda_detected", "regulatory_context", "technical_audience",
                   "small_team", "default", "prompt_version"]

@dataclass(frozen=True)
class TierDecision:
    tier: Tier
    rule: TierRule

def resolve_tier(transcript: str, metadata: ProjectMetadata, explicit: Tier | None) -> TierDecision
```

**Cadena de reglas** (la primera que casa gana; es una función pura, sin LLM, y se recalcula en cada turno, como recomienda la clase para perfiles mixtos):

| # | Regla | Tier | Condición |
|---|---|---|---|
| 0 | `explicit` | el pedido | `explicit is not None` (incluido `default`, que fuerza "sin adaptación") |
| 1 | `nda_detected` | `executive` | patrones `nda` de D5 en la transcripción **o** en algún elemento de `metadata.explicit_constraints` |
| 2 | `regulatory_context` | `executive` | patrones `regulation` de D5, en las mismas fuentes |
| 3 | `technical_audience` | `developer` | ≥ 2 términos técnicos **distintos** en la transcripción del turno (lista abajo) |
| 4 | `small_team` | `pm` | `metadata.assumed_team_size` no es nulo y es ≤ 2 |
| 5 | `default` | `default` | ninguna |

- **Fuentes**: la transcripción del turno (no los adjuntos, por coherencia con D5 y con la detección de idioma) y el metadata acumulado. `explicit_constraints` hace que un NDA del turno 2 siga contando en el turno 9 sin releer el historial, así que el tier no oscila al salir el NDA de la ventana. *Alternativa descartada*: solo la transcripción (como el directo, según la descripción). El tier saltaría de `executive` a `default` en cuanto el cliente deja de nombrar el NDA.
- **Términos técnicos** (ES+EN, sin distinguir mayúsculas, con límites de palabra; cuentan los distintos por grupo): `api`/`REST` en un mismo grupo (`REST` solo en mayúsculas, `RESTful` o junto a "api", para no contar "the rest of the app"; "REST API" cuenta una vez), `graphql`, `endpoint`, `webhook`, `sdk`, `microservicio(s)`/`microservice(s)`, `kubernetes`/`k8s`, `docker`, `ci/cd`, `terraform`, `serverless`, `lambda`, `oauth`, `jwt`, `orm`, `backend`, `frontend`, `postgres(ql)`, `mysql`, `mongodb`, `redis`, `kafka`, `typescript`, `latencia`/`latency`, `throughput`, `escalado horizontal`/`horizontal scaling`, `esquema de (la )?base de datos`/`database schema`, `refactor(izar)`, `iac`, `helm`, `grpc`, `airflow`, `spark`, `rabbitmq` (estos seis, del directo).
  - No cuentan los nombres de producto que usa cualquier cliente (Stripe, iOS, Shopify), ni "app", "web" o "datos".
  - El umbral de 2 es el del directo, y D16 mide cuántas muestras de `samples/` caen en `developer`. Si son casi todas, se sube a 3 (Open Questions 6).
- **`small_team`**: `assumed_team_size` es el equipo que **recomendamos**, no un dato del cliente. La regla dice "proyecto pequeño, lo lleva un PM o el propio cliente". Se renombra respecto a `low_budget_pm` del directo porque no mira el presupuesto. En el primer turno el metadata está vacío y no aplica.
- **Explicabilidad**: `rule` siempre acompaña al tier (respuesta, `GET`, log). No se devuelve el fragmento que casó (es texto del usuario).
- **Dónde se resuelve**: en `SessionService.estimate`, dentro del lock y **antes** de `estimate_turn`, con el metadata actual. El tier se pasa como dato a `estimate_turn(..., tier=decision.tier)`. Así `estimate_turn` sigue sin estado (S05 D6). Con éxito, `session.last_tier = decision`.
- **Con `prompt_version=v4`**: `v4` no tiene `<audience>`. Sin `tier` explícito, la decisión es `TierDecision(default, "prompt_version")`, sin resolver reglas, para no informar de un tier que no se ha aplicado. Con `tier` explícito y `v4`, **422** en `["body", "tier"]` ("requires prompt_version v5"). Ver Open Questions 3.
- **No en `/api/v1/estimate`**:
  - `/estimate` usa `v3`, que es inmutable y no tiene `<audience>`; añadirlo exigiría una `v6` de un disparo;
  - el tier dinámico se alimenta de la conversación (metadata acumulado), que `/estimate` no tiene;
  - la UI ya no usa `/estimate`.

  Si se quiere en un disparo, es un change propio.
- **Antipatrón "el tier lo envía el frontend" (notas 03 §7.1).** El override por Form es exactamente eso. Aquí no da acceso a nada: todos los tiers usan el mismo modelo, coste y pipeline, y el tier es una preferencia de presentación, no un permiso. Cuando haya autenticación, el valor por defecto vendrá de un *claim* y el override quedará como preferencia del usuario. Se documenta en Risks.
- **Antipatrón "el tier que solo cambia el tono" (§5).** Con un schema único, `v5` no cambia la **forma** del objeto, pero sí **qué contenido** va en cada campo: el criterio de las filas, la selección y el orden de los riesgos, y el foco del `summary` y de la narrativa (D10). Schemas por tier romperían el pipeline común (cálculo, render, metadata, evaluación) y son el siguiente paso cuando haya evals que lo justifiquen (§4.3: "cada tier nuevo exige evaluación"). Ver Risks y Open Questions 5.

### D10. Prompt `v5`

`app/prompts/estimation/v5/{system,user,examples,project_metadata,conversation_summary,audience}.j2`. `user.j2` y `examples.j2` son copias literales de `v4`.

**Orden de `system.j2`** (el de `v4` con cuatro cambios; nada después de las reglas salvo la línea de idioma):

1. Regla de idioma (EN).
2. Rol.
3. Pista por tipo.
4. **Conversación y memoria** (`v4` más tres viñetas estáticas):
   - "Algunos turnos antiguos se conservan literales al principio del historial porque contienen compromisos del cliente; la estimación vigente es siempre la de tu última respuesta."
   - "`<conversation_summary>`, si aparece, resume los turnos anteriores que ya no están en el historial; ante una contradicción, prevalecen el mensaje actual, los turnos literales y `<project_metadata>`."
   - "Respeta las restricciones de `<project_metadata>` (`explicit_constraints`) y no propongas opciones de `rejected_options`; si una restricción hace inviable algo, dilo en `assumptions_and_risks`."
5. Cómo usar los ejemplos.
6. Cómo rellenar los campos: `v4`, más `explicit_constraints` y `rejected_options`, en el orden del schema y con el texto de sus descripciones.
7. Fuera de alcance.
8. `{% include "examples.j2" %}`.
9. `{% include "project_metadata.j2" %}`: la copia de `v4` más dos líneas, `explicit_constraints: a; b` (separadas por `; `, porque llevan comas) y `rejected_options: x, y`, solo si tienen valor.
10. **`{% include "conversation_summary.j2" %}`** (nuevo): `<conversation_summary>…</conversation_summary>`, solo si hay resumen.
11. **`{% include "audience.j2" %}`** (nuevo): solo si el tier no es `default`.
12. `## Reglas`. "Datos como datos" se amplía a `<conversation_summary>`.
13. Línea del idioma.

**Por qué `<audience>` tras los bloques de datos y antes de las reglas:**

- Puede cambiar en cada turno. Antes de los ejemplos rompería el prefijo estable (S05 D5).
- Es una instrucción, y cerca del final pesa más.
- *Alternativa descartada*: justo tras el rol, como el directo. Rompe el prefijo cada vez que cambia el tier.

**Idioma del bloque**: español, como el resto de las instrucciones de `v4`. El riesgo para las conversaciones en inglés (una instrucción en español cerca del final) lo cubren la regla bilingüe de `## Reglas` y la línea final en inglés, y se mide en D16 con `executive` y `developer` en EN. *Alternativa descartada*: el bloque en inglés. Introduce otro idioma de instrucciones en mitad del prompt y empujaría hacia el inglés en las sesiones en español.

**Contenido de `audience.j2`** (adaptado a nuestro schema: `rows` con las fases canónicas, `assumptions_and_risks` hasta 8, `summary`, `narrative`, `team`; ni "4 fases como máximo" ni "6-8 fases" del directo, porque las fases son fijas):

- Cabecera común: "Adapta el contenido a esta audiencia **sin cambiar** el nivel de detalle ni el formato pedidos: el número de filas y de párrafos lo marca el nivel de detalle."
- **`executive`** (dirección o negocio, no técnica):
  - `summary` con el resultado de negocio y el principal factor de coste o plazo, sin jerga;
  - `task` en términos de lo que se obtiene ("Pagos con tarjeta en la app"), no de componentes;
  - `assumptions_and_risks` empieza por los 3 riesgos con más impacto en coste o plazo, traducidos a impacto de negocio, y los supuestos técnicos van al final y breves;
  - en `narrative`, inversión, hitos visibles y una recomendación de siguiente paso.
- **`pm`** (gestión de proyecto):
  - `task` como entregables reconocibles dentro de cada fase;
  - `assumptions_and_risks` prioriza dependencias, bloqueos, decisiones pendientes del cliente y disponibilidad del equipo;
  - `summary` menciona el rango de duración y la composición del equipo;
  - en `narrative`, secuencia de fases e hitos.
- **`developer`** (audiencia técnica):
  - `task` por componente técnico (backend, frontend, integraciones, infraestructura), con el detalle técnico entre paréntesis;
  - `assumptions_and_risks` incluye los supuestos de stack y las fuentes de incertidumbre que mueven la estimación más de un 20 %;
  - vocabulario técnico sin traducir;
  - en `narrative`, decisiones de arquitectura.

**Loader:**

- `SessionPromptVersion = Literal["v4", "v5"]` y `DEFAULT_SESSION_PROMPT_VERSION = "v5"`.
- `render_session_prompt` gana `conversation_summary: str | None = None` y `tier: Tier = Tier.DEFAULT`. `v4` los ignora: las variables de más no afectan con `StrictUndefined`, y el dorado lo garantiza.
- `example_payloads(..., include_memory_fields=version == "v5")`.
- `prompt_rendered` añade `tier` y `summary_chars`.
- `PUBLISHED_PROMPT_VERSIONS` = `("v3", "v4", "v5")` = carpetas.

### D11. Contrato HTTP

**`POST /api/v1/sessions/{id}/estimate`:**

- Form `tier: Annotated[Tier | None, Form(description="Audience of the estimate; omit for automatic resolution. Only with prompt_version v5.")] = None`. Un valor fuera del enum da 422 de FastAPI. Con `v4`, 422 (D9).
- Query `prompt_version: SessionPromptVersion = "v5"`.
- `SessionEstimateResponse` gana `tier: Tier` y `tier_rule: TierRule`, que son los del turno.

**`SessionInfo`** gana:

- `anchors_count: int`;
- `summary_chars: int`: 0 sin resumen. Se expone la longitud, no el texto: el resumen es una paráfrasis del cliente generada por el LLM, y la UI no lo necesita para el panel;
- `last_tier: Tier | None`;
- `last_tier_rule: TierRule | None`.

`project_metadata` ya lleva los dos campos nuevos.

- `Tier` y `TierRule` se reexportan desde `app.schemas` (el router no importa `app.sessions.tier` directamente). OpenAPI documenta el enum y el `Literal`.
- `SessionSnapshot` y `SessionTurnOutcome` ganan los mismos datos. El router solo copia.
- *Alternativa descartada*: devolver el texto del resumen en `GET`. Útil para depurar, pero expone contenido generado sin necesidad y engorda la respuesta en cada *rerun* de Streamlit. Ver Open Questions 7.

### D12. Configuración

| Variable | Campo | Tipo y rango | Defecto |
|---|---|---|---|
| `ANCHOR_DETECTION_MODE` | `anchor_detection_mode` | `Literal["heuristic", "llm"]` | `heuristic` |
| `COMPRESSION_MODEL` | `compression_model` | `str \| None` (vacío → `None`) | `None` = primario con fallback |
| `MAX_ANCHORED_TURNS` | `max_anchored_turns` | `int`, `0..10` (0 desactiva las anclas) | 3 |

- **`COMPRESSION_MODEL`**: se valida al arrancar con `resolve_request_model`. Un modelo no reconocido o sin su key da un error de configuración claro, como `PRIMARY_MODEL`. *Alternativa descartada*: el valor por defecto `gpt-4o-mini` fijo. Con el primario en Anthropic exigiría la key de OpenAI. Que vacío signifique "el primario" sigue la configuración del usuario.
- **3 anclas por defecto**: unos 3 × 1,8 k tokens en el caso típico. Cubren "NDA + presupuesto + plazo" sin degradar.
- **`SUMMARY_MAX_CHARS = 2000`** es una **constante**, no configuración: no hay un caso de uso para cambiarla, y otro parámetro añade combinaciones que probar.
- `get_session_store()` pasa `max_anchors`. `get_memory_compression(provider, settings) -> MemoryCompression(detect, summarizer, max_anchors)` es una **dependencia de FastAPI aparte** (`app/services/memory.py`). `get_session_service` la recibe con `Depends`.
- `.env.example`, `CONFIG_ENV_VARS` del `conftest`, el README y la spec `app-config` se actualizan.

### D13. Observabilidad (sin contenido)

| Evento | Campos |
|---|---|
| `anchor_detected` | `turn`, `rules` (ids), `source` (`heuristic`/`llm`) |
| `anchor_demoted` | `turn`, `anchors` |
| `anchor_classifier_failed` | `turn`, `error_type` |
| `history_compressed` | `evicted`, `anchored`, `demoted`, `summarized`, `backlog`, `dropped`, `anchors`, `summary_updated`, `summary_chars`, `summary_sha256` (12), `latency_ms` |
| `summary_failed` | `error_type`, `backlog` |
| `summary_backlog_dropped` | `dropped` |
| `history_compression_failed` | `error_type` (fallo inesperado, D4) |
| `tier_resolved` | `tier`, `rule`, `explicit` (bool), `prompt_version` |
| `session_turn_completed` | S05 + `anchors`, `summary_chars`, `tier`, `tier_rule`, `constraints` y `rejected` (recuentos) |
| `llm_call_*` | S04 + `llm_purpose` vía contextvar en las llamadas de memoria |

- Todos llevan `session` (contextvar de S05 D11).
- Nunca: el fragmento que casó una regla, el texto del resumen, el texto de las restricciones u opciones, ni la transcripción.
- El test de marcadores secretos de S05 se amplía: un marcador en una transcripción que se resume (el resumidor falso lo copia al resumen) y otro en una restricción no deben aparecer en ningún evento.

### D14. Streamlit (`frontend-engineer`)

- **"Opciones avanzadas"**:
  - `SESSION_PROMPT_VERSIONS = ("v5", "v4")`, con `v5` por defecto;
  - selector **"Audiencia"** con "Automático" (por defecto, no envía `tier`), "Dirección (ejecutivo)", "Gestión (PM)", "Técnica (desarrollo)" y "Sin adaptar" (`default`);
  - con `v4` elegida, el selector se deshabilita y se muestra "Solo con v5", así que no puede dar el 422.
- **Barra lateral**, "Memoria del proyecto":
  - los 4 campos de S05;
  - **"Restricciones"** (lista) y **"Descartado"** (lista);
  - `Turnos: N · en la ventana: h/max · anclas: a · resumen: sí (c caracteres)/no`;
  - debajo, **"Audiencia del último turno: Dirección — regla: NDA detectado"**.
- **Etiquetas de regla en español** (`TIER_RULE_LABELS`): `explicit` → "elegida por ti", `nda_detected` → "NDA detectado", `regulatory_context` → "normativa detectada", `technical_audience` → "vocabulario técnico", `small_team` → "equipo pequeño", `default` → "sin señales", `prompt_version` → "v4 no adapta la audiencia".
- **En cada turno del asistente**, dentro de "Métricas y validación": `Audiencia: … (regla)`.
- **Paridad** (test): `TIERS` y `TIER_RULE_LABELS` cubren exactamente los valores de `Tier` y `TierRule` (el test sí puede importar `app`; la UI no).
- La UI sigue sin imports de `app`.

### D15. Tests (`qa-engineer`, sin red)

**Antes de tocar el código**: el dorado nuevo de `v4` (D8). El de `v3` ya existe.

**Puros:**

- **Detector**:
  - un positivo ES y otro EN por regla;
  - varias reglas a la vez (orden);
  - mayúsculas de las siglas (`nda` en minúsculas dentro de "agenda" no casa);
  - **corpus de negativos** (D5);
  - las muestras de `samples/` con la lista esperada.
- **Tier**:
  - cada regla ES y EN;
  - la precedencia (NDA + vocabulario técnico → `executive`);
  - el override gana, también `default`;
  - un NDA solo en `explicit_constraints` → `executive`;
  - 1 término técnico → no `developer`, 2 distintos → `developer`, el mismo término repetido → no;
  - `assumed_team_size` `None`, 2 y 3;
  - negativos;
  - el tier de cada muestra de `samples/` (se fija).
- **`ConversationHistory`**:
  - `take_evicted` entrega y vacía;
  - `to_messages()` = anclas y ventana, alternando;
  - `max_anchors < 0` → `ValueError`;
  - los tests de S05 siguen en verde sin cambios.
- **Política** con `detect`/`summarize` locales:
  - nada expulsado → no llama a `summarize`;
  - expulsado ancla → no resume;
  - expulsado no ancla → resume con el previo;
  - **tope**: 4 anclas con `max_anchors=3` → la 1.ª va a `summarize`, en orden cronológico;
  - `max_anchors=0`;
  - **`summarize` lanza** → el resumen anterior intacto, el turno en el backlog, `summary_error` informado;
  - **reintento**: el siguiente turno resume backlog y nuevo juntos;
  - el backlog por encima del tope → `dropped`;
  - **invariante de conservación** con un test de propiedad (secuencias aleatorias de 1-40 turnos, decisiones y fallos, con `random.Random(seed)`, sin dependencias nuevas).
- **Metadata**:
  - acumulación y tope de restricciones;
  - descartadas que retiran tecnologías (`casefold`), sin retirar por subcadena;
  - readopción;
  - el mismo nombre en `technologies` y `rejected_options` del mismo turno → descartado;
  - fuera de alcance no toca nada.
- **Schema**:
  - normalizadores de los dos campos;
  - orden en `model_json_schema()["properties"]`;
  - normalizador de `ConversationSummary` (etiquetas, corte por frase, idempotencia).

**Prompts:**

- **`v5`**:
  - sin resumen ni tier, no hay bloques `<conversation_summary>` ni `<audience>`;
  - con ellos, el orden ejemplos < metadata < resumen < audiencia < `## Reglas` < línea de idioma;
  - el prefijo hasta los ejemplos es idéntico con dos tiers y dos resúmenes distintos;
  - el texto por tier;
  - las líneas nuevas del metadata solo con valor;
  - los JSON de los ejemplos con los campos nuevos tras `confidence_pct`;
  - `v3` y `v4` sin ellos (dorados).
- **Resumen**: `render_summary_prompt` con y sin resumen previo; la línea de idioma ES/EN; solo los `user`; `[…]` conservado.
- La regla `prompt_tag` bloquea `<conversation_summary>` y `<audience>`.

**Servicio:**

- `SessionService` con un `MemoryCompression` falso:
  - la compresión ocurre tras el turno y dentro del lock;
  - un `summarize` que lanza **no** cambia la respuesta (200) ni el turno guardado;
  - un `detect` que lanza → `history_compression_failed` y 200;
  - con un turno fallido (502) no hay compresión;
  - `last_tier` solo con éxito;
  - el tier llega a `render_session_prompt`.
- `ConversationSummarizer` y el clasificador LLM con `FakeProvider(structured={"summary": …})`:
  - devuelven el texto normalizado;
  - un `LLMProviderError` se propaga al llamador (la política lo captura);
  - el clasificador vuelve al heurístico ante el error;
  - un positivo heurístico no llama al LLM;
  - `COMPRESSION_MODEL` se pasa como override.

**API** (`httpx.AsyncClient`):

- **El *fixture* de la app sobrescribe `get_memory_compression`** con un compresor falso determinista (resumen = `"S:" + números de turno`). Así `FakeProvider.calls` sigue contando solo las estimaciones y los tests de S05 conservan su significado.
- La prueba de 8 turnos de S05 se actualiza a la fórmula nueva, con turnos sin anclas. El número de mensajes sigue siendo `2 × min(k − 1, 6) + 1`, y además el system de la llamada 8 contiene `<conversation_summary>`.
- **Conversación de 9 turnos** con el turno 2 "Hemos firmado un NDA y el presupuesto está cerrado en 40 000 €":
  - en la llamada 9, el primer par de mensajes es el turno 2 literal;
  - `anchors_count = 1`;
  - el system lleva el resumen con el turno 1 (con una ventana de 6, el turno 3 sigue en la ventana en la llamada 9);
  - `tier = executive`, `tier_rule = nda_detected` en el turno 2 y siguientes (por `explicit_constraints` del *payload* falso).
- `tier=developer` explícito → `tier_rule = explicit`.
- `tier` con `prompt_version=v4` → 422.
- Valor de `tier` inválido → 422.
- `GET` con los 4 campos nuevos.
- OpenAPI con `tier` en el multipart y los campos nuevos.

**Logs**: los marcadores secretos de D13.

**Streamlit**: la paridad de `TIERS`/`TIER_RULE_LABELS`; `tier` no se envía con "Automático" y sí con los demás; el selector deshabilitado con `v4`; el panel con anclas, resumen y tier (con `AppTest` como en S05).

**CI**: sin cambios en el *workflow*.

### D16. Medición con llamadas reales (plan para `tasks.md`)

Requiere **autorización del usuario**. En el proceso (servicio, para poder leer `session.history.summary` desde el script sin exponerlo por la API), sin Redis, con moderación real y `MAX_CONVERSATION_TURNS=3`, para que un ancla salga de la ventana con pocos turnos.

| Bloque | Modelo | Turnos | Qué se comprueba |
|---|---|---|---|
| A. ES, 7 turnos: muestra ES → "Hemos firmado un NDA y el presupuesto está cerrado en 40 000 €" → 3 seguimientos breves (Stripe; "descartamos Firebase, usaremos Supabase"; quitar el panel) → "Añade informes mensuales respetando lo acordado al principio" → "¿Y con un equipo de 2 personas?" | gpt-4o-mini | 7 (+~4 resúmenes) | El turno 2 anclado y fuera de la ventana desde el turno 5; restricciones con 40 000 y NDA en el turno 7; Firebase en `rejected_options` y no en `mentioned_technologies`; resumen en español; tier `executive`/`nda_detected` desde el turno 2 |
| B. EN, 6 turnos equivalentes (NDA → "fixed budget of $45k" …) | gpt-4o-mini | 6 (+~3) | Lo mismo en EN; resumen en inglés |
| C. Tier: la misma transcripción técnica EN con `tier` automático (→ `developer`), `executive` y `pm` explícitos; y una ES con "cumplir RGPD" (→ `executive`) | gpt-4o-mini | 4 | Regla correcta; diferencias visibles en `task` y `assumptions_and_risks` |
| D. ES, 4 turnos del bloque A con `COMPRESSION_MODEL=claude-haiku-4-5` | gpt-4o-mini + Haiku | 4 (+~1) | El resumen con otro proveedor; idioma |

**Coste estimado:**

- unas 21 estimaciones de ~7-9 k tokens de entrada con gpt-4o-mini ≈ 0,04 USD;
- ~8 resúmenes ≈ 0,003 USD;
- 1-2 resúmenes con Haiku ≈ 0,005 USD.

Total ≈ **0,05 USD**, ≤ 0,10 USD contando algún reintento.

**Umbrales** (100 % salvo que se indique):

- Las anclas esperadas se detectan, sin falsos positivos en los seguimientos.
- `anchors_count` y `summary_chars > 0` cuando corresponde.
- **Retención**: tras salir el turno 2 de la ventana, `explicit_constraints` del último turno contiene el importe (40 000 / 45k) y el NDA, y la estimación no lo contradice. Se revisan a mano `summary` y `assumptions_and_risks`.
- El idioma del resumen (`detect_language`) es el de la sesión.
- El resumen no contiene horas ni importes de las estimaciones (regex de "h" y "€/$" aparte de los del cliente; se informa).
- Olvido: Firebase en `rejected_options` y fuera de `mentioned_technologies`.
- Tier: regla esperada en los bloques A-C. En C, `executive` tiene menos términos técnicos en `task` que `developer` para la misma transcripción. Se informa con recuentos y no bloquea, porque es cualitativo.
- Como en S05: idioma del texto 100 %, título conservado 100 %, ningún seguimiento fuera de alcance, `score = 1.0`, ningún 502, ≥ 80 % al primer intento.
- `summary_failed = 0`. Latencia extra de la compresión (`history_compressed.latency_ms`) con p50 ≤ 3 s; se informa.
- Se registran `input_tokens` por turno, para contrastar D4 (crecimiento acotado tras el turno 4).
- **Regla de parada**: si cualquier umbral falla, o el gasto acumulado supera 0,08 USD, se para y se vuelve al usuario con los datos (como en S04 D13 y S05 D14). Los resultados se añaden a este documento.

**Primera medición (2026-11-13, gpt-4o-mini, 21 estimaciones + 2 resúmenes, 0,029 USD).** Se para por dos umbrales:

- Lo que cumple:
  - idioma 100 %, título conservado 100 %, `score = 1.0`, ningún 502, todas las estimaciones al primer intento;
  - anclas correctas: el turno 1 (la muestra cita el RGPD/GDPR) y el turno 2 (NDA y presupuesto), sin falsos positivos en los seguimientos;
  - olvido: Firebase en `rejected_options` y fuera de `mentioned_technologies` (ES y EN);
  - el presupuesto (40 000 € / $45k) retenido en `explicit_constraints`;
  - tier del bloque C correcto (`developer`/`technical_audience`, `explicit` y `regulatory_context` con RGPD);
  - resumen en el idioma de la sesión, sin horas ni importes;
  - compresión de 0,9 a 2,9 s en los turnos que resumen;
  - `input_tokens` de 6,0 k a 8,3 k en 7 turnos.
- **Fallo 1, el resumen inventa el proyecto**: el bloque A resumió "un sistema de gestión de inventarios… multilingüe" y el B "a web SaaS project". El turno 1 queda anclado, así que el resumidor solo ve seguimientos ("añadid Stripe", "descartamos Firebase") sin saber de qué proyecto se habla, y gpt-4o-mini rellena el hueco pese al "Do not invent".
- **Fallo 2, el tier pierde el NDA**: en B y D el modelo no puso el NDA en `explicit_constraints` (sí el presupuesto), y el tier pasó de `executive`/`nda_detected` a `default` en el turno 3, aunque el turno del NDA seguía en la ventana. D9 dependía de que el modelo rellenara el campo.
- Menores:
  - restricciones duplicadas que solo difieren en el punto final ("Fixed budget of $45k" / "…$45k.");
  - el bloque D no llegó a resumir (el único turno desalojado fue el turno 1, anclado), así que el resumen con Haiku quedó sin medir.

**Correcciones aprobadas por el usuario:**

1. El resumidor recibe el proyecto como contexto (`<project_context>` con `project_name` y `agreed_scope`) y la regla de describir solo lo que dicen los turnos: si solo traen cambios, se resumen como cambios sobre ese proyecto. `conversation_summary/v1` aún no se ha publicado, así que se edita en lugar de crear una `v2`. El guardrail bloquea también `<project_context>`.
2. `nda_detected` y `regulatory_context` leen también los `user` guardados de las anclas y de la ventana (`history_messages`), no solo la transcripción y `explicit_constraints`. Es determinista y no depende del modelo. `technical_audience` sigue mirando solo el turno.
3. La instrucción de `explicit_constraints` en `v5` nombra el NDA o la confidencialidad, la normativa, el presupuesto y los plazos.
4. `merge_unique` compara tras colapsar espacios y quitar la puntuación final.

La medición se repite con los bloques A y B y con el D ampliado a 6 turnos, para que Haiku resuma.

**Segunda medición (2026-11-16, tras las correcciones; 19 estimaciones y 3 resúmenes en sesión más 8 resúmenes directos, 0,031 USD).** Se cumplen todos los umbrales:

- **Tier:** `executive` en los 19 turnos de A, B y D: `regulatory_context` en el turno 1 (la muestra cita el RGPD/GDPR) y `nda_detected` del turno 2 en adelante. Ya no cae a `default` en el turno 3.
- **Restricciones:** el NDA y el presupuesto (40 000 € / $45k) están en `explicit_constraints` en las tres sesiones desde el turno 2 y se conservan hasta el final.
- **Anclas** en los turnos 1 y 2 en las tres sesiones, sin falsos positivos.
- **Olvido:** Firebase está en `rejected_options` y fuera de `mentioned_technologies`.
- **Resumen:** en el idioma de la sesión, de 271 a 338 caracteres y sin horas ni importes. `summary_failed = 0` y la compresión tardó de 1,4 a 1,8 s.
- **Fallo 1 resuelto:** los 8 resúmenes reproducidos con el proyecto como contexto (gpt-4o-mini y Haiku, ES y EN, primera pasada y acumulativa) solo describen el proyecto de `<project_context>` y los cambios de los turnos, sin inventar nada. Retoman el alcance del contexto al principio, algo aceptable porque es fiel y ayuda al estimador.
- **Calidad de las estimaciones:** idioma 100 %, título conservado 100 %, `score = 1.0`, ningún 502 y todas al primer intento. `input_tokens` va de 6,1 k a 8,5 k en 7 turnos (con `MAX_CONVERSATION_TURNS=3`).
- **Riesgos que quedan** (no bloquean):
  - duplicados semánticos en las restricciones ("Signed NDA with the client" / "NDA signed with the client"), que la normalización no puede detectar;
  - una vez el modelo puso "RGPD" en `technologies`;
  - la diferencia entre tiers en las tareas del bloque C es pequeña: cambia el encuadre y el orden más que el desglose, como se esperaba con un schema único.

**Gasto total de la medición: 0,060 USD** (tope de 0,10).

### D17. Cómo habilita Actor-Critic-Boss (sin implementarlo)

- **Patrón de llamada secundaria**: `ConversationSummarizer` deja establecido el molde de un agente auxiliar, que es el que seguirán el Critic y el Boss:
  - modelo de respuesta propio en `app/services`;
  - plantilla versionada en su carpeta (`app/prompts/<agente>/v1/`);
  - `generate_structured` con caché y fallback;
  - modelo configurable (`COMPRESSION_MODEL` → `CRITIC_MODEL`);
  - `llm_purpose` para atribuir costes.
- **`explicit_constraints` y `rejected_options`** son la lista de comprobación natural del Critic: ¿respeta la estimación el presupuesto, el plazo y la normativa? ¿Usa algo descartado?
- **`TierDecision`** es una entrada del Boss (criterios de aceptación distintos por audiencia).
- `estimate_turn` sigue aislando la generación en `_generate`. La compresión y el tier ocurren fuera (antes y después), así que sustituir `_generate` por Actor → Critic → Boss no los toca.
- `llm_purpose` en los logs permite medir el coste por agente desde el primer día.
- Lo que **no** se hace: ni crítico, ni jueces, ni iteraciones, ni evals.

## Risks / Trade-offs

- **[Latencia de la compresión síncrona]** +1-3 s en los turnos que expulsan un par que no es ancla (desde el turno 7). Mitigación: modelo barato, `latency_ms` en `history_compressed` y medición. Si molesta, la alternativa en segundo plano está analizada en D4.
- **[Inyección blanqueada por el resumen en el system]** (D7). Mitigaciones: guardrails de entrada, "datos como datos", etiquetas eliminadas, `prompt_tag` ampliado y tope de longitud. Riesgo residual aceptado. La alternativa (mensaje `user`) tiene sus propios problemas (Open Questions 1).
- **[Alucinación del resumen]** Un modelo pequeño puede inventar o desordenar hechos. Mitigaciones: precedencia de lo literal y lo tipado sobre el resumen en `v5`, compromisos protegidos por anclas y `explicit_constraints`, solo mensajes del cliente como entrada y medición manual. La notas recuerdan el límite de contexto de los modelos pequeños: aquí la entrada es ≤ ~6 k tokens.
- **[Falsos positivos y negativos de los patrones]** Las regex no cubren paráfrasis ("lo de la confidencialidad ya está firmado") ni otros idiomas. Mitigaciones: sesgo a la precisión, corpus de negativos, el modo LLM opcional para la cobertura, `explicit_constraints` como red y `rules` en los logs para ajustar (consejo 4 del mentor: medir antes de ampliar).
- **[El tier sobre-detecta `developer`]** Las descripciones de software son técnicas por naturaleza. Mitigación: lista sin nombres de producto, umbral de términos distintos y medición sobre `samples/` (Open Questions 6).
- **[El tier adapta el contenido, no el schema]** Riesgo de "maquillaje" (notas 03 §5). Mitigación: instrucciones concretas por campo (D10) y comparación en D16-C. Los schemas por tier necesitan evals (fuera de alcance).
- **[Override del tier desde el cliente]** (notas 03 §7.1). Sin impacto en el coste ni en los permisos hoy; revisar cuando haya autenticación.
- **[Restricciones que solo crecen]** Una restricción levantada sigue en el metadata, y el mensaje actual prevalece por instrucción. Open Questions 4.
- **[Readopción ambigua]** Si el modelo pone el mismo nombre en `technologies` y `rejected_options` en el mismo turno, gana el descarte (conservador). Se documenta y se prueba.
- **[Anclas con estimaciones viejas]** El Markdown de un ancla lleva cifras antiguas. Mitigación: `v5` declara vigente la última respuesta, y el tope acota los tokens (~5 k en el peor caso típico).
- **[Caché]** El schema cambia: invalidación única. El resumen en el system reduce aún más los aciertos en conversaciones largas. Aceptado.
- **[Memoria del proceso]** +~90 KB por sesión en el peor caso (D3). La LRU lo acota.
- **[Prompt caching de Anthropic]** Sigue sin `cache_control` (como en S05). El prefijo estable solo ayuda con OpenAI.
- **[Directo no contrastado]** Este diseño se basa en la descripción del directo, no en su código. Mitigación: tarea de verificación al principio (Context).

## Migration Plan

1. **Verificación**: contrastar con `session_05_live` (anclas, política, resumidor, `tier_resolver`, `<audience>`, configuración). Las diferencias que afecten a una decisión vuelven aquí.
2. **Dorado de `v4`** (qa), antes de tocar código. Comprobar que el de `v3` está en verde.
3. `app/config.py` (D12), `.env.example` y `CONFIG_ENV_VARS`.
4. `app/estimation/` (campos, normalizadores, `ComputedEstimate`, `example_payloads(include_memory_fields)`) y los datos de `app/context/examples.py`.
5. `app/sessions/` (`models.py`, `metadata.py`, `tier.py` y `compression/`).
6. `app/guardrails/input.py` (`prompt_tag` + `conversation_summary`, `audience`).
7. `app/prompts/` (`v5`, `conversation_summary/v1`, `anchor_classifier/v1` y el loader).
8. `app/services/` (`memory.py`, `estimate_turn(tier, conversation_summary)`, `SessionService` con tier y compresión, `get_memory_compression`).
9. `app/schemas.py` y `app/routers/sessions.py`.
10. `streamlit_app.py`.
11. Tests (D15) y specs delta.
12. README (memoria: anclas, resumen, olvido; tier y sus reglas; configuración) y `CLAUDE.md` (S05 live en la tabla de sesiones; `v5`; `app/sessions/compression/` y `app/services/memory.py` → `ai-engineer`; `app/prompts/conversation_summary/` y `anchor_classifier/`).
13. Medición D16.

**Notas de migración:**

- `/api/v1/estimate` solo gana `result.explicit_constraints` y `result.rejected_options` (aditivos).
- El endpoint de sesión pasa a `v5` por defecto. Un cliente que necesite `v4` lo pide por query.
- Las sesiones en memoria se pierden al desplegar (como siempre). Sin cambios en Docker.
- **Rollback**: revertir los commits del change.

## Open Questions

**Candidatas a resolver con el usuario** (se indica la opción recomendada; el diseño la aplica):

1. **Dónde va el resumen**: en el system, en un bloque `<conversation_summary>` (**recomendado**, D7), o como mensaje `user` sintético `[Earlier conversation summary…]` como el directo. La recomendada mantiene la alternancia, el contrato de mensajes y el equilibrio de idioma, pero mete texto generado en el system.
2. **Turnos que no se pudieron resumir**: backlog que se reintenta en el siguiente turno, con tope = ventana (**recomendado**, D4), o perderlos como el directo.
3. **Tier con `v4`**: 422 si se pide un `tier` explícito con `prompt_version=v4`, y `default`/`prompt_version` en automático (**recomendado**, D9). Alternativas: ignorarlo en silencio, o retirar `v4` del endpoint de sesión.
4. **Restricciones levantadas**: se mantiene la acumulación del brief (**recomendado** para este change) o se sustituye la lista por la del turno (el modelo devuelve las vigentes), lo que permitiría retirarlas con el riesgo de perder alguna si el modelo la omite. Es el mismo dilema que S05 tuvo con `technologies`.
5. **Alcance del tier**: adaptación del contenido dentro del schema único (**recomendado**, D9/D10) frente a schemas por tier (notas 03), que piden evals y otro change.
6. **Umbral de `technical_audience`**: 2 términos distintos como el directo (**recomendado**), y se sube a 3 si D16 o `samples/` muestran que casi todo cae en `developer`. ¿Se añade vocabulario de gestión ("hitos", "sprint", "roadmap") para `pm`, además del equipo pequeño? Recomendado: no, hasta medir.
7. **Texto del resumen en `GET /sessions/{id}`**: solo `summary_chars` (**recomendado**) o también el texto, para depurar desde la UI.
8. **Modo LLM del detector**: heurístico primero y LLM solo para los negativos (**recomendado**, D5), o el LLM decide siempre con fallback heurístico (el directo, según la descripción).
9. **Valores por defecto**: `MAX_ANCHORED_TURNS=3`, `SUMMARY_MAX_CHARS=2000` (constante), `COMPRESSION_MODEL` vacío = primario, `ANCHOR_DETECTION_MODE=heuristic`, topes de 8/15 restricciones y 10/20 descartes (por turno/metadata).
10. **Propietarios en `CLAUDE.md`**: `app/sessions/compression/`, `app/sessions/tier.py`, `app/services/memory.py` y las plantillas nuevas → `ai-engineer`.
11. **Medición D16**: ¿se autorizan unas 21 estimaciones y ~10 resúmenes reales (≈ 0,05 USD, tope 0,10 USD)? Se preguntará al llegar la tarea.

**Cambios de spec que se proponen** (los decide el orquestador con el usuario):

- `conversation-sessions`:
  - historial con anclas, resumen, backlog e invariante de conservación;
  - `take_evicted`;
  - la fórmula de mensajes `2 × (anclas + min(k − 1, N)) + 1`, que sustituye al escenario "Ventana respetada";
  - `SessionInfo` con los 4 campos;
  - `tier`/`tier_rule` en la respuesta;
  - `tier` en el multipart;
  - `prompt_version` `"v4" | "v5"` con `v5` por defecto;
  - el 422 de `tier` con `v4`;
  - las reglas de metadata del paso 1-4 de D8;
  - la compresión que nunca hace fallar el turno.
- `memory-compression` (nueva): reglas del detector con escenarios ES/EN y negativos, modo LLM con *fallback*, tope y degradación al resumen, resumen en el idioma de la sesión, fallo que conserva el anterior y deja el backlog.
- `audience-tier` (nueva): cadena de reglas, precedencia, override, fuentes (transcripción y `explicit_constraints`) y explicabilidad.
- `structured-estimation`: los dos campos, con su orden y normalizadores sin reintento, en `ComputedEstimate` y sin render.
- `prompt-templates` / `cag-prompt`: `v5` con el orden de D10, `<audience>` por tier, `<conversation_summary>` condicional, `v3`/`v4` con dorado, y las plantillas `conversation_summary/v1` y `anchor_classifier/v1`.
- `input-guardrails`: `prompt_tag` con `conversation_summary` y `audience`.
- `app-config`: las 3 variables y la validación de `COMPRESSION_MODEL`.
- `observability`: los eventos de D13 y `llm_purpose`.
- `estimation-form-ui`: el selector de audiencia y el panel ampliado.
- `ci-pipeline`: las suites de D15.
