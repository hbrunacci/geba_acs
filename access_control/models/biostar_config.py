from __future__ import annotations

from django.db import models
from django.utils import timezone


class BioStar2Config(models.Model):
    """
    Guarda configuración y estado de sesión de BioStar 2 (bs-session-id).
    La idea es evitar logins por cada request.
    """

    base_url = models.URLField()
    username = models.CharField(max_length=150)

    # Para no guardar passwords en DB (recomendado), lo dejamos fuera del modelo.
    # El password lo leemos de env.

    bs_session_id = models.CharField(max_length=512, blank=True, default="")
    session_obtained_at = models.DateTimeField(null=True, blank=True)

    verify_tls = models.BooleanField(default=False)
    timeout_seconds = models.PositiveIntegerField(default=15)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "BioStar 2 Config"
        verbose_name_plural = "BioStar 2 Config"

    def set_session(self, session_id: str) -> None:
        self.bs_session_id = session_id
        self.session_obtained_at = timezone.now()
        self.save(update_fields=["bs_session_id", "session_obtained_at", "updated_at"])

    @classmethod
    def get_solo(cls) -> "BioStar2Config":
        obj, _ = cls.objects.get_or_create(
            id=1,
            defaults={
                "base_url": "https://10.0.0.27",
                "username": "admin",
                "verify_tls": False,
                "timeout_seconds": 15,
            },
        )
        return obj
