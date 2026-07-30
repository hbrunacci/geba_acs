"""Diagnóstico de acceso por reconocimiento facial de un socio (consola).

Recibe DNI o número de socio y responde: quién es, qué foto tiene, si está en
BioStar con rostro y grupo, si está cargado en cada equipo o fue borrado, qué
errores registró y sus últimos accesos.

La lógica vive en ``access_control.services.diag_facial`` y la comparte con la
pantalla web (``/diag-facial/``). Acá solo se formatea la salida.

Ejemplos::

    python manage.py diag_facial 50258277
    python manage.py diag_facial 50258277 50494262 54457209
    python manage.py diag_facial --socio 901251 --dias 15
    python manage.py diag_facial 54457209 --equipos 538150641,538205516

Es de **solo lectura**: no escribe en xSys ni en BioStar.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from access_control.services.diag_facial import BIOSTAR_PREFIX_DEFAULT
from access_control.services.diag_facial import DiagFacialError
from access_control.services.diag_facial import diagnosticar


class Command(BaseCommand):
    help = "Diagnostica por qué un socio no entra por facial. Recibe DNI o número de socio."

    def add_arguments(self, parser) -> None:
        parser.add_argument("documentos", nargs="*", help="Uno o más DNI.")
        parser.add_argument("--socio", action="append", default=[], metavar="ID_CLIENTE",
                            help="Buscar por número de socio (Id_Cliente). Repetible.")
        parser.add_argument("--dias", type=int, default=10,
                            help="Días de historial de eventos y accesos (default 10).")
        parser.add_argument("--equipos", default="",
                            help="IDs de equipos BioStar separados por coma. Vacío = todos.")
        parser.add_argument("--biostar-prefix", default=BIOSTAR_PREFIX_DEFAULT,
                            help=f"Prefijo del linked server BioStar (default {BIOSTAR_PREFIX_DEFAULT}).")

    # ---------------------------------------------------------------- salida --

    def _tit(self, texto: str) -> None:
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(texto))

    def _ok(self, texto: str) -> None:
        self.stdout.write(self.style.SUCCESS("  OK   " + texto))

    def _warn(self, texto: str) -> None:
        self.stdout.write(self.style.WARNING("  OJO  " + texto))

    def _err(self, texto: str) -> None:
        self.stdout.write(self.style.ERROR("  MAL  " + texto))

    def _info(self, texto: str) -> None:
        self.stdout.write("       " + texto)

    # ---------------------------------------------------------------- handle --

    def handle(self, *args: Any, **opts: Any) -> None:
        dnis = [d.strip() for d in opts["documentos"] if str(d).strip()]
        socios = [s.strip() for s in opts["socio"] if str(s).strip()]
        if not dnis and not socios:
            raise CommandError("Indicá al menos un DNI o un --socio ID_CLIENTE.")
        for valor in dnis + socios:
            if not valor.isdigit():
                raise CommandError(f"'{valor}' no es numérico.")

        equipos = [int(e) for e in opts["equipos"].split(",") if e.strip().isdigit()]

        try:
            datos = diagnosticar(
                dnis=[int(d) for d in dnis],
                ids_cliente=[int(s) for s in socios],
                dias=int(opts["dias"]),
                equipos=equipos,
                prefix=opts["biostar_prefix"],
            )
        except DiagFacialError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f"Conectado a xSys por {datos['driver']}.")
        for aviso in datos["avisos"]:
            self._warn(aviso)
        if not datos["reportes"]:
            self._err("Ningún socio para reportar.")
            return
        for reporte in datos["reportes"]:
            self._imprimir(reporte)

    # --------------------------------------------------------------- reporte --

    def _imprimir(self, r: dict) -> None:
        s = r["socio"]
        dias = r["dias"]

        self.stdout.write("")
        self.stdout.write("=" * 78)
        self.stdout.write(f" {s['nombre_completo']}   ·   Socio {s['id_cliente']}   ·   DNI {s['dni']}")
        self.stdout.write("=" * 78)

        self._tit("1) Socio en xSys")
        self._info(f"Categoría        : {s['categoria_id']} ({s['categoria']})")
        self._info(f"Titular (Ref)    : {s['titular_ref'] or '— es titular —'}")
        self._info(f"Credencial       : {s['credencial'] or '(sin credencial)'}")
        if s["activo"]:
            self._ok("Socio ACTIVO")
        else:
            self._err("Socio INACTIVO en xSys: ninguna puerta lo va a habilitar.")
        if s["ult_cuota_paga"]:
            self._info(f"Última cuota paga: {s['ult_cuota_paga']}")
            if s["cuota_al_dia"]:
                self._ok("Cuota al día")
            else:
                self._warn("Cuota anterior al mes en curso: puede ser rechazado donde se exige UCP.")
        else:
            self._warn("Sin última cuota paga registrada.")

        self._tit("2) Foto en xSys (la que se empuja al facial)")
        foto = r["foto"]
        if not foto:
            self._err("SIN FOTO en xSys: no hay nada que enrolar en el facial.")
        else:
            self._info(f"Última foto: {foto['fecha']}  ·  {foto['bytes']:,} bytes  ·  {foto['anios']} años")
            if foto["cantidad"] > 1:
                self._info(f"(tiene {foto['cantidad']} fotos cargadas)")
            if foto["problemas"]:
                self._warn("Foto sospechosa (" + " y ".join(foto["problemas"]) + "). "
                           "Es la causa típica de ENROLL_FAIL / UPDATE_FAIL (VISUAL FACE).")
            else:
                self._ok("Foto reciente y con tamaño razonable")

        self._tit("3) Usuario en el server BioStar")
        b = r["biostar"]
        if not b["existe"]:
            self._err("NO EXISTE en BioStar: nunca se enroló (o fue borrado del server).")
        else:
            self._info(f"USRUID={b['usruid']}  nombre='{b['nombre']}'")
            if b["borrado"]:
                self._err("Marcado como BORRADO (DEL='Y') en BioStar.")
            if b["rostros"]:
                self._ok(f"Tiene {b['rostros']} rostro(s) cargado(s) en el server")
            else:
                self._err("SIN ROSTRO en el server: el equipo no puede reconocerlo.")
            if b["en_grupo_socios"]:
                self._ok(f"En el grupo de acceso Cuota Social. Grupos: {b['grupos']}")
            else:
                self._err(f"FUERA del grupo de acceso Cuota Social. Grupos: {b['grupos'] or 'ninguno'} "
                          "→ el equipo lo deniega por regla de acceso.")
        if r["lista_blanca"]:
            self._ok("Está en la lista blanca Suprema")
        else:
            self._warn("NO está en la lista blanca Suprema: xSys no lo empuja a los faciales.")

        self._tit("4) ¿Está cargado en cada equipo?")
        if not r["equipos"]:
            self._err(f"Sin eventos de carga/borrado en los últimos {dias} días: "
                      "no hay constancia de que esté cargado.")
        for eq in r["equipos"]:
            cuando = str(eq["fecha"])[:19]
            if eq["cargado"]:
                self._ok(f"{eq['equipo']:<32} cargado (último: {eq['evento']} el {cuando})")
            else:
                self._err(f"{eq['equipo']:<32} BORRADO el {cuando} ({eq['dias_fuera']} días afuera) "
                          "→ el equipo no lo tiene, por eso 'no le toma'.")

        self._tit(f"5) Eventos en BioStar (últimos {dias} días)")
        eventos = r["eventos"]
        fallos = [e for e in eventos if e["es_fallo"]]
        if not eventos:
            self._info("(sin eventos en el período)")
        else:
            if fallos:
                self._err(f"{len(fallos)} evento(s) de FALLO propios:")
                for e in fallos[:10]:
                    self._info(f"  {str(e['fecha'])[:19]}  {e['equipo']}  {e['evento']}")
            else:
                self._ok("Ningún evento de fallo atribuido a este socio")
                self._info("Nota: un rechazo de reconocimiento se registra como AUTH_FAILED_TIMEOUT "
                           "SIN usuario, así que no se puede atribuir a un socio. La ausencia de "
                           "fallos propios no significa que haya podido entrar.")
            self._info("")
            self._info("Timeline (más reciente primero):")
            for e in eventos[:20]:
                self._info(f"  {str(e['fecha'])[:19]}  {e['equipo'][:30]:<30} {e['evento']}")
            if len(eventos) > 20:
                self._info(f"  … y {len(eventos) - 20} eventos más")

        self._tit(f"6) Accesos registrados en xSys (últimos {dias} días)")
        accesos = r["accesos"]
        if not accesos:
            self._info("(ningún registro: no pasó por ningún lector, o no llegó a generar evento)")
        for a in accesos[:15]:
            marca = "RECHAZO" if a["es_rechazo"] else "ok     "
            self._info(f"  {str(a['fecha'])[:19]} {marca} acc={a['id_acceso']} "
                       f"{a['acceso'][:18]:<18} motivo={a['motivo']} {a['observacion'][:40]}")
        if len(accesos) > 15:
            self._info(f"  … y {len(accesos) - 15} registros más")

        self._tit("7) Novedades de sincronización")
        if not r["novedades"]:
            self._info("(sin novedades)")
        for n in r["novedades"]:
            linea = f"{str(n['fecha'])[:19]} Estado={n['estado']} Tipo={n['tipo']} {n['nota'][:52]}"
            self._warn(linea) if n["es_error"] else self._info("  " + linea)

        self._tit("DIAGNÓSTICO")
        if not r["alertas"]:
            self._ok("No se detectaron problemas de datos ni de carga. Si igual no le toma, "
                     "es reconocimiento (foto vs. cara real, luz, altura) o el equipo puntual.")
        else:
            for a in r["alertas"]:
                self._err(a)
            if r["sugerencias"]:
                self._info("")
                self._info("Sugerencias:")
                for sug in r["sugerencias"]:
                    self._info(f"  · {sug}")
