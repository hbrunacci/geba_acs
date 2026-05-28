"""
consulta_fallecidos.py
======================
Lee la hoja "05. Presuntos fallecidos" del Excel de depuración de GEBA,
consulta busca-datos.com.ar por DNI de cada socio y guarda los resultados
en un CSV con los campos parseados.

Uso:
    python consulta_fallecidos.py

Requisitos:
    pip install requests beautifulsoup4 openpyxl pandas

Salida:
    resultados_fallecidos.csv
"""

from __future__ import annotations

import csv
import re
import time
import random
from pathlib import Path

import requests
import pandas as pd
from bs4 import BeautifulSoup


# ── Configuración ──────────────────────────────────────────────────────────────
EXCEL_PATH  = "depuracion_GEBA_definitivo.xlsx"
SHEET_NAME  = "05. Presuntos fallecidos"
OUTPUT_CSV  = "resultados_fallecidos.csv"
URL         = "https://www.busca-datos.com.ar/"
PAUSA_MIN   = 1.5    # segundos mínimos entre consultas
PAUSA_MAX   = 3.0    # segundos máximos entre consultas
REINTENTOS  = 3      # intentos ante error de red
# ──────────────────────────────────────────────────────────────────────────────

FIELDNAMES = [
    "nro_socio", "dni_consultado", "apellido_socio", "nombre_socio",
    "fecha_nac_socio", "edad_socio",
    # campos parseados del sitio
    "cuit", "sexo", "nombre_completo_sitio", "edad_sitio",
    "estado",           # VIVO / FALLECIDO / SIN_DATOS / ERROR_RED
    "localidades",      # domicilios separados por " | "
    "estado_consulta",  # OK / SIN_DATOS / SIN_TARJETAS / ERROR_RED
    "mensaje",          # detalle si hubo error
]


# ── Carga de socios ────────────────────────────────────────────────────────────
def cargar_socios(excel_path: str, sheet_name: str) -> pd.DataFrame:
    df = pd.read_excel(excel_path, sheet_name=sheet_name, header=None)
    df.columns = df.iloc[1]
    df = df.iloc[2:].reset_index(drop=True)
    df.columns.name = None
    df = df.rename(columns={
        "Nº Socio": "nro_socio", "DNI": "dni",
        "Apellido": "apellido",  "Nombre": "nombre",
        "F. Nac.":  "fecha_nac", "Edad": "edad",
    })
    df["dni"] = pd.to_numeric(df["dni"], errors="coerce")
    df = df.dropna(subset=["dni"])
    df["dni"] = df["dni"].astype(int).astype(str)
    df["nro_socio"] = pd.to_numeric(df["nro_socio"], errors="coerce").fillna(0).astype(int)
    return df[["nro_socio", "dni", "apellido", "nombre", "fecha_nac", "edad"]]


# ── Consulta web ───────────────────────────────────────────────────────────────
def get_hidden_fields(soup: BeautifulSoup) -> dict[str, str]:
    hidden: dict[str, str] = {}
    for field in soup.find_all("input", {"type": "hidden"}):
        name  = str(field.get("name", ""))
        value = str(field.get("value", ""))
        if name:
            hidden[name] = value
    return hidden


def consultar_dni(session: requests.Session, dni: str) -> str:
    """Devuelve el HTML de respuesta del sitio para un DNI dado."""
    headers = {
        "User-Agent":      "Mozilla/5.0",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-AR,es;q=0.9",
        "Content-Type":    "application/x-www-form-urlencoded",
        "Origin":          URL.rstrip("/"),
        "Referer":         URL,
    }
    get_resp = session.get(URL, headers=headers, timeout=20)
    get_resp.raise_for_status()
    soup   = BeautifulSoup(get_resp.text, "html.parser")
    hidden = get_hidden_fields(soup)
    payload = {
        **hidden,
        "__Page_BotonClickeado": "BTNobtener",
        "__EVENTTARGET":         "BTNobtener",
        "__EVENTARGUMENT":       "",
        "txtBusqueda":           dni,
        "BTNobtener":            "Consultar Ahora",
    }
    post_resp = session.post(URL, headers=headers, data=payload, timeout=20)
    post_resp.raise_for_status()
    return post_resp.text


