## ADDED Requirements

### Requirement: Resolución del tier por reglas
El sistema SHALL definir en `app/sessions/tier.py` el enum `Tier` (`executive`, `pm`, `developer`, `default`), el tipo `TierRule` (`explicit`, `nda_detected`, `regulatory_context`, `technical_audience`, `small_team`, `default`, `prompt_version`), la clase inmutable `TierDecision(tier, rule)` y `resolve_tier(transcript, metadata, explicit, history_messages=()) -> TierDecision`, una función pura y determinista, sin LLM ni red, que aplica esta cadena y devuelve la primera regla que casa:

| # | Regla | Tier | Condición |
|---|---|---|---|
| 0 | `explicit` | el pedido | `explicit` no es `None` (incluido `default`) |
| 1 | `nda_detected` | `executive` | los patrones de la regla de ancla `nda` casan en la transcripción del turno, en el mensaje del cliente de alguna ancla o turno de la ventana (`history_messages`) o en algún elemento de `metadata.explicit_constraints` |
| 2 | `regulatory_context` | `executive` | los patrones de la regla de ancla `regulation` casan en las mismas fuentes |
| 3 | `technical_audience` | `developer` | la transcripción del turno contiene al menos 2 términos técnicos distintos |
| 4 | `small_team` | `pm` | `metadata.assumed_team_size` no es nulo y es ≤ 2 |
| 5 | `default` | `default` | ninguna de las anteriores |

Los patrones de NDA y normativa MUST ser los mismos objetos que usa el detector de anclas (una sola fuente). `history_messages` MUST ser los `user` guardados de las anclas y de la ventana (con los adjuntos como `[…]`); solo lo leen `nda_detected` y `regulatory_context`, porque un NDA o una normativa siguen vigentes aunque el cliente deje de nombrarlos, mientras que `technical_audience` mira solo la transcripción del turno. El tier MUST NOT resolverse sobre el texto de los adjuntos ni sobre el resumen, y MUST recalcularse en cada turno. `app/sessions/tier.py` MUST NOT importar `app.services` ni `app.prompts`.

#### Scenario: NDA en el turno
- **WHEN** se resuelve con la transcripción "Hemos firmado un NDA con el cliente" y un metadata vacío
- **THEN** la decisión es `executive` con `rule = "nda_detected"`

#### Scenario: Normativa en inglés
- **WHEN** se resuelve con la transcripción "The platform must be HIPAA compliant" y un metadata vacío
- **THEN** la decisión es `executive` con `rule = "regulatory_context"`

#### Scenario: NDA recordado por el metadata
- **GIVEN** un metadata con `explicit_constraints = ["NDA firmado con el cliente"]`
- **WHEN** se resuelve con la transcripción "Añade un panel de informes"
- **THEN** la decisión es `executive` con `rule = "nda_detected"`

#### Scenario: NDA recordado por el historial
- **GIVEN** un metadata sin restricciones y un historial cuyo turno 2 (en la ventana o anclado) dice "We have signed an NDA with the client"
- **WHEN** se resuelve con la transcripción "Add Stripe payments"
- **THEN** la decisión es `executive` con `rule = "nda_detected"`

#### Scenario: Precedencia
- **WHEN** se resuelve con una transcripción que menciona un NDA y los términos `kubernetes` y `graphql`
- **THEN** la decisión es `executive` con `rule = "nda_detected"`

#### Scenario: Sin señales
- **WHEN** se resuelve con "Queremos una app para reservar mesas en restaurantes" y un metadata vacío
- **THEN** la decisión es `default` con `rule = "default"`

#### Scenario: Muestras del repositorio
- **WHEN** se resuelve el tier de cada muestra de `samples/` con un metadata vacío
- **THEN** el resultado coincide con el fijado en el test

