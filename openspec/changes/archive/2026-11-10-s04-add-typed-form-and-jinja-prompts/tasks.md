## 0. Prerrequisitos

- [x] 0.1 Revisión del change por el usuario (proposal, specs, design y tasks)

## 1. Dominio y dependencias (backend-engineer)

- [x] 1.1 `uv add "jinja2>=3.1.6"` (dependencia directa) y `uv.lock` actualizado (D13)
- [x] 1.2 `app/domain.py` sin imports de `app`: `ProjectType`, `DetailLevel`, `OutputFormat` (`str, Enum`), límites (`MIN/MAX_DESCRIPTION_CHARS`, `MAX_REFERENCE_PROJECTS` y los de `ReferenceProject`), `ReferenceProject` (`extra="forbid"`) y `EstimationInput` congelados, `description` con recorte de espacios antes de los límites (D2, D7)
- [x] 1.3 Verificar: `ruff` sin errores e `import app.domain` sin cargar ningún otro módulo de `app`

## 2. Contexto y templates (ai-engineer)

- [x] 2.1 `app/context/examples.py`: lista canónica de fases (clave y etiquetas ES/EN), `phase` en cada fila, `project_type` y `phase_confidence` por ejemplo, agregación por fase calculada al importar; fuera `estimation`, `_render_markdown`, `format_examples`, `ExampleFormat` y `EXAMPLE_FORMATS` (D5)
- [x] 2.2 `app/prompts/loader.py`: `PromptVersion`, `PROMPT_VERSIONS`, `DEFAULT_PROMPT_VERSION`, `UnknownPromptVersionError`, un `Environment` por versión cacheado (`FileSystemLoader`, `StrictUndefined`, `trim_blocks`, `lstrip_blocks`, `autoescape=False`, filtro `amount`), `_render_context` con valores `str` y `render_estimation_prompt(request, version="v1", *, num_examples=3)`; evento `prompt_rendered` sin contenido (D3, D4, D9)
- [x] 2.3 `estimation/v1/system.j2` con el orden de D6: regla de idioma, rol, pista del tipo, cómo usar los ejemplos, bloque del formato, bloque del nivel, tarifas, esqueleto EN del formato pedido, `{% include "examples.j2" %}` y reglas con la regla de idioma de cierre como última línea (D5, D6)
- [x] 2.4 `estimation/v1/user.j2` (tipo, `<reference_projects>` con `{% for %}` omitido si está vacío, `<project_description>` al final) y `examples.j2` con una macro por formato a partir de los datos (D5, D7)
- [x] 2.5 `estimation/v2/`: copia completa de `v1` que solo cambia el párrafo del rol y una regla de estilo (D8)
- [x] 2.7 Idioma detectado (D14, tras la primera medición): `app/prompts/language.py` con `detect_language` (heurística sin dependencias, `None` si es ambiguo), `response_language` en el contexto del render y en `prompt_rendered`, y la línea final del system en `v1` y `v2`; tests en `tests/prompts/test_language.py`
- [x] 2.6 Verificar: render de todas las combinaciones sin error y revisión manual del system de `v1` en los tres formatos

## 3. Servicios (ai-engineer)

- [x] 3.1 `llm_service.py`: `EstimationService.estimate(request: EstimationInput, options)` renderiza con el loader y envía `system` + un mensaje `user`; `GenerationOptions` sin `preprocessing`/`example_format` y con `prompt_version` validado (`InvalidOptionError`); `EstimationResult` con `prompt_version` y sin los campos de preprocesado; `estimation_requested` con los campos nuevos; reexporta `PromptVersion` (D8, D10)
- [x] 3.2 Borrar el código muerto de D10 (strings del prompt, prompt del chat, `two_phase`, `prompt_context`, `stream_chat`, `EstimationStream`, `MAX_TRANSCRIPTION_CHARS`…), manteniendo `stream` del proveedor, `LLMStream` y `ChatMessage`
- [x] 3.3 `evaluation.py`: `evaluate_estimation(text, truncated, output_format)` con las comprobaciones por formato, `CHECK_COUNTS`, `output_format` y `checks_total` en `StructureCheck`, `has_breakdown_table` `None` en `narrative`; `estimation_evaluated` con `output_format` (D11)
- [x] 3.4 Verificar: `ruff` sin errores y cada ejemplo renderizado en cada formato obtiene `score == 1.0`

## 4. API (backend-engineer)

