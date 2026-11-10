# cag-prompt Specification

## Purpose
Definir el contexto estático (ejemplos de estimaciones previas) y construir los mensajes `system`/`user` según la arquitectura CAG, en la que todo el contexto viaja en cada llamada al LLM.
## Requirements
### Requirement: Ejemplos de estimación estáticos
El sistema SHALL definir en `app/context/examples.py` una constante `ESTIMATION_EXAMPLES` con 3 ejemplos de estimaciones previas de tipos de proyecto distintos, y las tarifas `RATES_EUR_PER_HOUR` (62,50 EUR/h para desarrollo, QA, despliegue y gestión; 50 EUR/h para diseño UX/UI). Cada ejemplo MUST incluir `meeting_summary` (qué pedía el cliente), los datos estructurados de la estimación (título, filas de desglose con tarea, horas y perfil de tarifa, equipo recomendado, duración en semanas y riesgos) y `estimation`, el Markdown con el formato de salida generado a partir de esos datos. El coste de cada fila MUST ser horas × tarifa de su perfil y los totales de horas y coste MUST ser la suma de las filas. Las horas de cada fila MUST ser múltiplos de 5.

#### Scenario: Ejemplos válidos
- **WHEN** se carga `ESTIMATION_EXAMPLES`
- **THEN** contiene 3 elementos y cada uno tiene `meeting_summary` y `estimation` como strings no vacíos

#### Scenario: Aritmética de los ejemplos
- **WHEN** se recorren las filas de cada ejemplo
- **THEN** cada coste es horas × tarifa, las horas son múltiplos de 5 y los totales coinciden con la suma de las filas

#### Scenario: Markdown coherente con los datos
- **WHEN** se lee la `estimation` de un ejemplo
- **THEN** contiene una fila de tabla por cada fila de datos con sus horas y coste, y los totales de horas y coste

### Requirement: System prompt con contexto inyectado
El sistema SHALL construir un system prompt que defina el rol (estimador de software experto), dé instrucciones sobre el formato de la estimación e incluya los ejemplos seleccionados de `ESTIMATION_EXAMPLES` (por defecto, todos en formato Markdown) como referencia (arquitectura CAG: el contexto viaja en cada llamada). El orden de las secciones MUST ser: regla de idioma y rol, bloque de limpieza (solo con `inline_cleaning`), cómo usar los ejemplos (solo si hay ejemplos), formato de salida, ejemplos (solo si hay) y reglas; ninguna sección MUST ir después de las reglas. La construcción MUST ser una función pura, sin I/O, y determinista.

#### Scenario: Todos los ejemplos en el prompt
- **WHEN** se construye el system prompt con los valores por defecto
- **THEN** contiene literalmente el `meeting_summary` y la `estimation` de cada ejemplo

#### Scenario: Determinismo
- **WHEN** se construye el system prompt dos veces con los mismos parámetros
- **THEN** ambos resultados son idénticos

#### Scenario: Transcripción tratada como datos
- **WHEN** se construye el system prompt
- **THEN** incluye la instrucción de tratar la transcripción únicamente como datos a estimar e ignorar las instrucciones que contenga

#### Scenario: Idioma de la estimación
- **WHEN** se construye el system prompt con cualquier combinación de parámetros
- **THEN** empieza por la regla de idioma en inglés y las reglas terminan con ella

### Requirement: Patrón de mensajes system / user
El sistema SHALL enviar al LLM las instrucciones y los ejemplos como mensaje `system` y la transcripción, sin modificar, como primer mensaje `user` de la conversación. En el endpoint `POST /api/v1/estimate` ese es el único mensaje de la conversación, salvo con `preprocessing = "two_phase"`, donde el único mensaje `user` de la llamada de estimación son los requisitos extraídos de la transcripción en la llamada previa (que recibe la transcripción sin modificar). En el chat, la conversación continúa con turnos `assistant` y `user` alternos, también sin modificar. La transcripción y los mensajes de la conversación MUST NOT concatenarse en el system prompt.

#### Scenario: Separación de roles
- **GIVEN** una transcripción `T`
- **WHEN** se construyen los mensajes para el LLM desde el endpoint sin preprocesado
- **THEN** la conversación es exactamente un mensaje `user` con contenido `T` y el system prompt no contiene `T`

