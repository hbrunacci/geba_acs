"""Exportación a Excel del detalle de deuda.

El archivo no es un volcado de la consulta: lo abre Tesorería y tiene que poder
trabajarlo sin reformatear nada. Por eso lleva una hoja de portada que deja
asentado de qué foto salió y con qué filtros —sin eso, dos archivos con el mismo
nombre y números distintos son indistinguibles—, una hoja de detalle con filtros
automáticos y panel congelado, y una hoja de resumen por categoría.

Los importes van como número con formato de moneda, no como texto: si van como
texto, la primera suma que alguien intente en Excel da cero y el archivo pierde
toda su utilidad.
"""

from __future__ import annotations

import io

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from xsys.services.tableros import socios_con_deuda

MONEDA = '"$"#,##0.00'
FECHA_MES = "mm/yyyy"

_AZUL = "1F3864"
_AZUL_CLARO = "D9E2F3"
_GRIS = "F2F2F2"

_TITULO = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
_CABECERA = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
_ETIQUETA = Font(name="Calibri", size=10, bold=True)
_TOTAL = Font(name="Calibri", size=10, bold=True)

_RELLENO_TITULO = PatternFill("solid", fgColor=_AZUL)
_RELLENO_CABECERA = PatternFill("solid", fgColor=_AZUL)
_RELLENO_TOTAL = PatternFill("solid", fgColor=_AZUL_CLARO)
_RELLENO_ALTERNO = PatternFill("solid", fgColor=_GRIS)

_BORDE_FINO = Border(bottom=Side(style="thin", color="BFBFBF"))

# (encabezado, atributo del modelo, ancho, formato)
COLUMNAS = [
    ("Nº de socio", "nro_socio", 13, None),
    ("Ficha", "id_cliente", 10, "0"),
    ("Documento", "doc_nro", 13, "0"),
    ("Apellido", "apellido", 24, None),
    ("Nombre", "nombre", 24, None),
    ("Categoría", "categoria", 22, None),
    ("Socio", "_socio", 8, None),
    ("Estado", "_estado", 10, None),
    ("Cuotas adeudadas", "cuotas", 17, "0"),
    ("Importe adeudado", "importe", 18, MONEDA),
    # Abierto por rubro: es lo primero que pregunta Tesorería cuando ve un
    # moroso —si debe la cuota o una actividad—, porque la gestión de cobranza
    # es distinta en cada caso.
    ("Cuota social", "importe_cuota_social", 16, MONEDA),
    ("Actividades", "importe_actividades", 16, MONEDA),
    ("Otros conceptos", "importe_otros", 16, MONEDA),
    ("Cuotas sociales", "cuotas_cuota_social", 16, "0"),
    ("Cuotas actividad", "cuotas_actividades", 16, "0"),
    ("Debe desde", "mes_mas_viejo", 12, FECHA_MES),
    ("Última impaga", "mes_mas_nuevo", 13, FECHA_MES),
]

# Qué columnas se totalizan al pie (1-based). Se listan por atributo y no por
# formato: "Ficha" y "Documento" también son numéricas y sumarlas no significa
# nada.
_ATTRS_SUMA = ("cuotas", "importe", "importe_cuota_social", "importe_actividades",
               "importe_otros", "cuotas_cuota_social", "cuotas_actividades")
_COLS_SUMA = [(i, c[3]) for i, c in enumerate(COLUMNAS, start=1)
              if c[1] in _ATTRS_SUMA]
_COL_ETIQUETA_TOTAL = min(i for i, _ in _COLS_SUMA) - 1


def _valor(fila, attr):
    if attr == "_socio":
        return "Socio" if fila.es_socio else "No socio"
    if attr == "_estado":
        return "Activo" if fila.activo else "De baja"
    v = getattr(fila, attr)
    if attr.startswith("importe"):
        return float(v or 0)
    return v


def generar(foto, *, categorias_ids=None, cuotas_min: int = 0,
            solo: str = "socios_activos", etiqueta_poblacion: str = "",
            etiqueta_categorias: str = "", usuario: str = "") -> bytes:
    """Devuelve el .xlsx completo como bytes, listo para servir."""
    filas = list(socios_con_deuda(foto, categorias_ids, cuotas_min, solo))

    wb = Workbook()
    _hoja_portada(wb.active, foto, filas, etiqueta_poblacion, etiqueta_categorias,
                  cuotas_min, usuario)
    _hoja_detalle(wb.create_sheet("Detalle"), filas)
    _hoja_categorias(wb.create_sheet("Por categoría"), filas)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# (encabezado, clave del dict, ancho, formato) para el listado del modal, que no