# ── Parser de resultado ────────────────────────────────────────────────────────
def parsear_tarjeta(tag) -> dict:
    """
    Extrae todos los campos de una tarjeta <a id="IDCUITxxxxxxxx">.

    Estructura del ID:  IDCUIT  PP  DDDDDDDD  V
      PP        = prefijo CUIT (20, 23, 24, 27…)
      DDDDDDDD  = 8 dígitos centrales del DNI
      V         = dígito verificador
    """
    cuit_id = tag.get("id", "")                          # ej: IDCUIT20023012015
    digitos  = cuit_id.replace("IDCUIT", "")             # ej: 20023012015
    prefijo  = digitos[:2]                               # ej: 20
    dni_num  = digitos[2:10]                             # ej: 02301201
    verif    = digitos[10:]                              # ej: 5
    cuit     = f"{prefijo}-{dni_num}-{verif}"

    # Sexo por prefijo
    sexo = "Femenino" if prefijo in ("23", "27") else "Masculino"

    # Primer div = nombre + edad + posible ✝
    primer_div = tag.find("div")
    texto_raw  = primer_div.get_text(separator=" ", strip=True) if primer_div else ""

    # Nombre limpio: sacar emojis, ✝ y "(N años)"
    nombre_limpio = re.sub(r'\(\d+\s*años?\)', '', texto_raw)   # quita edad
    nombre_limpio = re.sub(r'[^\w\sáéíóúüñÁÉÍÓÚÜÑ,.]', '', nombre_limpio)  # quita emojis/✝
    nombre_limpio = re.sub(r'\s+', ' ', nombre_limpio).strip()

    # Edad que publica el sitio
    edad_match = re.search(r'\((\d+)\s*años?\)', texto_raw)
    edad_sitio = edad_match.group(1) if edad_match else ""

    # Estado: fallecido si tiene el elemento .det-cruz con title "Fallecido"
    cruz = tag.find(class_="det-cruz")
    if cruz and "fallecido" in cruz.get("title", "").lower():
        estado = "FALLECIDO"
    elif "fallecido" in texto_raw.lower():
        estado = "FALLECIDO"
    else:
        estado = "VIVO"

    # Localidades: divs que contienen coma (patrón "Ciudad, Provincia")
    locs = []
    for d in tag.find_all("div"):
        txt = d.get_text(strip=True)
        # Excluir divs que contienen DNI/CUIT o la línea de nombre
        if re.search(r'[A-ZÁÉÍÓÚ][a-záéíóú].*,\s*[A-ZÁÉÍÓÚ]', txt) and \
           "DNI" not in txt and "CUIT" not in txt and "años" not in txt:
            loc_clean = re.sub(r'^[^\w]+', '', txt)   # quita emoji inicial
            locs.append(loc_clean)

    return {
        "cuit":                 cuit,
        "sexo":                 sexo,
        "nombre_completo_sitio": nombre_limpio,
        "edad_sitio":           edad_sitio,
        "estado":               estado,
        "localidades":          " | ".join(locs),
    }


def parsear_html(html: str, dni: str) -> list[dict]:
    """
    Parsea el HTML completo y devuelve una lista de registros
    (puede haber más de un CUIT por DNI).
    """
    soup    = BeautifulSoup(html, "html.parser")
    tarjetas = soup.find_all(id=re.compile(r"^IDCUIT"))

    # Sin datos
    error_lbl = soup.find(id="LBLcompletarDATOS")
    if error_lbl and not tarjetas:
        return [{"dni_consultado": dni,
                 "estado_consulta": "SIN_DATOS",
                 "mensaje": error_lbl.get_text(strip=True)}]

    if not tarjetas:
        return [{"dni_consultado": dni,
                 "estado_consulta": "SIN_TARJETAS",
                 "mensaje": "No se encontraron tarjetas en la respuesta"}]

    resultados = []
    for tag in tarjetas:
        r = parsear_tarjeta(tag)
        r["dni_consultado"]  = dni
        r["estado_consulta"] = "OK"
        r["mensaje"]         = ""
        resultados.append(r)

    return resultados


