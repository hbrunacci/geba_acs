"""Maneja un molinete Intelektron en modo ONLINE: la placa pregunta, geba_acs decide.

Mantiene abierta la sesión con la placa (itk_open con callbacks, keepalive y
reconexión automática) y, por cada lectura que la placa manda a validar,
decide habilitado/denegado y contesta. El equipo acciona su relé de
"Aceptado" del lector que leyó.

Quién decide (``--validador``):
  * ``local`` (default): la réplica Python de la cascada de Clever
    (``services.validador_local``). Lee las tablas de xSys, NO escribe nada en
    Clever. El paso queda registrado en NUESTRA base (``PasoMolinete``).
  * ``clever``: el SP ``CP_SCA_RegistrarAcceso`` de Clever (requiere EXECUTE);
    en modo real Clever graba el paso en ``CD_ES``.

Qué se hace con la decisión (``--modo``):
  * ``observar`` (default): se evalúa y registra, pero se contesta siempre
    DENEGADO: el molinete no abre. Sirve para ver que las lecturas llegan, a
    qué socio resuelve cada tag y qué se decidiría.
  * ``real``: la respuesta es la que recibe la placa; con habilitado abre.
  * ``libre``: ABRE CON CUALQUIER LECTURA, sin importar la validación (que
    igual se evalúa y registra). Sólo para probar el relé / el aspa; no
    dejarlo así.

La placa manda cada lectura como "No identificado" (106) porque no tiene
lista local de usuarios; también se atiende "Por autorizar" (2). Las lecturas
repetidas (mismo tag en pocos segundos) reciben la misma respuesta que la
primera sin volver a evaluar (antirrebote, como el SP de Clever).

Antes de usarlo con una placa nueva:
  1. La placa tiene que existir en CD_Controladores (Tipo_Cont 'K', Activo 0
     para que la app de Clever no la tome) con el Id_Acceso de su puerta: de
     ahí salen las reglas (categorías, contratos, productos, cuota).
  2. En la web de la placa: Comunicación -> Modo Envío = Online (u Online/
     Offline), y en Acciones -> lector -> Comunicación: Aceptado + No identificado.
  Ver scripts/xsys_controlador_intelektron_2026-10-28.sql.

Ejemplos:
  python manage.py intelektron_online --ip 10.0.0.117 --id-controlador 47
  python manage.py intelektron_online --ip 10.0.0.117 --id-controlador 47 --modo real
  python manage.py intelektron_online --ip 10.0.0.117 --id-controlador 47 --validador clever --modo real
"""

from __future__ import annotations

import hashlib
import queue
import signal
import threading
import time
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from access_control.services.intelectron.api3000_wrapper.api3000.online import (
    EV_ACEPTADO,
    EV_NO_IDENTIFICADO,
    EV_POR_AUTORIZAR,
    EventoUsuario,
    OnlineSession,
    build_answer,
    tag_for_event,
    tipo_acceso_from_event,
)
from access_control.services.intelektron_decision import Decision, DecisorClever
from access_control.services.validador_local import DecisionLocal, ValidadorLocal, ahora_local

# Eventos con los que la placa pide una decisión al host. Una placa sin lista
# local de usuarios (como las de Clever) manda cada lectura como
# "No identificado" (106) y espera la respuesta igual que para "Por autorizar"
# (2). Visto en vivo en 10.0.0.117 el 28/10/2026.
EVENTOS_A_DECIDIR = frozenset({EV_POR_AUTORIZAR, EV_NO_IDENTIFICADO})
ANTIREBOTE_SEG_DEFAULT = 3