# es el mismo que el general: acá cada fila es lo que la persona debe DE ESA
# actividad, más desde cuándo, que es lo que se fue a mirar al abrir el detalle.
COLUMNAS_DETALLE = [
    ("Nº de socio", "nro_socio", 13, None),
    ("Ficha", "id_cliente", 10, "0"),
    ("Documento", "doc_nro", 13, "0"),
    ("Socio", "nombre", 34, None),
    ("Categoría", "categoria", 22, None),
    ("Cuotas adeudadas", "cuotas", 17, "0"),
    ("Importe adeudado", "importe", 18, MONEDA),
    ("Debe desde", "desde", 12, FECHA_MES),
    ("Meses de atraso", "meses", 16, "0"),
    ("Antigüedad", "antiguedad", 16, None),
]


def generar_detalle(detalle: dict, *, usuario: str = "") -> bytes:
    """El listado del modal, con el mismo formato que el Excel general.

    Recibe la salida de ``tableros.detalle_actividad`` ya calculada, y NO la
    vuelve a pedir: el archivo tiene que contener exactamente lo que la persona
    está viendo en pantalla, incluidos los filtros que tenía puestos.
    """
    import datetime as _dt

    wb = Workbook()
    _portada_detalle(wb.active, detalle, usuario)

    ws = wb.create_sheet("Detalle")
    ws.freeze_panes = "A2"
    for i, (titulo, _clave, ancho, _fmt) in enumerate(COLUMNAS_DETALLE, start=1):
        celda = ws.cell(row=1, column=i, value=titulo)
        celda.font = _CABECERA
        celda.fill = _RELLENO_CABECERA
        celda.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = ancho
    ws.row_dimensions[1].height = 30

    filas = detalle.get("socios") or []
    for r, fila in enumerate(filas, start=2):
        for i, (_t, clave, _a, fmt) in enumerate(COLUMNAS_DETALLE, start=1):
            valor = fila.get(clave)
            if clave == "desde" and valor:
                # Texto ISO a fecha real, para que Excel la ordene como fecha.
                valor = _dt.datetime.strptime(valor, "%Y-%m-%d")
            celda = ws.cell(row=r, column=i, value=valor)
            if fmt:
                celda.number_format = fmt
            celda.border = _BORDE_FINO
            if r % 2 == 0:
                celda.fill = _RELLENO_ALTERNO

    if filas:
        fin = len(filas) + 1
        ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNAS_DETALLE))}{fin}"
        total = fin + 1
        ws.cell(row=total, column=5, value="TOTAL").font = _TOTAL
        ws.cell(row=total, column=5).fill = _RELLENO_TOTAL
        for col in (6, 7):
            letra = get_column_letter(col)
            celda = ws.cell(row=total, column=col,
                            value=f"=SUM({letra}2:{letra}{fin})")
            celda.font = _TOTAL
            celda.fill = _RELLENO_TOTAL
            celda.number_format = MONEDA if col == 7 else "#,##0"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _portada_detalle(ws, detalle, usuario):
    ws.title = "Informe"
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 52

    ws.merge_cells("A1:B1")
    c = ws["A1"]
    c.value = detalle.get("titulo") or "Detalle de deuda"
    c.font = _TITULO
    c.fill = _RELLENO_TITULO
    c.alignment = Alignment(vertical="center", indent=1)
    ws.row_dimensions[1].height = 28

    calculado = detalle.get("foto", {}).get("calculado_at") or ""
    if calculado:
        from django.utils.dateparse import parse_datetime
        dt_ = parse_datetime(calculado)
        calculado = timezone.localtime(dt_).strftime("%d/%m/%Y a las %H:%M") if dt_ else ""

    datos = [
        ("", ""),
        ("Datos calculados el", calculado or "—"),
        ("Archivo generado el", timezone.localtime().strftime("%d/%m/%Y a las %H:%M")),
        ("Generado por", usuario or "—"),
        ("", ""),
        ("Población incluida", "Socios activos"),
        ("Personas", detalle.get("total", 0)),
        ("Cuotas adeudadas", detalle.get("cuotas", 0)),
        ("Importe adeudado", float(detalle.get("importe", 0))),
        ("", ""),
    ]
    fila = 2
    for etiqueta, valor in datos:
        if etiqueta:
            ws.cell(row=fila, column=1, value=etiqueta).font = _ETIQUETA
            celda = ws.cell(row=fila, column=2, value=valor)
            if etiqueta == "Importe adeudado":
                celda.number_format = MONEDA
                celda.font = _TOTAL
            elif isinstance(valor, int):
                celda.number_format = "#,##0"
        fila += 1

    ws.cell(row=fila, column=1, value="Antigüedad de la deuda").font = _ETIQUETA
    fila += 1
    for t in detalle.get("antiguedad", []):
        ws.cell(row=fila, column=1, value="   " + t["tramo"])
        celda = ws.cell(row=fila, column=2,
                        value=f"{t['socios']} socios")
        celda.alignment = Alignment(horizontal="left")
        ws.cell(row=fila, column=3, value=float(t["importe"])).number_format = MONEDA
        fila += 1
    ws.column_dimensions["C"].width = 18


