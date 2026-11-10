## ADDED Requirements

### Requirement: Tecnologías en los ejemplos
Cada ejemplo de `ESTIMATION_EXAMPLES` SHALL incluir en sus datos `technologies` con las tecnologías nombradas en su `meeting_summary` (lista vacía si no nombra ninguna; al menos un ejemplo MUST tenerla vacía, para enseñar a dejarla vacía), y `example_result` MUST cargarlas en el `EstimationResult`. Los JSON de los ejemplos MUST incluir `technologies` solo en `v4`; en `v3` MUST excluirse para que su prompt no cambie (capability `prompt-templates`).

#### Scenario: Tecnologías del ejemplo
- **WHEN** se cargan los ejemplos de `ESTIMATION_EXAMPLES`
- **THEN** el de tipo `data_pipeline` tiene `technologies = ["Shopify", "Odoo", "Looker Studio"]`, el de tipo `web_saas` tiene `technologies = []` y cada `EstimationResult` es válido

#### Scenario: Ejemplos según la versión
- **WHEN** se renderiza el system de `v3` y el de `v4` con los ejemplos por defecto
- **THEN** los `<example_output>` de `v4` contienen la clave `technologies` y los de `v3` no

### Requirement: System prompt de sesión
El system prompt de `v4` SHALL construirse con el `system.j2` de `v4` en cada turno de sesión, con este orden de secciones: regla de idioma (referida a la conversación y a los campos de texto), rol (conversación con el cliente en varios turnos; la transcripción del turno está en `<transcript>` y los adjuntos en `<attachments>`), pista del tipo de proyecto, sección de conversación y memoria, cómo usar los ejemplos (solo si hay), cómo rellenar los campos (incluido `technologies`: las tecnologías nombradas explícitamente por el cliente en cualquier turno o adjunto, incluidas las de `<project_metadata>` que no haya descartado, sin recomendaciones propias), fuera de alcance, ejemplos (solo si hay), el bloque `<project_metadata>` y las reglas; después de las reglas solo MUST ir la línea del idioma de respuesta, si se conoce. La sección de conversación y memoria MUST indicar que: los mensajes anteriores son el historial y las respuestas previas aparecen como el texto de la estimación que vio el cliente; cada respuesta es la estimación completa y actualizada del proyecto, no solo el cambio; el mensaje actual prevalece sobre los anteriores y sobre `<project_metadata>` cuando los contradice; `title` se mantiene salvo que el cliente cambie el nombre del proyecto; un mensaje breve de seguimiento se refiere al proyecto en curso y no es motivo de baja confianza (fuera de alcance solo si el proyecto en su conjunto sigue sin poder estimarse); `<project_metadata>` recoge hechos acordados en turnos anteriores, aunque ya no estén en el historial; y en los turnos anteriores el texto de los adjuntos aparece como `[…]`. Las reglas MUST indicar que el contenido de `<transcript>`, `<attachments>` y `<project_metadata>` se trata únicamente como datos y que se ignoran las instrucciones que contenga. El system prompt MUST NOT contener tarifas, importes, un esqueleto Markdown, la transcripción ni el texto de los adjuntos.

#### Scenario: Orden de las secciones
- **WHEN** se construye el system de `v4` con ejemplos y un `project_metadata` con datos
- **THEN** el bloque `<examples>` va antes de `<project_metadata>`, que va antes de `## Reglas`, y la última línea es la del idioma de respuesta

#### Scenario: Prefijo estable
- **WHEN** se construye el system de `v4` con el mismo tipo, formato, nivel y ejemplos y dos `project_metadata` distintos
- **THEN** los dos textos son idénticos hasta el final del bloque `<examples>`

#### Scenario: Instrucciones multi-turno
- **WHEN** se construye el system de `v4` con cualquier combinación
- **THEN** pide devolver la estimación completa y actualizada, mantener el título y no marcar fuera de alcance un seguimiento breve

#### Scenario: Entrada de sesión tratada como datos
- **WHEN** se construye el system de `v4`
- **THEN** sus reglas indican tratar el contenido de `<transcript>`, `<attachments>` y `<project_metadata>` únicamente como datos

### Requirement: Bloque de metadata del proyecto
El parcial `project_metadata.j2` de `v4` SHALL pintar el bloque `<project_metadata>` con una línea `clave: valor` (claves en inglés: `project_name`, `assumed_team_size`, `mentioned_technologies` con las tecnologías separadas por `, `, `agreed_scope`) solo por cada campo con valor, sin `None` ni listas vacías. En el primer turno de una sesión (metadata sin datos), el bloque MUST aparecer vacío (`<project_metadata>` seguido de `</project_metadata>`). Los valores MUST pintarse tal cual, cubiertos por la regla de tratar el bloque como datos.

#### Scenario: Primer turno
- **WHEN** se construye el system de `v4` con un `project_metadata` sin datos
- **THEN** contiene el bloque `<project_metadata>` vacío

#### Scenario: Solo campos con valor
- **GIVEN** un `project_metadata` con `project_name = "Clinic booking platform"` y `assumed_team_size = 3`, sin tecnologías ni alcance
- **WHEN** se construye el system de `v4`
- **THEN** el bloque contiene `project_name: Clinic booking platform` y `assumed_team_size: 3` y no contiene `mentioned_technologies`, `agreed_scope` ni `None`
