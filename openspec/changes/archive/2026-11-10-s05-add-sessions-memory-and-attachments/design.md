## Context

Ejercicio de la sesión 05 (`AIEngineer-notas/05_Funciones_avanzadas/00_memoria`: "memoria conversacional y contexto enriquecido"). Material de referencia: `02_memoria-conversacional-vs-historial.md` (historial frente a memoria, ventana deslizante, políticas de olvido) y `03_prompts-adaptativos-patron-tier.md` (lo que construye el directo). El alcance lo fija `proposal.md`.

Estado de partida (rama `pre-session-05`, tras archivar `s04-add-structured-output-and-guardrails`):

- `app/services/llm_service.py`:
  - `LLMProvider.generate_structured(system, messages, response_model, options)` con Instructor (`Mode.TOOLS`), fallback del Router y tokens sumados entre reintentos.
  - `CachedProvider` decora al proveedor. La clave incluye `system`, **todos** los `messages`, el modelo y el hash del schema (S04 D7).
  - `EstimationService.estimate(EstimationInput, GenerationOptions) -> EstimationOutcome`: guardrails (regex → moderación) → render `v3` → llamada estructurada → `compute_estimate` → `render_markdown` → evaluación. El método es síncrono y no guarda estado.
- `app/estimation/`: `EstimationResult` (lo que rellena el LLM), `ComputedEstimate` (importes calculados en el código), render y ejemplos. No hay campo de tecnologías (S04 D2 lo dejó fuera a propósito).
- `app/guardrails/input.py`: `screen_texts`, reglas de inyección ES/EN, PII (email, teléfono, IBAN con mod-97). `InputGuardrailViolation(reason, field, rule)`.
- `app/prompts/loader.py`: `PromptVersion = Literal["v3"]`, `render_estimation_prompt(request, version, *, num_examples) -> (system, user)`. `v3` está publicada y no se edita.
- `app/routers/estimations.py`: endpoint síncrono (`def`), que FastAPI ejecuta en el *threadpool*.
- `streamlit_app.py`: formulario de un solo disparo, cliente HTTP sin imports de `app`.
- `Dockerfile`: `uvicorn` con un solo worker.

**Restricciones heredadas:**

- Solo `app/services` y `app/config.py` importan LiteLLM/Instructor. `app/sessions` y `app/attachments` tampoco los importan.
- Los routers no tienen lógica e importan solo de `app.services` y `app.schemas` (S04 D4).
- Nunca se registran transcripciones, prompts, respuestas, texto de adjuntos ni keys.
- Una versión de prompt publicada no se edita (`v3` sigue igual, byte a byte).
- S04 D14/D13: el idioma con gpt-4o-mini es frágil y se vuelve a medir siempre que cambia el prompt.

**Comprobado sin red (en `uv.lock`):**

- `fastapi 0.157.0`, `starlette 1.9.0`, `pydantic 2.14.1`, `httpx 0.28.1`.
- `anyio 4.15.1` es dependencia transitiva de Starlette y registra su plugin de pytest (marcador `anyio`). No hace falta `pytest-asyncio`.
- `python-multipart 0.0.32` ya está como transitiva. Pasa a dependencia directa porque FastAPI la necesita para `Form`/`File`.
- `pypdf`, `python-docx` y `lxml` (que trae `python-docx`) **no** están en el lock.

**Siguiente sesión (directo de S05):** compresión de memoria con anclas, tier dinámico y Actor-Critic-Boss. Este change las habilita sin implementarlas (D15).

## Goals / Non-Goals

**Goals:**

- Sesiones en memoria del proceso con historial de ventana deslizante (pares `user`/`assistant`) y un `project_metadata` tipado, separado del historial e inyectado en el system prompt.
- `project_metadata` determinista, derivado de la salida estructurada de cada turno, sin una segunda llamada al LLM.
- Adjuntos PDF y DOCX con extracción local, límites explícitos y errores 413/415/422 que nunca repiten contenido.
- Mismo pipeline de estimación (guardrails, salida estructurada, importes en el código, render, evaluación) para `/estimate` y para los turnos de sesión, sin duplicarlo.
- Coste por turno acotado: la ventana limita los turnos, y el texto de los adjuntos no se arrastra en el historial (D3).
- Tests sin red, incluidos los tres de integración del enunciado con `httpx.AsyncClient`, y una medición real acotada (≤ 0,10 USD).

**Non-Goals:**

- Resumen acumulativo, memoria con anclas, tier dinámico y Actor-Critic-Boss (directo).
- Persistencia de sesiones, TTL por inactividad, autenticación y sesiones compartidas entre workers.
- OCR, `.doc`, adjuntos multimodales (camino A) y chunking.
- Retirar tecnologías del `project_metadata` (política de olvido 1 de las notas): ver Open Questions.
- Mejorar los ejemplos con supuestos por fase (la "mejora pendiente `v4`" de S04 D13). `v4` cambia solo lo necesario para la sesión, para que la medición atribuya las diferencias a la memoria.
- Cambiar `POST /api/v1/estimate`, salvo el campo `technologies` dentro de `result`.

## Decisions

### D1. Decisiones ya tomadas (no se reabren)

| # | Decisión | Alternativas descartadas |
|---|---|---|
| 1 | Almacén en memoria del proceso (diccionario), con un tope de sesiones | Redis o base de datos (persistencia, fuera de alcance); diccionario sin tope |
| 2 | `POST /api/v1/sessions`, `GET /api/v1/sessions/{session_id}` y `POST /api/v1/sessions/{session_id}/estimate` | Rutas sin `/api/v1` (enunciado y solución oficial) |
| 3 | La respuesta del turno es el contrato de `/api/v1/estimate` más `session_id`, `turn` y `project_metadata` | Un contrato nuevo y reducido para sesiones |
| 4 | Adjuntos por el camino B (extracción local con pypdf y python-docx). Tipo no admitido → 415; ilegible o sin texto → 422 | Camino A (Files API del proveedor): acoplado al proveedor y al fallback cruzado OpenAI ↔ Anthropic |
| 5 | Guardrails: la transcripción bloquea como la descripción; en los adjuntos la inyección bloquea y la PII se enmascara | Bloquear también por PII en los adjuntos; no revisar los adjuntos |
| 6 | `project_metadata` derivado de forma determinista del resultado estructurado, más un campo `technologies` nuevo en `EstimationResult` | LLM extractor (segunda llamada por turno, la opción de la solución oficial); heurística con regex sobre el Markdown |
| 7 | Prompt `v4` = copia de `v3` + `<project_metadata>` + instrucciones multi-turno. El system se regenera en cada turno y no se guarda en el historial | Editar `v3`; guardar el system en el historial |
| 8 | El turno `assistant` guardado es el Markdown que vio el usuario | El JSON del `EstimationResult` |
| 9 | Los turnos fallidos (400, 404, 409, 413, 415, 422, 502) no entran en el historial ni tocan el `project_metadata` | Guardar el turno con un mensaje de error |
| 10 | `v3` sigue siendo la versión de `/api/v1/estimate` | Pasar `/estimate` a `v4` |
| 11 | La interfaz Streamlit pasa a ser conversacional, con sesión | Mantener el formulario de un disparo junto al chat |

### D2. Paquete `app/sessions/`: modelos, almacén y concurrencia

```
app/sessions/            # new; pydantic + stdlib + app.domain/app.estimation; no LLM imports
├── __init__.py
├── models.py            # Turn, ConversationHistory, ProjectMetadata, Session
├── store.py             # SessionStore, SessionNotFoundError, SessionBusyError
└── metadata.py          # update_project_metadata, merge_technologies
```

**Dependencias:**

- `sessions.models → {domain}`, `sessions.metadata → {sessions.models, estimation}`, `sessions.store → {sessions.models}`.
- `prompts → sessions.models` (el loader recibe `ProjectMetadata` tipado).
- `services → sessions`.
- Ninguno importa `services` ni `prompts`, así que no hay ciclos.

**`ChatMessage` pasa a `app/domain.py`.** Es un `TypedDict` sin dependencias. `llm_service` lo reexporta, así que los imports actuales no cambian. Así `ConversationHistory.to_messages()` devuelve el tipo exacto que consume el proveedor sin que `sessions` importe `services`.

**`Session`** (en `models.py`) es un `dataclass` mutable, no Pydantic, porque lleva un `Lock`:

```python
@dataclass(eq=False)
class Session:
    """In-process conversation state. Volatile by design (S05 exercise): lost on restart and
    not shared between workers; persistence belongs to the deployment module."""

    session_id: str  # canonical lowercase UUID4
    history: ConversationHistory
    project_metadata: ProjectMetadata = field(default_factory=ProjectMetadata)
    language: Language | None = None  # effective language of the last successful turn
    turn_count: int = 0  # successful turns, including those out of the window
    created_at: float = 0.0  # time.monotonic()
    updated_at: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
```

**`SessionStore`** (en `store.py`):

```python
class SessionStore:
    def __init__(self, *, max_sessions: int, max_turns: int,
                 clock: Callable[[], float] = time.monotonic) -> None: ...
    def create(self) -> Session                    # evicts the LRU session when full
    def get(self, session_id: str) -> Session      # SessionNotFoundError; refreshes recency
    @contextmanager
    def turn(self, session_id: str) -> Iterator[Session]   # get + non-blocking lock; SessionBusyError
    def __len__(self) -> int
```

- Internamente es un `OrderedDict[str, Session]` protegido por un `threading.Lock` del almacén. `get`/`turn` hacen `move_to_end`, y `create` hace `popitem(last=False)` mientras `len >= max_sessions`.
- **Concurrencia.** FastAPI ejecuta los endpoints síncronos en el *threadpool*. Dos turnos simultáneos sobre la misma sesión leerían el mismo historial y añadirían dos pares fuera de orden. **Decisión: lock por sesión no bloqueante → 409 `session_busy`.**
  - `turn()` hace `session.lock.acquire(blocking=False)`. Si ya está tomado, lanza `SessionBusyError`, y lo libera en el `finally`.
  - Todas las mutaciones del turno (añadir el par, el metadata, el idioma y el contador) ocurren con el lock tomado.
  - `GET` lee sin el lock de sesión. `ProjectMetadata` es inmutable y se sustituye entero, y los contadores son enteros, así que una lectura concurrente ve el estado anterior o el nuevo, nunca uno a medias.
  - *Alternativa descartada*: un lock bloqueante. La segunda petición ocuparía un hilo del *threadpool* (40 por defecto) durante toda la llamada al LLM, que puede pasar del minuto con reintentos, y el resultado sería el mismo turno dos veces. El caso real es un doble clic, y un 409 explícito es más honesto.
  - *Alternativa descartada*: sin control. Se corrompe el orden del historial.
