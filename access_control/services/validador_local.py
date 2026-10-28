"""Validador local de acceso: réplica en Python de la cascada de Clever.

Reproduce, paso a paso y en el mismo orden, lo que decide
``CP_SCA_RegistrarAcceso`` (y las funciones y procedimientos que llama) para
un acceso que NO es de evento, leyendo las MISMAS tablas de xSys con consultas
de sólo lectura. No ejecuta el SP, no escribe en ``CD_ES`` ni en ninguna otra
tabla de Clever: el registro del paso queda en nuestra base
(``access_control.PasoMolinete``), a cargo de quien llama.

Fuente: volcado de código ``C:\\Users\\User\\xsys_codigo\\xsys_geba`` al
28/10/2026 (``procedimientos/CP_SCA_RegistrarAcceso.sql`` y compañía). Cada
bloque de abajo cita el objeto de Clever que replica. REGLA: si cambia alguno
de esos objetos en Clever, hay que revisar acá.

Orden de la cascada (idéntico al SP):

  antirrebote (lo resuelve el llamador: ``buscar_antirebote``)
  101 sin datos · 102 acceso inexistente
  [CleverQR: sólo QR de 10 dígitos que empiezan con 9; no aplica a Mifare]
  identificación del cliente (CF_SCA_IdentifIdCliente)
  211 habilitado manualmente · 217 acompañante/visita
  103 persona inválida (115 si era un QR dinámico vencido)
  RECHAZOS: 104 inactiva · 118 deuda de actividades · 105 vencimientos ·
            106 contrato de rechazo · 107 categoría de rechazo ·
            108/109/110 antipassback · 111 foto · 113 turno · 309 cuota (tipo 2)
  HABILITACIONES: 202 master · 212 links · 203 cuota (tipo 1) · 204 contrato ·
            205 categoría · 310 kit · 206 producto · 207 producto del titular ·
            209 categoría por horario · 210 producto por horario
  112 no cumple ninguna condición habilitante

Diferencias conocidas con el SP (todas por no escribir en Clever):
  * 211: el SP marca usada la habilitación manual (``Flag_Permite_Paso = 0``);
    acá queda usable el resto del día.
  * 212: el SP incrementa ``Cant_Usos_Realizados`` del link; acá no.
  * 310: el SP genera el remito que consume la unidad del kit; acá se habilita
    si hay saldo pero no se descuenta.
  * 217: el SP ata la invitación al ``Id_ES``; acá no (la invitación dura el
    día y la ventana de reingreso la fija ``SCA_Visit_TmpLimit``).
  * Antipassback mira ``CD_ES`` (los pasos de los otros molinetes), no los
    nuestros. En SM-Alcorta (acceso 14) está apagado.
  * No se resuelven lecturas de DNI escaneado (``CF_IdentifDNI``): esta placa
    sólo tiene lector de credencial.
"""

from __future__ import annotations

import calendar
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

from django.utils import timezone

from xsys.services import mssql

from .intelektron_decision import Decision

logger = logging.getLogger(__name__)

# Tipos de cliente de socio que el club no factura: la cuota no se les exige
# (CF_SCA_ValidarUltCuotaPaga, 11/09/2026).
TIPOS_SOCIO_SIN_CUOTA = (1010, 1100, 1126)
TOKEN_QR_MINUTOS = 5


def ahora_local() -> datetime:
    """Hora del club (America/Argentina/Buenos_Aires), naive, sin microsegundos.

    Los contenedores corren en UTC: con ``datetime.now()`` entre las 21:00 y la
    medianoche se evaluaría con la fecha del día siguiente.
    """
    return timezone.localtime(timezone.now()).replace(tzinfo=None, microsecond=0)


# ----------------------------------------------------------------------------- resultado

@dataclass
class DecisionLocal(Decision):
    id_motivo: int = 0
    detalle: str = ""
    id_acceso: int = 0
    traza: list[str] = field(default_factory=list)
    antirebote: bool = False

    @property
    def resultado(self) -> str:
        return "S" if self.habilitado else "E"


# ----------------------------------------------------------------------------- config

@dataclass
class ConfigAcceso:
    id_acceso: int
    descripcion: str
    activo: int
    flag_evento: int
    flag_ucp: int
    dias_gracia: int
    meses_gracia: int
    horas_antipassback: int
    tiempo_antirebote: int          # centésimas de segundo (CD_Accesos.Tiempo_Antirebote)
    flag_foto_validar: int
    flag_links_validar: int
    tiempo_mensaje: int
    tipo_controlador: str           # Tipo del primer controlador del acceso ('E' / 'A')
    cont_tipos: dict[int, str]      # Id_Tipo_Con -> 'H' / 'R'
    cli_tipos: dict[int, str]       # Id_Tipo_Cli -> 'H' / 'R'
    venc_tipos: list[str]
    tiene_horarios: bool
    sectores_turno: list[tuple[int, int, int]]   # (Id_Sector, Minutos_Gracia_Prev, Minutos_Gracia_Post)
    motivos_acceso: dict[int, str]  # CD_Accesos_Motivos: Id_Motivo -> Descripcion_Motivo


# ----------------------------------------------------------------------------- SQL
# Cada consulta arranca con /*q:nombre*/ para poder simularla en las pruebas.

SQL_ACCESO = (
    "/*q:acceso*/ SELECT Id_Acceso, ISNULL(Descripcion,''), ISNULL(Activo,0), ISNULL(Flag_Evento,0), "
    "ISNULL(Flag_Ult_Cuota_Paga,0), ISNULL(Dias_Gracia,0), ISNULL(Meses_Gracia,0), ISNULL(Flag_AntiPassBack,0), "
    "ISNULL(Tiempo_Antirebote,0), ISNULL(Flag_Foto_Validar,0), ISNULL(Flag_Clientes_Links_Validar,0), "
    "ISNULL(Tiempo_Mensaje,1) FROM CD_Accesos WHERE Id_Acceso = ?"
)
SQL_TIPO_CONTROLADOR = ("/*q:tipo_controlador*/ SELECT TOP 1 ISNULL(Tipo,'E') FROM CD_Controladores "
                        "WHERE Id_Acceso = ? ORDER BY Id_Controlador")
