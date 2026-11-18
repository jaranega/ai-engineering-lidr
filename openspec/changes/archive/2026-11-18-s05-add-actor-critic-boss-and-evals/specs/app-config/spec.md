## ADDED Requirements

### Requirement: Configuración de la revisión Actor-Critic-Boss
El sistema SHALL admitir, además de las variables existentes:

| Variable | Tipo y rango | Default |
|---|---|---|
| `CRITIC_MODEL` | `str \| None` (vacío o solo espacios = `None`) | `None`: el modelo primario con fallback |
| `BOSS_MAX_ITERATIONS` | `int`, `1..5` | `3` |

Un valor fuera de rango MUST impedir el arranque con un error que nombre la variable y no contenga secrets. `CRITIC_MODEL`, si tiene valor, MUST validarse al arrancar como un modelo de la petición (`resolve_request_model`): un modelo no reconocido o cuyo proveedor no tiene API key MUST impedir el arranque con un error que nombre `CRITIC_MODEL`. `.env.example` MUST documentar las dos variables comentadas con su valor por defecto.

#### Scenario: Valores por defecto
- **WHEN** se arranca sin definir las variables
- **THEN** `CRITIC_MODEL = None` y `BOSS_MAX_ITERATIONS = 3`

#### Scenario: Iteraciones fuera de rango
- **WHEN** se arranca con `BOSS_MAX_ITERATIONS=0` o `BOSS_MAX_ITERATIONS=6`
- **THEN** el arranque falla con un error que nombra `BOSS_MAX_ITERATIONS`

#### Scenario: Modelo del Critic vacío
- **WHEN** se arranca con `CRITIC_MODEL=` (vacío o solo espacios)
- **THEN** la configuración es válida y `CRITIC_MODEL = None`

#### Scenario: Modelo del Critic sin key
- **GIVEN** `PRIMARY_MODEL=gpt-4o-mini`, `OPENAI_API_KEY` definida y `ANTHROPIC_API_KEY` sin definir
- **WHEN** se arranca con `CRITIC_MODEL=claude-haiku-4-5`
- **THEN** el arranque falla con un error que nombra `CRITIC_MODEL` y no contiene el valor de ninguna key

#### Scenario: Variables documentadas
- **WHEN** se inspecciona `.env.example`
- **THEN** contiene `CRITIC_MODEL` y `BOSS_MAX_ITERATIONS`
