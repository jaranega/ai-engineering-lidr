## ADDED Requirements

### Requirement: Revisión Actor-Critic-Boss en la interfaz
La sección "Opciones avanzadas" del formulario SHALL incluir la casilla "Revisión Actor-Critic-Boss", desmarcada por defecto, con una ayuda que avise de que el turno tarda más y cuesta más (hasta `BOSS_MAX_ITERATIONS` borradores más una revisión por borrador). Marcada, el turno MUST enviarse con `review=true`; desmarcada, MUST NOT enviarse el campo.

Cuando la respuesta trae `acb`, la interfaz MUST mostrar bajo el resultado un desplegable "Revisión Actor-Critic-Boss" con:
- la decisión final y el motivo de parada, con etiquetas en español:

  | Valor | Etiqueta |
  |---|---|
  | `accept` | "Aceptada" |
  | `synthesize` | "Sintetizada con avisos" |
  | `accepted` | "el revisor la aceptó" |
  | `out_of_scope` | "fuera de alcance, sin revisar" |
  | `critic_failed` | "el revisor no respondió" |
  | `rejected` | "el revisor la rechazó" |
  | `budget_exhausted` | "se agotaron las iteraciones" |
  | `actor_failed` | "falló un reintento" |

- el número de iteraciones;
- por cada iteración, el veredicto y una tabla de problemas con gravedad, categoría, campo y descripción, o "Sin problemas".

Si la respuesta no trae `acb`, el desplegable MUST NOT aparecer.

#### Scenario: Casilla marcada
- **WHEN** el usuario marca "Revisión Actor-Critic-Boss" y envía el turno
- **THEN** la petición multipart incluye `review=true`

#### Scenario: Casilla desmarcada
- **WHEN** el usuario envía el turno sin marcar la casilla
- **THEN** la petición no incluye `review` y no aparece el desplegable de revisión

#### Scenario: Traza sintetizada
- **GIVEN** una respuesta con `acb.final_decision = "synthesize"`, `stop_reason = "budget_exhausted"` y 3 iteraciones con problemas
- **WHEN** se muestra el resultado
- **THEN** el desplegable indica "Sintetizada con avisos — se agotaron las iteraciones", 3 iteraciones y la tabla de problemas de cada una
