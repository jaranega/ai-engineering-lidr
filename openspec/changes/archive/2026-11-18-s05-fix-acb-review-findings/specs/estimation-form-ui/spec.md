## ADDED Requirements

### Requirement: Controles según la versión seleccionada
La disponibilidad de "Audiencia" y de "Revisión Actor-Critic-Boss" SHALL depender de la versión del prompt **seleccionada** en el formulario, no de la del último turno enviado: la audiencia MUST estar disponible con `v5` y `v6` y la revisión solo con `v6`. Si el usuario ha elegido un tier o marcado la revisión y selecciona una versión que no los admite, la interfaz MUST mostrar un aviso junto al control indicando que no se enviará, en lugar de descartarlo en silencio. La ayuda de la casilla MUST hablar de "varios borradores (hasta `BOSS_MAX_ITERATIONS`, 3 por defecto)" sin fijar un número como límite absoluto.

#### Scenario: Revisión tras un turno con v4
- **GIVEN** un turno enviado con `v4`
- **WHEN** el usuario selecciona `v6` y marca "Revisión Actor-Critic-Boss"
- **THEN** la casilla está disponible y el siguiente turno se envía con `review=true`

#### Scenario: Revisión con una versión que no la admite
- **GIVEN** la casilla marcada
- **WHEN** el usuario selecciona `v4`
- **THEN** se muestra un aviso de que la revisión no se enviará con esa versión y la petición no incluye `review`
