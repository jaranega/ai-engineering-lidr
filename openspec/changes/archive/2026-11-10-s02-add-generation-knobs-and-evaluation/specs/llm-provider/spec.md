## MODIFIED Requirements

### Requirement: Límites de la llamada al LLM
Cada llamada al LLM, en modo normal o en streaming, MUST usar como límite de tokens de salida el valor de la petición o, si no lo indica, `max_tokens = 2048` (constante en el código, valor por defecto); con razonamiento extendido de Anthropic el límite efectivo es `max_tokens + thinking_budget`. Cada llamada usa un timeout de `LLM_TIMEOUT` segundos (por defecto 60) y como máximo `LLM_RETRIES` reintentos por modelo (por defecto 2): el primario agota sus reintentos antes de pasar al respaldo, y el respaldo tiene los mismos reintentos. Si la salida se corta por alcanzar el límite efectivo, el sistema MUST registrar un warning en el log, devolver el texto recibido y marcar el resultado con `truncated = true`.

#### Scenario: Salida truncada
- **WHEN** el proveedor indica que la generación terminó por límite de tokens (`finish_reason=length`, o `stop_reason=max_tokens` en Anthropic)
- **THEN** se registra un warning y el resultado contiene el texto recibido con `truncated = true`

#### Scenario: Salida completa
- **WHEN** el proveedor indica que la generación terminó normalmente
- **THEN** el resultado tiene `truncated = false`

#### Scenario: Salida truncada en streaming
- **WHEN** una respuesta en streaming termina por límite de tokens
- **THEN** se registra el mismo warning, el texto entregado se conserva y el resultado tiene `truncated = true`

#### Scenario: Límite por defecto
- **WHEN** se solicita una generación sin opciones de llamada (chat, SSE o `POST /api/v1/estimate` sin `max_tokens`)
- **THEN** la llamada usa `max_tokens = 2048`

#### Scenario: Límite de la petición
- **WHEN** se solicita una generación con `max_tokens = 4000`
- **THEN** la llamada usa `max_tokens = 4000`

#### Scenario: Límites configurados
- **GIVEN** `LLM_TIMEOUT=30` y `LLM_RETRIES=1`
- **WHEN** se solicita una generación
- **THEN** la llamada usa un timeout de 30 segundos y como máximo 1 reintento por modelo

#### Scenario: Errores sin datos sensibles en el log
- **WHEN** la llamada al LLM lanza un error
- **THEN** el log registra el tipo de error y el status HTTP, sin el mensaje completo del error ni ninguna API key

## ADDED Requirements

### Requirement: Opciones por llamada
La interfaz de proveedor SHALL aceptar, en `generate` y en `stream`, unas opciones de llamada opcionales: modelo destino (sustituye al primario y desactiva el fallback), `max_tokens` y presupuesto de razonamiento. Sin opciones, el comportamiento MUST ser el de los valores por defecto, de modo que el chat, el endpoint SSE y los dobles de prueba existentes no cambien.

#### Scenario: Sin opciones
- **WHEN** se llama a `generate(system, messages)` sin opciones
- **THEN** se usa el modelo primario con fallback y `max_tokens = 2048`

#### Scenario: Modelo destino
- **GIVEN** opciones con modelo destino `claude-haiku-4-5`
- **WHEN** se llama a `generate` y el modelo falla
- **THEN** se produce el error de dominio sin llamar al respaldo
