## ADDED Requirements

### Requirement: Configuración desde variables de entorno
El sistema SHALL cargar su configuración mediante `pydantic-settings` desde las variables de entorno y, de forma complementaria, desde un fichero `.env` en la raíz del proyecto. Las variables de entorno MUST tener prioridad sobre `.env`. Variables soportadas:

| Variable | Tipo | Default |
|---|---|---|
| `OPENAI_API_KEY` | `SecretStr \| None` | `None` |
| `ANTHROPIC_API_KEY` | `SecretStr \| None` | `None` |
| `LLM_PROVIDER` | `Literal["openai", "anthropic"]` | `openai` |
| `LLM_MODEL` | `str \| None` | `None` (se resuelve por proveedor) |
| `APP_ENV` | `str` | `development` |
| `LOG_LEVEL` | `str` | `DEBUG` |

#### Scenario: Carga con valores por defecto
- **GIVEN** que solo está definida `OPENAI_API_KEY`
- **WHEN** se instancia la configuración
- **THEN** `LLM_PROVIDER` vale `openai`, `APP_ENV` vale `development` y `LOG_LEVEL` vale `DEBUG`

#### Scenario: La variable de entorno prevalece sobre .env
- **GIVEN** un `.env` con `LLM_PROVIDER=openai` y la variable de entorno `LLM_PROVIDER=anthropic`
- **WHEN** se instancia la configuración
- **THEN** el proveedor efectivo es `anthropic`

#### Scenario: Proveedor no soportado
- **WHEN** `LLM_PROVIDER` vale `gemini`
- **THEN** la configuración falla con un error de validación que indica los valores permitidos

### Requirement: Modelo por defecto según el proveedor
Si `LLM_MODEL` no está definido, el sistema SHALL resolver el modelo efectivo según el proveedor: `gpt-4o-mini` para `openai` y `claude-haiku-4-5` para `anthropic`. Si `LLM_MODEL` está definido, MUST usarse tal cual.

#### Scenario: Default de OpenAI
- **WHEN** `LLM_PROVIDER=openai` y `LLM_MODEL` no está definido
- **THEN** el modelo efectivo es `gpt-4o-mini`

#### Scenario: Default de Anthropic
- **WHEN** `LLM_PROVIDER=anthropic` y `LLM_MODEL` no está definido
- **THEN** el modelo efectivo es `claude-haiku-4-5`

#### Scenario: Modelo explícito
- **WHEN** `LLM_PROVIDER=openai` y `LLM_MODEL=gpt-4o`
- **THEN** el modelo efectivo es `gpt-4o`

### Requirement: API key obligatoria para el proveedor activo
El sistema MUST exigir la API key del proveedor seleccionado y fallar en el arranque si falta. La key del proveedor no seleccionado MUST ser opcional.

#### Scenario: Falta la key del proveedor activo
- **GIVEN** `LLM_PROVIDER=anthropic` y `ANTHROPIC_API_KEY` sin definir
- **WHEN** se instancia la configuración
- **THEN** se produce un error de validación que menciona `ANTHROPIC_API_KEY`, sin revelar el valor de ninguna key

#### Scenario: El servicio no arranca y avisa
- **GIVEN** que falta la API key del proveedor activo
- **WHEN** se arranca el servicio con `uv run uvicorn app.main:app`
- **THEN** el proceso termina sin servir peticiones y el mensaje indica la variable que falta y cómo resolverlo (definirla en `.env` o cambiar `LLM_PROVIDER`)

#### Scenario: Key del otro proveedor ausente
- **GIVEN** `LLM_PROVIDER=openai`, `OPENAI_API_KEY` definida y `ANTHROPIC_API_KEY` sin definir
- **WHEN** se instancia la configuración
- **THEN** la configuración es válida

### Requirement: Secrets protegidos
Las API keys MUST almacenarse como `SecretStr` y NUNCA aparecer en el código fuente, en los logs ni en las respuestas HTTP. El repositorio MUST incluir un `.env.example` con todas las variables, sin valores, y `.env` MUST estar en `.gitignore`.

#### Scenario: Representación de la configuración
- **WHEN** se imprime o serializa el objeto de configuración
- **THEN** las API keys aparecen enmascaradas (`**********`)

#### Scenario: .env fuera del control de versiones
- **WHEN** se inspecciona `.gitignore`
- **THEN** contiene una regla que excluye `.env` y no excluye `.env.example`