SQL_CONT_TIPOS = "/*q:cont_tipos*/ SELECT Id_Tipo_Con, ISNULL(Flag_Habilitado,'H') FROM CD_Accesos_Cont_Tipos WHERE Id_Acceso = ?"
SQL_CLI_TIPOS = "/*q:cli_tipos*/ SELECT Id_Tipo_Cli, ISNULL(Flag_Habilitado,'H') FROM CD_Accesos_Cli_Tipos WHERE Id_Acceso = ?"
SQL_VENC_TIPOS = "/*q:venc_tipos*/ SELECT Id_Tipo_Venc FROM CD_Accesos_Venc WHERE Id_Acceso = ?"
SQL_HORARIOS = "/*q:horarios*/ SELECT MAX(1) FROM CD_Accesos_Horarios WHERE Id_Horario > 0 AND Id_Acceso = ?"
SQL_SECTORES = ("/*q:sectores*/ SELECT Id_Sector, ISNULL(Minutos_Gracia_Prev,0), ISNULL(Minutos_Gracia_Post,0) "
                "FROM CD_Accesos_Emp_Sedes_Sec WHERE Id_Acceso = ?")
SQL_MOTIVOS_ACCESO = "/*q:motivos_acceso*/ SELECT Id_Motivo, ISNULL(Descripcion_Motivo,'') FROM CD_Accesos_Motivos WHERE Id_Acceso = ?"
SQL_ID_ACCESO = "/*q:id_acceso*/ SELECT ISNULL(Id_Acceso,0) FROM CD_Controladores WHERE Id_Controlador = ?"
SQL_MOTIVOS = ("/*q:motivos*/ SELECT Id_CD_Motivo, ISNULL(Descripcion,''), ISNULL(Descripcion_Display,''), "
               "ISNULL(Descripcion_Pantalla,''), ISNULL(Descripcion_Personalizada,'') FROM CD_Motivos")
SQL_XPARAM = ("/*q:xparam*/ SELECT ISNULL(TipoDato,''), Valor_String, Valor_Integer, Valor_Decimal, Valor_Memo "
              "FROM xParametros WHERE IdParametro = ?")

SQL_CLIENTE_POR_CREDENCIAL = ("/*q:cli_credencial*/ SELECT ISNULL(MAX(Id_Cliente),0) FROM Clientes "
                              "WHERE Credencial_Nro = UPPER(LTRIM(RTRIM(?)))")
SQL_CLIENTE_POR_TOKEN = ("/*q:cli_token*/ SELECT ISNULL(MAX(ISNULL(Id_Cliente,0)),0) FROM Clientes_Links "
                         "WHERE Token = LOWER(LTRIM(RTRIM(?))) AND Fecha_Gen > ?")
SQL_CLIENTE_POR_TOKEN_SIN_FECHA = ("/*q:cli_token_sin_fecha*/ SELECT ISNULL(MAX(ISNULL(Id_Cliente,0)),0) "
                                   "FROM Clientes_Links WHERE Token = LOWER(LTRIM(RTRIM(?)))")
SQL_TOKEN_VENCIDO = ("/*q:token_vencido*/ SELECT TOP 1 ISNULL(Id_Cliente,0) FROM Clientes_Links "
                     "WHERE Token = LOWER(LTRIM(RTRIM(?))) ORDER BY Fecha_Gen DESC")
SQL_CLIENTE_POR_DOC = (
    "/*q:cli_doc*/ SELECT TOP 1 ISNULL(C.Id_Cliente,0) FROM Clientes C "
    "JOIN Clientes_Tipos T ON C.Id_Tipo_Cli = T.Id_Tipo_Cli WHERE C.Doc_Nro = ? "
    "ORDER BY C.Activo DESC, CASE T.Flag_Tipo WHEN 'P' THEN 1 WHEN 'S' THEN 2 WHEN '' THEN 3 WHEN 'N' THEN 4 ELSE 5 END, "
    "C.Ult_Cuota_Paga DESC"
)
SQL_PERMITIDO_MANUAL = ("/*q:permitido_manual*/ SELECT MAX(1) FROM CD_ES WHERE ISNULL(Flag_Permite_Paso,0) = 1 "
                        "AND CONVERT(DATE, Fecha) = ? AND Id_Cliente = ?")
SQL_INVITADO = (
    "/*q:invitado*/ SELECT CASE WHEN EXISTS (SELECT 1 FROM Clientes_Invitados_Mov CIM "
    "LEFT JOIN CD_ES CES ON CIM.Id_ES = CES.Id_ES "
    "WHERE CONVERT(DATE, CIM.Fecha) <= ? AND CONVERT(DATE, ISNULL(CIM.Fecha_Hasta, CIM.Fecha)) >= ? "
    "AND CIM.Id_Cliente_D = ? AND (ISNULL(CIM.Id_ES,0) = 0 OR CES.Fecha >= DATEADD(SECOND, -?, ?))) THEN 1 ELSE 0 END"
)
SQL_PERSONA = ("/*q:persona*/ SELECT Id_Cliente, ISNULL(Activo,0), ISNULL(Foto,''), ISNULL(Id_Cliente_Ref,0), "
               "ISNULL(Id_Tipo_Cli,0), Fecha_Nac, ISNULL(Sexo,''), ISNULL(Ult_Cuota_Paga,'19000101') "
               "FROM Clientes WHERE Id_Cliente = ?")
SQL_BAJA_REVISION = ("/*q:baja_revision*/ SELECT MAX(1) FROM CD_Clientes_Baja_Revision "
                     "WHERE Id_Cliente = ? AND ISNULL(Activo,1) = 1")
SQL_DEUDA_ACTIVIDADES = ("/*q:deuda_actividades*/ SELECT MAX(1) FROM CD_Clientes_Deuda_Actividades "
                         "WHERE Id_Cliente = ? AND ISNULL(Activo,1) = 1 AND ISNULL(Bloquea,0) = 1")
SQL_VENC_NO_EXISTE = ("/*q:venc_no_existe*/ SELECT ISNULL(MAX(Id_Tipo_Venc),'') FROM CD_Accesos_Venc WHERE Id_Acceso = ? "
                      "AND Id_Tipo_Venc NOT IN (SELECT DISTINCT Id_Tipo_Venc FROM Clientes_Venc WHERE Id_Cliente = ?)")
