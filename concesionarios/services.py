"""Armado del listado de concesionarios y estado de su documentación.

La persona vive en el espejo de xSys (``XsysSocio``) y el resto —empresa,
documentos, horario— es local. Acá se juntan las dos mitades en una sola pasada
por lote, sin N+1: el listado tiene que poder mostrar 200 concesionarios con su
documento más urgente sin hacer 200 consultas.
"""

from __future__ import annotations

from datetime import date, datetime
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