- **Tope y desalojo: LRU por último acceso**, `MAX_SESSIONS` (por defecto 1000, D10). Se registra `session_evicted` (D11).
  - Si se desaloja una sesión con un turno en curso (muy improbable, porque se acaba de tocar), el turno termina sobre un objeto huérfano y la siguiente petición da 404. Se acepta.
  - *Alternativa descartada*: un TTL por inactividad con limpieza periódica. Es la política de olvido 2 de las notas; necesita un hilo o un barrido y no la pide el ejercicio. La LRU acota la memoria, que es el riesgo real.
  - *Alternativa descartada*: rechazar la creación con 503 cuando está lleno. Bloquearía el servicio por sesiones abandonadas.
- **Memoria en el peor caso**: 6 turnos × (20 000 caracteres de transcripción + ~8 000 de Markdown) ≈ 170 KB por sesión, unos 170 MB con 1000 sesiones. Lo normal es un orden de magnitud menos.
- **`session_id`**: `str(uuid.uuid4())`. En la ruta se declara `session_id: uuid.UUID`, así que un formato inválido da **422** de FastAPI sin consultar el almacén, y OpenAPI lo documenta como `format: uuid`. El router lo normaliza con `str(session_id)` (minúsculas, con guiones), que es la clave del diccionario. Un UUID válido que no existe da **404**.
  - *Alternativa descartada*: 404 para todo. Oculta el formato, pero un UUID no es secreto por su forma, y el 422 es el comportamiento nativo y documentado.
  - El id es una **capacidad al portador**: quien lo conoce lee el metadata. UUID4 tiene 122 bits aleatorios y no se registra entero (D11).

### D3. `ConversationHistory` y qué se guarda en cada turno

```python
@dataclass(frozen=True)
class Turn:
    number: int        # 1-based ordinal in the session (survives the window)
    user: str
    assistant: str


class ConversationHistory:
    def __init__(self, max_turns: int) -> None: ...        # ValueError if max_turns < 1
    def append(self, *, user: str, assistant: str) -> Turn  # then drops the oldest turns
    def to_messages(self) -> list[ChatMessage]             # user/assistant alternating, oldest first
    @property
    def turns(self) -> tuple[Turn, ...]: ...
    @property
    def max_turns(self) -> int: ...
    def __len__(self) -> int: ...                           # turns in the window
```

**Invariantes** (las garantiza la estructura, no una comprobación):

- Se guardan **turnos** (pares), no mensajes sueltos. Nunca hay un `user` sin su `assistant`, y `to_messages()` siempre empieza por `user` y alterna, que es lo que exige Anthropic.
- `append` añade y después descarta los más antiguos mientras `len > max_turns`. Tras cualquier `append`, `len ≤ max_turns`.
- `user` y `assistant` no pueden estar vacíos (`ValueError`): el servicio solo añade turnos con éxito y con texto renderizado.
- **El system no forma parte del historial.** El enunciado pide que `to_messages_list()` incluya el system regenerado. Aquí `to_messages()` devuelve solo `user`/`assistant`, y el servicio pasa el system aparte, porque:
  - el proveedor ya recibe `(system, messages)` y LiteLLM lo coloca donde toca (parámetro `system` en Anthropic);
  - renderizar el system es cosa de `app/prompts`, y `sessions` no puede importar `prompts`.

  El invariante "el system siempre está presente" se cumple en el servicio: cada llamada lleva el system renderizado con el metadata de ese momento. Es lo mismo que hace la solución oficial.
- `Turn.number` sobrevive al recorte de la ventana: es el ancla del directo (D15).

**Qué contiene el `user` guardado.** Es el `user.j2` de `v4` renderizado (etiquetas `<project_type>` y `<transcript>`, D5), **con el texto de cada adjunto sustituido por un marcador**. El nombre del adjunto se mantiene en su separador:

```
<attachments>
--- attachment: especificacion.pdf ---
[…]
</attachments>
```

- **Por qué no se guarda el texto completo de los adjuntos.** Con el límite de 60 000 caracteres por turno (D7, unos 15 000 tokens), una conversación de 6 turnos con adjuntos reenviaría hasta ~90 000 tokens de documentos en cada llamada:
  - cerca del contexto de gpt-4o-mini (128 k);
  - ~0,10 USD por turno con Haiku.

  El efecto del documento ya está en la estimación de ese turno (el `assistant` guardado) y en el `project_metadata` (tecnologías, alcance, equipo).
- **Por qué el mensaje renderizado y no la transcripción en bruto**: el modelo ve todos los turnos con el mismo encuadre de etiquetas ("datos como datos") y con el `project_type` de cada turno, que puede cambiar.
- *Alternativas descartadas*:
  - Texto completo en el historial: coste y contexto (arriba).
  - Un extracto recortado (primeros N caracteres): arbitrario, porque la primera página de un PDF suele ser la portada, y además añade un parámetro más.
  - Mantener el texto solo durante K turnos: otra ventana dentro de la ventana; es terreno de la compresión del directo.
- **Consecuencia aceptada**: si en el turno 4 el usuario pregunta por un detalle del PDF del turno 2 que no llegó a la estimación, el modelo no lo tiene. Puede volver a adjuntarlo. Ver Risks y Open Questions.

**Crecimiento de tokens por turno** (gpt-4o-mini, `medium`):

- System ~8 k con 3 ejemplos, más el turno actual.
- Más el historial: ~0,3-5 k por `user` (transcripción de hasta 20 000 caracteres) y ~1-2 k por `assistant`.
- Caso típico en el turno 7: ~8 k + 6 × 1,8 k ≈ 19 k.
- Peor caso: transcripciones máximas en todos los turnos y adjuntos máximos en el actual, ~70 k. Cabe en los dos modelos.

### D4. `ProjectMetadata`, `technologies` y su actualización

**Modelo** (en `sessions/models.py`; también es el schema HTTP, sin un modelo espejo, como `ComputedEstimate` en S04 D3):

```python
class ProjectMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_name: str | None = None
    assumed_team_size: int | None = None
    mentioned_technologies: list[str] = []
    agreed_scope: str | None = None

    def filled_fields(self) -> list[str]: ...  # names of non-empty fields (logs, prompt)
```

No incluye `explicit_constraints` ni `rejected_options` (aparecen en las notas): el enunciado pide solo los cuatro campos y no hay fuente determinista para ellos.

**`technologies` en `EstimationResult`** (`app/estimation/schema.py`):

- **Posición**: después de `confidence_pct` y antes de `rows`. Así queda `language, title, summary, confidence_pct, technologies, rows, …`.
  - Después de `confidence_pct`, para no retrasar la decisión temprana de "no estimable" (S04 D2).
  - Antes de `rows`, para que el modelo se comprometa con el stack mencionado antes de desglosar (una fila "Integración con Stripe" sale de ahí).
  - *Alternativa descartada*: al final. Sería un resumen *a posteriori* y competiría con `narrative`.
- **Definición**:

  ```python
  technologies: list[TechName] = Field(
      default=[],
      max_length=MAX_TECHNOLOGIES,  # 15
      description="Technologies explicitly named by the client (languages, frameworks, "
      "platforms, cloud and third-party services) in the description, conversation or "
      "attachments; empty if none. Do not add your own recommendations.",
  )
  ```

  `TechName` es un `str` de 1 a 60 caracteres. Por defecto `[]`, así que el JSON Schema no lo marca como obligatorio.
- **Normalizador** (`field_validator(mode="before")`, idempotente y sin reintentos, según el principio de S04 D2):
  1. recortar espacios;
  2. quitar los vacíos y los de más de 60 caracteres;
  3. quitar duplicados sin distinguir mayúsculas (`casefold`), conservando la primera grafía;
  4. truncar a 15.

  Así las restricciones de campo no fallan nunca por este campo: es un campo secundario y no justifica una llamada de pago.
- **`ComputedEstimate`** gana `technologies: list[str]`, copiado tal cual en alcance **y** fuera de alcance: son hechos mencionados, no importes inventados. El Markdown **no** lo pinta. El texto de las tres plantillas de formato no cambia y la evaluación de S04 sigue igual.

**Impacto en `v3` (inmutable):**

- **El texto de `v3` no cambia.** Su system no menciona `technologies`. El modelo verá el campo en la definición de la herramienta y puede rellenarlo o dejarlo vacío. En `/estimate` aparece en `result.technologies` y no tiene más efecto. Es la única diferencia de `/estimate` que anuncia la propuesta.
- **Ejemplos de `v3` idénticos**:
  - `app/context/examples.py` gana `technologies` en los datos de cada ejemplo (los escribe `ai-engineer`: tecnologías nombradas en `meeting_summary`; p. ej. el 3 → `["Shopify", "Odoo", "Looker Studio"]` y el 1 → `[]`, que enseña a dejarlo vacío).
  - `example_result` lo carga.
  - `example_payloads(examples, output_format, *, include_technologies: bool = False)`: con `False` añade `"technologies"` al `exclude`. El loader pasa `True` solo en `v4`.
  - Un **test dorado** fija el `sha256` de los system y user de `v3` para varias combinaciones, calculados **antes** de tocar el código. Garantiza que `v3` no cambia ni un byte.
- **Caché**: `_schema_id` cambia con el schema, así que todas las entradas estructuradas (`v3` incluidas) se invalidan una vez. Se acepta: caducan por TTL y es el comportamiento diseñado en S04 D7. Un acierto antiguo ya no puede llegar sin `technologies`.

**Actualización** (`sessions/metadata.py`, función pura):

```python
MAX_METADATA_TECHNOLOGIES = 30

def update_project_metadata(current: ProjectMetadata, estimate: ComputedEstimate) -> ProjectMetadata
def merge_technologies(current: Sequence[str], new: Sequence[str], limit: int) -> list[str]
```

| Campo | Regla |
|---|---|
| `project_name` | `estimate.title` (se sobrescribe) |
| `assumed_team_size` | `sum(m.count for m in estimate.team)`; un perfil a tiempo parcial cuenta como sus personas (se documenta) |
| `mentioned_technologies` | unión de `current` + `estimate.technologies` en orden, sin duplicados por `casefold` (gana la primera grafía); si pasa de 30, se descartan las **más antiguas** |
| `agreed_scope` | `estimate.summary` |

