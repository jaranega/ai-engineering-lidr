"""Static CAG context: past software estimations injected into the system prompt.

These examples are the system's "knowledge". They are fictitious but internally
consistent: each total equals the sum of its breakdown, and each duration follows
from the recommended team (about 30 productive hours per full-time person per week).
They cover three project types and sizes (small integration, medium web platform,
large mobile app) so the model can calibrate granularity and order of magnitude.

Content is in Spanish (product language); the model answers in the language of the
transcription. Strings have no leading/trailing whitespace so they are injected verbatim.
"""

from typing import TypedDict


class EstimationExample(TypedDict):
    meeting_summary: str
    estimation: str


ESTIMATION_EXAMPLES: list[EstimationExample] = [
    {
        "meeting_summary": (
            "Una red de 5 clínicas de fisioterapia gestiona las citas por teléfono y con hojas "
            "de cálculo. Quiere una plataforma web interna para recepción y fisioterapeutas: "
            "agenda por sede y profesional, alta y ficha de pacientes con historial de "
            "sesiones, recordatorios automáticos de cita por email y SMS para reducir "
            "ausencias, y un panel con ocupación y cancelaciones por sede. Tres perfiles de "
            "acceso: administración, recepción y fisioterapeuta. No hay diseño previo. "
            "Esperan unos 40 usuarios internos y les gustaría tenerla en unos dos meses."
        ),
        "estimation": """## Estimación: Plataforma web de gestión de citas para clínicas

### Desglose de tareas
1. Descubrimiento y diseño UI/UX (flujos, wireframes y prototipo): 40 horas
2. Arquitectura, repositorio, CI/CD y entornos: 15 horas
3. Autenticación y roles (administración, recepción, fisioterapeuta): 25 horas
4. Agenda y reservas por sede y profesional (calendario, huecos, cambios): 80 horas
5. Fichas de pacientes e historial de sesiones: 50 horas
6. Recordatorios de cita por email y SMS (proveedor externo): 25 horas
7. Panel de métricas (ocupación y cancelaciones por sede): 30 horas
8. Testing y QA: 40 horas
9. Despliegue en producción y formación a usuarios: 15 horas

**Total estimado: 320 horas**
**Equipo recomendado: 2 desarrolladores full-stack + 1 diseñador UX/UI (part-time)**
**Duración estimada: 6-7 semanas**

### Supuestos y riesgos
- Se usa un proveedor de SMS de pago por uso; su coste no está incluido.
- No se migran datos históricos de las hojas de cálculo; si se requiere, añadir 20-30 horas.
- Los datos de salud exigen cumplir el RGPD (cifrado y registro de accesos).""",
    },
    {
        "meeting_summary": (
            "Una cadena de 30 cafeterías quiere una app móvil de fidelización para iOS y "
            "Android. Los clientes deben poder registrarse (email, Apple o Google), acumular "
            "puntos mostrando un código QR en caja, ver el catálogo y las promociones, y hacer "
            "pedidos anticipados con pago en la app para recogerlos en tienda. El TPV actual "
            "tiene una API documentada para registrar compras y canjes. Marketing necesita un "
            "backoffice web para crear promociones, enviar notificaciones push segmentadas y "
            "ver informes de uso. Tienen la identidad de marca, pero no diseños de la app. "
            "Quieren lanzarla antes de la campaña de Navidad, en unos tres meses."
        ),
        "estimation": """## Estimación: App móvil de fidelización y pedido anticipado

### Desglose de tareas
1. Descubrimiento y diseño UI/UX (app y backoffice): 70 horas
2. Setup multiplataforma (React Native), CI/CD y entornos: 25 horas
3. Backend API (usuarios, puntos, catálogo, promociones y pedidos): 90 horas
4. Registro, login social (Apple y Google) y perfil: 35 horas
5. Tarjeta de fidelización con QR y acumulación de puntos: 45 horas
6. Catálogo, pedido anticipado y pago integrado (Stripe): 100 horas
7. Integración con la API del TPV (compras y canjes): 60 horas
8. Notificaciones push segmentadas: 30 horas
9. Backoffice web (promociones, usuarios e informes): 70 horas
10. Testing y QA en dispositivos iOS y Android: 70 horas
11. Publicación en App Store y Google Play: 20 horas

**Total estimado: 615 horas**
**Equipo recomendado: 2 desarrolladores móviles (React Native) + 1 desarrollador backend + \
1 diseñador UX/UI (part-time) + 1 QA (part-time)**
**Duración estimada: 9-10 semanas**

### Supuestos y riesgos
- La API del TPV es estable y tiene entorno de pruebas; si no, la integración puede duplicarse.
- La revisión de las tiendas de apps puede tardar varios días; conviene reservar margen.
- No incluye pedidos a domicilio ni integración con plataformas de reparto.""",
    },
    {
        "meeting_summary": (
            "Una tienda online en Shopify con unos 300 pedidos diarios introduce los pedidos "
            "a mano en su ERP (Odoo) y actualiza el stock en Shopify una vez al día, lo que "
            "provoca ventas sin stock. Quieren sincronizar automáticamente pedidos y clientes "
            "de Shopify a Odoo, actualizar el stock de Odoo a Shopify cada pocos minutos, "
            "recibir una alerta cuando falle una sincronización y tener un informe diario de "
            "ventas en Looker Studio. No necesitan interfaz propia. Prioridad: estar en "
            "marcha en un mes."
        ),
        "estimation": """## Estimación: Integración Shopify-Odoo y reporting de ventas

### Desglose de tareas
1. Análisis de las APIs y mapeo de datos (pedidos, clientes, productos): 15 horas
2. Infraestructura serverless, cola de mensajes y gestión de secretos: 10 horas
3. Sincronización de pedidos y clientes Shopify → Odoo (webhooks): 30 horas
4. Sincronización de stock Odoo → Shopify (cada 15 minutos): 20 horas
5. Gestión de errores, reintentos y alertas por email: 15 horas
6. Informe diario de ventas en Looker Studio: 15 horas
7. Pruebas con datos reales y QA: 15 horas
8. Documentación y traspaso al equipo del cliente: 5 horas

**Total estimado: 125 horas**
**Equipo recomendado: 1 desarrollador backend con experiencia en integraciones**
**Duración estimada: 4-5 semanas**

### Supuestos y riesgos
- El cliente facilita acceso de API a Shopify y a Odoo, con un entorno de pruebas de Odoo.
- Los productos comparten SKU en ambos sistemas; si no, añadir 10-15 horas de normalización.
- Los límites de peticiones de la API de Shopify pueden obligar a procesar por lotes.""",
    },
]
