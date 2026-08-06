"""Sincroniza en BioStar el ESTADO de acceso de los socios enrolados: deshabilita
a los que no pueden ingresar (manteniendo el rostro) y rehabilita a los que sí.

Complemento de ``biostar_enroll_faces`` (que solo enrola). No borra caras: usa
``expiry_datetime`` vencido (o el flag ``disabled``) para que el equipo reconozca
al socio pero le deniegue el paso.

Uso:
    # PRUEBA CONTROLADA sobre un socio (recomendado ANTES de correr masivo):
    python manage.py biostar_sync_access_state --test 855315 --method expiry
    #   muestra estado antes/después, deshabilita, y verifica que el rostro se
    #   preserva. Luego pasás la cara por el equipo y mirás el evento en el visor.
    python manage.py biostar_sync_access_state --test 855315 --restore   # rehabilita

    # BACKFILL masivo (todos los enrolados):
    python manage.py biostar_sync_access_state --mode dryrun     # lista, sin tocar
    python manage.py biostar_sync_access_state --mode on         # aplica
    python manage.py biostar_sync_access_state --mode on --no-reenable  # solo morosos
"""

from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Deshabilita/rehabilita en BioStar a los socios enrolados según su habilitación (sin borrar el rostro)."

    def add_arguments(self, parser):
        parser.add_argument("--mode", choices=["off", "dryrun", "on"], default="dryrun",
                            help="dryrun (default): solo lista. on: aplica. off: nada.")
        parser.add_argument("--method", choices=["expiry", "disabled"], default="expiry",
                            help="expiry (default): vence el acceso (reconoce y deniega). "
                                 "disabled: usa el flag disabled del usuario.")
        parser.add_argument("--no-reenable", action="store_true",
                            help="Solo deshabilitar morosos; no rehabilitar a los habilitados.")
        parser.add_argument("--limit", type=int, default=2000,
                            help="Máx. a procesar por lado (default 2000).")
        parser.add_argument("--test", type=int, default=None, metavar="ID_CLIENTE",
                            help="Prueba controlada sobre UN socio: muestra estado, lo deshabilita "
                                 "y verifica que el rostro se preserva.")
        parser.add_argument("--restore", action="store_true",
                            help="Con --test: rehabilita al socio en vez de deshabilitarlo.")

    def handle(self, *args, **opts):
        if opts["test"] is not None:
            return self._test_one(opts["test"], opts["method"], restore=opts["restore"])

        from access_control.services.biostar_access_state import push_access_state_affected

        res = push_access_state_affected(
            None,  # None = todos los enrolados (backfill)
            mode=opts["mode"],
            method=opts["method"],
            reenable=not opts["no_reenable"],
            max_per_run=opts["limit"],
        )
        self.stdout.write(self.style.SUCCESS(str(res)))

    # ---- prueba controlada sobre un socio ----
    def _test_one(self, id_cliente: int, method: str, *, restore: bool):
        from access_control.services.biostar2_client import BioStar2Client

        client = BioStar2Client.from_db_and_env()

        def snapshot(tag):
            u = client.get_user(id_cliente)
            if not u:
                self.stdout.write(f"[{tag}] usuario {id_cliente} NO existe en BioStar")
                return None
            usr = u.get("User", u)
            caras = 0
            cred = usr.get("credentials") or {}
            vf = cred.get("visualFaces") or usr.get("visualFaces") or []
            caras = len(vf) if isinstance(vf, list) else 0
            self.stdout.write(
                f"[{tag}] name='{usr.get('name')}' disabled={usr.get('disabled')} "
                f"expiry={usr.get('expiry_datetime')} rostros(visualFaces)={caras}"
            )
            return usr

        self.stdout.write(self.style.WARNING(
            f"== PRUEBA sobre socio {id_cliente} — método {method} — "
            f"{'REHABILITAR' if restore else 'DESHABILITAR'} =="))
        antes = snapshot("ANTES")
        if antes is None:
            return

        resp = client.set_user_access_state(id_cliente, enabled=restore, method=method)
        code = None
        try:
            code = str(resp.json().get("Response", {}).get("code"))
        except Exception:
            pass
        self.stdout.write(f"PUT -> HTTP {getattr(resp,'status_code','?')} code {code}")

        despues = snapshot("DESPUES")
        if despues is not None:
            caras_antes = antes and len((antes.get('credentials') or {}).get('visualFaces') or [])
            caras_desp = len((despues.get('credentials') or {}).get('visualFaces') or [])
            if caras_desp and caras_desp == caras_antes:
                self.stdout.write(self.style.SUCCESS("OK: el rostro se preservó tras el cambio de estado."))
            else:
                self.stdout.write(self.style.ERROR(
                    "ATENCIÓN: el nº de rostros cambió — revisar antes de usar en masa."))
        self.stdout.write(
            "Ahora pasá la cara por un equipo y mirá el evento en el visor: "
            "debe aparecer RECONOCIDO y DENEGADO. Para revertir: "
            f"--test {id_cliente} --restore")
