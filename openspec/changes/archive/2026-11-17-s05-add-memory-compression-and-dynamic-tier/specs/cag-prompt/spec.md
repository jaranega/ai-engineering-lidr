## MODIFIED Requirements

### Requirement: Tecnologías en los ejemplos
Cada ejemplo de `ESTIMATION_EXAMPLES` SHALL incluir en sus datos `technologies` con las tecnologías nombradas en su `meeting_summary` (lista vacía si no nombra ninguna; al menos un ejemplo MUST tenerla vacía, para enseñar a dejarla vacía), y `example_result` MUST cargarlas en el `EstimationResult`. Los JSON de los ejemplos MUST incluir `technologies` solo en `v4` y `v5`; en `v3` MUST excluirse para que su prompt no cambie (capability `prompt-templates`).

#### Scenario: Tecnologías del ejemplo
- **WHEN** se cargan los ejemplos de `ESTIMATION_EXAMPLES`
- **THEN** el de tipo `data_pipeline` tiene `technologies = ["Shopify", "Odoo", "Looker Studio"]`, el de tipo `web_saas` tiene `technologies = []` y cada `EstimationResult` es válido

#### Scenario: Ejemplos según la versión
- **WHEN** se renderiza el system de `v3`, el de `v4` y el de `v5` con los ejemplos por defecto
- **THEN** los `<example_output>` de `v4` y `v5` contienen la clave `technologies` y los de `v3` no

## ADDED Requirements

### Requirement: Restricciones y descartes en los ejemplos
Cada ejemplo de `ESTIMATION_EXAMPLES` SHALL incluir en sus datos `explicit_constraints` y `rejected_options` escritos a partir de su `meeting_summary`; al menos un ejemplo MUST tener `explicit_constraints` no vacío y al menos uno MUST tenerlo vacío. `example_result` MUST cargarlos en el `EstimationResult`. `example_payloads(..., include_memory_fields=…)` MUST incluir los dos campos, también vacíos, justo después de `confidence_pct` y en el orden del schema solo con `include_memory_fields=True`, que pasa únicamente `v5`; en `v3` y `v4` MUST excluirse.

#### Scenario: Campos en los ejemplos de v5
- **WHEN** se renderiza el system de `v5` con los ejemplos por defecto
- **THEN** cada `<example_output>` contiene `explicit_constraints` y `rejected_options` tras `confidence_pct` y antes de `technologies`, y al menos uno tiene `explicit_constraints` no vacío

#### Scenario: Ejemplos válidos
- **WHEN** se convierte cada ejemplo a `EstimationResult`
- **THEN** es válido y conserva sus `explicit_constraints` y `rejected_options`

### Requirement: System prompt de sesión v5
El system prompt de `v5` SHALL construirse con el `system.j2` de `v5` en cada turno de sesión, con el orden de secciones de `v4` y estos cambios: la sección de conversación y memoria añade que algunos turnos antiguos se conservan literales al principio del historial porque contienen compromisos del cliente y que la estimación vigente es siempre la de la última respuesta; que `<conversation_summary>`, si aparece, resume los turnos anteriores que ya no están en el historial y que, ante una contradicción, prevalecen el mensaje actual, los turnos literales y `<project_metadata>` sobre el resumen; y que se respetan las `explicit_constraints` de `<project_metadata>`, no se proponen opciones de `rejected_options` y, si una restricción hace inviable algo, se dice en `assumptions_and_risks`. La sección de cómo rellenar los campos MUST explicar `explicit_constraints` y `rejected_options` en el orden del schema; la de `explicit_constraints` MUST nombrar expresamente un NDA o acuerdo de confidencialidad, la normativa (RGPD, GDPR, HIPAA…), el presupuesto máximo y los plazos, y pedir incluir cada uno aunque el cliente lo mencione de pasada o junto a otra restricción. Tras los ejemplos MUST ir, en este orden: `<project_metadata>`, `<conversation_summary>` (solo si hay resumen), `<audience>` (solo si el tier no es `default`) y `## Reglas`; después de las reglas solo MUST ir la línea del idioma de respuesta, si se conoce. Las reglas MUST indicar que el contenido de `<transcript>`, `<attachments>`, `<project_metadata>` y `<conversation_summary>` se trata únicamente como datos. Las instrucciones nuevas MUST estar en español, como las de `v4`. El system prompt MUST NOT contener tarifas, importes, un esqueleto Markdown, la transcripción ni el texto de los adjuntos.