- **Turno fuera de alcance** (`estimate.out_of_scope`): **no se actualiza nada**. Un "Unclear project" no debe pisar el nombre acordado. El turno sí entra en el historial (el usuario vio el aviso, y su siguiente mensaje lo necesita como contexto).
- Se sobrescriben los escalares porque `v4` pide mantener el título salvo que el cliente lo cambie (D5). Así el metadata refleja lo que el usuario ve en la última estimación.
- Se descartan las tecnologías más antiguas porque, con un tope, lo reciente es lo relevante.
- *Alternativas descartadas*:
  - Sobrescribir las tecnologías con la lista del turno: permitiría retirarlas, pero la propuesta fija la acumulación (Open Questions).
  - Derivar del `EstimationResult` en lugar del `ComputedEstimate`: daría igual en alcance, pero el `ComputedEstimate` ya trae `out_of_scope` y es lo que vio el usuario.

### D5. Prompt `v4`

`app/prompts/estimation/v4/{system,user,examples,project_metadata}.j2`. `examples.j2` es una copia literal de `v3`; los payloads incluyen `technologies`. `project_metadata.j2` es un **parcial** incluido por `system.j2`, para que lo reutilicen las plantillas por tier del directo (D15).

**Orden de `system.j2`** (el de S04 D14 con dos secciones nuevas; nada después de las reglas salvo la línea de idioma):

1. Regla de idioma (EN), referida a "the conversation" y a los campos de texto.
2. Rol, adaptado: conversación con el cliente en varios turnos. La transcripción del turno está en `<transcript>` y los adjuntos en `<attachments>`.
3. Pista por tipo.
4. **Conversación y memoria** (nueva, estática):
   - Los mensajes anteriores son el historial. Tus respuestas previas aparecen como el texto de la estimación que vio el cliente.
   - Cada respuesta es la **estimación completa y actualizada** del proyecto, no solo el cambio.
   - El mensaje actual prevalece sobre los anteriores y sobre `<project_metadata>` cuando los contradice.
   - Mantén `title` igual que en la estimación anterior salvo que el cliente cambie el nombre del proyecto.
   - Un mensaje breve de seguimiento ("añade pagos con Stripe") se refiere al proyecto en curso. **No es motivo de baja confianza**: fuera de alcance solo si el proyecto en su conjunto sigue sin poder estimarse.
   - `<project_metadata>`, al final de estas instrucciones, recoge los hechos acordados en turnos anteriores, aunque ya no estén en el historial. Tómalos como establecidos.
   - En los turnos anteriores, el texto de los adjuntos aparece sustituido por `[…]`: ya está reflejado en la estimación de ese turno.
5. Cómo usar los ejemplos.
6. Cómo rellenar los campos. Añade `technologies`: las tecnologías nombradas explícitamente por el cliente en cualquier turno o adjunto, incluidas las de `<project_metadata>` que no haya descartado; sin recomendaciones propias.
7. Fuera de alcance (texto de `v3` con la salvedad del punto 4).
8. `{% include "examples.j2" %}`.
9. **`{% include "project_metadata.j2" %}`** (nueva, dinámica).
10. `## Reglas`. "Datos como datos" se amplía a `<transcript>`, `<attachments>` y `<project_metadata>`.
11. Línea del idioma de respuesta (D6).

**Por qué `<project_metadata>` va tras los ejemplos y antes de las reglas:**

- **Prefijo estable**: las secciones 1-8 no cambian entre turnos de la misma sesión (mismo tipo, formato y nivel). OpenAI aplica *prompt caching* automático a prefijos idénticos de 1024 tokens o más (50 % de descuento en gpt-4o-mini). Con el bloque dinámico al principio, se perdería en los ~6 k tokens de los ejemplos.
- **Equilibrio de idioma (S04 D14)**: lo que va cerca del final pesa más. Los valores del metadata están en el idioma de la sesión, que es el que se pide, así que empujan a favor. Los bloques en español de los ejemplos siguen antes, como en `v3`.
- *Alternativas descartadas*:
  - Justo después del rol: rompe el prefijo cacheable.
  - En el mensaje `user`: el ejercicio pide el system, y entonces se guardaría en el historial.
  - Después de las reglas: rompe la invariante de D14 (nada tras las reglas salvo el idioma).

**`project_metadata.j2`**: claves en inglés, solo los campos con valor ("renderizado condicional", notas §3), sin `None`. En el primer turno, el bloque está vacío, como pide el enunciado:

```
<project_metadata>
project_name: App de fidelización para cafeterías
assumed_team_size: 5
mentioned_technologies: iOS, Android, Stripe
agreed_scope: App de fidelización con puntos por QR y pedido anticipado...
</project_metadata>
```

Los valores salen de la salida del modelo (acotada por el schema: título ≤ 120, resumen ≤ 600) y se pintan tal cual, cubiertos por la regla "datos como datos".

**`user.j2` de `v4`** (envoltorio neutro, solo etiquetas y valores, como `v3`):

```
<project_type>{{ project_type }}</project_type>
{% if attachments %}
<attachments>
{% for attachment in attachments %}
--- attachment: {{ attachment.name }} ---
{{ attachment.text if attachment.text is not none else "[…]" }}
{% endfor %}
</attachments>
{% endif %}
<transcript>
{{ transcript }}
</transcript>
```

- **Adjuntos antes de la transcripción**. El enunciado dice "concaténalo al transcript con un separador" sin fijar el orden. Va antes porque:
  - con contexto largo, el documento primero y la petición al final rinde mejor (guía de Anthropic para documentos largos);
  - se conserva la invariante de `v3`: lo último del mensaje es el texto del usuario.
- El separador `--- attachment: <nombre> ---` es el del enunciado. El nombre está saneado (D7), así que no puede cerrar etiquetas ni falsificar separadores.
- La regla `prompt_tag` del guardrail de inyección (S04 D8) añade `transcript`, `attachments?` y `project_metadata`. También afecta a `/estimate`; el riesgo de falso positivo es despreciable.

**Idioma.**

- `response_language = detect_language(transcript) or session.language`. `session.language` es el idioma efectivo del último turno con éxito.
- Las continuaciones breves ("Add Stripe payments") suelen dar `None`: heredar el idioma de la sesión evita perder la línea explícita de D14.
- Un cambio claro de idioma en el mensaje actual manda.
- No se detecta sobre los adjuntos: un PDF en inglés en una conversación en español no debe cambiar el idioma de la respuesta.
- Idioma de las etiquetas, igual que S04 D5: `response_language or result.language`.

**API del loader** (mínima: una función nueva, sin tocar la de `v3`):

```python
PromptVersion = Literal["v3"]                      # /estimate, unchanged
SessionPromptVersion = Literal["v4"]
SESSION_PROMPT_VERSIONS: tuple[str, ...] = get_args(SessionPromptVersion)
DEFAULT_SESSION_PROMPT_VERSION: SessionPromptVersion = "v4"
PUBLISHED_PROMPT_VERSIONS = PROMPT_VERSIONS + SESSION_PROMPT_VERSIONS   # == folders on disk


@dataclass(frozen=True)
class SessionPrompt:
    system: str
    user: str           # current turn, with the attachments' text
    history_user: str   # same message with each attachment's text replaced by "[…]"


def render_session_prompt(
    turn: SessionTurnInput,
    version: str = DEFAULT_SESSION_PROMPT_VERSION,
    *,
    attachments: Sequence[tuple[str, str]] = (),     # (safe name, masked text)
    project_metadata: ProjectMetadata | None = None,
    response_language: Language | None = None,
    num_examples: int = NUM_EXAMPLES,
) -> SessionPrompt
```

- Es una función aparte porque las entradas son otras (transcripción y adjuntos en lugar de descripción y proyectos de referencia), y porque así el camino de `v3` no se toca.
- El loader no detecta el idioma en `v4`: lo decide el servicio, que conoce la sesión.
- `prompt_rendered` registra `prompt="session"`, `version`, `attachments` (número), `metadata_fields` (nombres) y los hashes y longitudes de `system`, `user` e `history_user`.
- *Alternativa descartada*: añadir `project_metadata=None` a `render_estimation_prompt`. Mezcla los dos contratos de entrada en una firma y obliga a ramificar el contexto por versión.

**Nota de implementación (ai-engineer, 2026-11-10).**

- **Supuestos por fase en los ejemplos de `v4` con `detailed`** (revisa el Non-Goal "Mejorar los ejemplos con supuestos por fase", a petición del orquestador). La lección de S04 D13 es que, en `detailed`, gpt-4o-mini a veces deja una fase sin supuestos y necesita un reintento. La causa probable son los ejemplos: su JSON muestra `phases` sin `assumptions`, y el modelo copia esa forma. `v3` ya pedía los supuestos de forma explícita, así que reforzar solo la instrucción ataca el síntoma.
  - **Opción elegida**: `EstimationExample` gana `phase_assumptions` (1 o 2 supuestos por fase, en español, escritos a mano). `example_result(..., phase_assumptions=False)` y `example_payloads(..., include_phase_assumptions=False)` los cargan solo cuando se piden, y el loader los pide solo en `v4` con `detail_level == detailed`. La instrucción de `detailed` añade "sin excepción" y "como en las estimaciones de referencia".
  - **Coste**: unos 300 tokens de entrada, solo en `detailed`. Un reintento cuesta una llamada completa.
  - `v3` no cambia (dorado en verde). `summary` y `medium` tampoco, así que la medición D14 (`medium`) sigue atribuyendo las diferencias a la memoria.
  - *Alternativa descartada*: reforzar solo la instrucción. No cuesta tokens, pero sigue contradiciendo a los ejemplos.
- `technologies` aparece **siempre** en el JSON de los ejemplos de `v4`, también `[]` (ejemplo 1), justo después de `confidence_pct`. Con `exclude_defaults` desaparecería la lista vacía, que es justo lo que enseña a dejarla vacía. En el ejemplo 2, las tecnologías son las nombradas por el cliente (`iOS`, `Android`, `Sign in with Apple`, `Google Sign-In`). React Native y Stripe salen en sus filas pero son elección del estimador, así que no entran.
- El marcador `[…]` es una constante del loader (`ATTACHMENT_PLACEHOLDER`) que se pasa a `user.j2`, en lugar de un literal en la plantilla.
- `UnknownPromptVersionError(version, allowed)` y `_check_version(version, allowed)` reciben la lista admitida, así que el mensaje de error nombra la lista de cada función.

### D6. Servicio: turno de sesión

Dos piezas con responsabilidades separadas:

- **`EstimationService.estimate_turn`** (en `llm_service.py`): el pipeline de LLM de un turno, **sin estado**. Recibe el historial y el metadata como datos. Comparte con `estimate` un paso privado `_generate(...)`: llamada estructurada → `compute_estimate` → `render_markdown` → evaluación → `EstimationOutcome`. `estimate` se refactoriza para usarlo sin cambiar su comportamiento.
- **`SessionService`** (nuevo, `app/services/session_service.py`): el estado. Busca la sesión, toma el lock, extrae los adjuntos, llama a `estimate_turn` y, solo si todo va bien, añade el turno y actualiza el metadata.

```python
@dataclass(frozen=True)
class TurnOutcome:
    outcome: EstimationOutcome
    history_user: str        # user message to store (attachments' text replaced)


def estimate_turn(
    self,
    turn: SessionTurnInput,
    *,
    attachments: Sequence[ExtractedAttachment] = (),
    history: Sequence[ChatMessage] = (),
    project_metadata: ProjectMetadata | None = None,
    session_language: Language | None = None,
    options: GenerationOptions | None = None,
) -> TurnOutcome
```

**Orden en `estimate_turn`:**

1. `_check_options(options, allowed=SESSION_PROMPT_VERSIONS)`. `_check_options` gana el parámetro `allowed`; por defecto, `PROMPT_VERSIONS`.
2. `estimation_requested` con `mode="session"`, `attachments` (número) y `history_messages`.
3. **Enmascarado de PII** en el texto y el nombre de cada adjunto (D8). Se registra `attachment_pii_masked` con los recuentos.
4. **Regex**:
   - `screen_texts([("transcript", transcript)])`: inyección y PII, que bloquea;
   - después, por cada adjunto `i`, `screen_texts([("attachments", f"{name}\n{text}")])`. Ya está enmascarado, así que en la práctica solo puede saltar la inyección.

   La violación lleva `index=i`.
5. **Moderación**: una llamada con `[transcript, *textos enmascarados]`. Si se marca, 400 `moderation` con `field="input"`.
6. Idioma (D5) y `render_session_prompt`.
7. `messages = [*history, {"role": "user", "content": prompt.user}]`.
8. `_generate(...)` con `response_model_for(output_format, detail_level)`.
9. Devuelve `TurnOutcome(outcome, prompt.history_user)`.

**Orden en `SessionService.estimate`:**

```python
class SessionService:
    def __init__(self, store: SessionStore, estimation: EstimationService,
                 limits: AttachmentLimits) -> None: ...
    def create(self) -> str
    def info(self, session_id: str) -> SessionSnapshot      # SessionNotFoundError
    def estimate(self, session_id: str, turn: SessionTurnInput,
                 uploads: Sequence[UploadedFile],
                 options: GenerationOptions) -> SessionTurnOutcome
```

1. `with store.turn(session_id) as session:` → 404 / 409.
2. `structlog.contextvars.bound_contextvars(session=session_id[:8])`: todos los eventos del turno, `llm_call_*` incluidos, quedan correlacionados.
3. `extract_attachments(uploads, limits)` → `AttachmentError` (413/415/422). Va dentro del lock para que un doble envío reciba 409 enseguida, sin extraer dos veces.
4. `estimation.estimate_turn(turn, attachments=…, history=session.history.to_messages(), project_metadata=session.project_metadata, session_language=session.language, options=options)`.
5. **Solo con éxito**:
   - `session.history.append(user=t.history_user, assistant=t.outcome.text)`;
   - si no está fuera de alcance, `session.project_metadata = update_project_metadata(...)`;
   - `session.language = t.outcome.result.language`;
   - `turn_count += 1` y `updated_at`.

   Se registra `session_turn_completed`.
6. Devuelve `SessionTurnOutcome(outcome, session_id, turn=session.turn_count, project_metadata=session.project_metadata)`.

Cualquier excepción en los pasos 3-4 sale antes del paso 5: el turno no deja rastro (D1-9).

**Por qué dos clases**: `estimate_turn` se prueba con listas en memoria (sin almacén ni locks), y la sustitución del paso de generación por Actor-Critic-Boss no toca el estado (D15). *Alternativa descartada*: todo dentro de `EstimationService`, que mezclaría un pipeline sin estado con un almacén mutable y engordaría un fichero de 1200 líneas.

**Versión del prompt**: query param `prompt_version: SessionPromptVersion = "v4"` en `POST …/estimate`, simétrico a `/estimate`. Con un solo valor cuesta una línea y hace aditiva una `v5` (tier) en el directo. No se fija por sesión: el historial guarda mensajes ya renderizados, así que cambiar de versión entre turnos es posible y se documenta.

**Opciones de generación**: el turno usa `GenerationOptions(prompt_version=...)` con los demás valores por defecto (`num_examples=3`, modelo primario con fallback, `max_tokens=2048`, `evaluate=true`). El formulario multipart no expone `model`, `max_tokens`, `num_examples` ni `evaluate`. Contrato mínimo; la UI no los usa. La medición D14 usa el servicio directamente para probar Haiku.

**Caché**: sin cambios. La clave lleva el system (con el metadata) y todos los mensajes, así que solo acierta con una conversación idéntica (p. ej. dos sesiones con el mismo primer turno), y entonces es correcto. Un acierto también añade el turno y actualiza el metadata.

**`SessionTurnInput`** (en `app/domain.py`):

```python
MIN_TRANSCRIPT_CHARS = 1
MAX_TRANSCRIPT_CHARS = 20_000


class SessionTurnInput(BaseModel):
    model_config = ConfigDict(frozen=True)
    transcript: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=MIN_TRANSCRIPT_CHARS, max_length=MAX_TRANSCRIPT_CHARS
        ),
    ]
    project_type: ProjectType
    detail_level: DetailLevel
    output_format: OutputFormat
```

- **Mínimo 1** (frente a los 20 de `description` y del oficial): un seguimiento legítimo puede ser muy corto ("Sin app Android"). Un primer turno vacío de contenido acabará fuera de alcance, que ya está resuelto.
- **Máximo 20 000**: el límite de transcripción de S02. Una reunión de una hora transcrita cabe, y el peor caso del historial sigue siendo manejable (D3). El oficial admite 80 000, que multiplicado por 6 turnos se va a ~120 k tokens.
- *Alternativa descartada*: reutilizar `EstimationInput`. Su `description` tiene 20-2000 caracteres y lleva `reference_projects`, que el turno no usa.

### D7. Adjuntos: `app/attachments/extractor.py`

```python
AttachmentKind = Literal["pdf", "docx"]
AttachmentErrorReason = Literal["too_many_files", "file_too_large", "too_much_text",
                                "unsupported_type", "unreadable", "encrypted", "no_text"]

@dataclass(frozen=True)
class AttachmentLimits:
    max_files: int; max_bytes: int; max_chars: int

@dataclass(frozen=True)
class UploadedFile:          # built by the router from UploadFile, framework-free
    filename: str
    data: bytes

@dataclass(frozen=True)
class ExtractedAttachment:
    index: int; name: str; kind: AttachmentKind; text: str; size_bytes: int; pages: int | None

class AttachmentError(Exception):
    reason: AttachmentErrorReason; index: int | None; message: str   # fixed English, no content

def read_limited(stream: BinaryIO, max_bytes: int, index: int) -> bytes  # file_too_large
def extract_attachments(files: Sequence[UploadedFile], limits: AttachmentLimits) -> list[ExtractedAttachment]
def safe_attachment_name(raw: str, index: int, kind: AttachmentKind) -> str
```

Solo stdlib, `pypdf` y `python-docx`. Sin FastAPI (el router convierte `UploadFile` en `UploadedFile`), sin LiteLLM y sin `services`.

**Lectura acotada** (router, síncrono):

- Starlette ya ha volcado cada fichero a un `SpooledTemporaryFile` (en disco a partir de 1 MB) antes del endpoint.
- El router comprueba primero `len(attachments) > max_files` → `too_many_files`, sin leer nada.
- Por cada fichero, `upload.size > max_bytes` → `file_too_large`. Si no, `read_limited(upload.file, max_bytes, i)` lee como máximo `max_bytes + 1` y falla si los supera, así que nunca se carga en memoria más del límite.
- El endpoint es síncrono (`def`), como `/estimate`. `upload.file.read` se ejecuta en el *threadpool* y la llamada bloqueante al LLM no necesita `run_in_threadpool`.
- *Alternativa descartada*: `async def` con `await upload.read()`. Obligaría a envolver todo el servicio en `run_in_threadpool`, y Starlette ya ha leído el cuerpo de todas formas.
- Las partes de fichero vacías y sin nombre se ignoran: Swagger UI puede enviarlas al dejar el campo en blanco. Verificar en la implementación que no dan 422.
- `safe_attachment_name` aplica `mask_pii` (de `app.guardrails.input`, puro) al nombre en bruto antes de sanear: `@` pasaría a `_` y el email llegaría sin enmascarar al LLM.

**Tipo** (los *magic bytes* deciden y la extensión debe coincidir; `Content-Type` se ignora, porque los navegadores lo informan de forma inconsistente):

| Tipo | *Magic* | Extensión | Si no |
|---|---|---|---|
| PDF | `b"%PDF-"` en los primeros 1024 bytes | `.pdf` | 415 |
| DOCX | `PK\x03\x04` **y** `word/document.xml` en el zip | `.docx` | 415 (`.doc`, `.xlsx`, `.odt`, zip genérico) |

**PDF** (pypdf):

- `PdfReader(BytesIO(data))`.
- Si `is_encrypted`: `decrypt("")`. Muchos PDF solo tienen contraseña de propietario. Si devuelve `NOT_DECRYPTED` o lanza una excepción (p. ej. AES sin `cryptography`), → `encrypted`. No se añade `cryptography`.
- `page.extract_text()` página a página, acumulando. En cuanto el total del turno pasa de `max_chars`, → `too_much_text` sin seguir parseando.
- Cualquier otra excepción de pypdf → `unreadable` (se registra el tipo).

**DOCX** (python-docx):

- Antes de parsear, con `zipfile`: como máximo 1000 entradas y una suma de `file_size` sin comprimir ≤ 50 MB (`MAX_DOCX_UNCOMPRESSED_BYTES`, constante). Si no, → `unreadable`. Es la defensa frente a *zip bombs*: el ratio de compresión no se mira porque el tamaño absoluto ya acota la memoria.
- `Document(BytesIO(data))` y `document.iter_inner_content()` en el orden del documento (python-docx ≥ 1.1, **verificar**):
  - de cada párrafo, su texto;
  - de cada tabla, una línea por fila con las celdas unidas por `" | "`, sin repetir celdas combinadas consecutivas, porque las especificaciones suelen ir en tablas.