### Requirement: Vocabulario técnico
La regla `technical_audience` SHALL contar los términos técnicos distintos de la transcripción, sin distinguir mayúsculas y con límites de palabra, de una lista en español e inglés en la que cada grupo de sinónimos cuenta una vez: api/REST (`api`, y `REST` solo en mayúsculas, como `RESTful` o junto a "api"; "REST API" cuenta una vez), `graphql`, `endpoint`, `webhook`, `sdk`, microservicio/microservice, kubernetes/k8s, `docker`, `ci/cd`, `terraform`, `serverless`, `lambda`, `oauth`, `jwt`, `orm`, `backend`, `frontend`, postgres/postgresql, `mysql`, `mongodb`, `redis`, `kafka`, `typescript`, latencia/latency, `throughput`, escalado horizontal/horizontal scaling, esquema de base de datos/database schema, refactor/refactorizar, `iac`, `helm`, `grpc`, `airflow`, `spark` y `rabbitmq`. El umbral MUST ser de 2 términos distintos. Los nombres de producto de uso general (Stripe, iOS, Shopify) y palabras como "app", "web" o "datos" MUST NOT contar.

#### Scenario: Un término
- **WHEN** se resuelve con "Necesitamos una API para el catálogo" y un metadata vacío
- **THEN** la decisión no es `developer`

#### Scenario: Dos términos distintos
- **WHEN** se resuelve con "Exponemos un endpoint GraphQL detrás de Kubernetes" y un metadata vacío
- **THEN** la decisión es `developer` con `rule = "technical_audience"`

#### Scenario: El mismo término repetido
- **WHEN** se resuelve con "API pública y API interna" y un metadata vacío
- **THEN** la decisión no es `developer`

#### Scenario: Nombres de producto
- **WHEN** se resuelve con "App iOS con pagos Stripe y tienda Shopify" y un metadata vacío
- **THEN** la decisión no es `developer`

### Requirement: Equipo pequeño
La regla `small_team` SHALL aplicarse cuando el `assumed_team_size` del metadata acumulado (el equipo que recomendó la última estimación) no es nulo y es menor o igual que 2. En el primer turno de una sesión (metadata vacío) MUST NOT aplicarse.

#### Scenario: Tamaños de equipo
- **WHEN** se resuelve una transcripción sin otras señales con `assumed_team_size` `None`, `2` y `3`
- **THEN** las decisiones son `default`, `pm` con `rule = "small_team"` y `default`, respectivamente

### Requirement: Override explícito del tier
Si el turno recibe un `tier` explícito, `resolve_tier` SHALL devolverlo con `rule = "explicit"` sin evaluar las demás reglas, también cuando el valor es `default` (que fuerza "sin adaptación").

#### Scenario: Override sobre una regla
- **WHEN** se resuelve una transcripción con un NDA y `explicit = Tier.DEVELOPER`
- **THEN** la decisión es `developer` con `rule = "explicit"`

#### Scenario: Forzar sin adaptación
- **WHEN** se resuelve una transcripción con un NDA y `explicit = Tier.DEFAULT`
- **THEN** la decisión es `default` con `rule = "explicit"`

### Requirement: Explicabilidad del tier
Cada tier resuelto SHALL ir acompañado de la regla que lo decidió en la respuesta del turno (`tier`, `tier_rule`), en `GET /api/v1/sessions/{id}` (`last_tier`, `last_tier_rule`) y en el evento `tier_resolved`. El sistema MUST NOT devolver ni registrar el fragmento de texto que casó con una regla.

#### Scenario: Regla en la respuesta
- **GIVEN** una sesión y una transcripción con "We signed an NDA"
- **WHEN** se envía el turno
- **THEN** la respuesta lleva `tier = "executive"` y `tier_rule = "nda_detected"`

### Requirement: Tier y versión del prompt
El tier SHALL aplicarse solo con el prompt de sesión `v5`, el único con bloque `<audience>`. Con `prompt_version = v4` y sin `tier` explícito, la decisión MUST ser `TierDecision(default, "prompt_version")` sin evaluar las reglas. Con `prompt_version = v4` y un `tier` explícito, el turno MUST rechazarse con 422 (capability `conversation-sessions`). `POST /api/v1/estimate` MUST NOT aceptar ni resolver un tier.

#### Scenario: v4 sin tier explícito
- **WHEN** se envía un turno con `prompt_version=v4` y una transcripción con un NDA
- **THEN** la respuesta lleva `tier = "default"` y `tier_rule = "prompt_version"`

#### Scenario: Estimate sin tier
- **WHEN** se consulta `/openapi.json`
- **THEN** el cuerpo de `POST /api/v1/estimate` no tiene ningún campo `tier`
