"""Quién está habilitado a entrar pero no tiene con qué identificarse.

Pasó dos veces en cuatro días. El 18/12/2025 se cargaron a mano 35 docentes del
IGSM, uno cada minuto y medio, y seis quedaron con el documento en 0; el
04/09/2026 una de ellas quedó parada en el molinete de Alcorta y recién ahí nos
enteramos. Lo mismo con los alumnos del profesorado el 07/09.

El patrón es siempre el mismo: el alta se hace a mano, el documento es el campo
que se saltea, y el error no se ve hasta que la persona no puede entrar. Nadie
mira una ficha para comprobar que tenga documento.

Este chequeo lo da vuelta: busca a los que **pueden** entrar —tienen contrato
vigente— y **no pueden identificarse** en ningún molinete, y deja un aviso en la
pantalla de Avisos a socios el día que se carga la ficha, no el día que la
persona queda afuera.

Un molinete identifica de tres maneras y acá se exigen las tres en cero:

    documento   se teclea en el molinete (``CF_SCA_IdentifIdCliente``)
    credencial  la tarjeta
    facial      estar enrolado en BioStar

Corre contra el espejo local, así que no depende de la VPN ni le agrega carga al
SQL del club.
"""

from __future__ import annotations

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

# Categorías que no son una persona que cruza un molinete. Las fichas de
# concesiones y colegios son la empresa o la institución —"STADIO S.A. CANCHA 5",
# "COLEGIO SAN GREGORIO"—: no tienen documento porque no son alguien. Quien pasa
# es su gente, con su propia ficha. BICICLETA son las chapas de las bicis.
CATEGORIAS_NO_PERSONA = {
    1134,  # BICICLETA
    1015,  # CONCESIONARIO
    1019,  # COLEGIOS
}

TEXTO = ("Sin forma de identificarse en un molinete: la ficha no tiene documento "
         "ni credencial, y no está enrolado en el facial. Tiene {contratos} "
         "contrato(s) vigente(s) ({detalle}), así que debería poder entrar. "
         "Pedirle el DNI y cargarlo en la ficha {id_cliente}.")


def _enrolados_en_facial() -> set[int]:
    """``id_cliente`` de los que pueden entrar por la cara."""
    from access_control.models import BioStarUser

    ids = set()
    for user_id in BioStarUser.objects.filter(is_active=True).values_list("user_id", flat=True):
        try:
            ids.add(int(user_id))
        except (TypeError, ValueError):
            continue
    return ids


def sin_identificacion() -> list[dict]:
    """Los que pueden entrar y no tienen con qué identificarse.

    Devuelve un dict por persona con lo que hace falta para contactarla, ordenado
    por fecha de alta descendente: los cargados recién arriba, que son los que
    todavía se pueden corregir antes de que alguien quede parado en la puerta.
    """
    from django.db.models import Q

    from xsys.models import XsysContrato, XsysSocio

    # El filtro por documento ya deja unas decenas de filas sobre 54.000 socios,
    # así que la credencial se mira en Python: una credencial de espacios no es
    # cadena vacía para la base, pero tampoco identifica a nadie.
    candidatos = [
        s for s in (
            XsysSocio.objects
            .filter(activo=1)
            .filter(Q(doc_nro__isnull=True) | Q(doc_nro=0))
            .exclude(id_tipo_cli__in=CATEGORIAS_NO_PERSONA)
            .order_by("-fecha_alta")
        )
        if not (s.credencial_nro or "").strip()
    ]
    ids = [s.id_cliente for s in candidatos]
    if not ids:
        return []

    # Sólo los que tienen algo que los habilite: sin contrato vigente, que no
    # tengan documento no le impide entrar a nadie.
    contratos: dict[int, list[str]] = {}
    for c in XsysContrato.objects.filter(id_cliente__in=ids, activo=1):
        contratos.setdefault(c.id_cliente, []).append(c.descripcion or "sin descripción")

    facial = _enrolados_en_facial()

    filas = []
    for s in candidatos:
        if s.id_cliente not in contratos or s.id_cliente in facial:
            continue
        filas.append({
            "id_cliente": s.id_cliente,
            "nombre": (f"{s.apellido}, {s.nombre}".strip(", ") or s.razon_social),
            "categoria": s.categoria,
            "fecha_alta": s.fecha_alta,
            "email": s.email,
            "contratos": sorted(set(contratos[s.id_cliente])),
        })
    return filas


def revisar(*, dry_run: bool = False) -> dict:
    """Deja un aviso por cada uno, y resuelve los de quienes ya se arreglaron.

    La resolución automática es la mitad que importa: sin ella la pantalla se
    llena de gente que ya tiene el documento cargado y la lista deja de querer
    decir algo. El aviso resuelto queda como registro de que el problema existió.
    """
    from access_control.models import SocioAviso

    filas = sin_identificacion()
    afectados = {f["id_cliente"] for f in filas}
    stats = {"detectados": len(filas), "avisos_nuevos": 0, "avisos_resueltos": 0}

    abiertos = set(
        SocioAviso.objects
        .filter(tipo=SocioAviso.TIPO_SIN_IDENTIFICACION, resuelto=False)
        .values_list("id_cliente", flat=True)
    )

    nuevos = [
        SocioAviso(
            id_cliente=f["id_cliente"],
            tipo=SocioAviso.TIPO_SIN_IDENTIFICACION,
            texto=TEXTO.format(contratos=len(f["contratos"]),
                               detalle=", ".join(f["contratos"])[:150],
                               id_cliente=f["id_cliente"])[:500],
            creado_por="sistema",
        )
        for f in filas if f["id_cliente"] not in abiertos
    ]
    ya_resueltos = abiertos - afectados

    if not dry_run:
        if nuevos:
            SocioAviso.objects.bulk_create(nuevos)
        if ya_resueltos:
            SocioAviso.objects.filter(
                tipo=SocioAviso.TIPO_SIN_IDENTIFICACION, resuelto=False,
                id_cliente__in=ya_resueltos,
            ).update(resuelto=True, resuelto_at=timezone.now(), resuelto_por="sistema")

    stats["avisos_nuevos"] = len(nuevos)
    stats["avisos_resueltos"] = len(ya_resueltos)
    return stats


def revisar_best_effort() -> dict:
    """Para llamar desde la sincronización: no puede romper el sync."""
    try:
        return revisar()
    except Exception as exc:  # pragma: no cover - nunca romper el sync
        logger.warning("chequeo_identificacion: falló la revisión: %s", exc)
        return {"detectados": 0, "avisos_nuevos": 0, "avisos_resueltos": 0}
