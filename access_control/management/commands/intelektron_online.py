"""Atiende en línea uno o varios molinetes Intelektron: la placa pregunta, geba_acs decide.

Un solo proceso mantiene una sesión por placa (itk_open con callbacks,
keepalive, watchdog y reconexión automática) y, por cada lectura que una placa
manda a validar, decide habilitado/denegado, contesta y, si corresponde, manda
el pulso de relé que libera el aspa. Todas las placas comparten el mismo
validador y la misma conexión a xSys.

Placas (``--placa``, repetible, o ``--placas`` con una lista separada por ';'):
    ip[:puerto]/id_controlador[/id_acceso[/rele[/segundos_rele]]]
    p.ej.  10.0.0.117:3001/47/14/2/3
  Lo que falta toma el default general (--port, --rele, --rele-tiempo; el
  acceso sale de CD_Controladores). Los argumentos viejos (--ip
  --id-controlador --id-acceso) siguen valiendo y definen una sola placa.

Quién decide (``--validador``):
  * ``local`` (default): la réplica Python de la cascada de Clever
    (``services.validador_local``). Lee las tablas de xSys, NO escribe nada en
    Clever. El paso queda registrado en NUESTRA base (``PasoMolinete``) y en
    el visor de puerta.
  * ``clever``: el SP ``CP_SCA_RegistrarAcceso`` de Clever (requiere EXECUTE);
    en modo real Clever graba el paso en ``CD_ES``.

Qué se hace con la decisión (``--modo``):
  * ``observar`` (default): se evalúa y registra, pero se contesta siempre
    DENEGADO: el molinete no abre.
  * ``real``: la respuesta es la que recibe la placa; con habilitado abre
    (respuesta habilitada + pulso de --rele).
  * ``libre``: ABRE CON CUALQUIER LECTURA, sin validar (igual se evalúa y
    registra). Sólo para probar relé / aspa.

La placa manda cada lectura como "No identificado" (106) porque no tiene
lista local de usuarios; también se atiende "Por autorizar" (2). Las lecturas
repetidas (mismo tag, misma placa, pocos segundos) reciben la misma respuesta
sin volver a evaluar (antirrebote). Los eventos de entradas auxiliares
(sensores) se guardan en ``IntelektronEvent``.

Salud: cada vuelta del bucle principal toca ``--alive`` (default
/tmp/intelektron_online.alive); el healthcheck del contenedor mira su edad.

Ejemplos:
  python manage.py intelektron_online --placa 10.0.0.117/47/14 --modo real
  python manage.py intelektron_online --placas "10.0.0.117/47/14;10.0.0.118/48/14" --modo real
  python manage.py intelektron_online --ip 10.0.0.117 --id-controlador 47        # observar
"""

from __future__ import annotations

import hashlib
import queue
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
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
from access_control.services.paso_visor import espejar_en_visor
from access_control.services.validador_local import DecisionLocal, ValidadorLocal, ahora_local

# Eventos con los que la placa pide una decisión al host. Una placa sin lista
# local de usuarios (como las de Clever) manda cada lectura como
# "No identificado" (106) y espera la respuesta igual que para "Por autorizar"
# (2). Visto en vivo en 10.0.0.117 el 28/10/2026.
EVENTOS_A_DECIDIR = frozenset({EV_POR_AUTORIZAR, EV_NO_IDENTIFICADO})
ANTIREBOTE_SEG_DEFAULT = 3
ALIVE_DEFAULT = "/tmp/intelektron_online.alive"


@dataclass
class Placa:
    ip: str
    port: int = 3001
    id_controlador: int = 0
    id_acceso: int | None = None
    rele: int = 2
    rele_tiempo: int = 3
    dest_node: int = 1
    source_node: int = 0
    sesion: OnlineSession | None = None
    abiertos: int = 0
    denegados: int = 0
    errores: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def nombre(self) -> str:
        return f"{self.ip}:{self.port}/ctrl {self.id_controlador}/acceso {self.id_acceso or '?'}"


