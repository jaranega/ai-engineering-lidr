## ADDED Requirements

### Requirement: Tope de coste con coste desconocido
Con `--max-cost-usd`, el runner SHALL tratar como desconocido el coste de una fila con `cost_usd = null`, incluidas las filas de error de un turno que llegó a llamar al LLM. Ante el primer coste desconocido, MUST detener la ejecución e indicar en el resumen que el tope no puede garantizarse, en lugar de contarlo como 0. Sin `--max-cost-usd`, un coste desconocido MUST mostrarse como `-` y contarse aparte en el resumen. Los casos bloqueados por los guardrails (sin llamada al LLM) MUST NOT contar como coste desconocido.

#### Scenario: Coste desconocido con tope
- **GIVEN** `--max-cost-usd 0.25` y un proveedor simulado cuyo primer turno devuelve `cost_usd = null`
- **WHEN** se ejecutan tres casos
- **THEN** el runner se detiene tras el primero e indica que el coste es desconocido y el tope no puede garantizarse

#### Scenario: Caso bloqueado sin coste
- **GIVEN** `--max-cost-usd 0.25` y un primer caso bloqueado por inyección
- **WHEN** se ejecutan dos casos
- **THEN** el runner ejecuta los dos
