## MODIFIED Requirements

### Requirement: Configuración desde variables de entorno
El sistema SHALL cargar su configuración mediante `pydantic-settings` desde las variables de entorno y, de forma complementaria, desde un fichero `.env` en la raíz del proyecto. Las variables de entorno MUST tener prioridad sobre `.env`. Variables soportadas:

| Variable | Tipo | Default |
|---|---|---|
| `OPENAI_API_KEY` | `SecretStr \| None` | `None` |
| `ANTHROPIC_API_KEY` | `SecretStr \| None` | `None` |
| `PRIMARY_MODEL` | `str` | `gpt-4o-mini` |
| `FALLBACK_MODEL` | `str \| None` (vacío = sin respaldo) | sin definir: el modelo por defecto del otro proveedor (`claude-haiku-4-5` si el primario es de OpenAI, `gpt-4o-mini` si es de Anthropic) |
| `LLM_TIMEOUT` | `int` (segundos, > 0) | `60` |
| `LLM_RETRIES` | `int` (≥ 0) | `2` |
| `REDIS_URL` | `str` | `redis://localhost:6379/0` |
| `CACHE_TTL` | `int` (segundos, > 0) | `86400` |
| `APP_ENV` | `str` | `development` |
| `LOG_LEVEL` | `Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]` | `DEBUG` |

#### Scenario: Carga con valores por defecto
- **GIVEN** que solo está definida `OPENAI_API_KEY`
- **WHEN** se instancia la configuración
- **THEN** `PRIMARY_MODEL` vale `gpt-4o-mini`, `FALLBACK_MODEL` vale `claude-haiku-4-5`, `LLM_TIMEOUT` vale 60, `LLM_RETRIES` vale 2, `APP_ENV` vale `development` y `LOG_LEVEL` vale `DEBUG`

#### Scenario: La variable de entorno prevalece sobre .env
- **GIVEN** un `.env` con `PRIMARY_MODEL=gpt-4o-mini` y la variable de entorno `PRIMARY_MODEL=claude-haiku-4-5`
- **WHEN** se instancia la configuración
- **THEN** el modelo primario es `claude-haiku-4-5`

#### Scenario: Respaldo por defecto según el primario
- **GIVEN** `PRIMARY_MODEL=claude-haiku-4-5`, `ANTHROPIC_API_KEY` y `OPENAI_API_KEY` definidas y `FALLBACK_MODEL` sin definir
- **WHEN** se instancia la configuración
- **THEN** la configuración es válida y el modelo de respaldo es `gpt-4o-mini`

#### Scenario: Límites inválidos
- **WHEN** `LLM_TIMEOUT=0` o `LLM_RETRIES=-1` o `CACHE_TTL=0`
- **THEN** la configuración falla con un error de validación que nombra la variable

#### Scenario: Variables retiradas
- **GIVEN** un `.env` antiguo con `LLM_PROVIDER=anthropic` y `LLM_MODEL=claude-haiku-4-5`
- **WHEN** se instancia la configuración
- **THEN** esas variables se ignoran y se aplican `PRIMARY_MODEL` y `FALLBACK_MODEL`

## ADDED Requirements

### Requirement: Modelos soportados y su proveedor
El sistema SHALL deducir el proveedor (`openai` o `anthropic`) de `PRIMARY_MODEL` y de `FALLBACK_MODEL` sin llamar a la red. Si alguno de los dos no corresponde a OpenAI ni a Anthropic, la configuración MUST fallar indicando la variable y los proveedores admitidos. `FALLBACK_MODEL` MUST ser distinto de `PRIMARY_MODEL`.

#### Scenario: Modelo de proveedor no soportado
- **WHEN** `PRIMARY_MODEL=gemini-2.0-flash`
- **THEN** la configuración falla con un error que nombra `PRIMARY_MODEL` y los proveedores admitidos

#### Scenario: Respaldo igual al primario
- **WHEN** `PRIMARY_MODEL=gpt-4o-mini` y `FALLBACK_MODEL=gpt-4o-mini`
- **THEN** la configuración falla con un error que nombra `FALLBACK_MODEL`

### Requirement: API keys según los modelos configurados
El sistema MUST exigir la API key del proveedor del modelo primario y fallar en el arranque si falta. La key del proveedor del respaldo MUST ser opcional: si falta, o si `FALLBACK_MODEL` está vacío, el servicio arranca sin fallback y registra un warning al arrancar.

#### Scenario: Falta la key del primario
- **GIVEN** `PRIMARY_MODEL=claude-haiku-4-5` y `ANTHROPIC_API_KEY` sin definir
- **WHEN** se instancia la configuración
- **THEN** se produce un error de validación que menciona `ANTHROPIC_API_KEY` y `PRIMARY_MODEL`, sin revelar el valor de ninguna key

#### Scenario: El servicio no arranca y avisa
- **GIVEN** que falta la API key del proveedor del modelo primario
- **WHEN** se arranca el servicio con `uv run uvicorn app.main:app`
- **THEN** el arranque de la aplicación (lifespan) falla, el proceso termina sin servir peticiones y el mensaje indica la variable que falta y cómo resolverlo (definirla en `.env` o cambiar `PRIMARY_MODEL`)

#### Scenario: Importar la aplicación no requiere configuración
- **GIVEN** un entorno sin API keys
- **WHEN** se importa `app.main`
- **THEN** la importación no falla (la validación ocurre solo al arrancar la aplicación)

#### Scenario: Key vacía equivale a ausente
- **GIVEN** `PRIMARY_MODEL=gpt-4o-mini` y `OPENAI_API_KEY=` (vacía o solo espacios)
- **WHEN** se instancia la configuración
- **THEN** se produce el mismo error de validación que si la variable no estuviera definida

#### Scenario: El error no muestra valores de keys
- **GIVEN** `PRIMARY_MODEL=claude-haiku-4-5`, `ANTHROPIC_API_KEY` sin definir y `OPENAI_API_KEY=sk-test-visible-value`
- **WHEN** se produce el error de configuración
- **THEN** el texto del error no contiene `sk-test-visible-value`

#### Scenario: Sin key del respaldo
- **GIVEN** `PRIMARY_MODEL=gpt-4o-mini`, `OPENAI_API_KEY` definida y `ANTHROPIC_API_KEY` sin definir
- **WHEN** se arranca el servicio
- **THEN** la configuración es válida, el servicio arranca y el log contiene un warning de que el fallback está desactivado

#### Scenario: Fallback desactivado a propósito
- **GIVEN** `FALLBACK_MODEL=` (vacío o solo espacios)
- **WHEN** se arranca el servicio
- **THEN** la configuración es válida, no hay modelo de respaldo y el log contiene un warning de que el fallback está desactivado

## REMOVED Requirements

### Requirement: Modelo por defecto según el proveedor
**Reason**: `LLM_PROVIDER` y `LLM_MODEL` desaparecen; los modelos se configuran directamente con `PRIMARY_MODEL` y `FALLBACK_MODEL`, cada uno con su valor por defecto.
**Migration**: sustituir `LLM_MODEL=<modelo>` por `PRIMARY_MODEL=<modelo>`.

### Requirement: API key obligatoria para el proveedor activo
**Reason**: ya no hay un proveedor activo único; la regla pasa a depender de los modelos primario y de respaldo.
**Migration**: ver el requisito "API keys según los modelos configurados".