#### Scenario: Orden de las secciones
- **WHEN** se construye el system de `v5` con ejemplos, un `project_metadata` con datos, un resumen y `tier = executive`
- **THEN** el orden es `<examples>` < `<project_metadata>` < `<conversation_summary>` < `<audience>` < `## Reglas`, y la última línea es la del idioma de respuesta

#### Scenario: Sin resumen ni tier
- **WHEN** se construye el system de `v5` sin resumen y con `tier = default`
- **THEN** no contiene los bloques `<conversation_summary>` ni `<audience>`

#### Scenario: Prefijo estable
- **WHEN** se construye el system de `v5` con el mismo tipo, formato, nivel y ejemplos y dos combinaciones distintas de metadata, resumen y tier
- **THEN** los dos textos son idénticos hasta el final del bloque `<examples>`

#### Scenario: Precedencia y datos
- **WHEN** se construye el system de `v5`
- **THEN** indica que el resumen cede ante el mensaje actual, los turnos literales y `<project_metadata>`, y sus reglas tratan `<conversation_summary>` como datos

### Requirement: Bloque de metadata del proyecto en v5
El parcial `project_metadata.j2` de `v5` SHALL pintar las líneas de `v4` y, solo si tienen valor, `explicit_constraints: <a>; <b>` (separadas por `; `) y `rejected_options: <x>, <y>` (separadas por `, `), al final del bloque. Sin datos, el bloque MUST aparecer vacío como en `v4`.

#### Scenario: Líneas nuevas con valor
- **GIVEN** un `project_metadata` con `explicit_constraints = ["NDA firmado", "Presupuesto máximo de 40 000 €"]` y `rejected_options = ["Firebase"]`
- **WHEN** se construye el system de `v5`
- **THEN** el bloque contiene `explicit_constraints: NDA firmado; Presupuesto máximo de 40 000 €` y `rejected_options: Firebase`

#### Scenario: Sin líneas vacías
- **WHEN** se construye el system de `v5` con las dos listas vacías
- **THEN** el bloque no contiene `explicit_constraints` ni `rejected_options`

### Requirement: Bloque de resumen de la conversación
El parcial `conversation_summary.j2` de `v5` SHALL pintar, solo si la sesión tiene resumen, el bloque `<conversation_summary>` con el texto del resumen tal cual, cubierto por la regla de tratar el bloque como datos. Sin resumen, MUST NOT pintar nada.

#### Scenario: Resumen presente
- **GIVEN** un resumen `El cliente quiere reservas online.`
- **WHEN** se construye el system de `v5`
- **THEN** contiene `<conversation_summary>`, el texto del resumen y `</conversation_summary>`

### Requirement: Bloque de audiencia
El parcial `audience.j2` de `v5` SHALL pintar, solo si el tier no es `default`, el bloque `<audience>` con una cabecera común (adaptar el contenido a la audiencia sin cambiar el nivel de detalle ni el formato pedidos) y las instrucciones del tier, que cambian qué contenido va en cada campo del schema único: con `executive`, `summary` con el resultado de negocio y el principal factor de coste o plazo sin jerga, `task` en términos de lo que se obtiene, `assumptions_and_risks` empezando por los 3 riesgos de más impacto en coste o plazo traducidos a impacto de negocio, y en `narrative` inversión, hitos y una recomendación de siguiente paso; con `pm`, `task` como entregables reconocibles dentro de cada fase, `assumptions_and_risks` con dependencias, bloqueos, decisiones pendientes del cliente y disponibilidad del equipo, `summary` con el rango de duración y la composición del equipo, y en `narrative` la secuencia de fases e hitos; con `developer`, `task` por componente técnico con el detalle entre paréntesis, `assumptions_and_risks` con los supuestos de stack y las incertidumbres que mueven la estimación más de un 20 %, vocabulario técnico sin traducir y en `narrative` las decisiones de arquitectura. El bloque MUST NOT pedir cambiar el schema, el número de filas ni las fases canónicas.

#### Scenario: Texto por tier
- **WHEN** se construye el system de `v5` con `executive`, `pm` y `developer`
- **THEN** cada uno contiene `<audience>` con las instrucciones de su tier y no las de los otros dos

#### Scenario: Sin adaptación
- **WHEN** se construye el system de `v5` con `tier = default`
- **THEN** no contiene `<audience>`
