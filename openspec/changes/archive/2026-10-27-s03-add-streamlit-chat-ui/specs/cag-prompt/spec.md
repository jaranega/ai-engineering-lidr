## MODIFIED Requirements

### Requirement: Patrón de mensajes system / user
El sistema SHALL enviar al LLM las instrucciones y los ejemplos como mensaje `system` y la transcripción, sin modificar, como primer mensaje `user` de la conversación. En el endpoint `POST /api/v1/estimate` ese es el único mensaje de la conversación. En el chat, la conversación continúa con turnos `assistant` y `user` alternos, también sin modificar. La transcripción y los mensajes de la conversación MUST NOT concatenarse en el system prompt.

#### Scenario: Separación de roles
- **GIVEN** una transcripción `T`
- **WHEN** se construyen los mensajes para el LLM desde el endpoint
- **THEN** la conversación es exactamente un mensaje `user` con contenido `T` y el system prompt no contiene `T`

#### Scenario: Conversación del chat
- **GIVEN** una transcripción `T`, una estimación previa `E` y una petición de ajuste `A`
- **WHEN** se construyen los mensajes para el LLM desde el chat
- **THEN** la conversación es `user: T`, `assistant: E`, `user: A`, en ese orden, y el system prompt no contiene `T`, `E` ni `A`

## ADDED Requirements

### Requirement: System prompt del chat
El system prompt del chat SHALL depender del turno: cuando la conversación tiene un único mensaje (la transcripción), MUST ser exactamente el system prompt CAG del endpoint; cuando la conversación ya tiene historial, MUST ser ese mismo prompt, íntegro y sin modificaciones, seguido de una sección de conversación que indique al modelo: que el primer mensaje del usuario es la transcripción; que los mensajes posteriores son peticiones de ajuste del usuario sobre la estimación (no contenido de la transcripción) y deben responderse con la estimación completa actualizada en el mismo formato y en el idioma de la transcripción, aunque la petición esté escrita en otro idioma; que si un mensaje posterior es una transcripción nueva debe estimarla desde cero; y que si un mensaje no tiene relación con la estimación debe responder brevemente, en el idioma de ese mensaje, que solo genera y ajusta estimaciones. La construcción MUST ser pura y determinista, y el system prompt del endpoint MUST NOT cambiar.

#### Scenario: Primer turno con el prompt del endpoint
- **GIVEN** una conversación con un único mensaje `user`
- **WHEN** se construye el system prompt del chat
- **THEN** es idéntico al system prompt del endpoint

#### Scenario: Prompt del endpoint contenido
- **GIVEN** una conversación con historial (`user`, `assistant`, `user`)
- **WHEN** se construye el system prompt del chat
- **THEN** empieza exactamente por el del endpoint y añade la sección de conversación al final

#### Scenario: Idioma de los ajustes
- **WHEN** se construye el system prompt del chat para una conversación con historial
- **THEN** la sección de conversación indica responder los ajustes en el idioma de la transcripción

#### Scenario: Endpoint sin cambios
- **WHEN** `POST /api/v1/estimate` construye su system prompt
- **THEN** no contiene la sección de conversación

#### Scenario: Determinismo del prompt del chat
- **WHEN** se construye el system prompt del chat dos veces
- **THEN** ambos resultados son idénticos
