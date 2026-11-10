## ADDED Requirements

### Requirement: Configuración de la salida estructurada y la moderación
El sistema SHALL admitir, además de las variables existentes, `STRUCTURED_MAX_RETRIES` (`int`, `0..5`, por defecto `2`: reintentos por validación de la salida estructurada, como máximo `1 + STRUCTURED_MAX_RETRIES` intentos) y `MODERATION_ENABLED` (`bool`, por defecto `true`: la moderación se usa solo si además hay `OPENAI_API_KEY`). Un valor fuera de rango MUST impedir el arranque con un error que nombre la variable y no contenga secrets. `.env.example` MUST documentar las dos variables.

#### Scenario: Valores por defecto
- **WHEN** se arranca sin definir las variables
- **THEN** `STRUCTURED_MAX_RETRIES = 2` y `MODERATION_ENABLED = true`

#### Scenario: Reintentos fuera de rango
- **WHEN** se arranca con `STRUCTURED_MAX_RETRIES=9`
- **THEN** el arranque falla con un error que nombra `STRUCTURED_MAX_RETRIES`

#### Scenario: Moderación desactivada
- **GIVEN** `MODERATION_ENABLED=false` y una `OPENAI_API_KEY`
- **WHEN** se procesa una petición
- **THEN** no se llama a la moderación
