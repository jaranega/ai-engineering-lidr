## ADDED Requirements

### Requirement: Endpoints de depuración por configuración
El sistema SHALL admitir la variable `DEBUG_ENDPOINTS` (booleana, por defecto `false`), que monta `GET /api/v1/sessions/{id}/debug` (capability `conversation-sessions`). Con `true`, el arranque MUST registrar un `warning` `debug_endpoints_enabled`, sin más datos. `.env.example` MUST documentar la variable comentada, con la advertencia de que expone texto del cliente y nunca debe activarse en producción. `docker-compose.yml` MUST NOT activarla.

#### Scenario: Valor por defecto
- **WHEN** se arranca sin definir `DEBUG_ENDPOINTS`
- **THEN** su valor es `false` y la ruta de depuración no existe

#### Scenario: Activada
- **WHEN** se arranca con `DEBUG_ENDPOINTS=true`
- **THEN** se registra `debug_endpoints_enabled` y la ruta responde

#### Scenario: Valor inválido
- **WHEN** se arranca con `DEBUG_ENDPOINTS=quizas`
- **THEN** el arranque falla con un error que nombra `DEBUG_ENDPOINTS`
