## MODIFIED Requirements

### Requirement: Datos que nunca se registran
Ningún log del sistema, de la aplicación o de las librerías de terceros con el nivel configurado, MUST contener el contenido de las descripciones de proyecto, de los proyectos de referencia, de los system prompts o mensajes renderizados, las respuestas del LLM ni las API keys. Los mensajes de error de los SDK MUST NOT registrarse literalmente (solo el tipo de error y el status). Los loggers de terceros que vuelcan peticiones a nivel DEBUG MUST quedar como mínimo en INFO, y los loggers propios de LiteLLM MUST quedar silenciados (sus mensajes de error incluyen el cuerpo de la respuesta del proveedor) y su modo de depuración desactivado.

#### Scenario: Log sin contenido de la petición
- **GIVEN** `LOG_LEVEL=DEBUG` y una descripción con el texto `DESCRIPCION-SECRETA-123`
- **WHEN** se genera una estimación, con acierto o fallo de caché
- **THEN** ningún log contiene `DESCRIPCION-SECRETA-123`

#### Scenario: Log sin proyectos de referencia
- **GIVEN** `LOG_LEVEL=DEBUG` y un proyecto de referencia cuyo nombre es `REFERENCIA-SECRETA-456`
- **WHEN** se genera una estimación
- **THEN** ningún log contiene `REFERENCIA-SECRETA-456`

#### Scenario: Log sin API keys
- **GIVEN** `OPENAI_API_KEY=sk-test-visible-value` y un proveedor que falla con un error cuyo mensaje contiene la key
- **WHEN** se registra el fallo
- **THEN** ningún log contiene `sk-test-visible-value`

## REMOVED Requirements

### Requirement: Eventos del streaming por HTTP
**Reason**: Se retiran el endpoint SSE y el chat que consumía el stream (S04).
**Migration**: Los fallos de la app al llamar a la API se registran según la capability `estimation-form-ui` (`api_unreachable` y `api_request_failed`); el render del prompt se registra según `prompt-templates` (`prompt_rendered`).
