"""Backfill de miniaturas para fotos ya cargadas en el espejo.

Genera la miniatura (`XsysSocioFoto.thumbnail`) de las fotos existentes sin tener
que re-sincronizar. Por defecto solo procesa las que aún no tienen miniatura.

Ejemplos:
    python manage.py xsys_thumbnails
    python manage.py xsys_thumbnails --all        # regenera todas
    python manage.py xsys_thumbnails --limit 500
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from xsys.models import XsysSocioFoto
from xsys.services.images import make_thumbnail, pillow_disponible


class Command(BaseCommand):
    help = "Genera miniaturas para las fotos existentes del espejo xSys."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Regenera la miniatura aunque ya exista (por defecto solo las faltantes).",
        )
        parser.add_argument("--limit", type=int, default=None, help="Máximo de fotos a procesar.")
        parser.add_argument("--batch-size", type=int, default=200, help="Tamaño de lote de lectura.")

    def handle(self, *args, **options):
        if not pillow_disponible():
            raise CommandError("Pillow no está instalado; no se pueden generar miniaturas.")

        qs = XsysSocioFoto.objects.exclude(imagen__isnull=True)
        if not options["all"]:
            qs = qs.filter(thumbnail__isnull=True)
        qs = qs.only("id", "imagen").order_by("id")
        if options["limit"]:
            qs = qs[: options["limit"]]

        generadas = 0
        omitidas = 0
        total = 0
        for foto in qs.iterator(chunk_size=options["batch_size"]):
            total += 1
            data = bytes(foto.imagen) if foto.imagen else None
            thumb = make_thumbnail(data) if data else None
            if thumb is None:
                omitidas += 1
                continue
            foto.thumbnail = thumb
            foto.save(update_fields=["thumbnail"])
            generadas += 1
            if generadas % 500 == 0:
                self.stdout.write(f"  ...{generadas} miniaturas generadas")

        self.stdout.write(
            self.style.SUCCESS(
                f"Listo. Procesadas: {total} · generadas: {generadas} · "
                f"omitidas (sin imagen/corruptas): {omitidas}"
            )
        )