- Se omiten cabeceras y pies.
- python-docx usa un parser lxml con `resolve_entities=False` (**verificar**): sin XXE.

**Texto**:

- NFC; se quitan los caracteres de control salvo `\n` y `\t`; un máximo de dos saltos de línea seguidos; recorte.
- Vacío tras normalizar → `no_text` ("PDF escaneado: sin OCR").

**Límites** (D10):

| Límite | Defecto | Motivo |
|---|---|---|
| `MAX_ATTACHMENTS` | 5 por turno | Una propuesta, una especificación y algún anexo; acota la latencia de extracción |
| `MAX_ATTACHMENT_BYTES` | 10 MiB por fichero | PDF con imágenes habituales; el texto real es una fracción |
| `MAX_ATTACHMENT_CHARS` | 60 000 **en total** por turno | ~15 k tokens; con la transcripción máxima (~5 k) y el system (~8 k), el turno actual queda por debajo de 30 k. Coincide con la solución oficial |

Hay un solo límite de caracteres, total y no por fichero, porque lo que importa es el contexto. Dos límites serían redundantes.

**Superar un límite rechaza (413), no trunca**. Truncar perdería en silencio parte de la especificación, y el contrato de respuesta está fijado (D1-3), así que no hay dónde avisarlo. *Alternativa descartada*: truncar con un marcador `[truncado]`, que es lo que hace la solución oficial.

**Nombre saneado** (`safe_attachment_name`), para el separador y el prompt:

1. *basename* tras el último `/` o `\`;
2. NFC;
3. se quitan las categorías Unicode `Cc`/`Cf`;
4. cualquier carácter fuera de `[\w .,()+-]` → `_`, con lo que desaparecen `<`, `>`, `:` y las comillas;
5. las secuencias de 3 o más `-` → `-`, para que no se pueda falsificar `---`;
6. se colapsan los espacios;
7. máximo 80 caracteres conservando la extensión;
8. si queda vacío → `attachment-{index+1}.{kind}`.

Después, se enmascara la PII (D8).

**Errores**: `{"detail": {"reason", "index", "message"}}`.

- `index` es la posición del fichero (desde 0) o `null` (`too_many_files`).
- **No se devuelve el nombre del fichero**: puede contener datos personales, y la UI ya conoce su lista y traduce el índice a su propio nombre.
- `message` es una frase fija en inglés por `reason` (con el límite numérico configurado cuando aplica), nunca contenido.

| `reason` | HTTP |
|---|---|
| `too_many_files`, `file_too_large`, `too_much_text` | **413** (límites) |
| `unsupported_type` | **415** |
| `unreadable`, `encrypted`, `no_text` | **422** |

La regla es simple: los límites dan 413, el tipo da 415 y el contenido ilegible da 422. 413 no está en la propuesta: ver Open Questions.

### D8. Guardrails: enmascarado en adjuntos e índice de la violación

**`app/guardrails/input.py`** gana:

```python
PiiKind = Literal["email", "phone", "iban"]
PII_PLACEHOLDERS: dict[PiiKind, str] = {"email": "[EMAIL]", "phone": "[PHONE]", "iban": "[IBAN]"}

def mask_pii(text: str) -> tuple[str, dict[PiiKind, int]]   # masked text, counts per kind
```

- Se reutilizan **los mismos patrones** que `_pii_rule`, sobre el texto original, en este orden:
  1. email;
  2. IBAN, **solo** si pasa mod-97;
  3. teléfonos (las cinco reglas).

  El IBAN va antes que los teléfonos porque sus dígitos podrían casar con un patrón de teléfono.
- Es idempotente: los marcadores no casan con ningún patrón.
- El corpus de negativos de S04 ("10-12 semanas", "ISO 27001", "v2.3.1", …) no cambia al enmascarar.
- `InputGuardrailViolation(reason, field="", rule="", index: int | None = None)`.
  - `field` vale `"transcript"`, `"attachments"` o `"input"` (moderación).
  - `index` es la posición del adjunto.
  - `GuardrailHit.field` documenta los valores nuevos.
- **Respuesta 400 de sesión**: `{"detail": {"reason", "message", "field", "index"}}`. Así la UI puede decir "El adjunto «especificación.pdf» contiene instrucciones dirigidas al asistente". `/estimate` sigue enviando `{"reason", "message"}`, con su schema sin cambios.
- **Logs**: `input_guardrail_blocked` con `reason`, `field`, `rule` e `index`. Nunca el nombre ni el fragmento.
- **Falsos positivos más probables en documentos largos**: un manual técnico con `<system>` en un ejemplo de XML, o la especificación de un chatbot que cita "ignore previous instructions" como caso de prueba. Se acepta bloquear (D1-5) y se registra en Risks y Open Questions.

### D9. Contrato HTTP

Router nuevo `app/routers/sessions.py` (`tags=["sessions"]`, prefijo `/api/v1`). Solo valida, convierte y mapea errores. Depende de `SessionService` vía `Depends(get_session_service)`.

| Operación | Request | Respuestas |
|---|---|---|
| `POST /api/v1/sessions` | sin cuerpo | **201** `SessionCreated{session_id: str}` |
| `GET /api/v1/sessions/{session_id}` | `session_id: UUID` | **200** `SessionInfo`; **404**; **422** (UUID inválido) |
| `POST /api/v1/sessions/{session_id}/estimate` | multipart (abajo) + query `prompt_version: "v4"` | **200** `SessionEstimateResponse`; **400**; **404**; **409**; **413**; **415**; **422**; **502** |

**Multipart** (parámetros sueltos `Form`/`File`):

| Campo | Tipo | Reglas |
|---|---|---|
| `transcript` | `string` | obligatorio; `Form(min_length=1, max_length=20000)` sobre el valor recibido, y no vacío tras recortar (`SessionTurnInput`) |
| `project_type`, `detail_level`, `output_format` | enums de `app.domain` | obligatorios |
| `attachments` | `list[UploadFile]` | opcional; `.pdf`/`.docx`; límites de D7 |

El router construye `SessionTurnInput`. Un `ValidationError` (p. ej. solo espacios) se convierte en `RequestValidationError` con `loc = ["body", "transcript"]`, sin `input`. *Alternativa descartada*: un modelo `Form` de FastAPI junto a `File` en la misma operación. El soporte de modelos de formulario con ficheros no está comprobado, y los parámetros sueltos documentan igual en OpenAPI.

**Modelos** (`app/schemas.py`):

```python
class SessionCreated(BaseModel):
    session_id: str


class SessionInfo(BaseModel):
    session_id: str
    turn_count: int  # successful turns since creation
    history_turns: int  # turns currently in the window (<= max_turns)
    max_turns: int
    project_metadata: ProjectMetadata


class SessionEstimateResponse(EstimationResponse):
    session_id: str
    turn: int  # ordinal of this turn (== turn_count after it)
    project_metadata: ProjectMetadata  # after the update


class SessionError(BaseModel):  # 404 / 409
    reason: Literal["session_not_found", "session_busy"]
    message: str


class SessionGuardrailError(GuardrailError):  # 400 of the session endpoint
    field: Literal["transcript", "attachments", "input"]
    index: int | None


class AttachmentErrorDetail(BaseModel):  # 413 / 415 / 422 from attachments
    reason: AttachmentErrorReason
    index: int | None
    message: str
