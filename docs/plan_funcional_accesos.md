# Plan funcional: gestión de personas, accesos y reportes

## Objetivo

Extender el sistema para:

1. Registrar distintos tipos de personas vinculadas a la institución (por ejemplo: socios, profesores, concesionarios).
2. Gestionar documentación adjunta por tipo de persona, incluyendo fecha de vencimiento.
3. Configurar puertas de acceso y sus dispositivos asociados (molinetes, faciales, lectores de credenciales).
4. Configurar zonas de acceso controladas por puertas, con jerarquía por nivel de anillo.
5. Exponer reportes de estadísticas de accesos por categoría, sede y ventana temporal (últimos 5 días), además de un mapa de calor por hora del día.

## Alcance funcional

### 1) Tipos de persona y documentación

#### Entidades propuestas

- `PersonType`
  - `code` (único)
  - `name`
  - `description`
  - `is_active`
- `PersonTypeDocumentRequirement`
  - `person_type` (FK)
  - `document_type` (FK)
  - `requires_expiration` (bool)
  - `is_mandatory` (bool)
  - `max_validity_days` (opcional)
- `PersonDocument`
  - `person` (FK)
  - `document_type` (FK)
  - `file`
  - `issued_at`
  - `expires_at` (nullable si no vence)
  - `status` (vigente / por vencer / vencido)

#### Reglas de negocio

- Cada persona debe pertenecer a un tipo principal (`Person.person_type`).
- Los requisitos documentales se definen por tipo de persona y se validan al habilitar accesos.
- Si un documento obligatorio está vencido o ausente, la persona queda en estado restringido para el control de ingreso.
- Generar alertas para documentos que vencen dentro de una ventana configurable (ej.: 30 días).

### 2) Puertas y dispositivos

#### Entidades propuestas

- `AccessDoor`
  - `site` (FK)
  - `name`
  - `code` (único por sede)
  - `is_active`
- `DoorDevice`
  - `door` (FK)
  - `device_type` (`turnstile`, `facial`, `credential_reader`)
  - `vendor`
  - `serial_number`
  - `ip_address`
  - `direction` (`entry`, `exit`, `both`)
  - `is_active`

#### Reglas de negocio

- Una puerta puede tener uno o varios dispositivos.
- Todo registro de acceso debe quedar asociado al menos a una puerta; cuando exista granularidad, también al dispositivo.
- La desactivación de puerta/dispositivo no elimina histórico, sólo bloquea nuevos eventos.

### 3) Zonas de acceso por anillos

#### Entidades propuestas

- `AccessZone`
  - `site` (FK)
  - `name`
  - `ring_code` (formato de 2 dígitos: `00`, `10`, `11`, etc.)
  - `ring_level` (derivado del primer dígito)
  - `ring_order` (derivado del segundo dígito)
  - `parent_zone` (opcional, FK a sí misma)
  - `is_active`
- `DoorZoneControl`
  - `door` (FK)
  - `zone` (FK)
  - `control_type` (`entry`, `exit`, `both`)

#### Reglas de jerarquía

- `00` representa el perímetro.
- El primer dígito representa el nivel de profundidad (anillo).
- El segundo dígito representa el orden dentro del mismo anillo.
- Validación de tránsito: para entrar en una zona interna, debe existir trazabilidad por zonas previas requeridas (por ejemplo, para `10` se debió atravesar `00`).

#### Ejemplo del requerimiento

- `00`: Perimetral
- `10`: Pileta
- `11`: Cancha de tenis

Ambas zonas (`10` y `11`) comparten el mismo escalón jerárquico y dependen del paso previo por `00`.

### 4) Reportes y analítica

#### 4.1 Estadísticas por categoría (últimos 5 días)

- Entrada: sede (obligatoria), rango de fechas (default: últimos 5 días), categoría de persona (opcional).
- Salida:
  - cantidad total de accesos por día,
  - desglose por categoría de persona,
  - variación porcentual vs período anterior equivalente.

#### 4.2 Estadísticas por sede

- Comparativa entre sedes para el mismo período.
- Ranking de sedes por volumen de ingresos.

#### 4.3 Mapa de calor por hora

- Matriz `día x hora` (0–23) con cantidad de accesos.
- Filtros: sede, categoría de persona, tipo de puerta/zona.
- Exportables: JSON para frontend y CSV para auditoría.

## Diseño técnico recomendado

## API (Django REST Framework)

- Nuevos endpoints sugeridos:
  - `/api/person-types/`
  - `/api/document-requirements/`
  - `/api/person-documents/`
  - `/api/access-doors/`
  - `/api/door-devices/`
  - `/api/access-zones/`
  - `/api/door-zone-controls/`
  - `/api/reports/access-by-category/`
  - `/api/reports/access-by-site/`
  - `/api/reports/access-heatmap/`

## Persistencia y rendimiento

- Índices recomendados:
  - logs de acceso por `(site_id, occurred_at)`
  - logs de acceso por `(person_type_id, occurred_at)`
  - zonas por `(site_id, ring_code)` único
- Materialización opcional de agregados diarios/horarios para reportes de alto tráfico.

## Seguridad y trazabilidad

- Auditoría de cambios en configuración crítica (puertas, zonas, reglas documentales).
- Validaciones de permisos por rol para configuración vs consulta.
- Retención de evidencia documental con control de acceso al archivo adjunto.

## Plan de implementación por fases

### Fase 1: Modelo de dominio

- Crear modelos y migraciones para tipos de persona, documentos, puertas, dispositivos y zonas.
- Agregar constraints de unicidad y validaciones de `ring_code`.
- Incorporar administración en Django Admin.

### Fase 2: Reglas de acceso

- Integrar validación documental en motor de autorización.
- Integrar jerarquía de zonas en validación de tránsito.

### Fase 3: APIs y permisos

- Exponer endpoints CRUD para nuevas entidades.
- Definir permisos por grupo (operación/configuración/reportes).

### Fase 4: Reportes

- Implementar consultas agregadas para últimos 5 días.
- Implementar endpoint de heatmap horario.
- Agregar exportación CSV.

### Fase 5: Observabilidad y calidad

- Pruebas unitarias de reglas de negocio.
- Pruebas de integración API.
- Métricas y logs de performance en endpoints de reportes.

## Criterios de aceptación

1. Se pueden crear y administrar múltiples tipos de persona.
2. Se pueden adjuntar documentos por persona y controlar vencimientos.
3. Se pueden configurar puertas y múltiples dispositivos por puerta.
4. Se pueden definir zonas con codificación de anillos y jerarquía válida.
5. Se obtienen reportes por categoría y sede para últimos 5 días.
6. Se dispone de heatmap horario de accesos filtrable por sede.
