## ADDED Requirements

### Requirement: Configuración de sesiones y adjuntos
El sistema SHALL admitir, además de las variables existentes:

| Variable | Tipo y rango | Default |
|---|---|---|
| `MAX_CONVERSATION_TURNS` | `int`, `1..50` (turnos `user` + `assistant` en la ventana del historial) | `6` |
| `MAX_SESSIONS` | `int`, `1..100000` (sesiones en memoria antes de desalojar la menos usada) | `1000` |
| `MAX_ATTACHMENTS` | `int`, `0..20` (adjuntos por turno; `0` desactiva los adjuntos) | `5` |
| `MAX_ATTACHMENT_BYTES` | `int`, `1024..52428800` (tamaño de cada adjunto) | `10485760` |
| `MAX_ATTACHMENT_CHARS` | `int`, `1000..200000` (caracteres extraídos en total por turno) | `60000` |

Un valor fuera de rango MUST impedir el arranque con un error que nombre la variable y no contenga secrets. `.env.example` MUST documentar las cinco variables comentadas con su valor por defecto. El almacén de sesiones del proceso MUST construirse una sola vez con `MAX_SESSIONS` y `MAX_CONVERSATION_TURNS`. El tope de tamaño sin comprimir de un DOCX y el de entradas del zip son constantes de seguridad del código, no configuración.

#### Scenario: Valores por defecto
- **WHEN** se arranca sin definir las variables
- **THEN** `MAX_CONVERSATION_TURNS = 6`, `MAX_SESSIONS = 1000`, `MAX_ATTACHMENTS = 5`, `MAX_ATTACHMENT_BYTES = 10485760` y `MAX_ATTACHMENT_CHARS = 60000`

#### Scenario: Ventana fuera de rango
- **WHEN** se arranca con `MAX_CONVERSATION_TURNS=0`
- **THEN** el arranque falla con un error que nombra `MAX_CONVERSATION_TURNS`

#### Scenario: Ventana configurada
- **GIVEN** `MAX_CONVERSATION_TURNS=2`
- **WHEN** se consulta una sesión nueva
- **THEN** `max_turns = 2`

#### Scenario: Adjuntos desactivados
- **GIVEN** `MAX_ATTACHMENTS=0`
- **WHEN** se envía un turno con un adjunto
- **THEN** la respuesta es 413 con `reason = "too_many_files"`

#### Scenario: Variables documentadas
- **WHEN** se inspecciona `.env.example`
- **THEN** contiene `MAX_CONVERSATION_TURNS`, `MAX_SESSIONS`, `MAX_ATTACHMENTS`, `MAX_ATTACHMENT_BYTES` y `MAX_ATTACHMENT_CHARS`
