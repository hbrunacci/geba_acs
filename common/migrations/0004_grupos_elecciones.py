"""Crea los grupos de la pantalla de Elecciones, uno por sector (idempotente).

    elecciones_participacion   el resumen general y la velocidad de votación
    elecciones_mesas           el avance por mesa
    elecciones_horarios        los votos por hora y el acumulado
    elecciones_actividades     qué actividades tienen los que ya votaron
"""

from __future__ import annotations

from django.db import migrations

GRUPOS = (
    "elecciones_participacion",
    "elecciones_mesas",
    "elecciones_horarios",
    "elecciones_actividades",
)


def crear(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for nombre in GRUPOS:
        Group.objects.get_or_create(name=nombre)


def borrar(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=GRUPOS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0003_grupo_tableros"),
    ]

    operations = [
        migrations.RunPython(crear, borrar),
    ]
