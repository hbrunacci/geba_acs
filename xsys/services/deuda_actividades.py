"""Revisión en vivo de los bloqueados por deuda de actividades.

EL PROBLEMA QUE RESUELVE
------------------------
El bloqueo por deuda de actividades vive en ``CD_Clientes_Deuda_Actividades``,
una tabla que se cargó a mano desde una planilla el 02/09/2026 y que
``CP_SCA_RegistrarAcceso`` lee para frenar en el molinete a los que tienen
``Bloquea = 1``.

Nada en xSys escribe esa tabla: ni un trigger, ni un stored procedure, ni un
job. La revisamos entera y el único objeto de la base que la menciona es el SP
de acceso, y sólo para leerla. O sea que **pagar no desbloquea a nadie**: el
socio regulariza en Tesorería, su cuenta corriente queda en cero y el molinete
lo sigue frenando hasta que alguien edite la fila a mano. Medido el 09/09/2026,
16 de los 116 bloqueados ya no debían un peso de actividades y seguían afuera.

Este módulo cierra ese circuito.

POR QUÉ ES UN PROCESO APARTE Y NO PARTE DE LA FOTO DE DEUDA
-----------------------------------------------------------
La foto del tablero recorre la cuenta corriente entera —338.000 comprobantes,
unos cinco minutos— y corre cuatro veces por día. Para dejar entrar a alguien
que acaba de pagar eso es carísimo y lentísimo a la vez.

Acá se consulta SÓLO a los que están en la tabla de bloqueo. Son 163 personas:
la consulta filtra por ``Id_Cliente IN (...)`` y vuelve en menos de un segundo,
así que puede correr cada pocos minutos. Es la diferencia entre "pagaste y a la
tarde podés entrar" y "pagaste y mañana vemos".

QUÉ CUENTA COMO DEUDA DE ACTIVIDADES
------------------------------------
Exactamente lo mismo que el tablero, y a propósito: el mapeo de tipos de
contrato sale de ``xsys.services.tableros`` en vez de repetirse acá. Si el
tablero dice que alguien no debe actividades y el molinete lo sigue frenando,
alguien va a tener razón y no se va a poder saber cuál.

Con una diferencia que sí corresponde: **sólo cuenta lo vencido**. El club
factura por adelantado, así que el cupón del mes que viene ya existe y figura
impago sin que nadie deba nada todavía. Bloquear por eso sería frenar a alguien
por una cuota que aún no venció.

QUÉ HACE CON CADA CASO
----------------------
Respeta el criterio que ya venía usando el club (4 cuotas frena, 2 y 3 avisan):

    0 cuotas vencidas            -> Activo = 0. Regularizado, sale de la tabla.
    1 a 3 cuotas vencidas        -> Bloquea = 0. Pasa, pero el visor lo marca.
    4 o más                      -> sin cambios. Sigue bloqueado.

**Nunca bloquea a nadie nuevo, ni sube a nadie de aviso a bloqueo.** Sólo afloja.
Liberar a quien pagó es corregir un error del sistema; frenar a alguien es una
decisión del club, y esa siguió y sigue viniendo por la planilla.
"""

from __future__ import annotations

import datetime as _dt

from django.conf import settings
from django.utils import timezone

from xsys.services.mssql import connect
from xsys.services.tableros import TIPOS_CON_ACTIVIDADES, TIPOS_OPCIONALES

# Cuántas cuotas vencidas hacen falta para frenar en el molinete. Es el criterio
# con el que se armó la planilla del 25/08/2026: la tanda de 4 cuotas se bloquea
# y la de 2 y 3 sólo se avisa.
UMBRAL_BLOQUEO = 4

# Marca que queda en la Observación de la fila, para que quien mire la tabla en
# xSys sepa que la tocó este proceso y no una persona.
MARCA = "auto"

ESTADO_REGULARIZADO = "regularizado"
ESTADO_A_AVISO = "baja_a_aviso"
ESTADO_SIGUE = "sigue_bloqueado"
ESTADO_SIN_CAMBIO = "sin_cambio"


class RevisionError(Exception):
    """No se pudo revisar. El mensaje va tal cual al log del proceso."""


