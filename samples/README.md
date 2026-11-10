# Muestras de peticiones

Cuerpos JSON de petición para probar `POST /api/v1/estimate` a mano. Los dos describen el **mismo proyecto** (un sistema de reservas para un grupo de ocho restaurantes) en español y en inglés, con `project_type`, `detail_level` y `output_format` ya rellenos. Sirven para comprobar que la estimación se responde en el idioma de la descripción.

| Fichero | Idioma | `project_type` | `detail_level` | `output_format` |
|---|---|---|---|---|
| `reserva-restaurantes-es.json` | español | `web_saas` | `medium` | `line_items` |
| `restaurant-booking-en.json` | inglés | `web_saas` | `medium` | `line_items` |

## Uso con curl

Con el servidor arrancado (`uv run uvicorn app.main:app --reload`), el fichero se envía tal cual:

```bash
curl -X POST http://localhost:8000/api/v1/estimate \
  -H "Content-Type: application/json" \
  -d @samples/restaurant-booking-en.json
```

Para probar otra versión del prompt, añade el parámetro de consulta `prompt_version`:

```bash
curl -X POST "http://localhost:8000/api/v1/estimate?prompt_version=v2" \
  -H "Content-Type: application/json" \
  -d @samples/reserva-restaurantes-es.json
```

Para cambiar el nivel de detalle o el formato, edita los campos del JSON (`summary`, `medium`, `detailed`; `phases_table`, `line_items`, `narrative`). Estas llamadas usan el LLM real y tienen coste.