- [x] 4.1 `app/schemas.py`: `EstimationRequest(EstimationInput)` con los parámetros de generación restantes, reexporta los enums; `EstimationResponse` con `text` y `prompt_version`; `StructureCheckResponse` con `output_format`, `checks_total` y `has_breakdown_table` nullable; fuera `MessageText`, `ConversationMessage`, `StreamEstimationRequest`, `ExampleContext` y `ContextResponse` (D2, D10, D11)
- [x] 4.2 Router: query `prompt_version: PromptVersion = "v1"`, `InvalidOptionError("prompt_version")` → 422 en `("query", "prompt_version")`, el resto como hoy; fuera `/estimate/stream`, `stream_closer`, `estimate_stream_events` y `/context`; OpenAPI con 200, 422 y 502 (D8)
- [x] 4.3 Verificar: `ruff` sin errores y `/openapi.json` con los enums, `prompt_version` y `reference_projects`

## 5. Interfaz (frontend-engineer)

- [x] 5.1 `streamlit_app.py`: formulario `st.form` con descripción, tipo, nivel (`Medio`), formato (`Partidas`) y "Opciones avanzadas" con `prompt_version`; validación 20–2000 en cliente; `POST /api/v1/estimate` con timeout de lectura de 120 s; sin imports de `app` (D12)
- [x] 5.2 Resultado (texto, `prompt_version`, aviso de truncado, métricas, validación como aprobadas/`checks_total`) en `st.session_state`; errores 422 con nombres de campo sin `input`, 502/timeout/JSON inválido con el mensaje genérico; barra lateral solo con `/health`; logs `api_unreachable` y `api_request_failed` (D12)
- [x] 5.3 Verificar: `ruff` sin errores y la app arranca contra la API local

## 6. Tests (qa-engineer)

- [x] 6.1 `tests/prompts/test_estimation_v1.py` con los tres tests del enunciado (descripción literal en `<project_description>`; `phases_table` contiene `confidence_pct` y `narrative` no; `detailed` incluye la instrucción de supuestos por fase y `summary` no)
- [x] 6.2 Tests del loader: configuración del `Environment`, `StrictUndefined`, versión desconocida y `../v1`, carpetas = `PROMPT_VERSIONS`, todas las combinaciones, ningún `ProjectType.` en los prompts, regla de idioma al principio y al final, ni descripción ni referencias en el system, determinismo, `v2` frente a `v1`, `reference_projects`, `prompt_rendered` sin contenido
- [x] 6.3 Ejemplos y evaluación: aritmética por filas y por fases, fases canónicas, confianza; evaluación por formato (los escenarios de `estimation-evaluation`) y ejemplos con `score = 1.0` en cada formato
- [x] 6.4 API y opciones: contrato nuevo, 422 por descripción, enums, `reference_projects` y `prompt_version`, `v2`, mensajes separados, campos retirados ignorados, caché (clave con formato, nivel, tipo y versión; `prompt_version` en un acierto), 502; retirar los tests de SSE, chat y `/context`
- [x] 6.5 Streamlit con `AppTest` y httpx simulado: envío tipado, `v2`, validación en cliente, resultado, truncado, `validation = null`, 422 sin el texto, 502, API caída, URL configurada, sin imports de `app` y paridad de valores con `app.domain`; observabilidad (marcadores secretos en descripción y referencias)
- [x] 6.6 Estructura del proyecto con `app/domain.py` y `app/prompts/**`; `tests/test_samples.py` sobre los JSON nuevos
- [x] 6.7 Verificar: `uv run pytest -q` en verde sin red ni keys reales, y tabla de cobertura de los escenarios del change

## 7. Muestras, documentación y cierre (orquestador)

- [x] 7.1 `samples/`: sustituir los `.txt` por cuerpos JSON de petición (mismo proyecto en ES y EN) y actualizar `samples/README.md`
- [x] 7.2 README: cómo levantar (API, Docker, Streamlit), contrato nuevo con ejemplos de `curl` (`prompt_version`, `reference_projects`), formatos y niveles, estructura de `app/prompts/` y versionado, tests (`uv run pytest tests/prompts`); retirar SSE, chat y `/context`; sección de desviaciones de S04
- [x] 7.3 `CLAUDE.md`: `app/domain.py` y `app/prompts/` en la arquitectura, y `app/domain.py` en las rutas de `backend-engineer`
- [x] 7.4 Medición D14 con llamadas reales, **con autorización del usuario** y sin Redis; registrar los resultados en `design.md` y parar si algo queda por debajo del umbral
- [x] 7.5 Captura del formulario funcionando (opcional, enunciado): `docs/s04-formulario.png` y `docs/s04-resultado.png`, enlazadas en el README
- [x] 7.6 `openspec validate --all --strict`, `ruff` y `pytest` en verde en local
- [x] 7.7 Archivar el change tras la aprobación del usuario: escribir el Purpose de `prompt-templates` y `estimation-form-ui`, actualizar el de `estimation-api`, `generation-options` y `cag-prompt`, y borrar `openspec/specs/chat-ui` y `openspec/specs/streaming-api` si quedan sin requisitos
- [x] 7.8 Commit en `pre-session-04` y push (con la aprobación del usuario)