def _corte_vencido(hoy: _dt.date) -> _dt.date:
    """Primer día del mes que viene: todo lo anterior está vencido."""
    if hoy.month == 12:
        return _dt.date(hoy.year + 1, 1, 1)
    return _dt.date(hoy.year, hoy.month + 1, 1)


def _sql_cuotas_vencidas(cantidad_ids: int) -> str:
    """Cuenta comprobantes de ACTIVIDADES impagos y vencidos, por socio.

    El JOIN con Contratos es interno y no externo justamente al revés que en el
    tablero: acá sólo interesan los comprobantes que salieron de un contrato de
    actividad, y un comprobante sin contrato nunca lo es.
    """
    actividades = ",".join(str(i) for i in TIPOS_CON_ACTIVIDADES)
    opcionales = ",".join("'%s'" % t for t in TIPOS_OPCIONALES)
    marcadores = ",".join("?" for _ in range(cantidad_ids))
    return f"""
        SELECT  CC.Id_Cliente,
                cuotas  = COUNT(*),
                importe = SUM(CC.Saldo),
                mas_vieja = MIN(CC.Fecha)
        FROM    Clientes_CtaCte CC
                JOIN Cbtes B    ON B.Id_Trans = CC.Id_Trans
                JOIN Contratos O ON O.Id_Contrato = B.Id_Contrato AND B.Id_Contrato > 0
        WHERE   CC.Importe > 0 AND CC.Saldo > 0
          AND   B.Id_Tipo_Cbte NOT IN ({opcionales})
          AND   O.Id_Tipo_Con IN ({actividades})
          AND   CC.Fecha < ?
          AND   CC.Id_Cliente IN ({marcadores})
        GROUP BY CC.Id_Cliente
    """


def _conectar():
    try:
        conn = connect(settings.MSSQL_XSYS)
    except Exception as exc:  # pragma: no cover - depende de la red
        raise RevisionError(f"No se pudo conectar a xSys: {exc}") from exc
    conn.timeout = 0
    return conn


def revisar(aplicar: bool = False) -> dict:
    """Mira quién de los bloqueados ya pagó y, si ``aplicar``, los libera.

    Con ``aplicar=False`` no escribe nada: devuelve el mismo informe para poder
    verlo antes de tocar la tabla. Es el modo por defecto a propósito, porque lo
    que está en juego es dejar entrar gente.
    """
    hoy = timezone.localdate()
    corte = _corte_vencido(hoy)

    conn = _conectar()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT Id_Cliente, Cuotas, Bloquea, ISNULL(Observacion, '') "
            "FROM CD_Clientes_Deuda_Actividades WHERE ISNULL(Activo, 1) = 1"
        )
        filas = [(int(r[0]), int(r[1] or 0), bool(r[2]), (r[3] or "").strip())
                 for r in cur.fetchall()]
        if not filas:
            return _informe(hoy, [], aplicar, 0)

        ids = [f[0] for f in filas]
        cur.execute(_sql_cuotas_vencidas(len(ids)),
                    [_dt.datetime(corte.year, corte.month, corte.day)] + ids)
        vivo = {int(r[0]): {"cuotas": int(r[1]), "importe": float(r[2] or 0),
                            "mas_vieja": r[3]} for r in cur.fetchall()}

        # Nombres sólo para que el informe se pueda leer: el proceso decide con
        # los números, pero el que lee el log necesita saber de quién habla.
        nombres = _nombres(cur, ids)

        casos = []
        for id_cliente, cuotas_planilla, bloquea, observacion in filas:
            hoy_debe = vivo.get(id_cliente, {"cuotas": 0, "importe": 0.0,
                                             "mas_vieja": None})
            casos.append({
                "id_cliente": id_cliente,
                "nombre": nombres.get(id_cliente, ""),
                "cuotas_planilla": cuotas_planilla,
                "bloqueaba": bloquea,
                "cuotas_hoy": hoy_debe["cuotas"],
                "importe_hoy": hoy_debe["importe"],
                "observacion": observacion,
                "estado": _decidir(bloquea, hoy_debe["cuotas"]),
            })

        aplicados = _aplicar(cur, conn, casos, hoy) if aplicar else 0
    finally:
        conn.close()

    return _informe(hoy, casos, aplicar, aplicados)