```

Más sus envoltorios `…Response{detail}` para `responses=`.

- **422** tiene dos formas: la de FastAPI (validación) y `AttachmentErrorDetail` (contenido de un adjunto). OpenAPI documenta las dos en la descripción del 422.
- **502**: como `/estimate`, con el `detail` genérico `PROVIDER_ERROR_DETAIL`.
- *Alternativa descartada*: un `message_count` como en la solución oficial. `turn_count` y `history_turns` separan lo que se ha hablado de lo que el modelo ve, que es justo la lección del ejercicio.

### D10. Configuración

| Variable | Campo | Tipo y rango | Defecto |
|---|---|---|---|
| `MAX_CONVERSATION_TURNS` | `max_conversation_turns` | `int`, `1..50` | 6 |
| `MAX_SESSIONS` | `max_sessions` | `int`, `1..100000` | 1000 |
| `MAX_ATTACHMENTS` | `max_attachments` | `int`, `0..20` (0 desactiva los adjuntos: cualquier fichero da 413) | 5 |
| `MAX_ATTACHMENT_BYTES` | `max_attachment_bytes` | `int`, `1024..52428800` | 10485760 |
| `MAX_ATTACHMENT_CHARS` | `max_attachment_chars` | `int`, `1000..200000` | 60000 |

- `get_session_store()` (`lru_cache`, en `session_service.py`) construye el almacén único del proceso con `max_sessions` y `max_conversation_turns`. `get_session_service()` es la dependencia de FastAPI; los tests sobrescriben `get_session_store`.
- `.env.example`, `CONFIG_ENV_VARS` del `conftest` y el README se actualizan.
- `MAX_DOCX_UNCOMPRESSED_BYTES` (50 MB) y las 1000 entradas del zip son constantes de seguridad, no configuración.

### D11. Observabilidad

Todos los eventos sin contenido: ni transcripción, ni texto ni **nombre** de adjuntos, ni valores del metadata.

| Evento | Campos |
|---|---|
| `session_created` | `session` (8 primeros caracteres del UUID), `sessions` (total en el almacén) |
| `session_evicted` | `session`, `turn_count`, `idle_s` |
| `session_not_found` | `session` (si el UUID es válido) |
| `session_busy` | `session` |
| `session_turn_completed` | `session`, `turn`, `history_messages` (enviados al LLM antes del nuevo `user`), `history_turns`, `attachments`, `out_of_scope`, `language`, `metadata_updated` (bool), `metadata_fields` (nombres de los campos con valor), `technologies` (número) |
| `attachment_extracted` | `index`, `kind`, `size_bytes`, `chars`, `pages` |
| `attachment_rejected` | `index`, `reason`, `kind` (si se sabe), `size_bytes` (si se sabe), `error_type` (en `unreadable`) |
| `attachment_pii_masked` | `index`, `email`, `phone`, `iban` (recuentos), solo si hay alguno |
| `input_guardrail_blocked` | como S04 + `index` |

- **Nombre de fichero nunca**: es texto del usuario y suele llevar el nombre del cliente o de personas. Para depurar basta con `index` y `kind`.
- **`session` truncado**: el id completo es una capacidad (D2). 8 caracteres hexadecimales aleatorios bastan para correlacionar.
- Durante el turno se hace `bound_contextvars(session=…)` (el `merge_contextvars` ya está configurado), así que `llm_call_*`, `prompt_rendered` y los guardrails llevan `session`.
- Un test con marcadores secretos en la transcripción, en el texto de un PDF y en el nombre del fichero comprueba que no aparecen en ningún evento.

### D12. Streamlit (`frontend-engineer`)

**Estado** (`st.session_state`):

- `session_id`;
- `turns: list[dict]`, con transcripción, nombres de los adjuntos, `data` de la respuesta y tiempo de cliente;
- `uploader_key: int`, para vaciar el selector tras un envío con éxito.

**Ciclo de la sesión:**

- **Al cargar**, si no hay `session_id`: `POST /api/v1/sessions`. Si falla, aviso de API caída y se reintenta en el siguiente *rerun*.
- **En cada *rerun***: `GET /api/v1/sessions/{id}` (`GET_TIMEOUT` = 3 s) para el panel.
  - Un 404 (API reiniciada o sesión desalojada) crea una sesión nueva, vacía `turns` y muestra: "La conversación anterior ya no existe en el servidor (se reinició o caducó). Empieza de nuevo."
  - Así el GET justifica su existencia: detecta la pérdida **antes** de que el usuario escriba.

**Barra lateral:**

1. Health (como S04).
2. **"Memoria del proyecto"** (`project_metadata`): nombre, equipo asumido, tecnologías y alcance acordado. Si está vacío: "Aún no hay datos del proyecto". Debajo, `Turnos: {turn_count} · en la ventana: {history_turns}/{max_turns}` y el id corto.
3. Botón **"Nueva conversación"**: `POST /sessions`, vacía `turns` y rota `uploader_key`.

**Cuerpo:**

- Un `st.chat_message` por turno:
  - **`user`**: la transcripción (los primeros 500 caracteres y el resto en un `st.expander`) y un `st.caption` con los nombres de los adjuntos.
  - **`assistant`**: el aviso de fuera de alcance si aplica, el `text` en Markdown y un `st.expander("Métricas y validación")` **plegado** con las métricas, la validación y el `prompt_version`.
- Debajo, `st.form`:
  - `transcript` (`text_area`, `max_chars=20000`);
  - los tres enums, que conservan su valor entre turnos;
  - `st.file_uploader("Adjuntos", type=["pdf", "docx"], accept_multiple_files=True, key=f"uploads-{uploader_key}")`;
  - "Opciones avanzadas" con la versión (`SESSION_PROMPT_VERSIONS = ("v4",)`);
  - botón "Enviar".

**Envío:**

- Comprobaciones en el cliente: transcripción no vacía, ≤ 20 000; ≤ 5 ficheros y ≤ 10 MiB cada uno. Son copias de los valores por defecto, con un test de paridad contra `app.domain` y `Settings`; el servidor sigue siendo la fuente de verdad.
- La petición:

  ```python
  client.post(
      f"/api/v1/sessions/{id}/estimate",
      params={"prompt_version": v},
      data={"transcript": ..., "project_type": ..., "detail_level": ..., "output_format": ...},
      files=[("attachments", (f.name, f.getvalue(), MIME[ext])) for f in uploads],
  )
  ```

  `MIME`: `application/pdf` y `application/vnd.openxmlformats-officedocument.wordprocessingml.document`.
- `SESSION_ESTIMATE_TIMEOUT = httpx.Timeout(connect=3.0, read=180.0, write=30.0, pool=3.0)`: el historial y los adjuntos alargan la llamada, y se suben hasta 50 MB.

**Errores** (mensajes en español, por `reason`):

- **400**: `GUARDRAIL_MESSAGES`, con el nombre local del adjunto si `field == "attachments"`.
- **404**: recrea la sesión, conserva la transcripción en el formulario y avisa (no reenvía solo: la memoria se ha perdido y el usuario debe saberlo).
- **409**: "Ya hay una estimación en curso para esta conversación."
- **413/415/422 de adjuntos**: `ATTACHMENT_MESSAGES[reason]` con el nombre local por `index`.
- **Otros 422**: campos, como en S04.
- **502** y transporte: el genérico.

La UI sigue sin imports de `app` y sigue pintando el `text` (no `result`).

### D13. Tests (`qa-engineer`, sin red)

**Integración (los tres del enunciado)**, en `tests/test_sessions_api.py`, con `httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")`, `pytestmark = pytest.mark.anyio` y un fixture `anyio_backend` que devuelve `"asyncio"`. El plugin viene con `anyio 4.15.1`; primera tarea: comprobar que el marcador está registrado.

- `ASGITransport` **no ejecuta el *lifespan***. Se sobrescriben `get_settings`, `get_llm_provider`, `get_moderator` y `get_session_store` (un almacén nuevo por test).

1. **Dos turnos y metadata**:
   - `FakeProvider` con una lista de *payloads*: turno 1 con `title` "Clinic booking platform", equipo 2+1 y `technologies` `["Twilio"]`; turno 2 con equipo 2+1+1 y `["Stripe", "twilio"]`.
   - Comprueba:
     - `project_metadata` tras cada turno (nombre, 3 → 4, `["Twilio", "Stripe"]`, alcance);
     - `GET` coherente (`turn_count=2`);
     - que la segunda llamada al proveedor recibe 3 mensajes (`user`, `assistant` = Markdown del turno 1, `user`);
     - que el system de la segunda contiene `project_name: Clinic booking platform` dentro de `<project_metadata>`, y el de la primera, el bloque vacío.
2. **PDF que influye en la estimación**: `FakeProvider` con un *responder* `(system, messages) -> payload` que añade una fila "Payments integration" (más horas) si el último `user` contiene un marcador que **solo** está en el PDF. Sin adjunto `total_hours = H` y con el PDF, `> H`. Además:
   - el texto del PDF está entre `--- attachment: spec.pdf ---` y `</attachments>`;
   - en el siguiente turno, el `user` guardado lleva `[…]` y no el marcador.
3. **Ocho turnos y ventana**: con `MAX_CONVERSATION_TURNS = 6` (por defecto). En la llamada `k` (desde 1), `len(messages) == 2 * min(k - 1, 6) + 1` y nunca más de 13. Tras el turno 8, el marcador del turno 1 no aparece en ningún mensaje, `GET` da `turn_count=8` y `history_turns=6`.

**`FakeProvider`** (en `conftest.py`): `structured` acepta también una lista (un *payload* por llamada) o un *callable* `(system, messages) -> payload`. Los usos actuales no cambian.

**Fixtures de ficheros** (`tests/attachments/factories.py`):

- **`make_pdf(lines: list[str]) -> bytes`**: PDF mínimo hecho a mano, sin reportlab.
  - Objetos catálogo → páginas → página con `/Font << /F1 Helvetica >>` → *content stream* `BT /F1 12 Tf 72 720 Td (línea) Tj … ET`.
  - La tabla `xref` se calcula con los offsets reales y el texto es ASCII (con escapes `\(`, `\)`, `\\`).
  - Un test verifica que pypdf extrae las líneas, así que el fixture se valida a sí mismo.
- PDF cifrado: `PdfWriter`, `append(PdfReader(make_pdf(...)))` y `encrypt("pw", algorithm="RC4-128")`. RC4 no necesita `cryptography`.
- PDF sin texto: `PdfWriter().add_blank_page(612, 792)`.
- PDF corrupto: `b"%PDF-1.4\n" + basura`.
- **`make_docx(paragraphs, table=None) -> bytes`** con python-docx (`Document()`, `add_paragraph`, `add_table`, `save(BytesIO)`).
- *Zip bomb*: se baja `MAX_DOCX_UNCOMPRESSED_BYTES` con *monkeypatch* a 1 KB, en lugar de comprimir 60 MB en el test.

**Unitarios:**

- `ConversationHistory`:
  - ventana tras cada `append`;
  - pares y alternancia;
  - `to_messages()` sin system y en orden;
  - `Turn.number` monotónico tras recortar;
  - `max_turns < 1` → `ValueError`.
- `SessionStore`:
  - `create`/`get`;
  - 404 con un id desconocido;
  - LRU con `max_sessions=2` (un `get` refresca);
  - `turn()` ocupado → `SessionBusyError`, y el lock se libera tras una excepción;
  - creación concurrente desde varios hilos sin superar el tope.
- `update_project_metadata`:
  - reglas de la tabla de D4;
  - fuera de alcance no toca nada;
  - deduplicación por `casefold`;
  - tope de 30 descartando las más antiguas.
- Schema: normalizador de `technologies` (vacíos, más de 60, duplicados, más de 15) y posición del campo en `model_json_schema()["properties"]`.
- **Dorado de `v3`**:
  - hashes de los system y user de `v3` para 4 combinaciones, calculados antes del cambio;
  - JSON de los ejemplos de `v3` sin `technologies`;
  - en `v4`, con ellas.
- **`v4`**:
  - bloque vacío en el primer turno;
  - solo campos con valor;
  - orden de secciones (ejemplos < `<project_metadata>` < `## Reglas` < línea de idioma);
  - el prefijo hasta los ejemplos es idéntico con dos metadatas distintos;
  - adjuntos antes de `<transcript>` con su separador;
  - `history_user` con `[…]`;
  - el idioma heredado de la sesión cuando la detección da `None`;
  - carpetas = `PUBLISHED_PROMPT_VERSIONS`.
- Extractor:
  - cada `reason`;
  - *magic* frente a extensión (PDF renombrado a `.docx` → 415; `.doc` → 415);
  - límite de caracteres total entre dos ficheros;
  - tablas DOCX;
  - nombre saneado (rutas, `<`, `>`, `---`, controles, longitud, vacío);
  - `read_limited`.
- `mask_pii`:
  - cada tipo;
  - IBAN inválido no se enmascara;
  - idempotencia;
  - el corpus de negativos queda igual.
- Servicio:
  - orden: un adjunto con inyección no llama ni a la moderación ni al proveedor; la PII de un adjunto se enmascara y el proveedor recibe `[EMAIL]`;
  - la moderación recibe la transcripción y los adjuntos en una sola llamada;
  - un fallo (400, 502, 413) no añade turno ni toca el metadata;
  - un turno fuera de alcance añade turno pero no toca el metadata.
