## ADDED Requirements

### Requirement: Bloque de feedback del Critic en v6
El `user.j2` de `v6` SHALL añadir, solo cuando recibe `critic_feedback` y después de `</transcript>`, un bloque `<critic_feedback>` con:
- una frase fija en inglés: "A previous draft of this estimate was reviewed by an independent auditor. Produce a new estimate that fixes these issues and keeps everything else consistent:";
- una línea por cada problema `critical` o `major` del feedback, en el orden recibido, con el formato `- [<severity>] <category> at <field_path>: <description>` y ` (suggested fix: <suggested_fix>)` cuando lo hay.

Los problemas `minor` MUST NOT pintarse. Sin `critic_feedback`, el bloque MUST NOT aparecer y el `user.j2` de `v6` MUST renderizar lo mismo que el de `v5`. Las descripciones del Critic MUST pintarse sin escapar, igual que la transcripción.

#### Scenario: Bloque con un problema mayor
- **GIVEN** un feedback con un problema `major` `constraint_conflict` en `rows[4].hours` con la descripción "Supera el presupuesto cerrado de 40 000 €" y la corrección sugerida "Reducir alcance de informes"
- **WHEN** se renderiza el user de `v6`
- **THEN** el mensaje termina con `<critic_feedback>`, la frase fija, la línea `- [major] constraint_conflict at rows[4].hours: Supera el presupuesto cerrado de 40 000 € (suggested fix: Reducir alcance de informes)` y `</critic_feedback>`

#### Scenario: Solo problemas menores
- **GIVEN** un feedback con `verdict = "accept"` y dos problemas `minor`
- **WHEN** se renderiza el user de `v6` con ese feedback
- **THEN** el bloque `<critic_feedback>` contiene la frase fija y ninguna línea de problema
