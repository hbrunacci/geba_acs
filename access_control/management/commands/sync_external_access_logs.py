"""Comando para ejecutar la sincronización asíncrona de registros externos."""

from __future__ import annotations

import asyncio
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from access_control.services import (
    ExternalAccessLogError,
    ExternalAccessLogService,
    ExternalAccessLogSynchronizer,
)


class Command(BaseCommand):
    help = (
        "Sincroniza periódicamente los movimientos del sistema externo en la base local."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--interval",
            type=float,
            default=30.0,
            help="Intervalo en segundos entre sincronizaciones (por defecto: 30).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Cantidad máxima de registros a recuperar en cada iteración.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            service = ExternalAccessLogService()
        except ExternalAccessLogError as exc:  # pragma: no cover - validación previa
            raise CommandError(str(exc)) from exc

        synchronizer = ExternalAccessLogSynchronizer(
            service,
            limit=options.get("limit"),
            poll_interval=options.get("interval", 30.0),
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Iniciando sincronización de movimientos externos (Ctrl+C para detener)..."
            )
        )

        try:
            asyncio.run(synchronizer.run_forever())
        except KeyboardInterrupt:  # pragma: no cover - interacción manual
            self.stdout.write(self.style.WARNING("Sincronización interrumpida por el usuario."))
        except ExternalAccessLogError as exc:  # pragma: no cover - propagación controlada
            raise CommandError(str(exc)) from exc