class Command(BaseCommand):
    help = "Atiende en línea un molinete Intelektron: recibe cada lectura, decide y contesta."

    def add_arguments(self, parser):
        parser.add_argument("--ip", required=True, help="IP de la placa.")
        parser.add_argument("--port", type=int, default=3001, help="Puerto Host de la placa (default 3001).")
        parser.add_argument("--source-node", type=int, default=0, help="Nodo origen (default 0).")
        parser.add_argument("--dest-node", type=int, default=1, help="Nodo destino (default 1).")
        parser.add_argument("--id-controlador", type=int, required=True,
                            help="Id_Controlador de CD_Controladores asignado a esta placa.")
        parser.add_argument("--id-acceso", type=int, default=None,
                            help="Id_Acceso de la puerta. Default: el del controlador en CD_Controladores.")
        parser.add_argument("--validador", choices=("local", "clever"), default="local",
                            help="local: réplica en geba_acs, registra acá (default). clever: SP de Clever.")
        parser.add_argument("--modo", choices=("observar", "real", "libre"), default="observar",
                            help="observar: evalúa y deniega siempre (default). real: acciona el molinete. "
                                 "libre: abre con cualquier lectura sin validar (prueba de relé).")
        parser.add_argument("--grabar", action="store_true", help="Alias de --modo real.")
        parser.add_argument("--lectores-salida", default="",
                            help="source_id de los lectores de SALIDA separados por coma (default: ninguno).")
        parser.add_argument("--lectores-hex", default="1",
                            help="source_id de los lectores de credencial Mifare (tag hex), separados por coma. "
                                 "Los demás (QR/DNI) mandan el número en decimal. Default: 1.")
        parser.add_argument("--antirebote", type=int, default=None,
                            help="Segundos del antirrebote de lectura (default: xParametros SCA_Antirebote_Seg o 3).")
        parser.add_argument("--timeout", type=int, default=8000, help="Timeout de itk_open en ms.")
        parser.add_argument("--heartbeat", type=int, default=60, help="Segundos entre líneas de estado.")
        parser.add_argument("--duracion", type=int, default=0, help="Segundos a correr (0 = hasta que lo paren).")
        parser.add_argument("--log-nativo", default="/tmp/itk_online.log", help="Log de la librería nativa.")
        parser.add_argument("--keepalive", type=int, default=30,
                            help="Segundos entre keepalives (EcoTest) hacia la placa; 0 desactiva. Default 30.")
        parser.add_argument("--rele", type=int, default=2,
                            help="Relé a pulsar con itk_rele_control en cada apertura (0 = sólo la respuesta "
                                 "habilitada, confiando en la config 'Aceptado' de la placa). Default 2.")
        parser.add_argument("--rele-tiempo", type=int, default=3,
                            help="Segundos del pulso de --rele (0 = pulso corto). Default 3.")

    # ------------------------------------------------------------------ setup

    def handle(self, *args, **opts):
        ip, port = opts["ip"], opts["port"]
        self.id_controlador = opts["id_controlador"]
        self.id_acceso = opts["id_acceso"]
        self.validador = opts["validador"]
        self.modo = "real" if opts["grabar"] else opts["modo"]
        self.real = self.modo == "real"
        self.libre = self.modo == "libre"   # abre con cualquier lectura, sin validar
        self.rele, self.rele_tiempo = int(opts["rele"]), int(opts["rele_tiempo"])
        self.sesion = None
        self.lectores_salida = {int(x) for x in opts["lectores_salida"].split(",") if x.strip()}
        self.lectores_hex = {int(x) for x in opts["lectores_hex"].split(",") if x.strip()}
        self.cola: "queue.Queue[tuple]" = queue.Queue()
        self._parar = threading.Event()
        self._ultimas: dict[tuple[str, str], tuple[datetime, Decision]] = {}

        if self.validador == "local":
            self.local = ValidadorLocal()
            self.decisor = None
            if self.id_acceso is None:
                self.id_acceso = self.local.id_acceso_de(self.id_controlador) or None
            self.antirebote_seg = opts["antirebote"] if opts["antirebote"] is not None else \
                self.local._xparam_int("SCA_Antirebote_Seg", ANTIREBOTE_SEG_DEFAULT)
        else:
            self.local = None
            self.decisor = DecisorClever()
            # Con el SP, el antirrebote lo hace Clever cuando graba; acá sólo en observar.
            self.antirebote_seg = opts["antirebote"] if opts["antirebote"] is not None else \
                (0 if self.real else ANTIREBOTE_SEG_DEFAULT)

        self.stdout.write(self.style.SUCCESS(
            f"Molinete {ip}:{port} nodo {opts['dest_node']} · controlador {self.id_controlador} "
            f"acceso {self.id_acceso or '?'} · validador {self.validador} · modo {self.modo.upper()}"
            f"{' (no acciona)' if self.modo == 'observar' else ''}"
            f"{' (ABRE CON CUALQUIER LECTURA, SIN VALIDAR)' if self.libre else ''} · antirrebote {self.antirebote_seg} s"))
        if self.validador == "local" and not self.id_acceso:
            self.stderr.write(self.style.ERROR(
                "El controlador no tiene Id_Acceso en CD_Controladores y no se indicó --id-acceso."))
            return

        self.sesion = sesion = OnlineSession(
            ip=ip, port=port, dest_node=opts["dest_node"], source_node=opts["source_node"],
            timeout_ms=opts["timeout"], on_user=self._on_user, on_aux=self._on_aux,
            on_conn=self._on_conn, log_path=opts["log_nativo"],
            keepalive=(opts["keepalive"], 3) if opts["keepalive"] > 0 else None,
        )
        try:
            sesion.abrir()
        except Exception as exc:  # noqa: BLE001
            self.stderr.write(self.style.ERROR(f"No se pudo abrir la sesión con {ip}:{port}: {exc}"))
            sesion.uninit()
            return
        self.stdout.write(self.style.SUCCESS("Sesión abierta. Esperando lecturas (la placa tiene que estar en modo Online)."))

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, lambda *_: self._parar.set())
            except Exception:  # noqa: BLE001 - fuera del hilo principal
                pass

        inicio = time.time()
        ultimo_hb = inicio
        hilo = threading.Thread(target=sesion.mantener, kwargs={
            "parar": self._parar.is_set, "on_reconexion": self._on_reconexion}, daemon=True)
        hilo.start()
        try:
            while not self._parar.is_set():
                self._drenar_cola()
                if opts["duracion"] and time.time() - inicio >= opts["duracion"]:
                    break
                if time.time() - ultimo_hb >= opts["heartbeat"]:
                    ultimo_hb = time.time()
                    self.stdout.write(
                        f"{datetime.now():%H:%M:%S} · viva={sesion.viva} eventos={sesion.eventos} "
                        f"respuestas={sesion.respuestas} errores_rta={sesion.errores_respuesta} "
                        f"reconexiones={sesion.reconexiones} caidas_socket={sesion.caidas_socket} "
                        f"socket={sesion.hay_socket()}")
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        finally:
            self._parar.set()
            self._drenar_cola()
            sesion.uninit()
            if self.decisor:
                self.decisor.cerrar()
            if self.local:
                self.local.cerrar()
            self.stdout.write(self.style.WARNING(
                f"Cerrado. eventos={sesion.eventos} respuestas={sesion.respuestas} reconexiones={sesion.reconexiones}"))

    # ------------------------------------------------------------------ decisión

    def _decidir(self, tag: str, tipo: str, cuando: datetime) -> tuple[Decision, bool]:
        """Decide la lectura; si es una repetición reciente, repite la respuesta anterior."""
        clave = (tag, tipo)
        previa = self._ultimas.get(clave)
        if previa and self.antirebote_seg > 0 and cuando - previa[0] <= timedelta(seconds=self.antirebote_seg):
            return previa[1], True
        if self.local is not None:
            decision: Decision = self.local.decidir(
                tipo_acceso=tipo, tag=tag, id_controlador=self.id_controlador, id_acceso=self.id_acceso,
                cuando=cuando)
        else:
            decision = self.decisor.decidir(id_controlador=self.id_controlador, tipo_acceso=tipo, tag=tag,
                                            cuando=cuando, graba=self.real)
        if not decision.error:
            self._ultimas[clave] = (cuando, decision)
            if len(self._ultimas) > 500:
                corte = cuando - timedelta(seconds=max(self.antirebote_seg, 1) * 10)
                self._ultimas = {k: v for k, v in self._ultimas.items() if v[0] >= corte}
        return decision, False

    # --------------------------------------------------------- callbacks nativos

    def _on_user(self, ev: EventoUsuario):
        """Corre en el hilo de la librería con la placa esperando: decidir y volver."""
        cuando = ahora_local()
        if ev.event_code in EVENTOS_A_DECIDIR:
            tipo = tipo_acceso_from_event(ev, self.lectores_salida)
            tag = tag_for_event(ev, self.lectores_hex)
            decision, antirebote = self._decidir(tag, tipo, cuando)
            if self.libre:
                accionar, mensaje = True, f"LIBRE {decision.mensaje}"
            elif self.real:
                accionar, mensaje = bool(decision.habilitado), decision.mensaje
            else:
                accionar, mensaje = False, f"PRUEBA {decision.mensaje}"
            self.cola.put((ev, decision, tipo, tag, cuando, accionar, antirebote))
            return build_answer(access_id=ev.access_id, habilitado=accionar,
                                source_id=ev.source_id, mensaje=mensaje, cuando=cuando)
        # Notificaciones (aceptado offline, etc.): se acusan sin accionar nada.
        self.cola.put((ev, None, "", tag_for_event(ev, self.lectores_hex), cuando, False, False))
        return build_answer(access_id=ev.access_id, habilitado=(ev.event_code == EV_ACEPTADO),
                            source_id=ev.source_id, mensaje="", cuando=cuando, access_ctl=0)

    def _on_aux(self, input_id: int, event_code: int) -> None:
        self.stdout.write(f"{datetime.now():%H:%M:%S} · sensor {input_id} código {event_code}")

    def _on_conn(self, cause: int) -> None:
        if cause != 2:
            self.stdout.write(f"{datetime.now():%H:%M:%S} · conexión: cause={cause}")

    def _on_reconexion(self, n: int) -> None:
        self.stdout.write(f"{datetime.now():%H:%M:%S} · reconectado a la placa (#{n})")

    # ------------------------------------------------------------- persistencia

    def _drenar_cola(self) -> None:
        from access_control.models import IntelektronEvent, PasoMolinete

        while True:
            try:
                ev, decision, tipo, tag, cuando, accionar, antirebote = self.cola.get_nowait()
            except queue.Empty:
                return
            linea = f"{cuando:%H:%M:%S} · {ev.event_name} lector={ev.source_id} tag={tag} sentido={ev.direction}"
            if decision is not None:
                linea += f" -> {decision.resumen}"
                if isinstance(decision, DecisionLocal):
                    linea += f" motivo={decision.id_motivo}"
                linea += f" [{decision.duracion_ms:.0f} ms]"
                if antirebote:
                    linea += " (repetida)"
                if accionar:
                    linea += " ABRE"
                    if self.rele > 0 and self.sesion is not None:
                        ok = self.sesion.accionar_rele(self.rele, self.rele_tiempo, node=ev.node)
                        linea += f" rele {self.rele}/{self.rele_tiempo}s {'OK' if ok else 'FALLO'}"
            self.stdout.write(linea)
            try:
                fecha = timezone.make_aware(cuando, timezone.get_current_timezone())
                if decision is not None:
                    PasoMolinete.objects.create(
                        fecha=fecha, id_controlador=self.id_controlador, id_acceso=self.id_acceso,
                        tipo=tipo or "E", tag=tag, lector=ev.source_id, evento_codigo=ev.event_code,
                        id_cliente=decision.id_cliente, id_cliente_ref=decision.id_cliente_ref,
                        habilitado=bool(decision.habilitado), accionado=accionar,
                        id_motivo=getattr(decision, "id_motivo", None),
                        mensaje=(decision.mensaje or "")[:300], detalle=(getattr(decision, "detalle", "") or "")[:200],
                        info=(decision.info or "")[:10], validador=self.validador, modo=self.modo,
                        antirebote=antirebote, duracion_ms=round(decision.duracion_ms, 1),
                        error=(decision.error or "")[:300],
                        raw={"access_id": ev.access_id, "direction": ev.direction, "node": ev.node,
                             "traza": getattr(decision, "traza", [])},
                    )
                key = hashlib.sha1(
                    f"online|{cuando.isoformat()}|{ev.access_id}|{ev.event_code}|{ev.source_id}".encode()
                ).hexdigest()
                IntelektronEvent.objects.get_or_create(
                    dedupe_key=key,
                    defaults={
                        "device_ip": "", "dest_node": ev.node, "id_controlador": self.id_controlador,
                        "access_id": ev.access_id, "event_code": ev.event_code, "event_name": ev.event_name,
                        "direction": ev.direction, "source": ev.source_id,
                        "device_time": _aware(ev.device_time),
                        "raw": {"origen": "online", "tag": tag, "decidido": decision is not None,
                                "habilitado": bool(decision and decision.habilitado), "accionado": accionar},
                    },
                )
            except Exception as exc:  # noqa: BLE001 - no frenar el molinete por la base local
                self.stderr.write(self.style.WARNING(f"no se pudo guardar el paso: {exc}"))
                close_old_connections()


def _aware(dt):
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt
