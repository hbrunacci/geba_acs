"""Los grupos como los pidió el club: ``concesionarios`` y ``responsables``.

La 0002 había creado el grupo con mayúscula ("Concesionarios"). Acá se renombra
en vez de crear otro, para que no queden dos grupos casi iguales y alguien
asigne el que no gobierna nada: los nombres de grupo de Django distinguen
mayúsculas.
"""

from django.db import migrations

VIEJO = "Concesionarios"
NUEVO = "concesionarios"
RESPONSABLES = "responsables"


def aplicar(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    viejo = Group.objects.filter(name=VIEJO).first()
    nuevo = Group.objects.filter(name=NUEVO).first()
    if viejo and not nuevo:
        viejo.name = NUEVO
        viejo.save(update_fields=["name"])
    elif viejo and nuevo:
        # Los dos existen: se mudan los usuarios al que rige y se borra el otro.
        for user in viejo.user_set.all():
            user.groups.add(nuevo)
        viejo.delete()
    else:
        Group.objects.get_or_create(name=NUEVO)
    Group.objects.get_or_create(name=RESPONSABLES)


def revertir(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    grupo = Group.objects.filter(name=NUEVO).first()
    if grupo:
        grupo.name = VIEJO
        grupo.save(update_fields=["name"])
    # ``responsables`` sólo se borra si nadie quedó adentro.
    responsables = Group.objects.filter(name=RESPONSABLES).first()
    if responsables and not responsables.user_set.exists():
        responsables.delete()


class Migration(migrations.Migration):

    dependencies = [("concesionarios", "0002_datos_iniciales")]

    operations = [migrations.RunPython(aplicar, revertir)]