def nombre_archivo_detalle(detalle) -> str:
    """``deuda_HOCKEY_S_CESPED_2026-09-08_1930.xlsx``."""
    crudo = (detalle.get("titulo") or "detalle").lower()
    limpio = "".join(ch if ch.isalnum() else "_" for ch in crudo).strip("_")
    while "__" in limpio:
        limpio = limpio.replace("__", "_")
    sello = timezone.localtime().strftime("%Y-%m-%d_%H%M")
    return f"deuda_{limpio}_{sello}.xlsx"


def nombre_archivo(foto) -> str:
    """``deuda_socios_2026-09-08_1130.xlsx``: la fecha es la de la FOTO.

    Se usa la fecha del cálculo y no la de la descarga a propósito: dos archivos
    bajados el mismo día de la misma foto tienen que llamarse igual, y uno
    bajado hoy de una foto de ayer tiene que decir que es de ayer.
    """
    return f"deuda_socios_{timezone.localtime(foto.calculado_at):%Y-%m-%d_%H%M}.xlsx"


# --------------------------------------------------------------------------- #
# Hojas
# --------------------------------------------------------------------------- #

def _hoja_portada(ws, foto, filas, poblacion, categorias, cuotas_min, usuario):
    ws.title = "Informe"
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 52

    ws.merge_cells("A1:B1")
    c = ws["A1"]
    c.value = "Deuda de socios"
    c.font = _TITULO
    c.fill = _RELLENO_TITULO
    c.alignment = Alignment(vertical="center", indent=1)
    ws.row_dimensions[1].height = 28

    total = sum(f.importe for f in filas)
    cupones = sum(f.cuotas for f in filas)
    datos = [
        ("", ""),
        # Las dos en hora local: mezclarlas con UTC hacía que el archivo dijera
        # que se generó tres horas ANTES de que se calcularan los datos.
        ("Datos calculados el",
         timezone.localtime(foto.calculado_at).strftime("%d/%m/%Y a las %H:%M")),
        ("Archivo generado el",
         timezone.localtime().strftime("%d/%m/%Y a las %H:%M")),
        ("Generado por", usuario or "—"),
        ("", ""),
        ("Población incluida", poblacion or "Socios activos"),
        ("Categorías", categorias or "Todas"),
        ("Mínimo de cuotas", str(cuotas_min) if cuotas_min else "Sin mínimo"),
        ("", ""),
        ("Personas con deuda", len(filas)),
        ("Comprobantes impagos", cupones),
        ("Importe total adeudado", float(total)),
        ("", ""),
    ]
    fila = 2
    for etiqueta, valor in datos:
        if etiqueta:
            ws.cell(row=fila, column=1, value=etiqueta).font = _ETIQUETA
            celda = ws.cell(row=fila, column=2, value=valor)
            if etiqueta == "Importe total adeudado":
                celda.number_format = MONEDA
                celda.font = _TOTAL
            elif isinstance(valor, int):
                celda.number_format = "#,##0"
        fila += 1

    nota = ws.cell(
        row=fila + 1, column=1,
        value="Criterio: se cuenta como deuda todo comprobante con saldo impago, "
              "excluido el CUPÓN OPCIONAL PROFORMA (cuota social voluntaria), que "
              "se emite todos los meses y se paga sólo si el socio quiere. "
              "Incluirlo pondría en mora a socios que están al día.")
    nota.alignment = Alignment(wrap_text=True, vertical="top")
    nota.font = Font(name="Calibri", size=9, italic=True, color="595959")
    ws.merge_cells(start_row=fila + 1, start_column=1, end_row=fila + 4, end_column=2)


