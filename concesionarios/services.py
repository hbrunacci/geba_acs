"""Armado del listado de concesionarios y estado de su documentación.

La persona vive en el espejo de xSys (``XsysSocio``) y el resto —empresa,
documentos, horario— es local. Acá se juntan las dos mitades en una sola pasada
por lote, sin N+1: el listado tiene que poder mostrar 200 concesionarios con su
documento más urgente sin hacer 200 consultas.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable

from django.db.models import Q
from django.utils import timezone

from concesionarios.models import Concesionario, Documento
from xsys.models import XsysSocio

# Categoría de xSys que identifica a un concesionario (Clientes.Id_Tipo_Cli).
ID_TIPO_CLI_CONCESIONARIO = 1015

# Orden de urgencia: lo vencido primero, después lo que está por vencer.
_URGENCIA = {
    Documento.VENCIDO: 0,
    Documento.POR_VENCER: 1,
    Documento.VIGENTE: 2,
    Documento.SIN_VENCIMIENTO: 3,
}


def resumen_documental(ids: Iterable[int], hoy: date | None = None) -> dict[int, dict[str, Any]]:
    """``{id_cliente: {...}}`` con el estado documental de cada persona.

    ``proximo`` es el documento **vencido o por vencer** con la fecha más
    temprana: primero lo que ya venció (cuanto más viejo, más urgente) y después
    lo que está por vencer (cuanto más cerca, más urgente). Un solo criterio,
    que es ordenar por fecha ascendente dentro de esos dos estados.
    """
    hoy = hoy or timezone.localdate()
    ids = [int(i) for i in ids if i]
    salida: dict[int, dict[str, Any]] = {
        i: {"total": 0, "vencidos": 0, "por_vencer": 0, "bloqueado": False,
            "proximo": None, "estado": None}
        for i in ids
    }
    if not ids:
        return salida

    docs = (Documento.objects
            .filter(id_cliente__in=ids)
            .select_related("tipo")
            .order_by("fecha_vencimiento"))
    for doc in docs:
        fila = salida[doc.id_cliente]
        fila["total"] += 1
        estado = doc.estado(hoy)
        if estado == Documento.VENCIDO:
            fila["vencidos"] += 1
            if doc.tipo.bloquea_acceso:
                fila["bloqueado"] = True
        elif estado == Documento.POR_VENCER:
            fila["por_vencer"] += 1
        if estado in (Documento.VENCIDO, Documento.POR_VENCER):
            candidato = {
                "id": doc.id,
                "tipo": doc.tipo.nombre,
                "estado": estado,
                "fecha_vencimiento": doc.fecha_vencimiento,
                "dias": doc.dias_para_vencer(hoy),
                "bloquea_acceso": doc.tipo.bloquea_acceso,
            }
            actual = fila["proximo"]
            if actual is None or _mas_urgente(candidato, actual):
                fila["proximo"] = candidato
                fila["estado"] = estado
    for fila in salida.values():
        if fila["estado"] is None:
            fila["estado"] = Documento.VIGENTE if fila["total"] else None
    return salida


def _mas_urgente(a: dict, b: dict) -> bool:
    clave_a = (_URGENCIA[a["estado"]], a["fecha_vencimiento"])
    clave_b = (_URGENCIA[b["estado"]], b["fecha_vencimiento"])
    return clave_a < clave_b


def datos_persona(socio: XsysSocio | None, id_cliente: int) -> dict[str, Any]:
    """Los datos de la persona tal como los tiene el espejo.

    Si el espejo no la tiene se devuelve el id igual: es preferible una fila con
    el legajo y el resto vacío a que el concesionario desaparezca del listado.
    """
    if socio is None:
        return {"id_cliente": id_cliente, "apellido": "", "nombre": "",
                "nombre_completo": f"Legajo {id_cliente}", "doc_nro": None,
                "categoria": "", "id_tipo_cli": None, "activo": None, "sexo": "",
                "fecha_nac": None, "email": "", "credencial_nro": "",
                "en_el_espejo": False}
    completo = f"{socio.apellido}, {socio.nombre}".strip(", ") or socio.razon_social
    return {
        "id_cliente": socio.id_cliente,
        "apellido": socio.apellido,
        "nombre": socio.nombre,
        "nombre_completo": completo or f"Legajo {socio.id_cliente}",
        "doc_nro": socio.doc_nro,
        "categoria": socio.categoria,
        "id_tipo_cli": socio.id_tipo_cli,
        "activo": bool(socio.activo),
        "sexo": socio.sexo,
        "fecha_nac": socio.fecha_nac.date() if socio.fecha_nac else None,
        "email": socio.email,
        "credencial_nro": socio.credencial_nro,
        "en_el_espejo": True,
    }


def listar(*, empresa_id: int | None = None, doc: str = "", apellido: str = "",
           solo_activos: bool = False, con_problemas: bool = False,
           momento: datetime | None = None) -> list[dict[str, Any]]:
    """El listado de concesionarios con persona, empresa y documento más urgente.

    Los filtros por documento y apellido se resuelven contra el espejo y no
    contra la tabla local, porque el nombre y el DNI viven en xSys.
    """
    momento = momento or timezone.localtime()
    hoy = momento.date()

    qs = Concesionario.objects.select_related("empresa", "horario", "empresa__horario")
    if empresa_id:
        qs = qs.filter(empresa_id=empresa_id)
    if solo_activos:
        qs = qs.filter(activo=True)
    registros = list(qs)

    ids = [c.id_cliente for c in registros]
    socios = {s.id_cliente: s for s in XsysSocio.objects.filter(id_cliente__in=ids)}

    doc, apellido = doc.strip(), apellido.strip()
    if doc or apellido:
        filtro = Q()
        if doc:
            solo_digitos = "".join(ch for ch in doc if ch.isdigit())
            if solo_digitos:
                filtro |= Q(doc_nro=int(solo_digitos))
        if apellido:
            # Buena parte de los concesionarios están cargados como razón social,
            # con Apellido y Nombre vacíos ("RCA KIOSKO PB - SURACE"): buscar sólo
            # por apellido los dejaba afuera del filtro.
            filtro |= (Q(apellido__icontains=apellido) | Q(nombre__icontains=apellido)
                       | Q(razon_social__icontains=apellido))
        if filtro:
            permitidos = set(
                XsysSocio.objects.filter(filtro, id_cliente__in=ids)
                .values_list("id_cliente", flat=True))
        else:
            permitidos = set()
        registros = [c for c in registros if c.id_cliente in permitidos]
        ids = [c.id_cliente for c in registros]

    docs = resumen_documental(ids, hoy)

    filas = []
    for c in registros:
        resumen = docs.get(c.id_cliente, {})
        horario = c.horario_vigente
        filas.append({
            "id": c.id,
            "persona": datos_persona(socios.get(c.id_cliente), c.id_cliente),
            "empresa": {"id": c.empresa_id, "nombre": c.empresa.nombre,
                        "cuit": c.empresa.cuit, "activa": c.empresa.activa},
            "cargo": c.cargo,
            "activo": c.activo,
            "fecha_alta": c.fecha_alta,
            "fecha_baja": c.fecha_baja,
            "observaciones": c.observaciones,
            "horario": ({"id": horario.id, "nombre": horario.nombre,
                         "resumen": horario.resumen,
                         "propio": c.horario_id is not None}
                        if horario else None),
            "permite_ahora": c.permite_horario(momento),
            "documentos": {
                "total": resumen.get("total", 0),
                "vencidos": resumen.get("vencidos", 0),
                "por_vencer": resumen.get("por_vencer", 0),
                "bloqueado": resumen.get("bloqueado", False),
                "estado": resumen.get("estado"),
                "proximo": resumen.get("proximo"),
            },
        })

    if con_problemas:
        filas = [f for f in filas if f["documentos"]["proximo"]]

    # Lo urgente arriba: vencidos, después por vencer, después el resto.
    def orden(f):
        prox = f["documentos"]["proximo"]
        if not prox:
            return (2, date.max, f["persona"]["apellido"])
        return (_URGENCIA[prox["estado"]], prox["fecha_vencimiento"], f["persona"]["apellido"])

    filas.sort(key=orden)
    return filas


def estado_operativo(ids: Iterable[int], hoy: date | None = None) -> dict[int, dict[str, Any]]:
    """Cómo está hoy cada persona: empresa, baja y documentación que la frena.

    Es lo que consume tanto el listado de ingresos como el visor de puerta, para
    que los dos digan lo mismo. NO decide si entra —eso lo resuelve xSys— sino
    que avisa: la persona pasó, pero tiene la ART vencida o está dada de baja en
    la concesión.
    """
    hoy = hoy or timezone.localdate()
    ids = [int(i) for i in ids if i]
    if not ids:
        return {}
    registros = {
        c.id_cliente: c for c in
        Concesionario.objects.filter(id_cliente__in=ids).select_related("empresa")
    }
    docs = resumen_documental(registros.keys(), hoy)
    salida = {}
    for cid, c in registros.items():
        d = docs.get(cid, {})
        motivos = []
        if not c.activo:
            motivos.append("dado de baja en la concesión")
        elif not c.empresa.activa:
            motivos.append(f"la empresa {c.empresa.nombre} está inactiva")
        if d.get("bloqueado"):
            prox = d.get("proximo") or {}
            motivos.append(f"{prox.get('tipo', 'documentación')} vencida")
        salida[cid] = {
            "empresa": c.empresa.nombre,
            "empresa_id": c.empresa_id,
            "activo": c.activo,
            "doc_bloqueado": bool(d.get("bloqueado")),
            "doc_vencidos": d.get("vencidos", 0),
            "doc_por_vencer": d.get("por_vencer", 0),
            "doc_proximo": d.get("proximo"),
            "alerta": bool(motivos),
            "motivo": " · ".join(motivos),
        }
    return salida


# ------------------------------------------------------------------- ingresos
# CD_ES local sólo guarda 7 días (MSSQL_XSYS.CD_ES_RETENTION_DAYS), así que un
# listado con filtro por fecha tiene que ir a xSys. Son ~100 legajos: la consulta
# entra por IX_CDES_PorCli y es barata.
_SQL_INGRESOS = """
SELECT E.Id_ES, E.Fecha, E.Id_Cliente, E.Resultado, E.Id_Acceso, E.Id_Controlador,
       LTRIM(RTRIM(ISNULL(CT.Descripcion, ''))) AS lector,
       ISNULL(CT.Tipo_Cont, '') AS tipo_cont,
       CAST(E.Observacion AS VARCHAR(200)) AS motivo