def parsear_placa(spec: str, *, port: int = 3001, rele: int = 2, rele_tiempo: int = 3,
                  dest_node: int = 1, source_node: int = 0) -> Placa:
    """``ip[:puerto]/id_controlador[/id_acceso[/rele[/segundos]]]`` -> Placa."""
    partes = [p.strip() for p in spec.strip().split("/")]
    if len(partes) < 2 or not partes[0] or not partes[1]:
        raise ValueError(f"placa inválida '{spec}': se espera ip[:puerto]/id_controlador[/id_acceso[/rele[/seg]]]")
    host, _, puerto = partes[0].partition(":")
    try:
        placa = Placa(
            ip=host, port=int(puerto) if puerto else port, id_controlador=int(partes[1]),
            id_acceso=int(partes[2]) if len(partes) > 2 and partes[2] else None,
            rele=int(partes[3]) if len(partes) > 3 and partes[3] else rele,
            rele_tiempo=int(partes[4]) if len(partes) > 4 and partes[4] else rele_tiempo,
            dest_node=dest_node, source_node=source_node,
        )
    except ValueError as exc:
        raise ValueError(f"placa inválida '{spec}': {exc}") from exc
    return placa


class Command(BaseCommand):
    help = "Atiende en línea uno o varios molinetes Intelektron: recibe cada lectura, decide y contesta."

    def add_arguments(self, parser):
        parser.add_argument("--placa", action="append", default=[],
                            help="ip[:puerto]/id_controlador[/id_acceso[/rele[/seg]]]. Repetible.")
        parser.add_argument("--placas", default="",
                            help="Varias placas separadas por ';' (misma sintaxis que --placa).")
        parser.add_argument("--ip", default=None, help="IP de la placa (forma vieja, una sola placa).")
        parser.add_argument("--port", type=int, default=3001, help="Puerto Host por defecto (3001).")
        parser.add_argument("--source-node", type=int, default=0, help="Nodo origen (default 0).")
        parser.add_argument("--dest-node", type=int, default=1, help="Nodo destino (default 1).")
        parser.add_argument("--id-controlador", type=int, default=None,
                            help="Id_Controlador de CD_Controladores (forma vieja, con --ip).")
        parser.add_argument("--id-acceso", type=int, default=None,
                            help="Id_Acceso de la puerta (forma vieja). Default: el del controlador en CD_Controladores.")
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
        parser.add_argument("--alive", default=ALIVE_DEFAULT,
                            help="Archivo que se toca en cada vuelta del bucle (healthcheck). '' desactiva.")

    # ------------------------------------------------------------------ setup

    def _placas_de_opts(self, opts) -> list[Placa]:
        comunes = dict(port=opts["port"], rele=opts["rele"], rele_tiempo=opts["rele_tiempo"],
                       dest_node=opts["dest_node"], source_node=opts["source_node"])
        specs = list(opts["placa"]) + [s for s in opts["placas"].split(";") if s.strip()]
        try:
            placas = [parsear_placa(s, **comunes) for s in specs]
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if opts["ip"]:
            if not opts["id_controlador"]:
                raise CommandError("--ip requiere --id-controlador.")
            placas.append(Placa(ip=opts["ip"], id_controlador=opts["id_controlador"], id_acceso=opts["id_acceso"],
                                **comunes))
        if not placas:
            raise CommandError("Indicá al menos una placa (--placa ip/ctrl[/acceso] o --ip + --id-controlador).")
        vistos: set[tuple[str, int]] = set()
        for p in placas:
            if (p.ip, p.port) in vistos:
                raise CommandError(f"La placa {p.ip}:{p.port} está repetida.")
            vistos.add((p.ip, p.port))
        return placas

    def handle(self, *args, **opts):
        self.validador = opts["validador"]
        self.modo = "real" if opts["grabar"] else opts["modo"]
        self.real = self.modo == "real"
        self.libre = self.modo == "libre"   # abre con cualquier lectura, sin validar
        self.lectores_salida = {int(x) for x in opts["lectores_salida"].split(",") if x.strip()}
        self.lectores_hex = {int(x) for x in opts["lectores_hex"].split(",") if x.strip()}
        self.alive = opts["alive"] or ""
        self.cola: "queue.Queue[tuple]" = queue.Queue()
        self._parar = threading.Event()
        self._ultimas: dict[tuple[int, str, str], tuple[datetime, Decision]] = {}
        # Las decisiones corren en el hilo de recepción de la librería; con varias
        # placas pueden llegar a la vez y el validador tiene UNA conexión a xSys.
        self._lock_decision = threading.Lock()
        self.placas = self._placas_de_opts(opts)

        if self.validador == "local":
            self.local = ValidadorLocal()
            self.decisor = None
            for p in self.placas:
                if p.id_acceso is None:
                    p.id_acceso = self.local.id_acceso_de(p.id_controlador) or None
            self.antirebote_seg = opts["antirebote"] if opts["antirebote"] is not None else \
                self.local._xparam_int("SCA_Antirebote_Seg", ANTIREBOTE_SEG_DEFAULT)
        else:
            self.local = None
            self.decisor = DecisorClever()
            # Con el SP, el antirrebote lo hace Clever cuando graba; acá sólo en observar.
            self.antirebote_seg = opts["antirebote"] if opts["antirebote"] is not None else \
                (0 if self.real else ANTIREBOTE_SEG_DEFAULT)

        self.stdout.write(self.style.SUCCESS(
            f"{len(self.placas)} placa(s) · validador {self.validador} · modo {self.modo.upper()}"
            f"{' (no acciona)' if self.modo == 'observar' else ''}"
            f"{' (ABRE CON CUALQUIER LECTURA, SIN VALIDAR)' if self.libre else ''} · antirrebote {self.antirebote_seg} s"))
        sin_acceso = [p for p in self.placas if self.validador == "local" and not p.id_acceso]
        if sin_acceso:
            raise CommandError("Sin Id_Acceso (ni en CD_Controladores ni en la spec): "
                               + ", ".join(p.nombre for p in sin_acceso))

        for i, p in enumerate(self.placas):
            p.sesion = OnlineSession(
                ip=p.ip, port=p.port, dest_node=p.dest_node, source_node=p.source_node,
                timeout_ms=opts["timeout"],
                on_user=lambda ev, p=p: self._on_user(p, ev),
                on_aux=lambda input_id, code, p=p: self._on_aux(p, input_id, code),
                on_conn=lambda cause, p=p: self._on_conn(p, cause),
                log_path=opts["log_nativo"],
                keepalive=(opts["keepalive"], 3) if opts["keepalive"] > 0 else None,
                init=(i == 0),
            )
            try:
                p.sesion.abrir()
                self.stdout.write(self.style.SUCCESS(
                    f"Sesión abierta con {p.nombre} · relé {p.rele}/{p.rele_tiempo}s"))
            except Exception as exc:  # noqa: BLE001 - el bucle de mantenimiento la reintenta
                self.stderr.write(self.style.WARNING(f"No se pudo abrir {p.nombre}: {exc}. Se reintenta."))

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, lambda *_: self._parar.set())
            except Exception:  # noqa: BLE001 - fuera del hilo principal
                pass

        inicio = time.time()
        ultimo_hb = inicio
        for p in self.placas:
            threading.Thread(target=p.sesion.mantener, kwargs={
                "parar": self._parar.is_set,
                "on_reconexion": lambda n, p=p: self._on_reconexion(p, n)}, daemon=True).start()
        try:
            while not self._parar.is_set():
                self._drenar_cola()
                self._tocar_alive()
                if opts["duracion"] and time.time() - inicio >= opts["duracion"]:
                    break
                if time.time() - ultimo_hb >= opts["heartbeat"]:
                    ultimo_hb = time.time()
                    for p in self.placas:
                        self.stdout.write(self._linea_estado(p))
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        finally:
            self._parar.set()
            self._drenar_cola()
            for p in self.placas:
                if p.sesion is not None:
                    p.sesion.cerrar()
            if self.placas and self.placas[0].sesion is not None:
                self.placas[0].sesion.uninit()
            if self.decisor:
                self.decisor.cerrar()
            if self.local:
                self.local.cerrar()
            for p in self.placas:
                self.stdout.write(self.style.WARNING("Cerrado. " + self._linea_estado(p)))

    def _linea_estado(self, p: Placa) -> str:
        s = p.sesion
        return (f"{datetime.now():%H:%M:%S} · {p.ip} ctrl {p.id_controlador} · viva={s.viva} eventos={s.eventos} "
                f"respuestas={s.respuestas} abiertos={p.abiertos} denegados={p.denegados} errores_rta={s.errores_respuesta} "
                f"reconexiones={s.reconexiones} caidas_socket={s.caidas_socket} socket={s.hay_socket()}")

    def _tocar_alive(self) -> None:
        if not self.alive:
            return
        try:
            with open(self.alive, "w", encoding="ascii") as fh:
                fh.write(f"{time.time():.0f} {sum(1 for p in self.placas if p.sesion and p.sesion.viva)}/{len(self.placas)}\n")
        except OSError:
            pass

    # ------------------------------------------------------------------ decisión

    def _decidir(self, p: Placa, tag: str, tipo: str, cuando: datetime) -> tuple[Decision, bool]:
        """Decide la lectura; si es una repetición reciente en la misma placa, repite la respuesta."""
        clave = (p.id_controlador, tag, tipo)
        with self._lock_decision:
            previa = self._ultimas.get(clave)
            if previa and self.antirebote_seg > 0 and cuando - previa[0] <= timedelta(seconds=self.antirebote_seg):
                return previa[1], True
            if self.local is not None:
                decision: Decision = self.local.decidir(
                    tipo_acceso=tipo, tag=tag, id_controlador=p.id_controlador, id_acceso=p.id_acceso,
                    cuando=cuando)
            else:
                decision = self.decisor.decidir(id_controlador=p.id_controlador, tipo_acceso=tipo, tag=tag,
                                                cuando=cuando, graba=self.real)
            if not decision.error:
                self._ultimas[clave] = (cuando, decision)
                if len(self._ultimas) > 2000:
                    corte = cuando - timedelta(seconds=max(self.antirebote_seg, 1) * 10)
                    self._ultimas = {k: v for k, v in self._ultimas.items() if v[0] >= corte}
        return decision, False

    # --------------------------------------------------------- callbacks nativos

    def _on_user(self, p: Placa, ev: EventoUsuario):
        """Corre en el hilo de la librería con la placa esperando: decidir y volver."""
        cuando = ahora_local()
        if ev.event_code in EVENTOS_A_DECIDIR:
            tipo = tipo_acceso_from_event(ev, self.lectores_salida)
            tag = tag_for_event(ev, self.lectores_hex)
            decision, antirebote = self._decidir(p, tag, tipo, cuando)
            if self.libre:
                accionar, mensaje = True, f"LIBRE {decision.mensaje}"
            elif self.real:
                accionar, mensaje = bool(decision.habilitado), decision.mensaje
            else:
                accionar, mensaje = False, f"PRUEBA {decision.mensaje}"
            self.cola.put(("paso", p, ev, decision, tipo, tag, cuando, accionar, antirebote))
            return build_answer(access_id=ev.access_id, habilitado=accionar,
                                source_id=ev.source_id, mensaje=mensaje, cuando=cuando)
        # Notificaciones (aceptado offline, etc.): se acusan sin accionar nada.
        self.cola.put(("paso", p, ev, None, "", tag_for_event(ev, self.lectores_hex), cuando, False, False))
        return build_answer(access_id=ev.access_id, habilitado=(ev.event_code == EV_ACEPTADO),
                            source_id=ev.source_id, mensaje="", cuando=cuando, access_ctl=0)

    def _on_aux(self, p: Placa, input_id: int, event_code: int) -> None:
        """Entrada auxiliar (sensor de giro, pulsador...): se encola y se persiste."""
        self.cola.put(("aux", p, ahora_local(), int(input_id), int(event_code)))

    def _on_conn(self, p: Placa, cause: int) -> None:
        if cause != 2:
            self.stdout.write(f"{datetime.now():%H:%M:%S} · {p.ip} conexión: cause={cause}")

    def _on_reconexion(self, p: Placa, n: int) -> None:
        self.stdout.write(f"{datetime.now():%H:%M:%S} · {p.ip} reconectado (#{n})")

    # ------------------------------------------------------------- persistencia

    def _guardar_sensor(self, p: Placa, cuando: datetime, input_id: int, event_code: int) -> None:
        """Evento de entrada auxiliar -> IntelektronEvent (origen 'online-aux')."""
        from access_control.models import IntelektronEvent

        self.stdout.write(f"{cuando:%H:%M:%S} · {p.ip} SENSOR entrada {input_id} código {event_code}")
        try:
            key = hashlib.sha1(
                f"online-aux|{p.id_controlador}|{cuando.isoformat()}|{input_id}|{event_code}".encode()).hexdigest()
            IntelektronEvent.objects.get_or_create(
                dedupe_key=key,
                defaults={
                    "device_ip": p.ip, "dest_node": p.dest_node, "id_controlador": p.id_controlador,
                    "access_id": 0, "event_code": event_code, "event_name": f"sensor {input_id}",
                    "direction": 0, "source": input_id, "device_time": _aware(cuando),
                    "raw": {"origen": "online-aux", "input_id": input_id, "event_code": event_code,
                            "id_acceso": p.id_acceso},
                },
            )
        except Exception as exc:  # noqa: BLE001
            self.stderr.write(self.style.WARNING(f"no se pudo guardar el evento de sensor: {exc}"))
            close_old_connections()

    def _drenar_cola(self) -> None:
        from access_control.models import IntelektronEvent, PasoMolinete

        while True:
            try:
                item = self.cola.get_nowait()
            except queue.Empty:
                return
            if item[0] == "aux":
                self._guardar_sensor(*item[1:])
                continue
            _, p, ev, decision, tipo, tag, cuando, accionar, antirebote = item
            linea = f"{cuando:%H:%M:%S} · {p.ip} {ev.event_name} lector={ev.source_id} tag={tag} sentido={ev.direction}"
            if decision is not None:
                linea += f" -> {decision.resumen}"
                if isinstance(decision, DecisionLocal):
                    linea += f" motivo={decision.id_motivo}"
                linea += f" [{decision.duracion_ms:.0f} ms]"
                if antirebote:
                    linea += " (repetida)"
                if decision.error:
                    p.errores += 1
                if accionar:
                    p.abiertos += 1
                    linea += " ABRE"
                    if p.rele > 0 and p.sesion is not None:
                        ok = p.sesion.accionar_rele(p.rele, p.rele_tiempo, node=ev.node)
                        linea += f" rele {p.rele}/{p.rele_tiempo}s {'OK' if ok else 'FALLO'}"
                else:
                    p.denegados += 1
            self.stdout.write(linea)
            try:
                fecha = timezone.make_aware(cuando, timezone.get_current_timezone())
                if decision is not None:
                    paso = PasoMolinete.objects.create(
                        fecha=fecha, id_controlador=p.id_controlador, id_acceso=p.id_acceso,
                        tipo=tipo or "E", tag=tag, lector=ev.source_id, evento_codigo=ev.event_code,
                        id_cliente=decision.id_cliente, id_cliente_ref=decision.id_cliente_ref,
                        habilitado=bool(decision.habilitado), accionado=accionar,
                        id_motivo=getattr(decision, "id_motivo", None),
                        mensaje=(decision.mensaje or "")[:300], detalle=(getattr(decision, "detalle", "") or "")[:200],
                        info=(decision.info or "")[:10], validador=self.validador, modo=self.modo,
                        antirebote=antirebote, duracion_ms=round(decision.duracion_ms, 1),
                        error=(decision.error or "")[:300],
                        raw={"access_id": ev.access_id, "direction": ev.direction, "node": ev.node,
                             "ip": p.ip, "traza": getattr(decision, "traza", [])},
                    )
                    if self.modo != "observar":
                        espejar_en_visor(paso, p.id_controlador, p.id_acceso)
                key = hashlib.sha1(
                    f"online|{p.id_controlador}|{cuando.isoformat()}|{ev.access_id}|{ev.event_code}|{ev.source_id}".encode()
                ).hexdigest()
                IntelektronEvent.objects.get_or_create(
                    dedupe_key=key,
                    defaults={
                        "device_ip": p.ip, "dest_node": ev.node, "id_controlador": p.id_controlador,
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
