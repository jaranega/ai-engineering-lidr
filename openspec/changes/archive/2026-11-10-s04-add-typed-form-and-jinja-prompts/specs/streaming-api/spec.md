## REMOVED Requirements

### Requirement: Endpoint de estimación en streaming
**Reason**: Su único cliente era el chat multi-turno, que se retira en S04; el formulario usa `POST /api/v1/estimate` sin streaming.
**Migration**: Usar `POST /api/v1/estimate`. El proveedor conserva su método de streaming (capability `llm-provider`) por si una sesión posterior vuelve a exponerlo.

### Requirement: Formato de los eventos
**Reason**: Su único cliente era el chat multi-turno, que se retira en S04; el formulario usa `POST /api/v1/estimate` sin streaming.
**Migration**: Usar `POST /api/v1/estimate`. El proveedor conserva su método de streaming (capability `llm-provider`) por si una sesión posterior vuelve a exponerlo.

### Requirement: Errores durante el streaming
**Reason**: Su único cliente era el chat multi-turno, que se retira en S04; el formulario usa `POST /api/v1/estimate` sin streaming.
**Migration**: Usar `POST /api/v1/estimate`. El proveedor conserva su método de streaming (capability `llm-provider`) por si una sesión posterior vuelve a exponerlo.

### Requirement: Desconexión del cliente
**Reason**: Su único cliente era el chat multi-turno, que se retira en S04; el formulario usa `POST /api/v1/estimate` sin streaming.
**Migration**: Usar `POST /api/v1/estimate`. El proveedor conserva su método de streaming (capability `llm-provider`) por si una sesión posterior vuelve a exponerlo.