- API:
  - 201, 404 y 422 (UUID);
  - 409, simulado con el lock tomado;
  - 413/415/422 con la forma exacta y sin el nombre del fichero;
  - 400 con `field` e `index`;
  - OpenAPI con los tres endpoints, el multipart y los códigos.
- Logs: marcadores secretos (transcripción, texto del PDF, nombre del fichero) ausentes.
- CI: sin cambios en el *workflow*; las suites nuevas entran solas en `pytest`.

### D14. Medición con llamadas reales (plan para `tasks.md`)

Requiere **autorización del usuario**. Sin Redis y con moderación real. Un script en el *scratchpad* llama a la API levantada en local (o al servicio, para Haiku). El PDF se genera con `make_pdf`.

| Bloque | Modelo | Llamadas |
|---|---|---|
| ES, 3 turnos: muestra ES de `samples/` → "Añadimos pagos con Stripe y login con Google" → "Quitad el panel de métricas; el equipo será de 3 personas" | gpt-4o-mini | 3 |
| EN, 3 turnos: muestra EN → "Add Stripe payments and Google sign-in" → "Drop the analytics dashboard" | gpt-4o-mini | 3 |
| EN, 2 turnos con PDF: muestra EN → "See the attached technical spec" + PDF que pide integración con SAP y modo *offline* | gpt-4o-mini | 2 |
| ES, 2 turnos (como los dos primeros de ES), `GenerationOptions(model="claude-haiku-4-5")` vía servicio | claude-haiku-4-5 | 2 |
| Guardrails: PDF con "ignore previous instructions" (400 sin LLM) y PDF con un email (enmascarado; el turno sigue) | — / gpt-4o-mini | 0 / 1 |

**Coste estimado**:

- gpt-4o-mini: ~9 llamadas de ~10-15 k tokens de entrada ≈ 0,0025 USD cada una, ~0,025 USD.
- Haiku: 2 × ~0,018 USD.
- Total ≈ 0,06 USD, **≤ 0,10 USD** contando algún reintento.

**Umbrales**:

- **Coherencia**: `result.title` de los turnos 2 y 3 igual al del turno 1 (sin distinguir mayúsculas), 100 %.
- **Metadata visible**: al menos un campo cambia en cada turno 2 y 3; `mentioned_technologies` contiene Stripe y Google (o sus equivalentes) tras el turno 2, 100 %; el equipo del turno 3 de ES suma 3.
- **Ningún seguimiento fuera de alcance** (el riesgo nuevo de los mensajes breves), 100 %.
- **PDF**: el `total_hours` del turno con PDF difiere del turno 1, y SAP aparece en las tecnologías.
- **Idioma**: 100 % en el texto libre y en las etiquetas, también en los seguimientos breves.
- `validation.score = 1.0`; ningún 502; al menos el 80 % al primer intento.
- Guardrails: la inyección da 400 sin llamar al LLM; la PII enmascarada sale en `attachment_pii_masked` y el turno da 200.
- Se registran `input_tokens` por turno, para contrastar el crecimiento de D3.

La ventana de 8 turnos solo se prueba en los tests (no aporta nada con red). Si algo queda por debajo, **se para y se vuelve al usuario con los datos**, como en S04 D13. Los resultados se añaden a este documento.

#### Resultados de la medición (2026-11-10)

En el proceso, sin Redis y con moderación real: 13 turnos con LLM, más un bloqueo sin LLM. Coste: unos 0,041 USD (0,012 gpt-4o-mini y 0,029 Haiku), además de 0,0008 USD de una llamada accidental durante la tarea 8.

| Bloque | Modelo | Título conservado | Metadata | Fuera de alcance | `score` | `input_tokens` por turno |
|---|---|---|---|---|---|---|
| ES, 3 turnos | gpt-4o-mini | 3/3 | turno 2: Stripe y Google Login; turno 3: equipo de 4 a 3 | 0/3 | 1,0 | 5329 → 5786 → 6218 |
| EN, 3 turnos | gpt-4o-mini | 3/3 | turno 1: Stripe y Square; turno 2: + Google Sign-In | 0/3 | 1,0 | 5296 → 5783 → 6231 |
| EN + PDF (SAP y modo offline) | gpt-4o-mini | 2/2 | + SAP Business One; horas de 385 a 445 | 0/2 | 1,0 | 5296 → 5837 |
| Adjunto con inyección | — | — | — | — | — | 400 `prompt_injection`, `field=attachments`, `index=0`, sin LLM |
| Adjunto con email | gpt-4o-mini | sí | sin cambios | no | 1,0 | 6254; `attachment_pii_masked` registrado |
| ES, 2 turnos | claude-haiku-4-5 | 2/2 | Stripe, + Google Sign-In | 0/2 | 1,0 | 7481 → 8444 |

- **Umbrales cumplidos**:
  - coherencia del título 100 %;
  - las tecnologías del turno 2 contienen Stripe y Google en las tres conversaciones;
  - el equipo del turno 3 de ES suma 3;
  - ningún seguimiento breve queda fuera de alcance;
  - el PDF cambia `total_hours` y añade SAP;
  - idioma correcto en todos los turnos;
  - `score = 1.0`, ningún 502 y el 100 % al primer intento (también en `detailed`, gracias a los supuestos por fase en los ejemplos de `v4`; aquí solo se midió `medium`).
- **Crecimiento del contexto**: unos 450 tokens por turno con gpt-4o-mini (el Markdown de la respuesta anterior más el mensaje nuevo), como preveía D3. Con 6 turnos en la ventana serían unos 7.600 tokens de entrada, dentro de lo razonable.
- **Hallazgo menor**: en el turno 1 de ES (la muestra nombra Stripe y el TPV Square), gpt-4o-mini dejó `technologies` vacío; en EN y con Haiku sí las recogió. El turno 2 las recoge. No bloquea, pero es un buen candidato para el directo (extractor dedicado o ejemplo en español con tecnologías).
- En la conversación de la captura (11.3), el turno 2 ("login con Google") añadió la tarea, pero `technologies` no incluyó Google; el turno 3 sí recogió el TPV Square del PDF. Esto confirma que el campo `technologies` es el punto débil de derivar la memoria de la estimación: es el primer candidato a mejorar en el directo.
- **Error del script**: el primer intento con Haiku usó `GenerationOptions(model=...)` sin `prompt_version="v4"` y el servicio lo rechazó (`InvalidOptionError`, sin llamada). El endpoint HTTP no tiene ese problema porque fija `v4` por defecto en la sesión.

### D15. Cómo habilita el directo de S05 (sin implementarlo)

- **Compresión con anclas**:
  - `ConversationHistory` guarda `Turn(number, user, assistant)`: la unidad que se resume o se ancla, con un ordinal estable.
  - El recorte ocurre en un único punto (`append`) y el único consumidor es `SessionService` vía `to_messages()`. Una estrategia "resumen + ventana + anclas" sustituye ese método o envuelve la clase sin tocar el servicio de LLM.
  - El resumidor puede ser `generate_structured` con otro `response_model`, y reutiliza fallback, caché, reintentos y logs.
  - `history_turns` frente a `turn_count` en la API ya muestra qué se olvida.
- **Tier dinámico**:
  - `project_metadata.j2` es un parcial que pueden incluir las plantillas por tier (notas §3: "parciales con include para metadata del proyecto").
  - `render_session_prompt(version)` y `response_model_for(...)` son los dos puntos que elegiría un `resolve_tier_config`.
  - El query param `prompt_version` hace aditiva una `v5`.
- **Actor-Critic-Boss**:
  - `estimate_turn` aísla la generación en `_generate`. Un Actor (la llamada actual), un Critic (`generate_structured` con un modelo de crítica) y un Boss sustituyen ese paso.
  - Los guardrails, el render y la actualización de la sesión (solo con éxito) no cambian.
  - `ComputedEstimate` y `EstimationResult` son la entrada natural del Critic.
- **Evaluación** (notas 04): el `FakeProvider` con *responder* y las conversaciones de la medición D14 son la semilla de un *golden dataset* multi-turno.
- Lo que **no** se hace: ni resumen, ni anclas, ni tiers, ni crítico, ni TTL, ni persistencia.

**Ajustes tras los tests (orquestador, 2026-11-10):**
- `safe_attachment_name` enmascara los datos personales solo en la raíz del nombre, para que `jefe@cliente.es.pdf` quede como `[EMAIL].pdf` y conserve la extensión. Los corchetes se admiten en el nombre saneado para que sobrevivan los marcadores.
- Streamlit envía todos los campos como partes multipart aunque no haya adjuntos; `httpx` usaría `application/x-www-form-urlencoded` con `files` vacío.
- Orden de las comprobaciones en `POST /sessions/{id}/estimate`: `too_many_files` y `file_too_large` se comprueban antes de buscar la sesión. Una sesión desconocida con 6 ficheros recibe 413, no 404. Se acepta así: el límite es una protección barata que no necesita la sesión.

## Risks / Trade-offs

