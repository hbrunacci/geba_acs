"""APIs de la pantalla de socios: listado, ficha editable, cuenta corriente y grupo.

Va en su propio módulo y no en ``api_views``: ese archivo ya pasa las 1.700 líneas
y es el del visor de puertas, que es otra pantalla y otro público. Lo único que
comparten es el espejo.

Todas exigen el rol de socios (``PuedeSocios``). La de ficha además ESCRIBE en
xSys, que es la primera vez que la app lo hace: el detalle de por qué y con qué
recaudos está en ``xsys.services.ficha``.
"""

from __future__ import annotations

import datetime as _dt

from django.db.models import Exists, OuterRef, Q
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from common.roles import PuedeSocios
from xsys.models import XsysContrato, XsysSocio, XsysSocioEdicion, XsysSocioFoto
from xsys.services import cuenta_corriente, ficha, grupo_familiar
from xsys.services.cuota import cuota_al_dia, fecha_limite_ingreso

PAGINA = 40
PAGINA_MAX = 200
EDICIONES_EN_FICHA = 25


def _ip(request) -> str:
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if fwd:
        return fwd.split(",")[0].strip()[:45]
    return (request.META.get("REMOTE_ADDR") or "")[:45]


def _fecha(valor):
    if not valor:
        return None
    try:
        return _dt.date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


class _BaseSocios(APIView):
    permission_classes = [PuedeSocios]


class SociosListadoAPI(_BaseSocios):
    """GET /api/xsys/socios/listado/ → grilla paginada de todos los socios.

    Filtros: ``q`` (nombre / documento / credencial / nº de socio), ``activo``
    (1, 0 o vacío = todos), ``categoria`` (Id_Tipo_Cli), ``grupo``
    (titular | integrante | sin_grupo) y ``cuota`` (al_dia | vencida).
    """

    def get(self, request):
        p = request.query_params
        limit = max(1, min(int(p.get("limit") or PAGINA), PAGINA_MAX))
        offset = max(0, int(p.get("offset") or 0))

        qs = XsysSocio.objects.all()

        q = (p.get("q") or "").strip()
        if q:
            filtro = (
                Q(apellido__icontains=q)
                | Q(nombre__icontains=q)
                | Q(razon_social__icontains=q)
                | Q(credencial_nro__icontains=q)
                | Q(id_cliente_externo__icontains=q)
            )
            if q.isdigit():
                filtro |= Q(doc_nro=int(q)) | Q(id_cliente=int(q))
            qs = qs.filter(filtro)

        activo = (p.get("activo") or "").strip()
        if activo in ("0", "1"):
            qs = qs.filter(activo=int(activo))

        categoria = (p.get("categoria") or "").strip()
        if categoria.isdigit():
            qs = qs.filter(id_tipo_cli=int(categoria))

        # "Tiene integrantes" necesita mirar las otras filas: se resuelve con un
        # EXISTS correlacionado para no traer 50.000 socios a Python.
        tiene_integrantes = Exists(
            XsysSocio.objects.filter(id_cliente_ref=OuterRef("id_cliente")).exclude(
                id_cliente=OuterRef("id_cliente")
            )
        )
        qs = qs.annotate(con_integrantes=tiene_integrantes)

        grupo = (p.get("grupo") or "").strip()
        if grupo == "titular":
            qs = qs.filter(con_integrantes=True)
        elif grupo == "integrante":
            qs = qs.exclude(Q(id_cliente_ref=0) | Q(id_cliente_ref__isnull=True))
        elif grupo == "sin_grupo":
            qs = qs.filter(
                Q(id_cliente_ref=0) | Q(id_cliente_ref__isnull=True), con_integrantes=False
            )

        cuota = (p.get("cuota") or "").strip()
        if cuota in ("al_dia", "vencida"):
            # La regla de cuota es una función Python (espeja la de xSys), así que
            # no se puede empujar al SQL. Se limita el universo a los activos y se
            # filtra en memoria; con el filtro de texto o categoría puesto el
            # conjunto es chico, y sin filtro igual se corta por la paginación.
            ids = []
            for s in qs.filter(activo=1).values_list("id_cliente", "ult_cuota_paga"):
                al_dia = cuota_al_dia(s[1])
                if (cuota == "al_dia") == al_dia:
                    ids.append(s[0])
            qs = qs.filter(id_cliente__in=ids)

        orden = (p.get("orden") or "nombre").strip()
        ordenes = {
            "nombre": ("apellido", "nombre"),
            "id": ("id_cliente",),
            "alta": ("-fecha_alta",),
            "cuota": ("ult_cuota_paga",),
        }
        qs = qs.order_by(*ordenes.get(orden, ordenes["nombre"]))

        total = qs.count()
        pagina = list(qs[offset:offset + limit])

        con_foto = set(
            XsysSocioFoto.objects.filter(
                id_cliente__in=[s.id_cliente for s in pagina]
            ).values_list("id_cliente", flat=True)
        )

        return Response({
            "total": total,
            "limit": limit,
            "offset": offset,
            "resultados": [
                {
                    "id_cliente": s.id_cliente,
                    "nro_socio": s.id_cliente_externo or "",
                    "nombre": f"{s.apellido}, {s.nombre}".strip(", ") or s.razon_social,
                    "doc_nro": s.doc_nro,
                    "categoria": s.categoria,
                    "id_tipo_cli": s.id_tipo_cli,
                    "activo": bool(s.activo),
                    "credencial_nro": s.credencial_nro,
                    "ult_cuota_paga": (
                        s.ult_cuota_paga.date().isoformat() if s.ult_cuota_paga else None
                    ),
                    "cuota_al_dia": cuota_al_dia(s.ult_cuota_paga) if s.activo else False,
                    "es_integrante": bool(s.id_cliente_ref),
                    "es_titular": bool(getattr(s, "con_integrantes", False)),
                    "id_cliente_ref": s.id_cliente_ref or 0,
                    "tiene_foto": s.id_cliente in con_foto,
                }
                for s in pagina
            ],
        })


