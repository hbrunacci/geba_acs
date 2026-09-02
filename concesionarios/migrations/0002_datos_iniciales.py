"""Arranque mínimo: el grupo del rol y unos tipos de documento para empezar.

Los tipos se crean **sin** ``bloquea_acceso``: que un papel vencido deje a
alguien afuera es una decisión del club, no un default que aparezca solo. Se
marcan desde la pantalla de empresas y documentos.
"""

from django.db import migrations

TIPOS = [
    ("art", "ART", "Aseguradora de riesgos del trabajo", True, 30),
    ("seguro_ap", "Seguro de accidentes personales", "", True, 30),
    ("libreta_sanitaria", "Libreta sanitaria", "Manipulación de alimentos", True, 60),
    ("apto_medico", "Apto médico", "", True, 30),
    ("dni", "DNI", "Copia del documento", False, 0),
]


def cargar(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name="Concesionarios")

    TipoDocumento = apps.get_model("concesionarios", "TipoDocumento")
    for codigo, nombre, desc, vence, aviso in TIPOS:
        TipoDocumento.objects.get_or_create(
            codigo=codigo,
            defaults={"nombre": nombre, "descripcion": desc,
                      "requiere_vencimiento": vence, "dias_aviso": aviso or 30,
                      "bloquea_acceso": False, "activo": True},
        )


def descargar(apps, schema_editor):
    """Sólo saca los tipos que no se usaron: si ya hay documentos cargados con
    uno, borrarlo se llevaría puesto el dato de alguien."""
    TipoDocumento = apps.get_model("concesionarios", "TipoDocumento")
    for codigo, *_ in TIPOS:
        tipo = TipoDocumento.objects.filter(codigo=codigo).first()
        if tipo and not tipo.documentos.exists():
            tipo.delete()


class Migration(migrations.Migration):

    dependencies = [("concesionarios", "0001_initial")]

    operations = [migrations.RunPython(cargar, descargar)]
