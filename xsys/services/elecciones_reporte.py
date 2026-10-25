"""Reporte en PDF de los socios acreditados en una elección, por hora y por mesa.

Es para repartir a las listas durante la jornada: quién se acreditó, en qué
mesa y a qué hora. Sale del mismo dato que la pantalla de Elecciones
(``Clientes_Listas_Precios.Fecha_Proceso``, ver ``xsys.services.elecciones``),
cruzado con ``Clientes`` para el nombre.

Tiene dos partes:

1. El resumen: una grilla hora × mesa con la cantidad de acreditados, el total
   de cada hora y el acumulado.
2. El detalle: para cada hora, cada mesa con sus acreditados ordenados por hora
   exacta, con número de socio, apellido y nombre, y número de orden.

Las horas son las reales del registro, sin redondear a la jornada: si alguien
se acreditó a las 09:50 figura en "09 a 10", que es lo que va a querer
controlar una lista.

``demo()`` arma datos ficticios con la misma forma, para mostrar el reporte
antes de la elección sin exponer a ningún socio real.
"""

from __future__ import annotations

import datetime as _dt
import io
import random
from collections import defaultdict

from django.utils import timezone

from xsys.services import elecciones as E


def acreditados(id_lista: int) -> list[dict]:
    """Los que ya se acreditaron, con su mesa, hora y nombre."""
    filas = E._consultar("""
        SELECT  L.Nro_Mesa, L.Nro_Orden, L.Fecha_Proceso, L.Id_Cliente,
                RTRIM(ISNULL(C.Apellido, '')), RTRIM(ISNULL(C.Nombre, ''))
        FROM    Clientes_Listas_Precios L
                LEFT JOIN Clientes C ON C.Id_Cliente = L.Id_Cliente
        WHERE   L.Id_Lista_Precio = ? AND L.Fecha_Proceso IS NOT NULL
        ORDER BY L.Fecha_Proceso
    """, (id_lista,))
    return [{
        "mesa": int(m) if m is not None else 0,
        "orden": int(o) if o is not None else None,
        "fecha": f,
        "socio": int(c),
        "apellido": (a or "").strip(),
        "nombre": (n or "").strip(),
    } for m, o, f, c, a, n in filas]


_APELLIDOS = ("GARCIA", "FERNANDEZ", "GONZALEZ", "RODRIGUEZ", "LOPEZ", "MARTINEZ",
              "PEREZ", "GOMEZ", "DIAZ", "SANCHEZ", "ROMERO", "SOSA", "TORRES",
              "ALVAREZ", "RUIZ", "RAMIREZ", "FLORES", "BENITEZ", "ACOSTA", "MEDINA",
              "HERRERA", "SUAREZ", "AGUIRRE", "GIMENEZ", "MOLINA", "SILVA", "CASTRO",
              "ROJAS", "ORTIZ", "LUNA", "JUAREZ", "CABRERA", "RIOS", "MORALES")
_NOMBRES = ("MARIA", "JUAN", "CARLOS", "ANA", "LUCIA", "MARTIN", "SOFIA", "DIEGO",
            "LAURA", "PABLO", "VALENTINA", "JORGE", "CAROLINA", "MATIAS", "PAULA",
            "SANTIAGO", "FLORENCIA", "NICOLAS", "GABRIELA", "ALEJANDRO", "MARIANA",
            "FEDERICO", "ROCIO", "GUSTAVO", "CECILIA", "TOMAS", "VERONICA", "LUIS")


def demo(fecha: _dt.date, mesas: int = 11, cantidad: int = 2400) -> list[dict]:
    """Acreditados ficticios: nombres inventados, horarios con la forma de una
    jornada real (arranque lento, pico al mediodía y a la tarde)."""
    rnd = random.Random(20261025)
    pesos_hora = {10: 5, 11: 9, 12: 12, 13: 11, 14: 9, 15: 10, 16: 12, 17: 11, 18: 9, 19: 7, 20: 5}
    horas = [h for h, p in pesos_hora.items() for _ in range(p)]
    orden_por_mesa: dict[int, int] = defaultdict(int)
    filas = []
    for _ in range(cantidad):
        mesa = rnd.randint(1, mesas)
        orden_por_mesa[mesa] += rnd.randint(1, 3)
        h = rnd.choice(horas)
        filas.append({
            "mesa": mesa,
            "orden": orden_por_mesa[mesa],
            "fecha": _dt.datetime.combine(fecha, _dt.time(h, rnd.randint(0, 59), rnd.randint(0, 59))),
            "socio": rnd.randint(100000, 999999),
            "apellido": rnd.choice(_APELLIDOS),
            "nombre": rnd.choice(_NOMBRES),
        })
    filas.sort(key=lambda r: r["fecha"])
    return filas


def _franja(h: int) -> str:
    return f"{h:02d}:00 a {h + 1:02d}:00"


