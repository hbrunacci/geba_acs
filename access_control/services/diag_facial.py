"""Diagnóstico de acceso por facial de un socio, como datos estructurados.

Responde las preguntas de todo reclamo de "no me toma el facial": quién es,
qué foto tiene, si está en BioStar con rostro y grupo, si está cargado en cada
equipo o fue borrado, qué eventos/errores registró y sus últimos accesos.

Lo consume el management command ``diag_facial`` (salida de consola) y la vista
web ``diag_facial_view`` (misma información en pantalla). Es de **solo lectura**.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from django.conf import settings

# Prefijo del linked server de BioStar visto desde xSys.
BIOSTAR_PREFIX_DEFAULT = "[10.0.0.27].[BIOSTAR_AC].dbo"

# Grupo de acceso que habilita la entrada de socios (Cuota Social).
ACCESS_GROUP_SOCIOS = 2

# Umbrales para marcar una foto como sospechosa de ser rechazada por el equipo.
FOTO_ANTIGUA_ANIOS = 2
FOTO_CHICA_BYTES = 20_000

# Motivos de CD_ES que son rechazo (el resto habilita o es informativo).
MOTIVOS_RECHAZO = {103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 309}

# Un mismo DNI puede repetirse por error de carga. Pero hay documentos comodín
# (99999999 y similares) compartidos por cientos de socios sin DNI registrado:
# ahí no tiene sentido reportarlos a todos.
MAX_DUPLICADOS_LISTADOS = 8
MAX_DUPLICADOS_REPORTABLES = 5

# Eventos del log de BioStar que dejan al usuario cargado / lo sacan del equipo.
EVENTOS_CARGA = ("ENROLL_SUCCESS", "UPDATE_SUCCESS", "PARTIAL_UPDATE_SUCCESS")
EVENTOS_BORRADO = ("DELETE_SUCCESS",)


class DiagFacialError(RuntimeError):
    """No se pudo completar el diagnóstico (conexión, parámetros)."""


# --------------------------------------------------------------------------- #
# Conexión: pyodbc (producción) con fallback a pymssql (dev box sin ODBC 18)
# --------------------------------------------------------------------------- #


def conectar() -> tuple[Any, str]:
    """Devuelve (conexión, driver_usado). Prueba pyodbc y cae a pymssql."""

    try:
        from xsys.services.mssql import connect as _pyodbc_connect

        return _pyodbc_connect(), "pyodbc"
    except Exception as exc_pyodbc:
        try:
            import pymssql
        except ModuleNotFoundError:
            raise DiagFacialError(
                f"No hay pyodbc utilizable ({exc_pyodbc}) ni pymssql instalado."
            ) from exc_pyodbc

        cfg = getattr(settings, "MSSQL_XSYS", {}) or {}
        faltan = [k for k in ("HOST", "DATABASE", "USER", "PASSWORD") if not cfg.get(k)]
        if faltan:
            raise DiagFacialError("Faltan parámetros en MSSQL_XSYS: " + ", ".join(faltan))
        try:
            conn = pymssql.connect(
                server=cfg["HOST"],
                port=str(cfg.get("PORT") or 1433),
                user=cfg["USER"],
                password=cfg["PASSWORD"],
                database=cfg["DATABASE"],
                login_timeout=int(cfg.get("LOGIN_TIMEOUT", 20)),
                timeout=int(cfg.get("QUERY_TIMEOUT", 120)),
                tds_version="7.4",
            )
        except Exception as exc:
            raise DiagFacialError(
                f"No se pudo conectar a xSys ni por pyodbc ni por pymssql: {exc}. "
                "Si es la dev box, revisá la VPN y FREETDSCONF con encryption=off."
            ) from exc
        return conn, "pymssql"


def _rows(cursor, sql: str) -> list[dict]:
    """Ejecuta y devuelve filas como dicts, igual con pyodbc que con pymssql."""

    cursor.execute(sql)
    if cursor.description is None:
        return []
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, fila)) for fila in cursor.fetchall()]


def _tablas_log(dias: int, hoy: dt.date) -> list[str]:
    """Sufijos yyyymm de las tablas T_LG que cubren el rango (BioStar parte por mes)."""

    meses: list[str] = []
    cursor_fecha = hoy - dt.timedelta(days=dias)
    while cursor_fecha <= hoy:
        sufijo = cursor_fecha.strftime("%Y%m")
        if sufijo not in meses:
            meses.append(sufijo)
        if cursor_fecha.month == 12:
            cursor_fecha = cursor_fecha.replace(year=cursor_fecha.year + 1, month=1, day=1)
        else:
            cursor_fecha = cursor_fecha.replace(month=cursor_fecha.month + 1, day=1)
    return [m for m in meses if m.isdigit() and len(m) == 6]


def _fecha(valor) -> dt.date | None:
    if isinstance(valor, dt.datetime):
        return valor.date()
    if isinstance(valor, dt.date):
        return valor
    return None


# --------------------------------------------------------------------------- #
# Diagnóstico
# --------------------------------------------------------------------------- #


class DiagnosticadorFacial:
    def __init__(self, cursor, *, dias: int = 10, equipos: list[int] | None = None,
                 prefix: str = BIOSTAR_PREFIX_DEFAULT) -> None:
        self.cur = cursor
        self.dias = max(1, int(dias))
        self.prefix = prefix
        self.hoy = _fecha(_rows(cursor, "SELECT CONVERT(date, GETDATE()) AS hoy")[0]["hoy"])
        self.equipos = self._cargar_equipos(equipos or [])

    # ------------------------------------------------------------- catálogos --

    def _cargar_equipos(self, filtro: list[int]) -> dict[int, str]:
        sql = f"SELECT DEVID, NM, IP FROM {self.prefix}.T_DEV WHERE DEL <> 'Y'"
        equipos: dict[int, str] = {}
        for fila in _rows(self.cur, sql):
            devid = int(fila["DEVID"])
            if filtro and devid not in filtro:
                continue
            nombre = str(fila["NM"] or "").strip()
            ip = str(fila["IP"] or "").strip()
            equipos[devid] = f"{nombre} [{ip}]" if ip and ip not in nombre else nombre
        return equipos

    # ------------------------------------------------------------ resolución --

    CAMPOS_CLIENTE = """Id_Cliente, Apellido, Nombre, Doc_Nro, Activo, Id_Tipo_Cli,
                        Id_Cliente_Ref, Ult_Cuota_Paga, Credencial_Nro"""

    def buscar(self, *, dnis: list[int] | None = None,
               ids_cliente: list[int] | None = None) -> tuple[list[dict], list[str]]:
        """Resuelve socios por DNI y/o Id_Cliente. Devuelve (clientes, avisos)."""

        clientes: list[dict] = []
        avisos: list[str] = []

        for dni in dnis or []:
            filas = _rows(self.cur, f"SELECT {self.CAMPOS_CLIENTE} FROM Clientes WHERE Doc_Nro = {int(dni)}")
            if not filas:
                avisos.append(f"DNI {dni}: no existe ningún cliente con ese documento en xSys.")
                continue
            if len(filas) > 1:
                activos = [f for f in filas if f["Activo"] == 1]
                ids = [str(f["Id_Cliente"]) for f in filas]
                muestra = ", ".join(ids[:MAX_DUPLICADOS_LISTADOS])
                if len(ids) > MAX_DUPLICADOS_LISTADOS:
                    muestra += f", … (+{len(ids) - MAX_DUPLICADOS_LISTADOS} más)"
                avisos.append(
                    f"DNI {dni} está duplicado en xSys: {len(filas)} clientes "
                    f"[{muestra}], {len(activos)} activo(s)."
                )
                if len(filas) > MAX_DUPLICADOS_REPORTABLES:
                    avisos.append(
                        f"Demasiados clientes con el DNI {dni}: parece un documento comodín "
                        "(el que se carga cuando el socio no tiene DNI), no un duplicado real. "
                        "Identificá al socio y buscá por número de socio."
                    )
                    continue
                # Priorizar los activos, como hace CF_SCA_IdentifIdCliente.
                filas = activos or filas
            clientes.extend(filas)

        for cid in ids_cliente or []:
            filas = _rows(self.cur, f"SELECT {self.CAMPOS_CLIENTE} FROM Clientes WHERE Id_Cliente = {int(cid)}")
            if not filas:
                avisos.append(f"Número de socio {cid}: no existe en xSys.")
                continue
            clientes.extend(filas)

        return clientes, avisos

    # --------------------------------------------------------------- reporte --

    def reportar(self, cliente: dict) -> dict:
        """Arma el diagnóstico completo de un socio ya resuelto."""

        cid = int(cliente["Id_Cliente"])
        alertas: list[str] = []

        datos = self._datos_socio(cliente, alertas)
        foto = self._foto(cid, alertas)
        biostar = self._biostar(cid, alertas)
        lista_blanca = self._lista_blanca(cid, alertas)
        eventos = self._eventos(cid)
        equipos = self._estado_equipos(eventos, alertas)
        accesos = self._accesos(cid, alertas)
        novedades = self._novedades(cid, alertas)

        return {
            "socio": datos,
            "foto": foto,
            "biostar": biostar,
            "lista_blanca": lista_blanca,
            "equipos": equipos,
            "eventos": eventos,
            "accesos": accesos,
            "novedades": novedades,
            "alertas": _unicos(alertas),
            "sugerencias": self._sugerencias(_unicos(alertas)),
            "dias": self.dias,
        }

    def _datos_socio(self, cliente: dict, alertas: list[str]) -> dict:
        cat = _rows(
            self.cur,
            f"SELECT Descripcion FROM Clientes_Tipos WHERE Id_Tipo_Cli = {int(cliente['Id_Tipo_Cli'] or 0)}",
        )
        activo = cliente["Activo"] == 1
        if not activo:
            alertas.append("socio inactivo en xSys")

        ucp = _fecha(cliente["Ult_Cuota_Paga"])
        cuota_al_dia = None
        if ucp:
            cuota_al_dia = ucp >= self.hoy.replace(day=1)
            if not cuota_al_dia:
                alertas.append("última cuota paga anterior al mes en curso")
        else:
            alertas.append("sin última cuota paga registrada")

        return {
            "id_cliente": int(cliente["Id_Cliente"]),
            "apellido": str(cliente["Apellido"] or "").strip(),
            "nombre": str(cliente["Nombre"] or "").strip(),
            "nombre_completo": f"{str(cliente['Apellido'] or '').strip()}, {str(cliente['Nombre'] or '').strip()}",
            "dni": cliente["Doc_Nro"],
            "activo": activo,
            "categoria_id": cliente["Id_Tipo_Cli"],
            "categoria": str(cat[0]["Descripcion"]).strip() if cat else "?",
            "titular_ref": cliente["Id_Cliente_Ref"] or None,
            "credencial": str(cliente["Credencial_Nro"] or "").strip(),
            "ult_cuota_paga": ucp,
            "cuota_al_dia": cuota_al_dia,
        }

    def _foto(self, cid: int, alertas: list[str]) -> dict | None:
        fotos = _rows(
            self.cur,
            f"""SELECT Nro, Fecha, DATALENGTH(Foto) AS bytes FROM Clientes_Fotos
                WHERE Id_Cliente = {cid} ORDER BY Fecha DESC""",
        )
        if not fotos:
            alertas.append("sin foto en xSys")
            return None

        f = fotos[0]
        fecha = _fecha(f["Fecha"])
        bytes_ = int(f["bytes"] or 0)
        anios = round((self.hoy - fecha).days / 365.25, 1) if fecha else None

        problemas = []
        if anios is not None and anios >= FOTO_ANTIGUA_ANIOS:
            problemas.append(f"tiene {anios} años")
        if bytes_ < FOTO_CHICA_BYTES:
            problemas.append(f"pesa solo {bytes_:,} bytes")
        if problemas:
            alertas.append("foto de baja calidad (la causa típica de los FAIL VISUAL FACE)")

        # Imagen embebida (data URI) para mostrarla en el reporte. Se trae el blob
        # de la MISMA fuente en vivo (xSys), así funciona aunque el socio no esté
        # en el espejo local. Se reduce a miniatura para no inflar la página.
        data_uri = self._foto_data_uri(cid)

        return {"fecha": fecha, "bytes": bytes_, "anios": anios,
                "cantidad": len(fotos), "problemas": problemas, "data_uri": data_uri}

    def _foto_data_uri(self, cid: int) -> str | None:
        """Miniatura JPEG (data URI) de la foto más reciente del socio, o None."""
        import base64

        from xsys.services.images import make_thumbnail

        try:
            filas = _rows(
                self.cur,
                f"""SELECT TOP 1 Foto FROM Clientes_Fotos
                    WHERE Id_Cliente = {cid} AND Foto IS NOT NULL ORDER BY Fecha DESC""",
            )
            raw = filas[0]["Foto"] if filas else None
            if not raw:
                return None
            thumb = make_thumbnail(bytes(raw), size=(240, 300))
            if not thumb:
                return None
            return "data:image/jpeg;base64," + base64.b64encode(thumb).decode("ascii")
        except Exception:  # no romper el diagnóstico si la imagen falla
            return None

    def _biostar(self, cid: int, alertas: list[str]) -> dict:
        filas = _rows(
            self.cur,
            f"""SELECT U.USRID, U.USRUID, U.NM, U.DEL,
                       (SELECT COUNT(*) FROM {self.prefix}.T_CRDT CR WHERE CR.USRUID = U.USRUID) AS rostros
                FROM {self.prefix}.T_USR U WHERE U.USRID = '{cid}'""",
        )
        if not filas:
            alertas.append("no existe en BioStar")
            return {"existe": False}

        u = filas[0]
        usruid = int(u["USRUID"])
        borrado = str(u["DEL"] or "").upper() == "Y"
        rostros = int(u["rostros"] or 0)
        if borrado:
            alertas.append("marcado como borrado (DEL='Y') en BioStar")
        if not rostros:
            alertas.append("sin rostro cargado en el server BioStar")

        grupos = _rows(self.cur, f"SELECT ACSGRUID FROM {self.prefix}.T_ACSGRUSS WHERE USRUID = {usruid}")
        ids_grupo = sorted({int(g["ACSGRUID"]) for g in grupos})
        en_grupo = ACCESS_GROUP_SOCIOS in ids_grupo
        if not en_grupo:
            alertas.append(f"fuera del grupo de acceso {ACCESS_GROUP_SOCIOS} (Cuota Social)")

        return {"existe": True, "usruid": usruid, "nombre": str(u["NM"] or "").strip(),
                "borrado": borrado, "rostros": rostros, "grupos": ids_grupo,
                "en_grupo_socios": en_grupo}

    def _lista_blanca(self, cid: int, alertas: list[str]) -> bool:
        filas = _rows(self.cur, f"SELECT COUNT(*) AS n FROM CD_Lista_Blanca_Suprema WHERE Id_Cliente = {cid}")
        esta = bool(filas and int(filas[0]["n"]) > 0)
        if not esta:
            alertas.append("fuera de la lista blanca Suprema")
        return esta

    def _eventos(self, cid: int) -> list[dict]:
        tablas = _tablas_log(self.dias, self.hoy)
        if not tablas:
            return []
        partes = [
            f"""SELECT L.SRVDT, L.PKTDEVID, L.EVT, T.NM AS evento
                FROM {self.prefix}.T_LG{sufijo} L
                LEFT JOIN {self.prefix}.T_EVTTYP T ON T.EVT = L.EVT
                WHERE L.USRID = '{cid}' AND L.SRVDT >= DATEADD(day, -{self.dias}, GETDATE())"""
            for sufijo in tablas
        ]
        sql = " UNION ALL ".join(partes) + " ORDER BY SRVDT DESC"
        try:
            filas = _rows(self.cur, sql)
        except Exception:
            return []

        salida = []
        for f in filas:
            devid = int(f["PKTDEVID"] or 0)
            if self.equipos and devid not in self.equipos:
                continue
            nombre_evt = str(f["evento"] or "")
            salida.append({
                "fecha": f["SRVDT"],
                "devid": devid,
                "equipo": self.equipos.get(devid, str(devid)),
                "evento": nombre_evt,
                "es_fallo": "FAIL" in nombre_evt.upper() or "DENIED" in nombre_evt.upper(),
            })
        return salida

    def _estado_equipos(self, eventos: list[dict], alertas: list[str]) -> list[dict]:
        """Último evento de carga/borrado por equipo → si hoy está cargado."""

        estado: dict[int, dict] = {}
        for e in eventos:  # del más reciente al más viejo
            devid = e["devid"]
            if devid in estado:
                continue
            nombre_evt = e["evento"].upper()
            if nombre_evt in EVENTOS_CARGA:
                cargado = True
            elif nombre_evt in EVENTOS_BORRADO:
                cargado = False
            else:
                continue
            fecha = _fecha(e["fecha"])
            estado[devid] = {
                "devid": devid,
                "equipo": e["equipo"],
                "cargado": cargado,
                "evento": e["evento"],
                "fecha": e["fecha"],
                "dias_fuera": (self.hoy - fecha).days if (not cargado and fecha) else None,
            }

        if not estado:
            alertas.append(f"sin eventos de carga/borrado en los últimos {self.dias} días")
        for info in estado.values():
            if info["cargado"] is False:
                alertas.append(f"BORRADO del equipo {info['equipo']}")

        return sorted(estado.values(), key=lambda i: i["equipo"])

    def _accesos(self, cid: int, alertas: list[str]) -> list[dict]:
        filas = _rows(
            self.cur,
            f"""SELECT E.Fecha, E.Id_Acceso, A.Descripcion AS acceso, E.Id_Controlador,
                       E.Id_CD_Motivo, E.Id_Tarjeta, E.Observacion
                FROM CD_ES E LEFT JOIN CD_Accesos A ON A.Id_Acceso = E.Id_Acceso
                WHERE E.Id_Cliente = {cid} AND E.Fecha >= DATEADD(day, -{self.dias}, GETDATE())
                ORDER BY E.Fecha DESC""",
        )
        salida = []
        for f in filas:
            motivo = f["Id_CD_Motivo"]
            salida.append({
                "fecha": f["Fecha"],
                "id_acceso": f["Id_Acceso"],
                "acceso": str(f["acceso"] or "").strip(),
                "controlador": f["Id_Controlador"],
                "motivo": motivo,
                "es_rechazo": motivo in MOTIVOS_RECHAZO,
                "tarjeta": str(f["Id_Tarjeta"] or "").strip(),
                "observacion": str(f["Observacion"] or "").strip(),
            })
        rechazos = [a for a in salida if a["es_rechazo"]]
        if rechazos:
            alertas.append(f"{len(rechazos)} rechazo(s) registrados en xSys")
        return salida

    def _novedades(self, cid: int, alertas: list[str]) -> list[dict]:
        filas = _rows(
            self.cur,
            f"""SELECT TOP 8 Fecha, Estado, Tipo, Nota FROM CD_Clientes_Novedades
                WHERE Id_Cliente = {cid} ORDER BY Fecha DESC""",
        )
        salida = []
        for f in filas:
            nota = str(f["Nota"] or "").strip()
            es_error = bool(nota) and ("fail" in nota.lower() or "error" in nota.lower())
            if es_error:
                alertas.append("novedades de sincronización con error")
            salida.append({"fecha": f["Fecha"], "estado": f["Estado"],
                           "tipo": f["Tipo"], "nota": nota, "es_error": es_error})
        return salida

    @staticmethod
    def _sugerencias(alertas: list[str]) -> list[str]:
        sug: list[str] = []
        if any(a.startswith("BORRADO del equipo") for a in alertas) or \
           any(a.startswith("sin eventos de carga") for a in alertas):
            sug.append("Re-empujar al socio a los equipos: "
                       "BioStar2Client.add_user_to_device([id], device_id, overwrite=True)")
        if any("foto" in a for a in alertas):
            sug.append("Renovar la foto en xSys y re-enrolar: las fotos viejas o chicas son "
                       "las que el equipo rechaza con ENROLL_FAIL / UPDATE_FAIL (VISUAL FACE).")
        if any("grupo de acceso" in a for a in alertas):
            sug.append(f"Agregar al access group {ACCESS_GROUP_SOCIOS} (Cuota Social) en BioStar.")
        if "no existe en BioStar" in alertas:
            sug.append("Enrolar con POST /api/users. Si responde 500 / code 131085, el alta de "
                       "BioStar está caída y hay que revisar el server 10.0.0.27.")
        if any("cuota" in a for a in alertas):
            sug.append("Verificar la cuota: los accesos con última cuota paga obligatoria lo "
                       "rechazan aunque el facial funcione.")
        if any("lista blanca" in a for a in alertas):
            sug.append("Revisar por qué no entra en CD_Lista_Blanca_Suprema (xSys no lo empuja a los faciales).")
        return sug


def _unicos(valores: list[str]) -> list[str]:
    vistos: list[str] = []
    for v in valores:
        if v not in vistos:
            vistos.append(v)
    return vistos


def diagnosticar(*, dnis: list[int] | None = None, ids_cliente: list[int] | None = None,
                 dias: int = 10, equipos: list[int] | None = None,
                 prefix: str = BIOSTAR_PREFIX_DEFAULT) -> dict:
    """Punto de entrada: abre la conexión, resuelve y arma los reportes."""

    conn, driver = conectar()
    try:
        cur = conn.cursor()
        diag = DiagnosticadorFacial(cur, dias=dias, equipos=equipos, prefix=prefix)
        clientes, avisos = diag.buscar(dnis=dnis, ids_cliente=ids_cliente)
        reportes = [diag.reportar(c) for c in clientes]
        return {"driver": driver, "avisos": avisos, "reportes": reportes,
                "equipos": diag.equipos, "dias": diag.dias}
    finally:
        try:
            conn.close()
        except Exception:
            pass
