## ADDED Requirements

### Requirement: Ejemplos de estimación estáticos
El sistema SHALL definir en `app/context/examples.py` una constante `ESTIMATION_EXAMPLES` con al menos dos ejemplos de estimaciones previas. Cada ejemplo MUST incluir `meeting_summary` (qué pedía el cliente) y `estimation` (desglose de tareas con horas, total, equipo recomendado y duración). Los ejemplos MUST cubrir tipos de proyecto distintos.

#### Scenario: Ejemplos válidos
- **WHEN** se carga `ESTIMATION_EXAMPLES`
- **THEN** contiene 2 o más elementos y cada uno tiene `meeting_summary` y `estimation` como strings no vacíos

### Requirement: System prompt con contexto inyectado
El sistema SHALL construir un system prompt que defina el rol (estimador de software experto), dé instrucciones sobre el formato de la estimación e incluya **todos** los ejemplos de `ESTIMATION_EXAMPLES` como referencia (arquitectura CAG: el contexto viaja en cada llamada). La construcción MUST ser una función pura, sin I/O, y determinista.

#### Scenario: Todos los ejemplos en el prompt
- **WHEN** se construye el system prompt
- **THEN** contiene literalmente el `meeting_summary` y la `estimation` de cada ejemplo

#### Scenario: Determinismo
- **WHEN** se construye el system prompt dos veces
- **THEN** ambos resultados son idénticos

#### Scenario: Idioma de la estimación
- **WHEN** se construye el system prompt
- **THEN** incluye la instrucción de responder en el mismo idioma en que está escrita la transcripción

### Requirement: Patrón de mensajes system / user
El sistema SHALL enviar al LLM las instrucciones y los ejemplos como mensaje `system` y la transcripción, sin modificar, como único mensaje `user`. La transcripción MUST NOT concatenarse en el system prompt.

#### Scenario: Separación de roles
- **GIVEN** una transcripción `T`
- **WHEN** se construyen los mensajes para el LLM
- **THEN** el mensaje `user` es exactamente `T` y el system prompt no contiene `T`
