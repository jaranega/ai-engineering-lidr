## MODIFIED Requirements

### Requirement: Tests sin coste ni red
La suite de tests MUST ejecutarse sin API keys reales, sin servidor Redis y sin ninguna llamada de red (ni a proveedores LLM ni a recursos que LiteLLM pueda descargar, como su mapa de precios), sustituyendo el proveedor por un doble de prueba mediante la inyección de dependencias de FastAPI en la API y mediante la sustitución del proveedor en la app Streamlit, la llamada de LiteLLM por un doble en los tests del proveedor, y Redis por `fakeredis`.

#### Scenario: Ejecución en CI
- **GIVEN** un entorno sin API keys reales ni Redis
- **WHEN** se ejecuta `uv run pytest`
- **THEN** todos los tests pasan y no se realiza ninguna llamada de red

#### Scenario: Mapa de precios de LiteLLM local
- **WHEN** se importa la aplicación
- **THEN** LiteLLM carga su mapa de modelos desde la copia local incluida en el paquete, sin descargarlo

### Requirement: Tests de comportamiento del servicio
La suite SHALL cubrir, como mínimo: `GET /health` (200, también con Redis caído), `POST /api/v1/estimate` correcto con el proveedor simulado, el 422 por transcripción vacía, el 502 por fallo del proveedor, la inclusión de los ejemplos CAG en el system prompt, el proveedor LiteLLM (generación, streaming, truncado, fallback, errores) con LiteLLM simulado, la caché (acierto, fallo, qué no se guarda, streaming, Redis caído) con `fakeredis`, los eventos de log y la ausencia de datos sensibles en ellos, y la app Streamlit ejecutada sin navegador (`streamlit.testing.v1.AppTest`) con el proveedor simulado: respuesta en el historial, conversación multi-turno, error del proveedor, error de configuración y panel lateral.

#### Scenario: Cobertura mínima
- **WHEN** se ejecuta `uv run pytest`
- **THEN** existen y pasan tests para cada uno de los casos listados