def _hoja_detalle(ws, filas):
    ws.freeze_panes = "A2"
    for i, (titulo, _attr, ancho, _fmt) in enumerate(COLUMNAS, start=1):
        celda = ws.cell(row=1, column=i, value=titulo)
        celda.font = _CABECERA
        celda.fill = _RELLENO_CABECERA
        celda.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = ancho
    ws.row_dimensions[1].height = 30

    for r, fila in enumerate(filas, start=2):
        for i, (_titulo, attr, _ancho, fmt) in enumerate(COLUMNAS, start=1):
            celda = ws.cell(row=r, column=i, value=_valor(fila, attr))
            if fmt:
                celda.number_format = fmt
            celda.border = _BORDE_FINO
            if r % 2 == 0:
                celda.fill = _RELLENO_ALTERNO

    fin = len(filas) + 1
    if filas:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNAS))}{fin}"
        total = fin + 1
        # Fila de totales fuera del autofiltro, para que no se sume a sí misma
        # ni desaparezca al filtrar. Las fórmulas van como fórmula y no como
        # valor: si el usuario filtra, Excel las recalcula.
        etiqueta = ws.cell(row=total, column=_COL_ETIQUETA_TOTAL, value="TOTAL")
        etiqueta.font = _TOTAL
        etiqueta.fill = _RELLENO_TOTAL
        for col, formato in _COLS_SUMA:
            letra = get_column_letter(col)
            celda = ws.cell(row=total, column=col,
                            value=f"=SUM({letra}2:{letra}{fin})")
            celda.font = _TOTAL
            celda.fill = _RELLENO_TOTAL
            celda.number_format = formato or "#,##0"


def _hoja_categorias(ws, filas):
    ws.freeze_panes = "A2"
    encabezados = ["Categoría", "Personas", "Cuotas adeudadas", "Importe adeudado",
                   "Cuota social", "Actividades", "Otros conceptos",
                   "Importe promedio"]
    anchos = [26, 12, 18, 18, 16, 16, 16, 18]
    for i, (titulo, ancho) in enumerate(zip(encabezados, anchos), start=1):
        celda = ws.cell(row=1, column=i, value=titulo)
        celda.font = _CABECERA
        celda.fill = _RELLENO_CABECERA
        celda.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = ancho
    ws.row_dimensions[1].height = 30

    resumen: dict[str, dict] = {}
    for f in filas:
        acc = resumen.setdefault(f.categoria, {
            "n": 0, "cuotas": 0, "importe": 0.0,
            "cuota": 0.0, "activ": 0.0, "otros": 0.0})
        acc["n"] += 1
        acc["cuotas"] += f.cuotas
        acc["importe"] += float(f.importe or 0)
        acc["cuota"] += float(f.importe_cuota_social or 0)
        acc["activ"] += float(f.importe_actividades or 0)
        acc["otros"] += float(f.importe_otros or 0)

    orden = sorted(resumen.items(), key=lambda kv: -kv[1]["importe"])
    for r, (categoria, acc) in enumerate(orden, start=2):
        ws.cell(row=r, column=1, value=categoria)
        ws.cell(row=r, column=2, value=acc["n"]).number_format = "#,##0"
        ws.cell(row=r, column=3, value=acc["cuotas"]).number_format = "#,##0"
        for col, clave in ((4, "importe"), (5, "cuota"), (6, "activ"), (7, "otros")):
            ws.cell(row=r, column=col, value=acc[clave]).number_format = MONEDA
        ws.cell(row=r, column=8,
                value=acc["importe"] / acc["n"] if acc["n"] else 0).number_format = MONEDA
        for col in range(1, 9):
            ws.cell(row=r, column=col).border = _BORDE_FINO

    if orden:
        total = len(orden) + 2
        ws.cell(row=total, column=1, value="TOTAL").font = _TOTAL
        ws.cell(row=total, column=1).fill = _RELLENO_TOTAL
        for col in range(2, 8):
            letra = get_column_letter(col)
            celda = ws.cell(row=total, column=col,
                            value=f"=SUM({letra}2:{letra}{total - 1})")
            celda.font = _TOTAL
            celda.fill = _RELLENO_TOTAL
            celda.number_format = MONEDA if col >= 4 else "#,##0"
