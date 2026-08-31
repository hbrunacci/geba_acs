from __future__ import annotations

from django.db import models
from django.utils import timezone


class XsysBajaRevision(models.Model):
    """Espejo de ``CD_Clientes_Baja_Revision``: socios cuya baja está en duda.

    El 28/08/2026, entre las 15:41 y las 15:46, un proceso externo marcó 1.259
    socios como fallecidos. La auditoría de xSys no lo registra —``Id_Usuario``
    0 en ``Clientes_Hist``, nada en ``Seg_Usuarios_Audit``—, así que no pasó por
    la aplicación: se escribió directo contra la base. De los 1.259, 1.164
    estaban de alta, y al menos uno (la socia #185083) demostró estar viva.

    Mientras la oficina de Socios revisa caso por caso, a esta gente no se la
    frena en la puerta: ``CP_SCA_RegistrarAcceso`` saltea el rechazo por persona
    inactiva y los resuelve por su categoría, como venían entrando. Acá se
    espeja la lista para poder avisarle al operador y dejar el aviso en el
    legajo — el paso lo decide xSys, esto es sólo para que se vea.

    Cuando un socio se revisa, en xSys se le pone ``Activo = 0`` en esa tabla y
    vuelve a regir la regla normal; acá esa fila queda con ``en_revision=False``.
    """

    id_cliente = models.BigIntegerField(primary_key=True)
    origen = models.CharField(max_length=60, blank=True, default="")
    # Sigue en revisión (todavía se lo deja pasar). Es el ``Activo`` de xSys,
    # renombrado porque "activo" ahí significa lo contrario de lo que parece:
    # activo = la excepción está vigente, no que el socio esté activo.
    en_revision = models.BooleanField(default=True, db_index=True)
    fecha_baja_orig = models.DateTimeField(null=True, blank=True)
    motivo_orig = models.SmallIntegerField(null=True, blank=True)
    observacion = models.CharField(max_length=200, blank=True, default="")
    synced_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "xsys_baja_revision"
        verbose_name = "Baja en revisión"
        verbose_name_plural = "Bajas en revisión"

    def __str__(self) -> str:  # pragma: no cover - representación auxiliar
        return f"baja en revisión {self.id_cliente} ({self.origen})"