FROM CD_ES E
LEFT JOIN CD_Controladores CT ON CT.Id_Controlador = E.Id_Controlador
WHERE E.Id_Cliente IN ({ids})
  AND E.Fecha >= ? AND E.Fecha < ?
ORDER BY E.Fecha DESC
"""
TOPE_INGRESOS = 3000
DIAS_MAXIMO = 92


def ingresos(*, desde: date, hasta: date, empresa_id: int | None = None,
             solo_rechazos: bool = False, cursor=None) -> dict[str, Any]:
    """Pasadas de los concesionarios en un rango, con quién es y cómo está hoy.

    ``hasta`` es inclusivo: el operador pide "del 1 al 5" y espera que el 5
    entre entero.
    """
    if (hasta - desde).days > DIAS_MAXIMO:
        return {"error": f"El rango no puede superar los {DIAS_MAXIMO} días.",
                "results": [], "resumen": {}}

    qs = Concesionario.objects.select_related("empresa")
    if empresa_id:
        qs = qs.filter(empresa_id=empresa_id)
    registros = {c.id_cliente: c for c in qs}
    if not registros:
        return {"results": [], "resumen": _resumen([]), "sin_concesionarios": True}

    ids = list(registros)
    sql = _SQL_INGRESOS.format(ids=",".join(str(i) for i in ids))
    limites = (datetime.combine(desde, datetime.min.time()),
               datetime.combine(hasta + timedelta(days=1), datetime.min.time()))

    filas_sql = _consultar(sql, limites, cursor)
    if filas_sql is None:
        return {"error": "No se pudo consultar los movimientos en xSys.",
                "results": [], "resumen": _resumen([])}

    socios = {s.id_cliente: s for s in XsysSocio.objects.filter(id_cliente__in=ids)}
    estados = estado_operativo(ids)
    con_foto = _ids_con_foto(ids)

    filas = []
    for f in filas_sql[:TOPE_INGRESOS]:
        (id_es, fecha, cid, resultado, id_acceso, id_ctrl, lector, tipo_cont, motivo) = f
        estado = estados.get(cid, {})
        permitido = (resultado or "").strip().upper() == "S"
        filas.append({
            "id_es": int(id_es),
            "fecha": fecha,
            "persona": datos_persona(socios.get(cid), cid),
            "empresa": estado.get("empresa", ""),
            "tiene_foto": cid in con_foto,
            "permitido": permitido,
            "resultado": (resultado or "").strip(),
            "motivo": (motivo or "").strip(),
            "lector": lector or f"Controlador {id_ctrl}",
            "lectura": "facial" if (tipo_cont or "").upper() == "F" else "credencial",
            "id_acceso": id_acceso,
            "alerta": estado.get("alerta", False),
            "alerta_motivo": estado.get("motivo", ""),
            "doc_bloqueado": estado.get("doc_bloqueado", False),
        })
    return {
        "results": filas,
        "resumen": _resumen(filas),
        "truncado": len(filas_sql) > TOPE_INGRESOS,
        "total_crudo": len(filas_sql),
    }


def _resumen(filas: list[dict]) -> dict[str, int]:
    return {
        "eventos": len(filas),
        "personas": len({f["persona"]["id_cliente"] for f in filas}),
        "pasaron": sum(1 for f in filas if f["permitido"]),
        "rechazos": sum(1 for f in filas if not f["permitido"]),
        "con_alerta": sum(1 for f in filas if f["alerta"]),
    }


def _consultar(sql, params, cursor=None):
    """Ejecuta contra xSys. Devuelve None si la base no está disponible."""
    if cursor is not None:
        cursor.execute(sql, params)
        return cursor.fetchall()
    from django.conf import settings

    from xsys.services.mssql import xsys_cursor
    try:
        with xsys_cursor(settings.MSSQL_XSYS) as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    except Exception:
        return None


def _ids_con_foto(ids: list[int]) -> set[int]:
    """Quién tiene cara para mostrar: la cargada a mano o la de xSys."""
    from xsys.models import XsysSocioFoto

    from concesionarios.models import FotoPersona
    propias = set(FotoPersona.objects.filter(id_cliente__in=ids)
                  .values_list("id_cliente", flat=True))
    espejo = set(XsysSocioFoto.objects.filter(id_cliente__in=ids)
                 .values_list("id_cliente", flat=True))
    return propias | espejo


def candidatos_sin_registrar(limite: int = 50, busqueda: str = "") -> list[dict[str, Any]]:
    """Socios con categoría CONCESIONARIO en xSys que todavía no están cargados.

    Sirve para dar de alta sin tipear el legajo a mano: la persona ya existe en
    xSys, acá sólo falta decir para qué empresa trabaja.
    """
    ya = set(Concesionario.objects.values_list("id_cliente", flat=True))
    qs = XsysSocio.objects.filter(id_tipo_cli=ID_TIPO_CLI_CONCESIONARIO).exclude(id_cliente__in=ya)
    busqueda = busqueda.strip()
    if busqueda:
        filtro = (Q(apellido__icontains=busqueda) | Q(nombre__icontains=busqueda)
                  | Q(razon_social__icontains=busqueda))
        digitos = "".join(ch for ch in busqueda if ch.isdigit())
        if digitos:
            filtro |= Q(doc_nro=int(digitos)) | Q(id_cliente=int(digitos))
        qs = qs.filter(filtro)
    return [datos_persona(s, s.id_cliente) for s in qs.order_by("apellido", "nombre")[:limite]]
