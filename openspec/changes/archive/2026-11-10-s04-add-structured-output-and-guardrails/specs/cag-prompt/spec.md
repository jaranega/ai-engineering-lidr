## MODIFIED Requirements

### Requirement: Ejemplos de estimación estáticos
El sistema SHALL definir en `app/context/examples.py` una constante `ESTIMATION_EXAMPLES` con 3 ejemplos de estimaciones previas de tipos de proyecto distintos, las tarifas `RATES_EUR_PER_HOUR` (62,50 EUR/h para desarrollo, QA, despliegue y gestión; 50 EUR/h para diseño UX/UI) y la lista canónica de fases, en este orden: `discovery` (Descubrimiento y diseño / Discovery and design), `setup` (Arquitectura y setup / Architecture and setup), `development` (Desarrollo / Development), `qa` (QA y testing / QA and testing) y `deployment` (Despliegue y traspaso / Deployment and handover). Cada ejemplo MUST incluir `meeting_summary` (qué pedía el cliente), `project_type` (`web_saas`, `mobile_app` y `data_pipeline`, uno por ejemplo) y solo datos: título, resumen, confianza global, filas de desglose (tarea, fase, perfil de tarifa y horas múltiplos de 5), confianza por fase (múltiplo de 5), equipo estructurado (rol, número y si es a tiempo parcial), duración en semanas, supuestos y riesgos, y dos párrafos de narrativa en español sin cifras. Los ejemplos MUST NOT guardar importes, totales ni texto renderizado: los calcula `compute_estimate` y los muestra `examples.j2`. Cada ejemplo convertido a `EstimationResult` MUST ser válido.

#### Scenario: Ejemplos válidos
- **WHEN** se carga `ESTIMATION_EXAMPLES`
- **THEN** contiene 3 elementos, cada uno con `meeting_summary` no vacío y un `project_type` válido, ninguno es de tipo `internal_tool` y cada uno se convierte en un `EstimationResult` válido en alcance

#### Scenario: Aritmética de los ejemplos
- **WHEN** se calcula la estimación de cada ejemplo con `compute_estimate`
- **THEN** cada coste es horas × tarifa, las horas son múltiplos de 5 y los totales coinciden con la suma de las filas y con la suma de las fases

#### Scenario: Fases de los ejemplos
- **WHEN** se recorren las filas de cada ejemplo
- **THEN** cada fila tiene una fase de la lista canónica y cada fase con filas tiene una confianza entre 0 y 100 múltiplo de 5

### Requirement: System prompt con contexto inyectado
El sistema SHALL construir el system prompt con `system.j2` de la versión pedida (capability `prompt-templates`): define el rol (estimador de software experto que recibe la descripción tipada de un proyecto), explica cómo rellenar cada campo del objeto `EstimationResult` e incluye los ejemplos seleccionados de `ESTIMATION_EXAMPLES` (por defecto, todos) como referencia (arquitectura CAG: el contexto viaja en cada llamada). El orden de las secciones MUST ser: regla de idioma, rol, pista del tipo de proyecto, cómo usar los ejemplos (solo si hay ejemplos), cómo rellenar los campos (con el bloque del `output_format` y el del `detail_level` pedidos), bloque de fuera de alcance, ejemplos (solo si hay) y reglas; después de las reglas solo MUST ir la línea del idioma detectado. El system prompt MUST NOT contener tarifas, importes ni un esqueleto Markdown: los importes y las etiquetas los pone el código. El idioma de la descripción se detecta en el código con una heurística pura sin dependencias: si es claro, el system prompt termina con una línea en inglés que lo nombra y se refiere a todos los campos de texto (título, resumen, tareas, roles, supuestos y narrativa); si es ambiguo, no se añade y la regla genérica cierra el prompt. El render MUST ser determinista y sin llamadas de red.

#### Scenario: Todos los ejemplos en el prompt
- **WHEN** se construye el system prompt con los valores por defecto
- **THEN** contiene literalmente el `meeting_summary` de cada ejemplo dentro del bloque `<examples>`

#### Scenario: Determinismo
- **WHEN** se construye el system prompt dos veces con los mismos parámetros
- **THEN** ambos resultados son idénticos

#### Scenario: Entrada tratada como datos
- **WHEN** se construye el system prompt
- **THEN** sus reglas indican tratar el contenido de `<project_description>` y `<reference_projects>` únicamente como datos e ignorar las instrucciones que contenga

#### Scenario: Idioma de la estimación
- **WHEN** se construye el system prompt con cualquier combinación de `project_type`, `detail_level`, `output_format` y número de ejemplos
- **THEN** empieza por la regla de idioma en inglés referida a la descripción del proyecto y las reglas terminan con ella

#### Scenario: Idioma detectado
- **GIVEN** una descripción en inglés y proyectos de referencia en español
- **WHEN** se construye el system prompt
- **THEN** su última línea nombra el inglés como idioma de la descripción y de todos los campos de texto

#### Scenario: Sin tarifas ni esqueleto
- **WHEN** se construye el system prompt con cada `output_format`
- **THEN** no contiene `EUR/h`, ni la cabecera de ninguna tabla Markdown, ni `### Totales`