SQL_VENC_VENCIDO = ("/*q:venc_vencido*/ SELECT ISNULL(MAX(CV.Id_Tipo_Venc),'') FROM Clientes_Venc CV, CD_Accesos_Venc CAV "
                    "WHERE CV.Id_Tipo_Venc = CAV.Id_Tipo_Venc AND CAV.Id_Acceso = ? AND CV.Id_Cliente = ? "
                    "AND CONVERT(DATE, ISNULL(CV.Fecha,'19000101')) < ?")
SQL_VENC_DESCRIPCION = ("/*q:venc_descripcion*/ SELECT SUBSTRING(RTRIM(LTRIM(Descripcion)),1,16) FROM Clientes_Venc_Tipos "
                        "WHERE Id_Tipo_Venc = ?")
SQL_VENC_FECHA = "/*q:venc_fecha*/ SELECT CONVERT(VARCHAR(10), Fecha, 105) FROM Clientes_Venc WHERE Id_Cliente = ? AND Id_Tipo_Venc = ?"
SQL_CONTRATO = (
    "/*q:contrato*/ SELECT ISNULL(MAX(CO.Id_Contrato),0) FROM Contratos CO "
    "JOIN CD_Accesos_Cont_Tipos CACT ON CO.Id_Tipo_Con = CACT.Id_Tipo_Con AND CACT.Id_Acceso = ? "
    "WHERE CO.Id_Cliente = ? AND ISNULL(CACT.Flag_Habilitado,'H') = ? {activo}"
    "AND CO.Fecha_Desde < DATEADD(DAY, 1, ?) AND (CO.Fecha_Hasta IS NULL OR CO.Fecha_Hasta >= ?)"
)
SQL_CONTRATO_RECHAZO = SQL_CONTRATO.format(activo="AND CO.Activo = 1 ").replace("q:contrato", "q:contrato_rechazo")
SQL_CONTRATO_HABIL = SQL_CONTRATO.format(activo="")
SQL_CONTRATO_DESCRIPCION = ("/*q:contrato_descripcion*/ SELECT RTRIM(LTRIM(CT.Descripcion)) FROM Contratos_Tipos CT "
                            "JOIN Contratos CO ON CT.Id_Tipo_Con = CO.Id_Tipo_Con WHERE CO.Id_Contrato = ?")
SQL_TIPO_DESCRIPCION = "/*q:tipo_descripcion*/ SELECT RTRIM(LTRIM(Descripcion)) FROM Clientes_Tipos WHERE Id_Tipo_Cli = ?"
SQL_CATEG_HIST = ("/*q:categ_hist*/ SELECT TOP 1 Id_Tipo_Cli FROM Clientes_Hist WHERE Id_Cliente = ? AND Fecha <= ? "
                  "ORDER BY Fecha DESC")
SQL_CATEG_TIPO = ("/*q:categ_tipo*/ SELECT ISNULL(Hasta_Edad,0), ISNULL(Id_Tipo_Cli_Sig,0), ISNULL(Id_Tipo_Cli_Sig_Fem,0) "
                  "FROM Clientes_Tipos WHERE Id_Tipo_Cli = ?")
SQL_MASTER = "/*q:master*/ SELECT ISNULL(MAX(Id_Cliente),0) FROM CD_Cli_Master WHERE Id_Cliente = ?"
SQL_ANTIPASSBACK = ("/*q:antipassback*/ SELECT ISNULL(MAX(CE.Tipo),'S') FROM CD_ES CE WHERE CE.Id_Cliente = ? "
                    "AND CE.Resultado = 'S' AND CE.Fecha <= DATEADD(SECOND, -?, ?) AND CE.Fecha >= DATEADD(HOUR, -?, ?)")
SQL_FOTO_BASE = "/*q:foto_base*/ SELECT MAX(1) FROM Clientes_Fotos WHERE Id_Cliente = ?"
SQL_TURNO_TITULAR = (
    "/*q:turno_titular*/ SELECT MAX(1) FROM CRM_Turnos WHERE Id_Cliente = ? AND Fecha <= ? AND Fecha >= ? "
    "AND Id_Recurso IN (SELECT Id_Recurso FROM Empresas_Sedes_Sec_Recursos WHERE Id_Sector = ? AND ISNULL(Activo,0) = 1)"
)
SQL_TURNO_INTEGRANTE = (
    "/*q:turno_integrante*/ SELECT MAX(1) FROM CRM_Turnos CT JOIN CRM_Turnos_Clientes CTC ON CT.Id_Turno = CTC.Id_Turno "
    "WHERE CTC.Id_Cliente = ? AND CT.Fecha <= ? AND CT.Fecha >= ? "
    "AND Id_Recurso IN (SELECT Id_Recurso FROM Empresas_Sedes_Sec_Recursos WHERE Id_Sector = ? AND ISNULL(Activo,0) = 1)"
)
SQL_TIPO_FLAG = "/*q:tipo_flag*/ SELECT ISNULL(Flag_Tipo,'') FROM Clientes_Tipos WHERE Id_Tipo_Cli = ?"
SQL_LINK = ("/*q:link*/ SELECT ISNULL(MIN(Id_Link),0) FROM Clientes_Links WHERE Id_Cliente = ? AND Fecha_Desde <= ? "
            "AND (Fecha_Hasta IS NULL OR Fecha_Hasta >= ?) AND (Cant_Usos_Permitidos - Cant_Usos_Realizados) > 0")