class SocioFichaAPI(_BaseSocios):
    """GET/PATCH /api/xsys/socios/<id_cliente>/ficha/ → ficha de xSys."""

    def get(self, request, id_cliente: int):
        valores = ficha.leer(id_cliente)
        if valores is None:
            return Response({"detail": "No existe la ficha en xSys."},
                            status=status.HTTP_404_NOT_FOUND)
        return Response({
            "id_cliente": id_cliente,
            "definicion": ficha.definicion(),
            "valores": valores,
            "ediciones": _ediciones(id_cliente),
        })

    def patch(self, request, id_cliente: int):
        cambios = request.data.get("cambios")
        if not isinstance(cambios, dict):
            return Response({"detail": "Falta el objeto 'cambios'."},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            resultado = ficha.guardar(
                id_cliente,
                cambios,
                usuario=request.user.get_username(),
                ip=_ip(request),
                confirmado=bool(request.data.get("confirmar")),
            )
        except ficha.FichaConfirmacion as exc:
            # 409: el pedido es válido pero hay que aceptar la consecuencia primero.
            # La pantalla muestra los motivos y reenvía con confirmar=true.
            return Response(
                {"detail": "Hace falta confirmar el cambio.", "confirmar": exc.motivos},
                status=status.HTTP_409_CONFLICT,
            )
        except ficha.FichaError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        resultado["ediciones"] = _ediciones(id_cliente)
        return Response(resultado)


def _ediciones(id_cliente: int) -> list[dict]:
    return [
        {
            "campo": e.campo,
            "etiqueta": e.etiqueta,
            "anterior": e.valor_anterior,
            "nuevo": e.valor_nuevo,
            "usuario": e.usuario,
            "cuando": e.creado_at.isoformat(),
        }
        for e in XsysSocioEdicion.objects.filter(id_cliente=id_cliente)[:EDICIONES_EN_FICHA]
    ]


class SocioCuentaCorrienteAPI(_BaseSocios):
    """GET /api/xsys/socios/<id_cliente>/cuenta-corriente/ → cargos, pagos y saldo.

    Se consulta en vivo contra xSys. Sin ``desde`` toma los últimos seis meses; la
    pantalla pagina con ``offset`` y puede ampliar la ventana hacia atrás.
    """

    def get(self, request, id_cliente: int):
        p = request.query_params
        datos = cuenta_corriente.movimientos(
            id_cliente,
            desde=_fecha(p.get("desde")),
            hasta=_fecha(p.get("hasta")),
            limit=int(p.get("limit") or cuenta_corriente.PAGINA),
            offset=int(p.get("offset") or 0),
            solo_impagos=(p.get("impagos") in ("1", "true")),
        )
        if not datos["offset"]:
            datos["resumen"] = cuenta_corriente.resumen(id_cliente)
        return Response(datos)


class SocioGrupoFamiliarAPI(_BaseSocios):
    """GET /api/xsys/socios/<id_cliente>/grupo-familiar/ → titular e integrantes."""

    def get(self, request, id_cliente: int):
        return Response(grupo_familiar.de(id_cliente))


class SocioContratosAPI(_BaseSocios):
    """GET /api/xsys/socios/<id_cliente>/contratos/ → contratos con sus datos clave.

    Sale del espejo (``XsysContrato``), que ya trae la disciplina, el último pago y
    la deuda por contrato calculados en el sync. Por defecto sólo los activos.
    """

    def get(self, request, id_cliente: int):
        qs = XsysContrato.objects.filter(id_cliente=id_cliente)
        if request.query_params.get("todos") not in ("1", "true"):
            qs = qs.filter(activo=1)
        socio = XsysSocio.objects.filter(pk=id_cliente).first()
        limite = fecha_limite_ingreso(socio.ult_cuota_paga) if socio else None
        return Response({
            "id_cliente": id_cliente,
            "cuota_limite": limite.isoformat() if limite else None,
            "cuota_al_dia": cuota_al_dia(socio.ult_cuota_paga) if socio else False,
            "contratos": [
                {
                    "id_contrato": c.id_contrato,
                    "descripcion": c.descripcion,
                    "actividad": c.producto_desc,
                    "id_tipo_con": c.id_tipo_con,
                    "activo": bool(c.activo),
                    "desde": c.fecha_alta.date().isoformat() if c.fecha_alta else None,
                    "hasta": c.fecha_hasta.date().isoformat() if c.fecha_hasta else None,
                    "deuda": float(c.deuda) if c.deuda is not None else None,
                    "ultimo_pago_fecha": (
                        c.ultimo_pago_fecha.isoformat() if c.ultimo_pago_fecha else None
                    ),
                    "ultimo_pago_importe": (
                        float(c.ultimo_pago_importe) if c.ultimo_pago_importe is not None else None
                    ),
                    "ultimo_cbte_fecha": (
                        c.ultimo_cbte_fecha.isoformat() if c.ultimo_cbte_fecha else None
                    ),
                }
                for c in qs.order_by("-activo", "descripcion")
            ],
        })