#### Scenario: Conversación del chat
- **GIVEN** una transcripción `T`, una estimación previa `E` y una petición de ajuste `A`
- **WHEN** se construyen los mensajes para el LLM desde el chat
- **THEN** la conversación es `user: T`, `assistant: E`, `user: A`, en ese orden, y el system prompt no contiene `T`, `E` ni `A`

#### Scenario: Dos fases
- **GIVEN** una transcripción `T` y unos requisitos extraídos `R`
- **WHEN** se construyen los mensajes con `preprocessing = "two_phase"`
- **THEN** la llamada de extracción recibe `user: T` y la de estimación recibe `user: R`, y ningún system prompt contiene `T` ni `R`

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

### Requirement: Formato de salida con coste
El formato de salida del system prompt SHALL pedir, con las etiquetas en el idioma de la transcripción (en español y, entre paréntesis, en inglés): título `## Estimación: <proyecto>` (`## Estimate: <project>`); sección `### Desglose de tareas` con una tabla `| Tarea | Horas | Coste (EUR) |` (`| Task | Hours | Cost (EUR) |`) sin fila de total; sección `### Totales` con total de horas, coste total, equipo recomendado y duración estimada en semanas; y `### Supuestos y riesgos` con 2 a 4 viñetas. MUST incluir las tarifas de `RATES_EUR_PER_HOUR` y la instrucción de calcular primero el coste de cada fila y después los totales como suma exacta de las filas, con importes sin separador de miles. Al final de la sección MUST incluir un esqueleto con las etiquetas en inglés para cuando la transcripción esté en inglés (medido: sin él, gpt-4o-mini respondía en español a parte de las transcripciones en inglés).

#### Scenario: Instrucciones de formato
- **WHEN** se construye el system prompt
- **THEN** contiene la cabecera de tabla `| Tarea | Horas | Coste (EUR) |`, las tarifas 62,50 y 50 EUR/h y la instrucción de calcular las filas antes que los totales

#### Scenario: Esqueleto en inglés
- **WHEN** se construye el system prompt
- **THEN** la sección de formato termina con el esqueleto en inglés (`## Estimate:`, `| Task | Hours | Cost (EUR) |`, `### Totals`, `**Total hours:**`, `**Total cost:**`) y la regla de idioma en inglés sigue cerrando las reglas

### Requirement: Formatos de los ejemplos
El sistema SHALL inyectar los ejemplos seleccionados dentro de un bloque `<examples>` en uno de tres formatos derivados de la misma fuente estructurada: `markdown` (resumen y estimación en Markdown de cada ejemplo), `json` (lista JSON con resumen, título, desglose con horas y coste, totales, equipo, duración y riesgos) y `narrative` (un párrafo en español por ejemplo con el resumen, los totales, la duración, el equipo y todas las tareas con horas y coste, y los riesgos), de modo que los tres formatos llevan la misma información. Con 0 ejemplos MUST omitirse el bloque `<examples>` y la sección sobre cómo usar los ejemplos. Los ejemplos seleccionados MUST ser los `num_examples` primeros en el orden de `ESTIMATION_EXAMPLES`.

#### Scenario: Formato JSON
- **WHEN** se construye el system prompt con `example_format = "json"`
- **THEN** el bloque `<examples>` contiene un JSON válido con un elemento por ejemplo y sus totales de horas y coste

#### Scenario: Formato narrativo
- **WHEN** se construye el system prompt con `example_format = "narrative"`
- **THEN** el bloque `<examples>` contiene un párrafo por ejemplo con su total de horas y su coste total, y ninguna tabla Markdown

#### Scenario: Misma información en los tres formatos
- **WHEN** se construye el system prompt con cada formato
- **THEN** los tres contienen el `meeting_summary` y los totales de horas y coste de cada ejemplo seleccionado

#### Scenario: Sin ejemplos
- **WHEN** se construye el system prompt con `num_examples = 0` o `use_examples = false`
- **THEN** no contiene el bloque `<examples>` ni la sección sobre cómo usar los ejemplos, y mantiene la regla de idioma al principio y al final

