"""Reglas de anti-reuso: "paso pendiente" y anti-passback.

Regla 1 — PASO PENDIENTE (activa)
---------------------------------
Cuando un socio valida en un molinete queda "paso pendiente" en ESE molinete
durante unos segundos: tiene esa ventana para cruzarlo. Si dentro de la ventana
la misma credencial o el mismo rostro aparecen en OTRO molinete, ese segundo
intento se marca como conflicto y el visor muestra
``Paso pendiente Molinete: XX``.

Es lo que permite detectar que una credencial ya fue usada y alguien la está
pasando por otro molinete. Revalidar en el MISMO molinete no es conflicto: es la
persona que todavía no cruzó.

Regla 2 — ANTI-PASSBACK (implementada pero DESACTIVADA)
------------------------------------------------------
Quien ya ingresó no debería poder volver a ingresar durante unos minutos. Está
escrita y se activa con ``ANTIPASSBACK_ACTIVO=1``, pero hoy queda apagada porque
**no hay forma de saber si el socio efectivamente cruzó**: los molinetes todavía
no reportan el giro del aspa asociado a la validación. Con el dato de "ingresó"
disponible, alcanza con encender el flag y llamar a ``registrar_ingreso()``
desde donde se detecte el cruce.

Ojo con activarla antes de tener esa señal: sin confirmación de cruce, a quien
validara sin pasar (porque se arrepintió, o el molinete no giró) se le negaría
la entrada durante los minutos siguientes.

Sobre la precisión temporal
---------------------------
Se usa la hora de INGESTA y no la de los equipos: los relojes de los faciales
driftean y el ``server_datetime`` de BioStar viene desfasado (ver el visor, que
por eso ordena por ``synced_at``). La ingesta agrega su propio retardo —CD_ES
~1 s, facial ~2-4 s—, así que la ventana no es exacta al segundo. Para lo que se
busca alcanza: una persona no puede estar físicamente en dos molinetes con
segundos de diferencia, así que la detección del reuso es lo que importa, no el
límite exacto. Cuando el molinete valide contra nosotros en línea, el instante
será exacto y esto se vuelve preciso.
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)

VENTANA_ENV = "PASO_PENDIENTE_SEGUNDOS"
VENTANA_DEFAULT = 5

ANTIPASSBACK_ACTIVO_ENV = "ANTIPASSBACK_ACTIVO"
ANTIPASSBACK_MINUTOS_ENV = "ANTIPASSBACK_MINUTOS"
ANTIPASSBACK_MINUTOS_DEFAULT = 5


def ventana_segundos() -> int:
    try:
        return max(0, int(os.getenv(VENTANA_ENV, str(VENTANA_DEFAULT))))
    except (TypeError, ValueError):
        return VENTANA_DEFAULT


def antipassback_activo() -> bool:
    return (os.getenv(ANTIPASSBACK_ACTIVO_ENV, "0") or "0").strip().lower() in ("1", "true", "on")


def antipassback_minutos() -> int:
    try:
        return max(0, int(os.getenv(ANTIPASSBACK_MINUTOS_ENV, str(ANTIPASSBACK_MINUTOS_DEFAULT))))
    except (TypeError, ValueError):
        return ANTIPASSBACK_MINUTOS_DEFAULT


# ------------------------------------------------------- puentes de BioStar
# Tipos de controlador de xSys que NO son un molinete físico sino el puente por
# el que un facial le avisa a xSys que alguien pasó ('F' facial, 'W' su variante).
_TIPOS_PUENTE = ("F", "W")


def controladores_puente() -> set[int]:
    """Controladores de xSys que sólo replican pasos de los faciales.

    Un cruce por facial se registra DOS veces: lo manda BioStar (con su equipo) y
    lo manda xSys por este controlador. Son el mismo cruce, así que el de xSys no
    puede generar paso pendiente propio: si lo hiciera, chocaría contra el del
    facial y marcaría conflicto a todo el mundo.

    Y no alcanza con mapearlos a una columna: medido sobre 3 días, los CINCO
    equipos faciales del club reportan por el MISMO controlador (68, "Sup BioStar
    API Alcorta"), así que no hay columna a la que asignarlo sin mentir en las
    otras cuatro. Se los excluye de la regla, que es lo exacto: el paso ya quedó
    registrado por el lado del facial, con su equipo real.
    """
    from xsys.models import XsysControlador

    return set(
        XsysControlador.objects.filter(tipo_cont__in=_TIPOS_PUENTE)
        .values_list("id_controlador", flat=True)
    )


# --------------------------------------------------------------- molinetes
def mapa_molinetes() -> dict[str, dict]:
    """Índice ``origen -> molinete`` para ubicar en qué columna cae un evento.

    Claves: ``c<id_controlador>`` para los eventos de xSys y ``d<device_id>``
    para los faciales de BioStar. El molinete es el grupo (la columna del visor),
    que es justamente la unidad física que le interesa a la regla.
    """
    from institutions.models import DoorTurnstileGroup

    mapa: dict[str, dict] = {}
    for g in DoorTurnstileGroup.objects.all().only(
        "id", "nombre", "door_id", "id_controladores", "biostar_device_ids"
    ):
        destino = {"key": f"g{g.id}", "nombre": g.nombre, "door_id": g.door_id}
        for c in (g.id_controladores or []):
            mapa[f"c{int(c)}"] = destino
        for d in (g.biostar_device_ids or []):
            mapa[f"d{int(d)}"] = destino
    return mapa


def resolver_molinete(mapa: dict[str, dict], *, id_controlador=None, device_id=None) -> dict:
    """Molinete al que pertenece un evento, con respaldo si la puerta no está armada."""
    if device_id is not None:
        m = mapa.get(f"d{int(device_id)}")
        if m:
            return m
        return {"key": f"d{int(device_id)}", "nombre": f"Facial {int(device_id)}", "door_id": None}
    if id_controlador is not None:
        m = mapa.get(f"c{int(id_controlador)}")
        if m:
            return m
        return {"key": f"c{int(id_controlador)}", "nombre": f"Ctrl {int(id_controlador)}", "door_id": None}
    return {"key": "", "nombre": "", "door_id": None}


# ------------------------------------------------------------------ regla 1
def evaluar(id_cliente, molinete: dict, *, cuando=None, origen: str = "") -> str:
    """Evalúa y actualiza el estado de paso pendiente de un socio.

    Devuelve el NOMBRE del molinete en el que ya estaba pendiente si esto es un
    conflicto (validó en otro molinete dentro de la ventana), o cadena vacía si
    no lo es. En este último caso deja al socio pendiente en ``molinete``.
    """
    from access_control.models import PasoPendiente

    if not id_cliente or not molinete.get("key"):
        return ""
    ventana = ventana_segundos()
    if ventana <= 0:
        return ""

    cuando = cuando or timezone.now()
    expira = cuando + timedelta(seconds=ventana)

    try:
        actual = PasoPendiente.objects.filter(pk=id_cliente).first()
        if actual and actual.expira_en > cuando and actual.molinete_key != molinete["key"]:
            # Conflicto: la misma identidad en otro molinete dentro de la ventana.
            # NO se pisa la reserva original — el socio sigue debiendo cruzar el
            # primero, y un tercer intento tiene que seguir señalando ese mismo.
            return actual.molinete_nombre or actual.molinete_key

        PasoPendiente.objects.update_or_create(
            id_cliente=id_cliente,
            defaults={
                "molinete_key": molinete["key"],
                "molinete_nombre": (molinete.get("nombre") or "")[:60],
                "door_id": molinete.get("door_id"),
                "origen": origen[:12],
                "iniciado_en": cuando,
                "expira_en": expira,
            },
        )
    except Exception as exc:  # pragma: no cover - nunca romper la ingesta
        logger.warning("paso_pendiente: fallo evaluando socio %s: %s", id_cliente, exc)
        return ""
    return ""


def purgar(antiguedad_horas: int = 6) -> int:
    """Borra reservas viejas. Son efímeras; sin purga la tabla sólo acumula."""
    from access_control.models import PasoPendiente

    limite = timezone.now() - timedelta(hours=antiguedad_horas)
    borradas, _ = PasoPendiente.objects.filter(expira_en__lt=limite).delete()
    return borradas


# ------------------------------------------------------------------ regla 2
def registrar_ingreso(id_cliente, molinete: dict, *, cuando=None) -> None:
    """Marca que el socio EFECTIVAMENTE cruzó (para el anti-passback).

    Todavía no la llama nadie: los molinetes no reportan el cruce. Queda lista
    para engancharla cuando el hardware lo permita.
    """
    if not antipassback_activo():
        return
    # Reservado para cuando exista la señal de cruce. Se deja explícito para que
    # activar el flag sin conectar el hardware no dé una falsa sensación de que
    # la regla está operando.
    logger.info("antipassback: ingreso de %s en %s (aún sin señal de cruce real)",
                id_cliente, molinete.get("nombre"))


def bloqueado_por_antipassback(id_cliente, *, cuando=None) -> int:
    """Minutos que le faltan para poder volver a ingresar; 0 si no aplica.

    Devuelve 0 mientras ``ANTIPASSBACK_ACTIVO`` esté apagado, que es el estado
    actual por no tener cómo saber si el socio cruzó.
    """
    if not antipassback_activo():
        return 0
    from access_control.models import PasoPendiente

    minutos = antipassback_minutos()
    if minutos <= 0:
        return 0
    cuando = cuando or timezone.now()
    ultimo = PasoPendiente.objects.filter(pk=id_cliente).first()
    if not ultimo or not ultimo.iniciado_en:
        return 0
    transcurridos = (cuando - ultimo.iniciado_en).total_seconds() / 60.0
    restan = minutos - transcurridos
    return int(restan) + 1 if restan > 0 else 0