def _decidir(bloqueaba: bool, cuotas_hoy: int) -> str:
    if cuotas_hoy <= 0:
        return ESTADO_REGULARIZADO
    if bloqueaba and cuotas_hoy < UMBRAL_BLOQUEO:
        return ESTADO_A_AVISO
    return ESTADO_SIGUE if bloqueaba else ESTADO_SIN_CAMBIO


def _nombres(cur, ids: list[int]) -> dict:
    marcadores = ",".join("?" for _ in ids)
    cur.execute(
        f"SELECT Id_Cliente, RTRIM(ISNULL(Apellido,'')) + ', ' + "
        f"RTRIM(ISNULL(Nombre,'')) FROM Clientes WHERE Id_Cliente IN ({marcadores})",
        ids)
    return {int(r[0]): (r[1] or "").strip(", ") for r in cur.fetchall()}


def _aplicar(cur, conn, casos: list[dict], hoy: _dt.date) -> int:
    """Escribe los cambios en xSys, todo o nada.

    Cada UPDATE lleva su propia condición de seguridad en el WHERE (el estado
    que se leyó sigue estando) para que dos corridas simultáneas, o una corrida
    y una edición a mano, no se pisen: la segunda no encuentra la fila y no
    hace nada, en vez de sobrescribir lo que decidió la primera.
    """
    a_tocar = [c for c in casos
               if c["estado"] in (ESTADO_REGULARIZADO, ESTADO_A_AVISO)]
    if not a_tocar:
        return 0

    sello = hoy.strftime("%d/%m/%Y")
    hechos = 0
    conn.autocommit = False
    try:
        for c in a_tocar:
            if c["estado"] == ESTADO_REGULARIZADO:
                nota = f"{MARCA}: regularizado {sello}, sin deuda de actividades vencida"
                # Fecha_Baja además de Activo: la columna existe justamente para
                # esto y hasta ahora estaba vacía en las 163 filas. Sin ella, la
                # tabla dice que alguien está regularizado pero no desde cuándo.
                cur.execute(
                    "UPDATE CD_Clientes_Deuda_Actividades "
                    "SET Activo = 0, Fecha_Baja = GETDATE(), Observacion = ? "
                    "WHERE Id_Cliente = ? AND ISNULL(Activo, 1) = 1",
                    (_nota(c["observacion"], nota), c["id_cliente"]))
            else:
                nota = (f"{MARCA}: baja a aviso {sello}, "
                        f"quedan {c['cuotas_hoy']} cuota(s) vencida(s)")
                cur.execute(
                    "UPDATE CD_Clientes_Deuda_Actividades "
                    "SET Bloquea = 0, Observacion = ? "
                    "WHERE Id_Cliente = ? AND ISNULL(Activo, 1) = 1 AND Bloquea = 1",
                    (_nota(c["observacion"], nota), c["id_cliente"]))
            c["aplicado"] = cur.rowcount == 1
            hechos += 1 if c["aplicado"] else 0
        conn.commit()
    except Exception as exc:
        conn.rollback()
        raise RevisionError(f"No se pudieron aplicar los cambios: {exc}") from exc
    finally:
        conn.autocommit = True
    return hechos


def _nota(anterior: str, nueva: str, tope: int = 200) -> str:
    """Agrega la marca sin perder lo que ya había, y sin pasarse del campo."""
    anterior = (anterior or "").strip()
    # Si ya hay una marca automática previa, se reemplaza: interesa la última,
    # no la historia, que igual queda en el log del proceso.
    if anterior.startswith(MARCA + ":"):
        anterior = ""
    texto = f"{anterior} | {nueva}".strip(" |") if anterior else nueva
    return texto[:tope]


def _informe(hoy, casos, aplicar, aplicados) -> dict:
    por_estado: dict[str, list] = {}
    for c in casos:
        por_estado.setdefault(c["estado"], []).append(c)
    return {
        "fecha": hoy.isoformat(),
        "aplicar": bool(aplicar),
        "revisados": len(casos),
        "regularizados": por_estado.get(ESTADO_REGULARIZADO, []),
        "bajados_a_aviso": por_estado.get(ESTADO_A_AVISO, []),
        "siguen_bloqueados": len(por_estado.get(ESTADO_SIGUE, [])),
        "sin_cambio": len(por_estado.get(ESTADO_SIN_CAMBIO, [])),
        "aplicados": aplicados,
    }
