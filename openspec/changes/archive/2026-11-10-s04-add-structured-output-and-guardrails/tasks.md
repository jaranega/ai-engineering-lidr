## 0. Prerrequisitos

- [x] 0.1 Revisión del change (decisiones del orquestador con autorización del usuario para no consultarle; preguntas abiertas del design resueltas)
- [x] 0.2 Verificar en el código de Instructor 1.17.0: `from_litellm`, `create_with_completion`, hooks `completion:response`/`parse:error`, reintentos solo por validación con `max_retries` entero y plantillas solo con `context` (D6, Open Questions 13)

## 1. Dependencias y configuración (backend-engineer)

- [x] 1.1 `uv add "instructor==1.17.0"` y revisar las transitivas en `uv.lock` (D14)
- [x] 1.2 `app/config.py`: `STRUCTURED_MAX_RETRIES` (0..5, por defecto 2) y `MODERATION_ENABLED` (por defecto `true`); `.env.example` documentado (spec `app-config`)
- [x] 1.3 `app/domain.py`: `Language = Literal["es", "en"]`; `app/prompts/language.py` lo reutiliza (D4)

## 2. Estimación estructurada (ai-engineer)

- [x] 2.1 `app/estimation/schema.py`: `EstimationResult` y modelos anidados en el orden de D2, normalizaciones idempotentes, validadores de coherencia, `response_model_for(output_format, detail_level)` cacheado con el mismo nombre y JSON Schema (D2)
- [x] 2.2 `app/estimation/compute.py`: `ComputedEstimate`, `compute_estimate` (costes por fila, fases en orden canónico, totales) y el filtro de fuera de alcance con `estimation_out_of_scope` (D3)
- [x] 2.3 `app/estimation/render.py`: `LABELS` es/en, `format_amount(value, language)`, `render_markdown(estimate, output_format, detail_level)` con la estructura de S04 por formato, `detailed`, párrafo de fases en `narrative` y aviso de fuera de alcance (D4)
- [x] 2.4 `app/context/examples.py` solo con datos (sin importes precalculados; `summary`, confianza global, `team` estructurado y 2 párrafos de `narrative` en español sin cifras por ejemplo) y `app/estimation/examples.py` (`example_result`, `example_payloads`) (D4)
- [x] 2.5 Verificar: invariante de los ejemplos (`score = 1.0` en cada formato × nivel × idioma) y totales del ejemplo 1

## 3. Prompt v3 (ai-engineer)

- [x] 3.1 `app/prompts/estimation/v3/{system,user,examples}.j2` con el orden de D14 (regla de idioma, rol, pista, ejemplos, cómo rellenar los campos con el bloque del formato y del nivel, fuera de alcance, ejemplos JSON, reglas, línea de idioma detectado); sin tarifas ni esqueleto Markdown
- [x] 3.2 Loader: `PromptVersion = Literal["v3"]`, `DEFAULT_PROMPT_VERSION = "v3"`, contexto sin `rates`/`amount`, con `example_payloads`; borrar `v1/` y `v2/`
- [x] 3.3 Verificar: render de todas las combinaciones y revisión manual del system de `v3` en los tres formatos

## 4. Guardrails y moderación (ai-engineer)

- [x] 4.1 `app/guardrails/input.py`: normalización, patrones de inyección ES y EN, PII (email, IBAN con mod-97, teléfonos), `screen_texts`, `InputGuardrailViolation` con mensajes fijos (D8)
- [x] 4.2 `app/services/moderation.py`: `Moderator`, `LiteLLMModerator` (una llamada, fail-open, `moderation_checked`/`moderation_failed`), `NullModerator`, `get_moderator()` con `moderation_disabled` (D8)

## 5. Proveedor, caché y servicio (ai-engineer)

- [x] 5.1 `llm_service.py`: `StructuredResult`, `generate_structured` en `LiteLLMProvider` con Instructor `Mode.TOOLS` por llamada sobre `router.completion`, hooks para tokens e intentos, `StructuredOutputError` (`truncated`/`invalid`), sin `context`; `LLMCallOptions` sin `thinking_budget` (D6)
- [x] 5.2 `CachedProvider.generate_structured` y `app/services/cache.py` con la clave y el valor de D7, revalidación y `cache_entry_invalid`
- [x] 5.3 `EstimationService.estimate`: guardrails → moderación → render → `generate_structured` → `compute_estimate` → `render_markdown` → evaluación (`null` fuera de alcance); `EstimationOutcome` con `result`; `GenerationOptions` sin `thinking_budget`; reexporta `InputGuardrailViolation` (D8, D9, D10)
- [x] 5.4 Verificar: `ruff` sin errores y capas sin ciclos (`estimation`/`guardrails` no importan `services` ni `prompts`)

## 6. API (backend-engineer)

- [x] 6.1 `app/schemas.py`: `EstimationResponse.result: ComputedEstimate`, `GuardrailError`/`GuardrailErrorResponse`, fuera `thinking_budget` y sus límites (D10)
- [x] 6.2 Router: `InputGuardrailViolation` → 400 `{"detail": {"reason", "message"}}` con `input_guardrail_blocked`; moderador por `Depends`; OpenAPI 200/400/422/502 (D8, D10)
- [x] 6.3 Verificar: `/openapi.json` con `prompt_version` solo `v3`, el 400 y el schema de `result`

## 7. Interfaz (frontend-engineer)

- [x] 7.1 `streamlit_app.py`: versiones `("v3",)`, 400 por motivo con mensajes en español (`GuardrailRejected`, `api_request_failed` con `reason="guardrail"`), aviso de fuera de alcance (D11)

## 8. Tests (qa-engineer)

- [x] 8.1 `tests/estimation/` (schema, compute, render, ejemplos) y `tests/prompts/` sobre `v3` (los tres tests del enunciado en `test_estimation_v3.py`, loader, ejemplos JSON válidos, bloques por formato y nivel, fuera de alcance, idioma) (D12)
- [x] 8.2 `tests/guardrails/` (positivos ES y EN, corpus de negativos con las muestras, IBAN, `reference_projects`) y moderación simulada (D12)
- [x] 8.3 Proveedor estructurado con un Router simulado, caché estructurada y servicio con `FakeProvider.generate_structured` (D12)
- [x] 8.4 API (400, 422 de `v1`/`v2`, `thinking_budget` ignorado, `result`, OpenAPI), Streamlit (400 por motivo, fuera de alcance, versión `v3`), observabilidad (marcadores secretos) y estructura del proyecto; retirar los tests de `v1`/`v2` y de thinking
- [x] 8.5 Verificar: `uv run pytest -q` en verde sin red ni keys reales

## 9. Documentación y cierre (orquestador)

- [x] 9.1 README (salida estructurada, `result`, guardrails y 400, `v3`, cálculos en el código, desviaciones respecto al directo) y `CLAUDE.md` (`app/estimation/` y `app/guardrails/` de `ai-engineer`, `instructor` en el stack, patrón de mensajes)
- [x] 9.2 Medición D13 con llamadas reales sin Redis (presupuesto autorizado); registrar los resultados en `design.md` y parar si algo queda por debajo del umbral
- [x] 9.3 `openspec validate --all --strict`, `ruff` y `pytest` en verde
- [x] 9.4 Archivar el change y escribir el Purpose de `structured-estimation` e `input-guardrails`