SQL_PRODUCTO_DESCRIPCION = "/*q:producto_descripcion*/ SELECT ISNULL(Descripcion_Resumida, Id_Producto) FROM Productos WHERE Id_Producto = ?"
SQL_CATEG_HORARIO = (
    "/*q:categ_horario*/ SELECT ISNULL(MAX(Id_Tipo_Cli),0) FROM CD_Accesos_Horarios WHERE Id_Acceso = ? "
    "AND ISNULL(Id_Producto,'') = '' AND Id_Tipo_Cli = ? AND Dia = ? "
    "AND ((Hora_Desde IS NULL AND Hora_Hasta IS NULL) OR (CAST(? AS TIME) BETWEEN CAST(Hora_Desde AS TIME) AND CAST(Hora_Hasta AS TIME)))"
)
SQL_KIT_REMITO_HOY = (
    "/*q:kit_remito*/ SELECT COUNT(*) FROM Cbtes C JOIN Cbtes_Tipos T ON C.Id_Tipo_Cbte = T.Id_Tipo_Cbte "
    "JOIN Cbtes_Items I ON C.Id_Trans = I.Id_Trans WHERE T.Compromete_Remitos IN (1,-1) AND C.Id_Estado_Cbte = 2 "
    "AND I.Id_Producto = ? AND I.Id_Cliente = ? AND CONVERT(DATE, C.Fecha) = ?"
)
SQL_KIT_SALDO = (
    "/*q:kit_saldo*/ SELECT TOP 1 ISNULL(CI.Cantidad_Saldo,0) FROM Cbtes_Items CI JOIN Cbtes CB ON CI.Id_Trans = CB.Id_Trans "
    "JOIN Cbtes_Tipos CT ON CT.Id_Tipo_Cbte = CB.Id_Tipo_Cbte WHERE CI.Id_Cliente = ? AND CI.Id_Producto = ? "
    "AND ((CT.Compromete_Factura = 1 AND CB.Id_Estado_Cbte IN (4,2)) OR (CB.Id_Estado_Cbte IN (1) AND CI.Imp_Final = 0)) "
    "AND CI.Cantidad_Saldo > 0 AND CONVERT(DATE, CI.Fecha_Venc) >= ? AND CONVERT(DATE, CI.Fecha_QA) <= ? "
    "ORDER BY CB.Id_Trans ASC"
)

# Comprobantes que cuentan como "producto comprado" (CP_SCA_ValidarProducVta con
# SCA_Perm_ProdXPed = 0) y que no fueron anulados por una nota de crédito del
# mismo importe.
_SQL_PROD_BASE = (
    "SELECT ISNULL(MAX(CI.Id_Producto),'') FROM Cbtes_Items CI, Cbtes CB, Cbtes_Tipos CT, CD_Accesos_Prod CA{join_horario}, Productos PR "
    "WHERE CI.Id_Trans = CB.Id_Trans AND CT.Id_Tipo_Cbte = CB.Id_Tipo_Cbte AND CA.Id_Acceso = ? AND CA.Id_Producto = CI.Id_Producto "
    "AND CI.Id_Cliente = ? AND PR.Id_Producto = CA.Id_Producto {horario} "
    "AND ((CT.Compromete_Factura = 1 AND CB.Id_Estado_Cbte IN (4, 2)) OR (CB.Id_Estado_Cbte IN (1) AND CI.Imp_Final = 0)) "
    "AND NOT EXISTS (SELECT 1 FROM Cbtes_Aplica CAFR JOIN Cbtes_Aplica CANR ON CANR.Id_Trans_D = CAFR.Id_Trans_D AND CANR.Importe = CAFR.Importe "
    "JOIN Cbtes CBF ON CBF.Id_Trans = CAFR.Id_Trans JOIN Cbtes_Tipos CTF ON CTF.Id_Tipo_Cbte = CBF.Id_Tipo_Cbte AND CTF.Compromete_Factura = 1 "
    "JOIN Cbtes CBN ON CBN.Id_Trans = CANR.Id_Trans JOIN Cbtes_Tipos CTN ON CTN.Id_Tipo_Cbte = CBN.Id_Tipo_Cbte AND CTN.Compromete_Factura = -1 "
    "WHERE CAFR.Id_Trans = CB.Id_Trans) {titular} {consumible}"
)
_HORARIO_FILTRO = ("AND CAH.Dia = ? AND ((CAH.Hora_Desde IS NULL AND CAH.Hora_Hasta IS NULL) "
                   "OR (CAST(? AS TIME) BETWEEN CAST(CAH.Hora_Desde AS TIME) AND CAST(CAH.Hora_Hasta AS TIME)))")


def _sql_producto(*, horario: bool, titular: bool, consumible: bool, variante: str) -> str:
    """Arma las variantes de CP_SCA_ValidarProducVta / _Kit sin SQL dinámico."""
    base = _SQL_PROD_BASE.format(
        join_horario=", CD_Accesos_Horarios CAH" if horario else "",
        horario="AND CA.Id_Producto = CAH.Id_Producto AND CA.Id_Acceso = CAH.Id_Acceso" if horario else "",
        titular="" if horario else ("AND ISNULL(CA.Valida_En_Titular,0) = 1" if titular
                                    else ("AND ISNULL(CA.Valida_En_Titular,0) = 0" if consumible
                                          else "AND ISNULL(CA.Valida_En_Titular,0) IN (1,0)")),
        consumible="" if horario else ("AND CA.Flag_Consumible = 1" if consumible else "AND ISNULL(CA.Flag_Consumible,0) = 0"),
    )
    if variante == "kit":
        filtro = ""
    elif variante == "sin_periodo":
        filtro = " AND ISNULL(PR.Flag_Mes,0) = 0 AND ISNULL(PR.Flag_Periodo,0) = 0"
    elif variante == "mes":
        filtro = (" AND ISNULL(PR.Flag_Mes,0) = 1 AND ? <= DATEADD(DAY, ISNULL(CA.Dias_Gracia,0), "
                  "DATEADD(DAY, 1, EOMONTH(DATEADD(MONTH, ISNULL(CA.Meses_Gracia,0), CONVERT(date, CI.Fecha_QA)), -1)))")
    elif variante == "periodo" and not horario:
        filtro = (" AND ISNULL(PR.Flag_Periodo,0) = 1 AND CI.Fecha_QA < ? "
                  "AND CONVERT(VARCHAR(8), DATEADD(DAY, CA.Dias_Gracia, CASE WHEN CA.Meses_Gracia IS NULL THEN CI.Fecha_Venc "
                  "ELSE EOMONTH(CI.Fecha_Venc, CA.Meses_Gracia) END), 112) > ?")
    else:  # periodo con horario
        filtro = " AND ISNULL(PR.Flag_Periodo,0) = 1 AND CI.Fecha_QA < ? AND CI.Fecha_Venc > ?"
    if horario:
        filtro += " " + _HORARIO_FILTRO
    return f"/*q:producto_{variante}{'_horario' if horario else ''}{'_titular' if titular else ''}*/ " + base + filtro


