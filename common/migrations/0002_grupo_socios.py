"""Crea el grupo ``socios`` (idempotente).

Es la oficina de Socios: con este grupo ve la pantalla de avisos a socios sin
necesitar el rol de puertas ni ser administrador.
"""

from __future__ import annotations

from django.db import migrations

GRUPO = "socios"


def crear(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name=GRUPO)


def borrar(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=GRUPO).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0001_roles_groups"),
    ]

    operations = [
        migrations.RunPython(crear, borrar),
    ]