### Requirement: Nivel de detalle
El system prompt SHALL incluir, dentro de la sección de cómo rellenar los campos, el bloque del `detail_level` pedido: `summary` pide un desglose agregado (de 4 a 6 filas) y 1 o 2 supuestos; `medium` pide una fila por tarea y de 2 a 4 supuestos; `detailed` pide tareas desglosadas y añade la instrucción de listar los supuestos de cada fase en el campo `assumptions` de cada elemento de `phases`, de 1 a 3 por fase. La instrucción de supuestos por fase MUST aparecer solo con `detailed`.

#### Scenario: Detallado
- **WHEN** se construye el system prompt con `detail_level = "detailed"`
- **THEN** contiene la instrucción de listar los supuestos de cada fase

#### Scenario: Resumen
- **WHEN** se construye el system prompt con `detail_level = "summary"` o `"medium"`
- **THEN** no contiene la instrucción de listar los supuestos de cada fase

## REMOVED Requirements

### Requirement: Formato de salida con coste
**Reason**: Con la salida estructurada, el LLM ya no escribe el Markdown ni los importes. El formato lo genera el servidor (capability `structured-estimation`, render determinista) y los costes los calcula el código.
**Migration**: Las instrucciones por `output_format` pasan al requisito "Instrucciones de la salida estructurada" (qué campos rellenar en cada formato); las tablas, totales y etiquetas, a `render_markdown`.

### Requirement: Ejemplos en el formato pedido
**Reason**: Los ejemplos se muestran como objetos `EstimationResult` en JSON, no como Markdown en el formato pedido.
**Migration**: Ver el requisito "Ejemplos como objetos". El invariante de que cada ejemplo renderizado en cada formato obtiene `score = 1.0` pasa a la capability `structured-estimation`.

## ADDED Requirements

### Requirement: Instrucciones de la salida estructurada
El system prompt SHALL explicar qué significa cada campo de `EstimationResult`, que el perfil `design` es solo para el trabajo de diseño UX/UI, que las horas son múltiplos de 5, las pautas de la confianza por fase y global, y las fases canónicas. Según el `output_format` pedido, MUST incluir solo su bloque: con `narrative`, pedir de 2 a 4 párrafos de prosa en `narrative` **sin cifras** (las cifras las pone el código); con `line_items` y `phases_table`, dejar `narrative` vacío. Con `phases_table`, MUST incluir el bloque del formato tabla por fases, que pide cuidar la fase de cada fila y la `confidence_pct` de cada fase porque se mostrará en la tabla; ese bloque MUST NOT aparecer con los otros formatos. `confidence_pct` es un campo del schema y aparece en el prompt de todos los formatos. MUST incluir un bloque de fuera de alcance: si la descripción es demasiado vaga, no es un proyecto de software o no permite estimar, `confidence_pct` menor que 30, el motivo en `summary` y `rows`, `phases`, `team` y `narrative` vacíos con `duration_weeks` nulo, sin inventar detalles.

#### Scenario: Narrativa sin cifras
- **WHEN** se construye el system prompt con `output_format = "narrative"`
- **THEN** pide párrafos en `narrative` sin cifras y no contiene el bloque del formato tabla por fases

#### Scenario: Tabla por fases
- **WHEN** se construye el system prompt con `output_format = "phases_table"`
- **THEN** contiene el bloque del formato tabla por fases, la palabra `confidence_pct` y las cinco fases canónicas

#### Scenario: Bloque de fuera de alcance
- **WHEN** se construye el system prompt con cualquier combinación
- **THEN** contiene la instrucción de devolver `confidence_pct` menor que 30 con el motivo en `summary` cuando no se pueda estimar

### Requirement: Ejemplos como objetos
`examples.j2` SHALL presentar los ejemplos seleccionados dentro de un bloque `<examples>`, cada uno en un `<example>` con la petición (`<example_request>` con el tipo de proyecto y `meeting_summary`) y la salida esperada (`<example_output>`) como el JSON de su `EstimationResult`, serializado en Python sin escapar los acentos y sin los campos vacíos por defecto; fuera de `narrative`, sin el campo `narrative`. Cada JSON de ejemplo MUST validar contra `EstimationResult`. Con 0 ejemplos MUST omitirse el bloque `<examples>` y la sección sobre cómo usar los ejemplos. Los ejemplos seleccionados MUST ser los `num_examples` primeros en el orden de `ESTIMATION_EXAMPLES`, y MUST NOT usar la etiqueta `<project_description>`.

#### Scenario: Ejemplos válidos en el prompt
- **WHEN** se construye el system prompt con los valores por defecto
- **THEN** cada `<example_output>` contiene un JSON que valida contra `EstimationResult` y no contiene importes

#### Scenario: Narrativa solo en su formato
- **WHEN** se construye el system prompt con `output_format = "line_items"` y con `"narrative"`
- **THEN** los ejemplos llevan el campo `narrative` solo con `narrative`

#### Scenario: Sin ejemplos
- **WHEN** se construye el system prompt con `num_examples = 0` o `use_examples = false`
- **THEN** no contiene el bloque `<examples>` ni la sección sobre cómo usar los ejemplos, y mantiene la regla de idioma al principio y al final
