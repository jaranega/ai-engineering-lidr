# Muestras de transcripciones

Transcripciones de prueba para probar `POST /api/v1/estimate` a mano.

| Fichero | Idioma | Descripción |
|---|---|---|
| `landing-hubspot-blog.txt` | español | La transcripción del enunciado: landing page, integración con HubSpot y blog con editor WYSIWYG. |
| `reserva-restaurantes-en.txt` | inglés | Reunión más larga y realista sobre un sistema de reservas para un grupo de restaurantes. Sirve para comprobar que la estimación se responde en el idioma de la transcripción. |

## Uso con curl

Con el servidor arrancado (`uv run uvicorn app.main:app --reload`) y `jq` instalado, que se encarga de escapar el texto como JSON:

```bash
jq -Rs '{transcription: .}' samples/landing-hubspot-blog.txt \
  | curl -X POST http://localhost:8000/api/v1/estimate \
      -H "Content-Type: application/json" -d @-
```

Cambia el fichero para probar la otra muestra. Estas llamadas usan el LLM real y tienen coste.
