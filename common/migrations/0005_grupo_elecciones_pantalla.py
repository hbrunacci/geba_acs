"""Crea el grupo de la vista de Elecciones para el televisor (idempotente).

    elecciones_pantalla   resumen, horarios y avance por mesa en una sola hoja
"""

from __future__ import annotations

from django.db import migrations

GRUPO = "elecciones_pantalla"


def crear(apps, schema_editor):
    apps.get_model("auth", "Group").objects.get_or_create(name=GRUPO)


def borrar(apps, schema_editor):
    apps.get_model("auth", "Group").objects.filter(name=GRUPO).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0004_grupos_elecciones"),
    ]

    operations = [
        migrations.RunPython(crear, borrar),
    ]