# ── Control de progreso ────────────────────────────────────────────────────────
def dnis_ya_procesados(output_csv: str) -> set[str]:
    path = Path(output_csv)
    if not path.exists():
        return set()
    procesados = set()
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            procesados.add(row.get("dni_consultado", ""))
    return procesados


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    print("=" * 65)
    print("  Consulta masiva DNI — Presuntos fallecidos GEBA")
    print("=" * 65)

    print(f"\n→ Leyendo {EXCEL_PATH} ...")
    df = cargar_socios(EXCEL_PATH, SHEET_NAME)
    total = len(df)
    print(f"  {total} socios a consultar")

    ya_procesados = dnis_ya_procesados(OUTPUT_CSV)
    pendientes    = df[~df["dni"].isin(ya_procesados)]
    print(f"  {len(ya_procesados)} ya procesados — {len(pendientes)} pendientes\n")

    output_path   = Path(OUTPUT_CSV)
    escribir_hdr  = not output_path.exists()
    session       = requests.Session()
    procesados    = len(ya_procesados)
    errores       = 0

    with open(output_path, "a", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=FIELDNAMES,
                                extrasaction="ignore", lineterminator="\n")
        if escribir_hdr:
            writer.writeheader()

        for _, row in pendientes.iterrows():
            nro_socio = row["nro_socio"]
            dni       = row["dni"]
            apellido  = str(row["apellido"])
            nombre    = str(row["nombre"])

            print(f"  [{procesados+1}/{total}] {apellido}, {nombre} (DNI {dni}) ...",
                  end=" ", flush=True)

            # Datos base del socio (siempre presentes en cada fila)
            base = {
                "nro_socio":       str(nro_socio),
                "dni_consultado":  dni,
                "apellido_socio":  apellido,
                "nombre_socio":    nombre,
                "fecha_nac_socio": str(row.get("fecha_nac", "")),
                "edad_socio":      str(row.get("edad", "")),
            }

            # Consulta con reintentos
            html = None
            for intento in range(1, REINTENTOS + 1):
                try:
                    html = consultar_dni(session, dni)
                    break
                except Exception as e:
                    if intento < REINTENTOS:
                        print(f"\n    ⚠ intento {intento} fallido: {e} — reintentando...",
                              end=" ")
                        time.sleep(5)
                    else:
                        fila_error = {**base,
                                      "estado_consulta": "ERROR_RED",
                                      "mensaje": str(e)}
                        writer.writerow(fila_error)
                        csvfile.flush()
                        errores += 1
                        print("ERROR_RED")

            if html is None:
                procesados += 1
                time.sleep(random.uniform(PAUSA_MIN, PAUSA_MAX))
                continue

            # Parsear y escribir (puede ser 1 o más filas por DNI)
            registros = parsear_html(html, dni)
            estado_resumen = []
            for reg in registros:
                fila = {**base, **reg}
                writer.writerow(fila)
                estado_resumen.append(
                    f"{reg.get('nombre_completo_sitio','?')} [{reg.get('estado','?')}]"
                )
            csvfile.flush()
            print(" / ".join(estado_resumen))

            procesados += 1
            time.sleep(random.uniform(PAUSA_MIN, PAUSA_MAX))

    print(f"\n{'='*65}")
    print(f"  Completado: {procesados} procesados, {errores} con error de red")
    print(f"  Resultado: {output_path.resolve()}")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    main()
