"""Crea el grupo ``tableros`` (idempotente).

Con este grupo se ve la pantalla de Tableros —padrón, altas y bajas, categorías
y deuda— sin necesitar ningún otro rol del sistema.
"""

from __future__ import annotations

from django.db import migrations

GRUPO = "tableros"


def crear(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name=GRUPO)


def borrar(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=GRUPO).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0002_grupo_socios"),
    ]

    operations = [
        migrations.RunPython(crear, borrar),
    ]