# ----------------------------------------------------------------------------- funciones puras

def dia_semana_clever(d: date | datetime) -> int:
    """CD_Accesos_Horarios.Dia: 0 domingo … 6 sábado (DATEPART(DW) con DATEFIRST 7, menos 1)."""
    return (d.weekday() + 1) % 7


def fin_de_mes(d: date, meses: int = 0) -> date:
    """EOMONTH(d, meses)."""
    m0 = (d.month - 1) + meses
    y = d.year + m0 // 12
    m = (m0 % 12) + 1
    return date(y, m, calendar.monthrange(y, m)[1])


def edad_categ(fecha: date, fecha_nac: date) -> int:
    """SF_Edad_Categ: edad en años comparando los meses, con el día en 1."""
    edad = fecha.year - fecha_nac.year
    if fecha.month == fecha_nac.month or fecha_nac.month > fecha.month:
        edad -= 1
    return edad


def limite_cuota(ult_cuota_paga: date, dias_gracia: int, meses_gracia: int) -> date:
    """CF_SCA_ValidarUltCuotaPaga: DATEADD(DAY, dias, EOMONTH(ucp, meses))."""
    return fin_de_mes(ult_cuota_paga, meses_gracia) + timedelta(days=dias_gracia)


def es_numerico(dato: str) -> bool:
    """CF_IsNumeric: todos los caracteres pasan ISNUMERIC de a uno (dígitos y signos)."""
    return all(ch.isdigit() or ch in "+-.,$" for ch in dato)


def _as_date(v) -> date | None:
    if v is None:
        return None
    return v.date() if isinstance(v, datetime) else v


# ----------------------------------------------------------------------------- validador