def pdf(eleccion: dict, filas: list[dict], *, demo: bool = False) -> bytes:
    """Arma el PDF. ``eleccion`` es un registro de ``elecciones.listar()``."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)

    AZUL = colors.HexColor("#0b3d6e")
    GRIS = colors.HexColor("#5b6675")
    LINEA = colors.HexColor("#c9d2dc")
    FONDO = colors.HexColor("#eef3f8")
    ROJO = colors.HexColor("#b42318")

    st = getSampleStyleSheet()
    s_tit = ParagraphStyle("tit", parent=st["Title"], textColor=AZUL, fontSize=18, spaceAfter=2)
    s_sub = ParagraphStyle("sub", parent=st["Normal"], textColor=GRIS, fontSize=9.5, alignment=TA_CENTER)
    s_h1 = ParagraphStyle("h1", parent=st["Heading2"], textColor=AZUL, fontSize=13, spaceBefore=10, spaceAfter=4)
    s_h2 = ParagraphStyle("h2", parent=st["Heading4"], textColor=colors.black, fontSize=10, spaceBefore=6, spaceAfter=2)
    s_demo = ParagraphStyle("demo", parent=st["Normal"], textColor=ROJO, fontSize=10,
                            alignment=TA_CENTER, fontName="Helvetica-Bold")

    generado = timezone.localtime()
    nombre = eleccion.get("nombre") or "Elecciones"
    total = len(filas)
    padron = eleccion.get("padron") or 0

    por_hora: dict[int, list[dict]] = defaultdict(list)
    for f in filas:
        por_hora[f["fecha"].hour].append(f)
    horas = sorted(por_hora)
    mesas = sorted({f["mesa"] for f in filas}) or list(range(1, 12))

    def pie(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(GRIS)
        txt = f"{nombre} · Socios acreditados · generado {generado:%d/%m/%Y %H:%M}"
        canvas.drawString(15 * mm, 10 * mm, txt)
        canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"Página {doc.page}")
        if demo:
            canvas.setFillColor(ROJO)
            canvas.setFont("Helvetica-Bold", 8)
            canvas.drawCentredString(A4[0] / 2, A4[1] - 9 * mm, "DEMO · DATOS FICTICIOS")
        canvas.restoreState()

    historia = [Paragraph(f"{nombre}", s_tit),
                Paragraph("Socios acreditados por hora y por mesa", s_sub),
                Spacer(1, 3)]
    if demo:
        historia.append(Paragraph("DEMO: nombres, números de socio y horarios son ficticios. "
                                  "El reporte real sale de xSys durante la jornada.", s_demo))
    linea = f"Corte al {generado:%d/%m/%Y %H:%M} · {total:,} acreditados".replace(",", ".")
    if padron:
        pct = f"{100 * total / padron:.1f}".replace(".", ",")
        linea += f" de un padrón de {padron:,}".replace(",", ".") + f" ({pct} %)"
    historia += [Paragraph(linea, s_sub), Spacer(1, 6)]

    # 1. Resumen hora × mesa.
    historia.append(Paragraph("Resumen por hora y mesa", s_h1))
    cab = ["Hora"] + [f"M{m}" for m in mesas] + ["Total", "Acum."]
    datos = [cab]
    acum = 0
    for h in horas:
        cuenta = defaultdict(int)
        for f in por_hora[h]:
            cuenta[f["mesa"]] += 1
        acum += len(por_hora[h])
        datos.append([_franja(h)] + [cuenta.get(m, 0) or "" for m in mesas]
                     + [len(por_hora[h]), acum])
    tot_mesa = defaultdict(int)
    for f in filas:
        tot_mesa[f["mesa"]] += 1
    datos.append(["Total"] + [tot_mesa.get(m, 0) for m in mesas] + [total, ""])
    ancho_hora = 26 * mm
    resto = (A4[0] - 30 * mm - ancho_hora) / (len(mesas) + 2)
    t = Table(datos, colWidths=[ancho_hora] + [resto] * (len(mesas) + 2), repeatRows=1)
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
        ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 8),
        ("FONT", (-2, 1), (-2, -1), "Helvetica-Bold", 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("BACKGROUND", (0, -1), (-1, -1), FONDO),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.4, LINEA),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#f7f9fb")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    historia.append(t)

    # 2. Detalle: cada hora en su página, y dentro cada mesa.
    for h in horas:
        historia.append(PageBreak())
        historia.append(Paragraph(f"{_franja(h)} · {len(por_hora[h])} acreditados", s_h1))
        por_mesa: dict[int, list[dict]] = defaultdict(list)
        for f in por_hora[h]:
            por_mesa[f["mesa"]].append(f)
        for m in sorted(por_mesa):
            gente = sorted(por_mesa[m], key=lambda r: r["fecha"])
            filas_t = [["Hora", "N° socio", "Apellido y nombre", "N° orden"]]
            filas_t += [[f["fecha"].strftime("%H:%M"), f["socio"],
                         f"{f['apellido']}, {f['nombre']}".strip(", "),
                         f["orden"] if f["orden"] is not None else ""] for f in gente]
            tm = Table(filas_t, colWidths=[18 * mm, 24 * mm, 110 * mm, 22 * mm], repeatRows=1)
            tm.setStyle(TableStyle([
                ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5),
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
                ("TEXTCOLOR", (0, 0), (-1, 0), AZUL),
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, AZUL),
                ("LINEBELOW", (0, 1), (-1, -1), 0.25, LINEA),
                ("ALIGN", (0, 0), (1, -1), "CENTER"),
                ("ALIGN", (3, 0), (3, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            titulo = Paragraph(f"Mesa {m} · {len(gente)} acreditado{'s' if len(gente) != 1 else ''}", s_h2)
            # El título no queda solo al pie de una página, separado de su tabla.
            historia.append(KeepTogether([titulo, tm]) if len(gente) <= 25 else titulo)
            if len(gente) > 25:
                historia.append(tm)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=14 * mm, bottomMargin=16 * mm,
                            title=f"{nombre} · Acreditados", author="GEBA ACS")
    doc.build(historia, onFirstPage=pie, onLaterPages=pie)
    return buf.getvalue()
