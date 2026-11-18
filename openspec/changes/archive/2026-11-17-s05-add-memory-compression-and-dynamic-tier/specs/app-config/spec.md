## ADDED Requirements

### Requirement: Configuración de la compresión de la memoria
El sistema SHALL admitir, además de las variables existentes:

| Variable | Tipo y rango | Default |
|---|---|---|
| `ANCHOR_DETECTION_MODE` | `Literal["heuristic", "llm"]` | `heuristic` |
| `COMPRESSION_MODEL` | `str \| None` (vacío o solo espacios = `None`) | `None`: el modelo primario con fallback |
| `MAX_ANCHORED_TURNS` | `int`, `0..10` (`0` desactiva las anclas) | `3` |

Un valor fuera de rango o no admitido MUST impedir el arranque con un error que nombre la variable y no contenga secrets. `COMPRESSION_MODEL`, si tiene valor, MUST validarse al arrancar como un modelo de la petición (`resolve_request_model`): un modelo no reconocido o cuyo proveedor no tiene API key MUST impedir el arranque con un error que nombre `COMPRESSION_MODEL`. `SUMMARY_MAX_CHARS` (2000) es una constante del código, no configuración. `.env.example` MUST documentar las tres variables comentadas con su valor por defecto. El almacén de sesiones MUST construirse con `MAX_ANCHORED_TURNS` como `max_anchors` del historial.

#### Scenario: Valores por defecto
- **WHEN** se arranca sin definir las variables
- **THEN** `ANCHOR_DETECTION_MODE = "heuristic"`, `COMPRESSION_MODEL = None` y `MAX_ANCHORED_TURNS = 3`

#### Scenario: Tope de anclas fuera de rango
- **WHEN** se arranca con `MAX_ANCHORED_TURNS=11` o con `ANCHOR_DETECTION_MODE=regex`
- **THEN** el arranque falla con un error que nombra la variable

#### Scenario: Modelo de compresión vacío
- **WHEN** se arranca con `COMPRESSION_MODEL=` (vacío o solo espacios)
- **THEN** la configuración es válida y `COMPRESSION_MODEL = None`

#### Scenario: Modelo de compresión sin key
- **GIVEN** `PRIMARY_MODEL=gpt-4o-mini`, `OPENAI_API_KEY` definida y `ANTHROPIC_API_KEY` sin definir
- **WHEN** se arranca con `COMPRESSION_MODEL=claude-haiku-4-5`
- **THEN** el arranque falla con un error que nombra `COMPRESSION_MODEL` y no contiene el valor de ninguna key

#### Scenario: Variables documentadas
- **WHEN** se inspecciona `.env.example`
- **THEN** contiene `ANCHOR_DETECTION_MODE`, `COMPRESSION_MODEL` y `MAX_ANCHORED_TURNS`