class ValidadorLocal:
    """Evalúa una lectura contra la cascada de Clever leyendo xSys sin escribir nada.

    ``ejecutar(sql, params) -> list[tuple]`` se puede inyectar (pruebas). El
    acceso y los motivos se cachean ``cache_seg`` segundos. Seguro para un hilo
    a la vez (lock interno).
    """

    def __init__(self, *, config: dict[str, Any] | None = None, query_timeout: int = 4,
                 ejecutar: Callable[[str, tuple], list[tuple]] | None = None, cache_seg: int = 60) -> None:
        self._config = dict(config or mssql.get_config())
        self._config.setdefault("QUERY_TIMEOUT", query_timeout)
        self._conn = None
        self._lock = threading.Lock()
        self._ejecutar = ejecutar or self._ejecutar_sql
        self._cache_seg = cache_seg
        self._accesos: dict[int, tuple[float, ConfigAcceso | None]] = {}
        self._motivos: tuple[float, dict[int, tuple[str, str, str, str]]] | None = None
        self._params: dict[str, tuple[float, str]] = {}

    # -- conexión -------------------------------------------------------------

    def _connection(self):
        if self._conn is None:
            self._conn = mssql.connect(self._config)
            try:
                self._conn.autocommit = True
            except Exception:  # noqa: BLE001
                pass
        return self._conn

    def cerrar(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _ejecutar_sql(self, sql: str, params: tuple) -> list[tuple]:
        for intento in (1, 2):
            try:
                cur = self._connection().cursor()
                try:
                    cur.execute(sql, params)
                    return [tuple(r) for r in cur.fetchall()]
                finally:
                    cur.close()
            except Exception:
                self.cerrar()
                if intento == 2:
                    raise
        return []  # pragma: no cover

    def _escalar(self, sql: str, *params) -> Any:
        filas = self._ejecutar(sql, tuple(params))
        return filas[0][0] if filas else None

    # -- catálogos cacheados ---------------------------------------------------

    def xparam(self, nombre: str) -> str:
        """CF_xParam: el valor según TipoDato, como texto ('' si no existe)."""
        ahora = time.monotonic()
        hit = self._params.get(nombre)
        if hit and ahora - hit[0] < self._cache_seg * 5:
            return hit[1]
        filas = self._ejecutar(SQL_XPARAM, (nombre,))
        valor = ""
        if filas:
            tipo, v_str, v_int, v_dec, v_memo = filas[0]
            tipo = (tipo or "").upper()
            if tipo == "INTEGER":
                valor = str(v_int if v_int is not None else 0)
            elif tipo == "DECIMAL":
                valor = str(v_dec if v_dec is not None else 0)
            elif tipo == "STRING":
                valor = v_str or ""
            elif tipo == "MEMO":
                valor = v_memo or ""
        self._params[nombre] = (ahora, valor)
        return valor

    def _xparam_int(self, nombre: str, default: int) -> int:
        v = self.xparam(nombre).strip()
        try:
            return int(float(v)) if v else default
        except ValueError:
            return default

    def motivos(self) -> dict[int, tuple[str, str, str, str]]:
        ahora = time.monotonic()
        if self._motivos and ahora - self._motivos[0] < self._cache_seg * 5:
            return self._motivos[1]
        tabla = {int(r[0]): (r[1] or "", r[2] or "", r[3] or "", r[4] or "") for r in self._ejecutar(SQL_MOTIVOS, ())}
        self._motivos = (ahora, tabla)
        return tabla

    def id_acceso_de(self, id_controlador: int) -> int:
        return int(self._escalar(SQL_ID_ACCESO, int(id_controlador)) or 0)

    def config_acceso(self, id_acceso: int) -> ConfigAcceso | None:
        ahora = time.monotonic()
        hit = self._accesos.get(id_acceso)
        if hit and ahora - hit[0] < self._cache_seg:
            return hit[1]
        filas = self._ejecutar(SQL_ACCESO, (id_acceso,))
        cfg = None
        if filas:
            r = filas[0]
            cfg = ConfigAcceso(
                id_acceso=int(r[0]), descripcion=(r[1] or "").strip(), activo=int(r[2]), flag_evento=int(r[3]),
                flag_ucp=int(r[4]), dias_gracia=int(r[5]), meses_gracia=int(r[6]), horas_antipassback=int(r[7]),
                tiempo_antirebote=int(r[8]), flag_foto_validar=int(r[9]), flag_links_validar=int(r[10]),
                tiempo_mensaje=int(r[11] or 1),
                tipo_controlador=(self._escalar(SQL_TIPO_CONTROLADOR, id_acceso) or "E"),
                cont_tipos={int(a): (b or "H") for a, b in self._ejecutar(SQL_CONT_TIPOS, (id_acceso,))},
                cli_tipos={int(a): (b or "H") for a, b in self._ejecutar(SQL_CLI_TIPOS, (id_acceso,))},
                venc_tipos=[str(a) for (a,) in self._ejecutar(SQL_VENC_TIPOS, (id_acceso,))],
                tiene_horarios=bool(self._escalar(SQL_HORARIOS, id_acceso)),
                sectores_turno=[(int(a), int(b), int(c)) for a, b, c in self._ejecutar(SQL_SECTORES, (id_acceso,))],
                motivos_acceso={int(a): (b or "") for a, b in self._ejecutar(SQL_MOTIVOS_ACCESO, (id_acceso,))},
            )
        self._accesos[id_acceso] = (ahora, cfg)
        return cfg

    # -- mensaje (CP_SCA_RegistrarAcceso_Rta) ---------------------------------

    def mensaje_para(self, cfg: ConfigAcceso | None, id_motivo: int, detalle: str) -> str:
        descripcion, display, pantalla, personalizada = self.motivos().get(id_motivo, ("", "", "", ""))
        por_acceso = cfg.motivos_acceso.get(id_motivo, "") if cfg else ""
        mensaje = pantalla or display or por_acceso or personalizada or descripcion
        if detalle:
            mensaje = f"{mensaje} {detalle}"
        return mensaje.strip()

    # -- identificación (CF_SCA_IdentifIdCliente) ----------------------------

    def identificar_cliente(self, tag: str, cuando: datetime) -> int:
        dato = (tag or "").strip()
        if not dato:
            return 0
        qr_dinamico = self._xparam_int("QR_Dinamico", 0) == 1
        desde = cuando - timedelta(minutes=TOKEN_QR_MINUTOS)

        def por_credencial() -> int:
            return int(self._escalar(SQL_CLIENTE_POR_CREDENCIAL, dato) or 0)

        def por_token(vigente: bool) -> int:
            token = dato[:10]
            if vigente:
                return int(self._escalar(SQL_CLIENTE_POR_TOKEN, token, desde) or 0)
            return int(self._escalar(SQL_CLIENTE_POR_TOKEN_SIN_FECHA, token) or 0)

        if len(dato) > 60:
            # Lectura de DNI escaneado: CF_IdentifDNI no se replica (esta placa no lo lee).
            if qr_dinamico:
                return por_token(True) or por_credencial()
            return por_credencial()

        if not es_numerico(dato):
            # Credencial Mifare (hex de 8).
            return por_credencial() or (por_token(True) if qr_dinamico else por_credencial())

        if int(dato.replace(",", "").replace(".", "") or 0) < 3_000_000_000:
            # Documento.
            return int(self._escalar(SQL_CLIENTE_POR_DOC, int(dato)) or 0) or por_credencial()

        # QR Intelektron numérico de 10 que empieza con 9.
        if qr_dinamico:
            return por_token(True) or por_credencial()
        return por_token(False)

    # -- categoría a la fecha (SF_Categ) -------------------------------------

    def categoria(self, id_cliente: int, cuando: datetime, *, id_tipo_cli: int, fecha_nac, sexo: str) -> int:
        hoy = ahora_local().date().replace(day=1)
        if cuando.date() < hoy:
            hist = self._escalar(SQL_CATEG_HIST, id_cliente, cuando)
            if hist:
                return int(hist)
        if not id_tipo_cli:
            return 0
        filas = self._ejecutar(SQL_CATEG_TIPO, (id_tipo_cli,))
        if not filas:
            return id_tipo_cli
        hasta, sig, sig_fem = (int(x or 0) for x in filas[0])
        if not sig or not hasta:
            return id_tipo_cli
        nac = _as_date(fecha_nac) or date(1900, 1, 1)
        if edad_categ(cuando.date(), nac) > hasta:
            return sig_fem if (sexo == "F" and sig_fem) else sig
        return id_tipo_cli

    # -- cuota (CF_SCA_ValidarUltCuotaPaga) -----------------------------------

    def cuota_al_dia(self, cfg: ConfigAcceso, cuando: datetime, *, id_tipo_cli: int, ult_cuota_paga) -> bool:
        if cfg.flag_ucp == 0:
            return False
        if cfg.flag_ucp == 2:
            flag_tipo = (self._escalar(SQL_TIPO_FLAG, id_tipo_cli) or "").strip() if id_tipo_cli else ""
            if flag_tipo not in ("P", "S") or id_tipo_cli in TIPOS_SOCIO_SIN_CUOTA:
                return True
        ucp = _as_date(ult_cuota_paga) or date(1900, 1, 1)
        return limite_cuota(ucp, cfg.dias_gracia, cfg.meses_gracia) >= cuando.date()

    # -- antipassback (CF_SCA_Antipassback, modo normal) ---------------------

    def antipassback(self, cfg: ConfigAcceso, id_cliente: int, cuando: datetime, tipo_acceso: str) -> str:
        if cfg.horas_antipassback == 0 or self._escalar(SQL_MASTER, id_cliente):
            return "O"
        ultimo = self._escalar(SQL_ANTIPASSBACK, id_cliente, cfg.tiempo_antirebote // 100, cuando,
                               cfg.horas_antipassback, cuando) or "S"
        if ultimo != tipo_acceso:
            return "O"
        if tipo_acceso == "E":
            return "E" if cfg.tipo_controlador == "A" else "Y"
        return "S"

    # -- turno (CP_SCA_ValidarTurno) -----------------------------------------

    def tiene_turno(self, cfg: ConfigAcceso, id_cliente: int, cuando: datetime) -> bool:
        if not cfg.sectores_turno:
            return True
        for id_sector, prev, post in cfg.sectores_turno:
            desde = cuando + timedelta(minutes=prev)
            hasta = cuando - timedelta(minutes=post)
            if self._escalar(SQL_TURNO_TITULAR, id_cliente, desde, hasta, id_sector):
                return True
            if self._escalar(SQL_TURNO_INTEGRANTE, id_cliente, desde, hasta, id_sector):
                return True
        return False

    # -- productos (CP_SCA_ValidarProducVta / _Kit) ---------------------------

    def producto_comprado(self, cfg: ConfigAcceso, id_cliente: int, cuando: datetime, *, titular: bool,
                          horario: bool) -> str:
        if self._xparam_int("SCA_Perm_ProdXPed", 0) != 0:
            logger.warning("SCA_Perm_ProdXPed <> 0: la réplica sólo contempla comprobantes facturados")
        fecha = cuando.date()
        base = (cfg.id_acceso, id_cliente)
        extra_h = (dia_semana_clever(cuando), cuando.strftime("%H:%M:%S")) if horario else ()
        # El SP compara con literales 'YYYYMMDD' (CONVERT(..., 112)) de fecha+1 y fecha-1.
        manana, ayer = (fecha + timedelta(days=1)).strftime("%Y%m%d"), (fecha - timedelta(days=1)).strftime("%Y%m%d")
        variantes = (("sin_periodo", ()), ("mes", (fecha,)), ("periodo", (manana, ayer)))
        for variante, params in variantes:
            sql = _sql_producto(horario=horario, titular=titular, consumible=False, variante=variante)
            prod = (self._escalar(sql, *base, *params, *extra_h) or "").strip()
            if prod:
                return prod
        return ""

    def kit_comprado(self, cfg: ConfigAcceso, id_cliente: int, cuando: datetime) -> str:
        """CP_SCA_ValidarProducVta_Kit sin generar el remito."""
        sql = _sql_producto(horario=False, titular=False, consumible=True, variante="kit")
        prod = (self._escalar(sql, cfg.id_acceso, id_cliente) or "").strip()
        if not prod:
            return ""
        fecha = cuando.date()
        if self._escalar(SQL_KIT_REMITO_HOY, prod, id_cliente, fecha):
            return prod
        saldo = int(self._escalar(SQL_KIT_SALDO, id_cliente, prod, fecha, fecha) or 0)
        if saldo > 0:
            logger.warning("kit %s del socio %s habilitado SIN consumir la unidad (la réplica no genera remitos)", prod, id_cliente)
            return prod
        return ""

    # -- cascada ----------------------------------------------------------------

    def decidir(self, *, tipo_acceso: str, tag: str, id_controlador: int | None = None,
                id_acceso: int | None = None, cuando: datetime | None = None) -> DecisionLocal:
        inicio = time.perf_counter()
        cuando = (cuando or ahora_local()).replace(microsecond=0)
        with self._lock:
            try:
                return self._decidir(tipo_acceso, tag, id_controlador, id_acceso, cuando, inicio)
            except Exception as exc:  # noqa: BLE001 - red, timeout, SQL
                logger.warning("validador local falló: %s", exc)
                return DecisionLocal(False, "SIN CONEXION", "ERROR", 0, 0, 1, error=str(exc)[:200],
                                     duracion_ms=(time.perf_counter() - inicio) * 1000, id_motivo=0,
                                     id_acceso=int(id_acceso or 0))

    def _decidir(self, tipo_acceso, tag, id_controlador, id_acceso, cuando, inicio) -> DecisionLocal:
        traza: list[str] = []
        if id_acceso is None:
            id_acceso = self.id_acceso_de(int(id_controlador or 0))
        cfg = self.config_acceso(int(id_acceso))

        def rta(habilitado: bool, motivo: int, info: str, id_cliente: int = 0, id_cliente_ref: int = 0,
                detalle: str = "") -> DecisionLocal:
            return DecisionLocal(
                habilitado=habilitado, mensaje=self.mensaje_para(cfg, motivo, detalle), info=info,
                id_cliente=int(id_cliente or 0), id_cliente_ref=int(id_cliente_ref or 0),
                tiempo_mensaje=cfg.tiempo_mensaje if cfg else 1,
                duracion_ms=(time.perf_counter() - inicio) * 1000, id_motivo=motivo, detalle=detalle,
                id_acceso=int(id_acceso), traza=traza,
            )

        tag = (tag or "").strip()
        if not tag:
            return rta(False, 101, "ERROR")
        if cfg is None:
            return rta(False, 102, "ERROR")
        if cfg.flag_evento:
            d = rta(False, 102, "ERROR")
            d.error = "acceso de evento (Flag_Evento = 1): la réplica local no lo contempla"
            return d
        if len(tag) == 10 and es_numerico(tag) and tag[0] == "9":
            # CleverQR (CP_SCA_ValidoCleverQR): esta placa no lee QR. Se deja
            # seguir por identificación de token, como el SP cuando el QR es válido.
            traza.append("qr")

        id_cliente = self.identificar_cliente(tag, cuando)
        traza.append(f"cliente={id_cliente}")
        fecha = cuando.date()

        if id_cliente and self._escalar(SQL_PERMITIDO_MANUAL, fecha, id_cliente):
            return rta(True, 211, "ADVER", id_cliente)
        if id_cliente:
            limite = self._xparam_int("SCA_Visit_TmpLimit", 0)
            if self._escalar(SQL_INVITADO, fecha, fecha, id_cliente, limite, cuando):
                return rta(True, 217, "ADVER", id_cliente)

        persona = self._ejecutar(SQL_PERSONA, (id_cliente,)) if id_cliente else []
        if not persona:
            if len(tag) >= 10 and es_numerico(tag[:10]):
                vencido = int(self._escalar(SQL_TOKEN_VENCIDO, tag[:10]) or 0)
                if vencido:
                    return rta(False, 115, "ERROR", vencido)
            return rta(False, 103, "ERROR")
        _, activo, _foto, id_cliente_ref, id_tipo_cli, fecha_nac, sexo, ucp = persona[0]
        activo, id_cliente_ref, id_tipo_cli = int(activo), int(id_cliente_ref), int(id_tipo_cli)
        sexo = (sexo or "").strip()

        # ---- rechazos ----
        if activo == 0 and not self._escalar(SQL_BAJA_REVISION, id_cliente):
            return rta(False, 104, "ADVER", id_cliente, id_cliente_ref)
        if self._escalar(SQL_DEUDA_ACTIVIDADES, id_cliente):
            return rta(False, 118, "ERROR", id_cliente, id_cliente_ref)
        if cfg.venc_tipos:
            venc = (self._escalar(SQL_VENC_NO_EXISTE, cfg.id_acceso, id_cliente) or "").strip()
            if not venc:
                venc = (self._escalar(SQL_VENC_VENCIDO, cfg.id_acceso, id_cliente, fecha) or "").strip()
            if venc:
                desc = (self._escalar(SQL_VENC_DESCRIPCION, venc) or "").strip()
                fv = self._escalar(SQL_VENC_FECHA, id_cliente, venc)
                detalle = f"{desc} vencido el: {fv}" if fv else ""   # texto + NULL = NULL en T-SQL
                return rta(False, 105, "ADVER", id_cliente, id_cliente_ref, detalle)
        if any(f == "R" for f in cfg.cont_tipos.values()):
            contrato = int(self._escalar(SQL_CONTRATO_RECHAZO, cfg.id_acceso, id_cliente, "R", cuando, cuando) or 0)
            if contrato:
                desc = (self._escalar(SQL_CONTRATO_DESCRIPCION, contrato) or "").strip()
                return rta(False, 106, "ADVER", id_cliente, id_cliente_ref, desc)
        categ = None
        if any(f == "R" for f in cfg.cli_tipos.values()):
            categ = self.categoria(id_cliente, cuando, id_tipo_cli=id_tipo_cli, fecha_nac=fecha_nac, sexo=sexo)
            traza.append(f"categ={categ}")
            if cfg.cli_tipos.get(categ) == "R":
                # El SP arma la descripción con @ValidarTipo, que a esa altura es NULL: sin detalle.
                return rta(False, 107, "OBSER", id_cliente, id_cliente_ref)
        apb = self.antipassback(cfg, id_cliente, cuando, tipo_acceso)
        if apb != "O":
            return rta(False, {"Y": 108, "E": 109, "S": 110}[apb], "ADVER", id_cliente, id_cliente_ref)
        if cfg.flag_foto_validar == 1:
            if self._xparam_int("Cli_Fotos_Base", 0) == 1:
                con_foto = bool(self._escalar(SQL_FOTO_BASE, id_cliente))
            else:
                con_foto = bool((_foto or "").strip())   # el SP mira el archivo con xp_dirtree
            if not con_foto:
                return rta(False, 111, "ADVER", id_cliente, id_cliente_ref)
        if tipo_acceso == "E" and not self.tiene_turno(cfg, id_cliente, cuando):
            return rta(False, 113, "ADVER", id_cliente, id_cliente_ref)
        if cfg.flag_ucp == 2 and not self.cuota_al_dia(cfg, cuando, id_tipo_cli=id_tipo_cli, ult_cuota_paga=ucp):
            return rta(False, 309, "ADVER", id_cliente, id_cliente_ref)

        # ---- habilitaciones ----
        if self._escalar(SQL_MASTER, id_cliente):
            return rta(True, 202, "OBSER", id_cliente, id_cliente_ref)
        if cfg.flag_links_validar == 1 and self._escalar(SQL_LINK, id_cliente, cuando, cuando):
            return rta(True, 212, "OBSER", id_cliente, id_cliente_ref)
        if cfg.flag_ucp == 1 and self.cuota_al_dia(cfg, cuando, id_tipo_cli=id_tipo_cli, ult_cuota_paga=ucp):
            return rta(True, 203, "OBSER", id_cliente, id_cliente_ref)
        if any(f == "H" for f in cfg.cont_tipos.values()):
            contrato = int(self._escalar(SQL_CONTRATO_HABIL, cfg.id_acceso, id_cliente, "H", cuando, cuando) or 0)
            if contrato:
                desc = (self._escalar(SQL_CONTRATO_DESCRIPCION, contrato) or "").strip()
                return rta(True, 204, "OBSER", id_cliente, id_cliente_ref, desc)
        if any(f == "H" for f in cfg.cli_tipos.values()):
            if categ is None:
                categ = self.categoria(id_cliente, cuando, id_tipo_cli=id_tipo_cli, fecha_nac=fecha_nac, sexo=sexo)
                traza.append(f"categ={categ}")
            if cfg.cli_tipos.get(categ) == "H":
                desc = (self._escalar(SQL_TIPO_DESCRIPCION, categ) or "").strip()
                return rta(True, 205, "OBSER", id_cliente, id_cliente_ref, desc)
        prod = self.kit_comprado(cfg, id_cliente, cuando)
        if prod:
            return rta(True, 310, "OBSER", id_cliente, id_cliente_ref, self._desc_producto(prod))
        prod = self.producto_comprado(cfg, id_cliente, cuando, titular=False, horario=False)
        if prod:
            return rta(True, 206, "OBSER", id_cliente, id_cliente_ref, self._desc_producto(prod))
        if id_cliente_ref:
            prod = self.producto_comprado(cfg, id_cliente_ref, cuando, titular=True, horario=False)
            if prod:
                return rta(True, 207, "OBSER", id_cliente, id_cliente_ref, self._desc_producto(prod))
        if cfg.tiene_horarios:
            tipo_h = int(self._escalar(SQL_CATEG_HORARIO, cfg.id_acceso, id_tipo_cli, dia_semana_clever(cuando),
                                       cuando.strftime("%H:%M:%S")) or 0)
            if tipo_h:
                desc = (self._escalar(SQL_TIPO_DESCRIPCION, tipo_h) or "").strip()
                return rta(True, 209, "OBSER", id_cliente, id_cliente_ref, desc)
            prod = self.producto_comprado(cfg, id_cliente, cuando, titular=False, horario=True)
            if prod:
                return rta(True, 210, "OBSER", id_cliente, id_cliente_ref, self._desc_producto(prod))
        return rta(False, 112, "ADVER", id_cliente, id_cliente_ref)

    def _desc_producto(self, id_producto: str) -> str:
        return (self._escalar(SQL_PRODUCTO_DESCRIPCION, id_producto) or id_producto).strip()


__all__ = ["ValidadorLocal", "DecisionLocal", "ConfigAcceso", "ahora_local", "dia_semana_clever",
           "fin_de_mes", "edad_categ", "limite_cuota", "es_numerico"]
