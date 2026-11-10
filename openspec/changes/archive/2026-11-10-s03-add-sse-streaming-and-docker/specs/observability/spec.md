## ADDED Requirements

### Requirement: Eventos del streaming por HTTP
El endpoint SSE SHALL registrar `estimate_stream_cancelled` (con el número de fragmentos enviados) cuando el cliente se desconecta antes del final y `estimate_stream_failed` (solo con el tipo de error) cuando una excepción inesperada que no es del proveedor corta la generación. La app Streamlit SHALL registrar `api_unreachable` (tipo de error) cuando no puede conectar con la API y `api_stream_failed` (motivo: estado HTTP distinto de 200, evento `error`, stream sin evento terminal o error de transporte a mitad). Ninguno de estos eventos MUST incluir el contenido de los mensajes, de la respuesta ni mensajes de error literales.

#### Scenario: Cancelación registrada
- **GIVEN** un stream SSE que ha entregado dos fragmentos
- **WHEN** el cliente se desconecta
- **THEN** se registra `estimate_stream_cancelled` con `fragments = 2`

#### Scenario: API inaccesible desde la UI
- **GIVEN** que la API no acepta conexiones
- **WHEN** la app Streamlit intenta enviar un mensaje
- **THEN** se registra `api_unreachable` con el tipo de error y sin el texto del mensaje
