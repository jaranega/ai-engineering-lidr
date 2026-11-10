## 0. Prerrequisitos

- [x] 0.1 Revisión del change por el usuario (proposal, specs, design y tasks)
- [x] 0.2 `s03-add-llm-wrapper-cache-logging` y `s03-add-sse-streaming-and-docker` archivados, en ese orden (las deltas de `estimation-api`, `llm-provider` y `response-cache` de este change parten del estado que dejan; si el change 2 modifica el requisito "Endpoint de estimación", rebasar aquí su texto antes de aplicar)

## 1. Contexto, prompt y servicios (ai-engineer)

- [x] 1.1 `app/context/examples.py`: `RATES_EUR_PER_HOUR`, `EstimationExample` ampliado (título, filas con tarea/horas/perfil, equipo, duración, riesgos) y los 3 ejemplos reescritos con coste y la misma calibración de horas; `estimation` renderizada una vez al importar con el formato de D3 (D1)
- [x] 1.2 `format_examples(examples, fmt)` con `markdown`, `json` y `narrative` dentro de `<examples>`; `_HOW_TO_USE_EXAMPLES` neutro para los tres formatos (D2)
- [x] 1.3 `_OUTPUT_FORMAT` con la tabla de coste, `### Totales`, tarifas, parciales antes que totales y sin separador de miles; orden de secciones y reglas de idioma de S03 intactos (D3)
- [x] 1.4 `build_system_prompt(examples, example_format, inline_cleaning)` pura y cacheada; bloque `_INLINE_CLEANING` justo después del rol (D3, D5)
- [x] 1.5 `GenerationOptions` y `LLMCallOptions`; el Protocol acepta `options: LLMCallOptions | None = None` en `generate` y `stream` (D4)
- [x] 1.6 `LiteLLMProvider`: `max_tokens` efectivo, override por `router_factory` (Router de un solo despliegue, sin fallback, `lru_cache`), `thinking` solo para Anthropic con `max_tokens + budget` y warning `thinking_budget_ignored` en OpenAI, `additional_drop_params: ["thinking"]` en los despliegues OpenAI de `build_router`; `truncated` respecto al límite efectivo (D4)
- [x] 1.7 `two_phase`: `EXTRACTION_SYSTEM_PROMPT` con la regla de idioma al principio, `EXTRACTION_MAX_TOKENS = 1500`, errores/extracción vacía → `LLMProviderError`, truncada → warning; `EstimationService.estimate(transcription, options)` devuelve los datos de la fase previa por separado y `latency_ms` total (D5)
- [x] 1.8 `app/services/evaluation.py`: `evaluate_estimation(text, truncated) -> StructureCheck` puro con etiquetas ES/EN, lectura de números, 8 comprobaciones, `score` e `issues`; lo ejecuta el servicio si `evaluate` (también en aciertos); log `estimation_evaluated` (D6)
- [x] 1.9 Clave de caché con `model`, `model_override`, `max_tokens` y `thinking_budget` (D7)
- [x] 1.10 Verificar: `ruff` sin errores; cada `estimation` de los ejemplos obtiene `score == 1.0`; el chat y el SSE siguen funcionando sin opciones

## 2. API y configuración (backend-engineer)

- [x] 2.1 `Settings.resolve_request_model(name) -> Deployment` sin red, reutilizando la resolución de modelos de S03; errores sin valores de keys (D4)
- [x] 2.2 `EstimationRequest` con los 8 campos opcionales y sus límites (importados de `llm_service`); `EstimationResponse` con `preprocessing`, `extracted_requirements`, `preprocessing_*` y `validation` (D8)
- [x] 2.3 Router: mapea a `GenerationOptions`, convierte `InvalidOptionError` en 422 con la forma de Pydantic (`loc: ["body", "model"]`) y `LLMProviderError` en 502; OpenAPI documenta 200, 422 y 502 (D4, D8)
- [x] 2.4 Verificar: `ruff` sin errores y `/openapi.json` con los campos y límites nuevos

## 3. Tests (qa-engineer)

- [x] 3.1 Ejemplos: 3 ejemplos, horas múltiplo de 5, coste = horas × tarifa, totales = suma, Markdown coherente con los datos
- [x] 3.2 Formateadores y prompt: los tres formatos con la misma información, JSON válido, sin bloque con 0 ejemplos, selección de los N primeros, orden de secciones, regla de idioma al principio y al final con cualquier combinación, bloque de limpieza tras el rol, determinismo
- [x] 3.3 Evaluación: casos ES y EN con `score = 1.0`, totales que no cuadran, sin tabla, truncada, lectura de números, puntuación parcial, ejemplos de referencia con `score = 1.0`
- [x] 3.4 Opciones en la API: valores por defecto, 422 por cada regla (incluido `model` sin key o no soportado), `use_examples=false`, `num_examples`, `max_tokens`, `evaluate=false`, `two_phase` (dos llamadas, requisitos como `user`, campos de la fase previa, 502 por fallo o extracción vacía), `cost_usd` sin la fase previa
- [x] 3.5 Proveedor: `max_tokens` efectivo y truncado; override sin fallback; thinking con Anthropic (`max_tokens + budget`) e ignorado con OpenAI (warning); contrato sin red con `mock_response`/`get_optional_params` para `additional_drop_params` y el Router por override
- [x] 3.6 Caché: la clave cambia con `max_tokens`, `thinking_budget`, `example_format` y override del mismo modelo; `evaluate` no entra
- [x] 3.7 Verificar: `uv run pytest -q` en verde sin red ni keys reales y tabla de cobertura de los escenarios del change

## 4. Documentación y cierre

- [x] 4.1 README: parámetros de `POST /api/v1/estimate` con ejemplos de `curl`, nuevo formato de salida con coste y tarifas, campos de la respuesta y `validation` (orquestador)
- [x] 4.2 Verificación manual de idioma y formato con llamadas reales según D10, con consentimiento del usuario (coste estimado de céntimos); registrar resultados y `score`
- [x] 4.3 `openspec validate --all --strict`, `ruff` y `pytest` en verde en local
- [x] 4.4 Archivar el change (`/opsx:archive`) tras la aprobación del usuario
- [x] 4.5 Commit en `pre-session-04` (con la aprobación del usuario)