- **[Sesiones volátiles y por proceso]** Se pierden al reiniciar y no se comparten entre workers. Mitigación: un worker en el `Dockerfile` (ya es así), un 404 explícito y la UI que recrea la sesión y avisa. Documentado en README y docstrings.
- **[Sin autenticación]** Quien conoce un `session_id` lee y escribe la sesión. Mitigación: UUID4 (122 bits) y nunca se registra entero. La autenticación pertenece al backend de negocio (tier, JWT en las notas).
- **[Memoria]** Peor caso de ~170 MB con 1000 sesiones (D2). Mitigación: LRU y `MAX_SESSIONS` configurable.
- **[Cuerpo de la petición sin tope global]** Starlette vuelca el multipart entero a disco antes del endpoint: un cliente puede subir más de 5 × 10 MiB antes de recibir el 413. Mitigación: lectura acotada a memoria (D7). Se recomienda un límite de cuerpo en el proxy de despliegue (fuera de alcance).
- **[PDF maliciosos o costosos]** pypdf puede tardar con PDF patológicos. Mitigación: tope de bytes, corte por caracteres a mitad del parseo y las protecciones de descompresión de pypdf. Se registra `latency` en `attachment_extracted` si la medición lo pide.
- **[Falsos positivos de inyección en documentos]** XML de ejemplo con `<system>`, o especificaciones de chatbots. Mitigación: corpus de negativos con documentos técnicos y `rule` + `index` en los logs. La alternativa (neutralizar en vez de bloquear) va a Open Questions.
- **[Deriva multi-turno]** El modelo cambia el título, devuelve solo el delta o marca fuera de alcance un seguimiento breve. Mitigación: instrucciones explícitas de `v4` (D5) y umbrales de D14.
- **[Detalle de adjuntos perdido en turnos posteriores]** Solo queda lo que entró en la estimación y el metadata (D3). Mitigación: volver a adjuntar; es el caso de uso de la compresión con anclas del directo.
- **[Tecnologías que solo crecen]** Si el cliente pasa de Rails a Node, las dos quedan en el metadata. Mitigación: el mensaje actual prevalece sobre `<project_metadata>` en el prompt (D5) y el tope descarta las más antiguas. Retirar de verdad es la política de olvido 1 (Open Questions).
- **[El Markdown como turno `assistant`]** El modelo ve sus respuestas previas como texto y responde con una herramienta forzada. Es válido en los dos proveedores, pero el Markdown ocupa más tokens que el JSON (~1,5 k frente a ~1 k). Decisión fijada (D1-8); D14 vigila el idioma y la coherencia.
- **[Prompt caching parcial]** El prefijo estable solo ayuda con OpenAI y solo si tipo, formato y nivel no cambian entre turnos. Anthropic necesitaría `cache_control` explícito (no se usa).
- **[Invalidación de la caché]** El schema cambia (`technologies`) y todas las entradas estructuradas caducan una vez. Se acepta.
- **[409 con dobles clics]** Un usuario que pulsa dos veces ve un aviso en lugar de esperar. La UI desactiva el botón mientras espera (`st.spinner` dentro del envío).
- **[Moderación de adjuntos]** El texto, ya enmascarado, se envía a OpenAI aunque el primario sea Anthropic (como en S04). `MODERATION_ENABLED=false` lo desactiva.
- **[APIs sin verificar]** `iter_inner_content` (python-docx ≥ 1.1), `resolve_entities=False` en python-docx, `PdfReader.decrypt` y el plugin `anyio`. Mitigación: primera tarea de verificación contra las versiones instaladas; si algo difiere, se vuelve aquí.

**Verificación de APIs (tarea 1.2)** (`pypdf 7.0.1`, `python-docx 1.2.0`, `lxml 6.1.3`, `anyio 4.15.1`, `python-multipart 0.0.32`):

- `Document.iter_inner_content()` existe y devuelve párrafos y tablas en orden de documento (`Paragraph, Table, Paragraph`). Sin cambios.
- El parser de python-docx es `etree.XMLParser(remove_blank_text=True, resolve_entities=False)`. Un DOCX con entidad externa `file:///etc/hostname` en `document.xml` se lee con texto vacío: no hay XXE. Sin cambios.
- `PdfReader.decrypt("")`: con **contraseña de usuario vacía** (RC4-128) devuelve `PasswordType.USER_PASSWORD` y el PDF se lee; con **contraseña de usuario no vacía** devuelve `PasswordType.NOT_DECRYPTED` (valor 0) y `len(reader.pages)` lanza `FileNotDecryptedError`. Para el test del PDF cifrado hay que crearlo con `PdfWriter.encrypt("secret", algorithm="RC4-128")` (con contraseña de usuario vacía se descifra). **Ajuste**: la rama `encrypted` se decide por `decrypt("") == NOT_DECRYPTED`.
- **Ajuste**: pypdf solo cifra/descifra con AES si está instalado `cryptography` (no lo está; no se añade): `PdfWriter.encrypt(..., algorithm="AES-*")` lanza `DependencyError`, y leer un PDF AES lanzaría el mismo error. El extractor debe tratar `pypdf.errors.DependencyError` como `encrypted` (o `corrupt_file`) y no dejarlo escapar; el test usa RC4.
- El plugin `anyio` registra `@pytest.mark.anyio`; el `anyio_backend` por defecto solo parametriza `asyncio` (no hay `trio`). Un test `async` con `httpx.AsyncClient(transport=httpx.ASGITransport(app=app))` pasa. **Nota para los tests**: `/health` construye `Settings`, así que el test necesita `OPENAI_API_KEY` (la fixture autouse del `conftest` borra las variables) o el override de `get_settings`.
- `python-multipart` ya venía instalado como dependencia transitiva (`0.0.32`); `lxml 6.1.3` es la única transitiva nueva de `python-docx`, y `pypdf` no añade ninguna.

## Migration Plan

1. `uv add pypdf python-docx python-multipart` (versiones `>=` de las resueltas) y revisar `lxml` en `uv.lock`. Verificar las APIs de la última viñeta de Risks y el plugin `anyio`.
2. **Antes de tocar el código**: el test dorado de `v3` (hashes de system/user) (qa).
3. `app/domain.py` (`ChatMessage`, `SessionTurnInput`, límites) y `app/config.py` (D10; `.env.example`).
4. `app/estimation/` (`technologies` en el schema y en `ComputedEstimate`; `example_payloads(include_technologies)`) y los datos de `app/context/examples.py`.
5. `app/sessions/` y `app/attachments/`.
6. `app/guardrails/input.py` (`mask_pii`, `index`, etiquetas nuevas en `prompt_tag`).
7. `app/prompts/` (`v4`, `render_session_prompt`, versiones).
8. `app/services/` (`_generate`, `estimate_turn`, `_check_options(allowed)`, `session_service.py` y reexportaciones).
9. `app/schemas.py`, `app/routers/sessions.py` y `app/main.py`.
10. `streamlit_app.py`.
11. Tests y CI (D13).
12. README (camino B y por qué, cómo se extrae el `project_metadata`, volatilidad, límites) y `CLAUDE.md` (arquitectura, propietarios de `app/sessions/` y `app/attachments/`, dependencias).
13. Medición D14.

**Notas de migración:**

- `/api/v1/estimate` solo gana `result.technologies`, que es aditivo. La caché estructurada se invalida una vez por el hash del schema. Docker: sin cambios (un worker).
- **Rollback**: revertir los commits del change. Las sesiones en memoria desaparecen con el proceso.

## Open Questions

**Resueltas por el orquestador (2026-11-10)** con la opción recomendada en las 14, con la autorización del usuario para decidir sin consultarle: 409 `session_busy` con lock no bloqueante; 413 para los límites de adjuntos; rechazar (no truncar) al superar `MAX_ATTACHMENT_CHARS`; texto de los adjuntos sustituido por `[…]` en el historial; 422 para un `session_id` mal formado; los límites de la pregunta 6; `technologies` tras `confidence_pct` con topes 15/30; tecnologías acumuladas; `index` en lugar del nombre de fichero; 400 de sesión con `field` e `index`; la inyección en adjuntos bloquea; query `prompt_version` (`"v4"`) en el endpoint de sesión; `app/sessions/` y `app/services/session_service.py` para `ai-engineer` y `app/attachments/` para `backend-engineer`; medición D14 autorizada dentro del presupuesto de llamadas reales de esta puesta al día (0,30 USD en total; el change anterior gastó 0,10).


**Candidatas a "resueltas por el orquestador"** (se ha elegido la opción recomendada; el usuario puede revocarlas):

1. **Concurrencia en una sesión**: 409 `session_busy` con lock no bloqueante (recomendado) frente a un lock bloqueante que encola el segundo turno.
2. **Código para los límites de adjuntos**: **413** para `too_many_files`, `file_too_large` y `too_much_text` (recomendado), frente a 422 para todo lo que no sea el tipo. La propuesta solo menciona 415 y 422: habría que añadir 413 (y 409) a la spec `conversation-sessions`/`attachments`.
3. **Rechazar o truncar** al superar `MAX_ATTACHMENT_CHARS`: rechazar con 413 (recomendado, porque el contrato no tiene dónde avisar) o truncar con un marcador como la solución oficial.
4. **Texto de los adjuntos en el historial**: sustituido por `[…]` en los turnos posteriores (recomendado, por coste) o conservado entero.
5. **`session_id` mal formado**: 422 de FastAPI (recomendado) o 404.
6. **Límites**: transcripción `1..20000`; 5 adjuntos, 10 MiB por fichero y 60 000 caracteres en total por turno; `MAX_SESSIONS=1000` con LRU; `MAX_CONVERSATION_TURNS=6`.
7. **Posición de `technologies`** en el schema (tras `confidence_pct`), tope de 15 por turno y 30 en el metadata con descarte de las más antiguas.
8. **Tecnologías acumuladas** (propuesta) frente a sobrescritas con la lista del turno. La segunda permitiría la política de olvido 1 de las notas (el modelo devuelve la lista vigente), con el riesgo de perder alguna si el modelo la omite. Recomendado: mantener la acumulación de la propuesta y dejar el olvido para el directo.
9. **Errores sin nombre de fichero** (`index` en su lugar) en las respuestas y los logs (recomendado), frente a devolver el nombre saneado.
10. **400 de sesión con `field` e `index`** (recomendado) frente a la forma exacta de `/estimate` (`reason` y `message`).
11. **Inyección en adjuntos**: bloquear (decisión de la propuesta) frente a neutralizar las etiquetas propias y seguir. Se confirma que se bloquea.
12. **Query `prompt_version` (`"v4"`)** en el endpoint de sesión (recomendado) frente a una versión fija sin parámetro.
13. **Propietarios en `CLAUDE.md`**: `app/sessions/` y `app/services/session_service.py` → `ai-engineer` (memoria y contexto del LLM); `app/attachments/` → `backend-engineer` (parseo de ficheros, límites, E/S). Alternativa: los dos paquetes para `ai-engineer`.
14. **Medición D14**: ¿se autorizan unas 11 llamadas reales (≤ 0,10 USD) al final del change? Se preguntará cuando llegue la tarea.

**Cambios de spec que se proponen** (los decide el orquestador con el usuario):

- `conversation-sessions`: 409 `session_busy`, 422 por UUID inválido, `SessionInfo` con `turn_count`/`history_turns`/`max_turns`, y el turno fuera de alcance que entra en el historial sin tocar el metadata.
- `attachments`: 413 para los límites, la forma `{"reason", "index", "message"}` sin el nombre, los *magic bytes* con la extensión coincidente, y los adjuntos antes de la transcripción en el mensaje.
- `input-guardrails`: `field`/`index` en el 400 de sesión, `mask_pii` y las etiquetas nuevas en la regla `prompt_tag` (afecta también a `/estimate`).
- `structured-estimation`: `technologies` con normalizador y sin reintentos; también en `ComputedEstimate`, sin render en el Markdown.
- `prompt-templates`/`cag-prompt`: `v3` inmutable con test dorado; `v4` con el orden de D5 y el bloque vacío en el primer turno.
