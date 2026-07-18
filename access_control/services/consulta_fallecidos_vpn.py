"""
consulta_fallecidos_vpn.py
==========================
Lee la hoja "05. Presuntos fallecidos" del Excel de depuración de GEBA,
consulta busca-datos.com.ar por DNI de cada socio y guarda los resultados
en un CSV con los campos parseados.

Usa ProtonVPN con archivos .ovpn locales para rotar IP entre consultas.

Uso:
    sudo python consulta_fallecidos_vpn.py

Requisitos:
    pip install requests beautifulsoup4 openpyxl pandas
    sudo apt install openvpn

    ⚠ Necesita permisos de root/sudo para manejar interfaces VPN (tun).

Estructura de archivos esperada:
    proton_configs/          ← un nivel arriba del script
        ar-free-01.protonvpn.com.udp.ovpn
        jp-free-01.protonvpn.com.udp.ovpn
        ...
    consulta_fallecidos_vpn.py   ← este script
    vpn_credentials.txt          ← usuario OpenVPN (línea 1) y contraseña (línea 2)
    vpn_servidores_fallidos.txt  ← se crea automáticamente

Parámetros clave (ajustar abajo):
    ROTATE_EVERY    = cambiar VPN cada N consultas
    COUNTRY_FILTER  = prefijo de país, ej: "ar", "jp", "us" — None = cualquiera

Salida:
    resultados_fallecidos.csv
"""

from __future__ import annotations

import csv
import json
import os
import re
import signal
import subprocess
import sys
import time
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
import pandas as pd
from bs4 import BeautifulSoup


# ── Configuración general ──────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).resolve().parent

# INPUT: CSV con los pendientes (BLOQUEADO + SIN_DATOS) generado por el filtro.
#        Si se deja vacío ("") el script lee directamente del Excel.
INPUT_CSV   = BASE_DIR / "pendientes_sin_datos_filtrado.csv"   # ← fuente de entrada

EXCEL_PATH  = BASE_DIR / "depuracion_GEBA_definitivo.xlsx"     # solo si INPUT_CSV = ""
SHEET_NAME  = "05. Presuntos fallecidos"

OUTPUT_CSV  = BASE_DIR / "resultados_reprocesados.csv"         # salida de esta corrida
URL         = "https://www.busca-datos.com.ar/"

PAUSA_MIN  = 2    # segundos mínimos entre consultas
PAUSA_MAX  = 3    # segundos máximos entre consultas
REINTENTOS = 3    # reintentos ante error de red

# ── Configuración VPN ──────────────────────────────────────────────────────────
ROTATE_EVERY        = 30     # rotar VPN cada N consultas (0 = desactivar rotación)
PROTON_CONFIGS_DIR  = BASE_DIR.parent / "proton_configs"  # carpeta con los .ovpn
USED_CONFIGS_DIR    = PROTON_CONFIGS_DIR / "ar"            # carpeta donde se mueven configs ya usados
COUNTRY_FILTER      = None   # prefijo de país: "ar", "jp", "us", etc. None = cualquiera
CREDENTIALS_FILE    = BASE_DIR / "vpn_credentials.txt"    # usuario\ncontraseña (2 líneas)
FAILED_SERVERS_FILE  = BASE_DIR / "vpn_servidores_bloqueados.json"
BLOQUEO_CUARENTENA_H = 8      # horas que un servidor queda fuera de uso tras bloqueo
# ──────────────────────────────────────────────────────────────────────────────

FIELDNAMES = [
    "nro_socio", "dni_consultado", "apellido_socio", "nombre_socio",
    "fecha_nac_socio", "edad_socio",
    "cuit", "sexo", "nombre_completo_sitio", "edad_sitio",
    "estado",
    "localidades",
    "estado_consulta",
    "mensaje",
    "vpn_servidor",   # nombre del archivo .ovpn usado
    "vpn_pais",       # prefijo de país extraído del nombre
    "vpn_ip",         # IP pública verificada al conectar
]


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO VPN — ProtonVPN con configs locales y DNS fijo
# ══════════════════════════════════════════════════════════════════════════════
#
#  OpenVPN corre normalmente pero con --dhcp-option DNS 8.8.8.8 para que
#  el DNS del sistema siga funcionando al conectar, en lugar de usar el
#  DNS que empuja el servidor VPN (que solía fallar).


class ProtonVPNClient:
    """
    Gestiona conexiones ProtonVPN via NetworkManager (nmcli).

    Al inicializar:
      1. Borra todas las conexiones VPN existentes en NetworkManager
      2. Reimporta todos los .ovpn de la carpeta
      3. Configura credenciales automáticamente

    Al rotar: usa nmcli connection up/down para cambiar de servidor.
    """

    def __init__(
        self,
        configs_dir: Path = PROTON_CONFIGS_DIR,
        credentials_file: Path = CREDENTIALS_FILE,
        failed_servers_file: Path = FAILED_SERVERS_FILE,
        country_filter: Optional[str] = None,
    ) -> None:
        self.configs_dir         = Path(configs_dir)
        self.credentials_file    = Path(credentials_file)
        self.failed_servers_file = Path(failed_servers_file)
        self.country_filter      = country_filter.lower() if country_filter else None

        self._connections: list[str] = []   # nombres de conexión nmcli
        self._index: int = 0
        self.current_servidor: str = ""
        self.current_pais: str = ""
        self.current_ip: str = ""

        self._failed: set[str] = self._load_failed()

        # Leer credenciales
        creds = self.credentials_file.read_text().splitlines()
        self._username = creds[0].strip()
        self._password = creds[1].strip() if len(creds) > 1 else ""

        self._setup_connections()

    # ── Persistencia de servidores bloqueados (con timestamp) ────────────────
    def _load_failed(self) -> set[str]:
        """
        Lee el JSON de servidores bloqueados y devuelve solo los que todavía
        están en cuarentena (bloqueados hace menos de BLOQUEO_CUARENTENA_H horas).
        Los que ya cumplieron la cuarentena se eliminan del archivo automáticamente.
        """
        if not self.failed_servers_file.exists():
            return set()

        try:
            with open(self.failed_servers_file, encoding="utf-8") as f:
                data: dict = json.load(f)
        except (json.JSONDecodeError, ValueError):
            # Archivo corrupto o formato viejo (.txt) — ignorar
            print("  [VPN] Archivo de bloqueados corrupto o formato viejo — ignorando.", flush=True)
            return set()

        ahora      = datetime.now()
        cuarentena = timedelta(hours=BLOQUEO_CUARENTENA_H)
        activos    = {}
        liberados  = []

        for nombre, info in data.items():
            try:
                ts = datetime.fromisoformat(info["bloqueado_en"])
            except (KeyError, ValueError):
                continue
            if ahora - ts < cuarentena:
                activos[nombre] = info
            else:
                liberados.append(nombre)

        # Reescribir el archivo sin los que ya se liberaron
        if liberados:
            with open(self.failed_servers_file, "w", encoding="utf-8") as f:
                json.dump(activos, f, indent=2, ensure_ascii=False)
            print(
                f"  [VPN] {len(liberados)} servidor(es) liberado(s) de cuarentena: "
                + ", ".join(liberados),
                flush=True
            )

        if activos:
            print(
                f"  [VPN] {len(activos)} servidor(es) en cuarentena (bloqueados < {BLOQUEO_CUARENTENA_H}h):",
                flush=True
            )
            for nombre, info in activos.items():
                ts  = datetime.fromisoformat(info["bloqueado_en"])
                eta = ts + cuarentena
                print(f"    • {nombre}  (bloqueado {ts:%d/%m %H:%M} — libre a las {eta:%H:%M})", flush=True)

        return set(activos.keys())

    def _persist_failed(self, name: str, motivo: str = "error de conexión VPN") -> None:
        """
        Registra un servidor como bloqueado con fecha/hora actual y motivo.
        Si el archivo ya existe, agrega o actualiza la entrada.
        """
        ahora = datetime.now()

        # Leer registro existente
        data: dict = {}
        if self.failed_servers_file.exists():
            try:
                with open(self.failed_servers_file, encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, ValueError):
                data = {}

        data[name] = {
            "bloqueado_en": ahora.isoformat(timespec="seconds"),
            "libre_desde":  (ahora + timedelta(hours=BLOQUEO_CUARENTENA_H)).isoformat(timespec="seconds"),
            "motivo":       motivo,
        }

        with open(self.failed_servers_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        self._failed.add(name)
        print(
            f"  [VPN] Servidor {name!r} en cuarentena hasta "
            f"{(ahora + timedelta(hours=BLOQUEO_CUARENTENA_H)):%d/%m/%Y %H:%M:%S}  ({motivo})",
            flush=True
        )

    # ── Setup: borrar y reimportar todas las conexiones ───────────────────────
    def _setup_connections(self) -> None:
        """Borra conexiones VPN existentes y reimporta todos los .ovpn."""
        if not self.configs_dir.exists():
            raise RuntimeError(f"No se encontró la carpeta: {self.configs_dir}")

        ovpn_files = sorted(self.configs_dir.glob("*.ovpn"))
        if not ovpn_files:
            raise RuntimeError(f"No hay archivos .ovpn en {self.configs_dir}")

        if self.country_filter:
            ovpn_files = [f for f in ovpn_files
                         if f.name.lower().startswith(self.country_filter + "-")]

        print(f"  [VPN] Borrando conexiones VPN anteriores...", flush=True)
        # Borrar todas las conexiones openvpn existentes
        result = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
            capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and "vpn" in parts[1].lower():
                subprocess.run(["nmcli", "connection", "delete", parts[0]],
                               capture_output=True)

        print(f"  [VPN] Importando {len(ovpn_files)} configs...", flush=True)
        imported = []
        for ovpn in ovpn_files:
            name = ovpn.stem.replace("_", ".")   # au-11_protonvpn_tcp → au-11.protonvpn.tcp
            if name in self._failed:
                continue
            # Importar
            r = subprocess.run(
                ["nmcli", "connection", "import", "type", "openvpn", "file", str(ovpn)],
                capture_output=True, text=True
            )
            if r.returncode != 0:
                print(f"    ⚠ No se pudo importar {ovpn.name}: {r.stderr.strip()}", flush=True)
                continue

            # Configurar credenciales en el archivo .nmconnection
            nm_file = Path(f"/etc/NetworkManager/system-connections/{name}.nmconnection")
            if nm_file.exists():
                txt = nm_file.read_text()
                # Quitar password-flags=1 y agregar password-flags=0 + username
                lines_out = []
                vpn_section = False
                username_added = False
                for line in txt.splitlines():
                    if line.strip() == "[vpn]":
                        vpn_section = True
                        lines_out.append(line)
                        continue
                    if vpn_section and not username_added:
                        lines_out.append(f"password-flags=0")
                        lines_out.append(f"username={self._username}")
                        username_added = True
                        vpn_section = False
                    if "password-flags=" in line:
                        continue  # quitar cualquier password-flags existente
                    if line.strip().startswith("username="):
                        continue  # quitar username existente (lo pusimos arriba)
                    lines_out.append(line)

                # Agregar sección vpn-secrets si no existe
                if "[vpn-secrets]" not in txt:
                    lines_out.append("")
                    lines_out.append("[vpn-secrets]")
                    lines_out.append(f"password={self._password}")
                else:
                    # Reemplazar password existente
                    new_lines = []
                    in_secrets = False
                    for line in lines_out:
                        if line.strip() == "[vpn-secrets]":
                            in_secrets = True
                            new_lines.append(line)
                            new_lines.append(f"password={self._password}")
                            continue
                        if in_secrets and line.startswith("password="):
                            continue
                        new_lines.append(line)
                    lines_out = new_lines

                nm_file.write_text("\n".join(lines_out) + "\n")
                nm_file.chmod(0o600)

            imported.append(name)

        subprocess.run(["nmcli", "connection", "reload"], capture_output=True)

        # Excluir fallidos
        antes = len(imported)
        imported = [n for n in imported if n not in self._failed]
        if antes - len(imported):
            print(f"  [VPN] {antes - len(imported)} excluidas por historial.", flush=True)

        random.shuffle(imported)
        self._connections = imported
        self._index = 0

        print(f"  [VPN] {len(self._connections)} conexiones ProtonVPN listas"
              + (f" (país: {self.country_filter})" if self.country_filter else ""),
              flush=True)

        if not self._connections:
            raise RuntimeError("No hay conexiones VPN disponibles 1.")

    # ── IP pública ─────────────────────────────────────────────────────────────
    def _get_public_ip(self) -> str:
        for service in ["https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com"]:
            try:
                result = subprocess.run(
                    ["curl", "--silent", "--max-time", "8", service],
                    capture_output=True, text=True
                )
                ip = result.stdout.strip()
                if ip:
                    return ip
            except Exception:
                continue
        return "desconocida"

    # ── Conexión / desconexión ─────────────────────────────────────────────────
    def connect(self) -> None:
        """Conecta al siguiente servidor disponible via nmcli."""
        self.disconnect()

        if not self._connections:
            raise RuntimeError("No hay conexiones VPN disponibles 2.")

        intentos     = 0
        max_intentos = len(self._connections)

        while intentos < max_intentos:
            name = self._connections[self._index % len(self._connections)]
            pais = name.split("-")[0].upper()

            if name in self._failed:
                self._index += 1
                intentos += 1
                continue

            print(f"  [VPN] Conectando a {name} ...", end=" ", flush=True)
            r = subprocess.run(
                ["nmcli", "connection", "up", name],
                capture_output=True, text=True,
                timeout=30
            )
            if r.returncode == 0:
                time.sleep(2)  # esperar que se estabilice
                ip_publica = self._get_public_ip()
                print(f"OK  →  IP pública: {ip_publica}", flush=True)
                self.current_servidor = name
                self.current_pais     = pais
                self.current_ip       = ip_publica
                self._index += 1
                # Mover el .ovpn usado a la subcarpeta "ar" (ya utilizado)
                self._marcar_usado(name)
                return
            else:
                err = r.stderr.strip() or r.stdout.strip()
                print(f"error — marcando como fallido ({err[:80]})", flush=True)
                self._persist_failed(name, motivo=f"fallo al conectar: {err[:120]}")

            self._index += 1
            intentos += 1

        raise RuntimeError("No se pudo conectar a ningún servidor ProtonVPN.")

    def _marcar_usado(self, name: str) -> None:
        """Mueve el .ovpn usado a la subcarpeta ar/ dentro de proton_configs."""
        stem    = name.replace(".", "_")
        matches = list(self.configs_dir.glob(f"{stem}.ovpn"))
        if not matches:
            matches = list(self.configs_dir.glob(f"{name.split('.')[0]}*.ovpn"))
        if matches:
            used_dir = self.configs_dir / "ar"
            used_dir.mkdir(exist_ok=True)
            try:
                matches[0].rename(used_dir / matches[0].name)
            except Exception as e:
                print(f"  [VPN] Advertencia: no se pudo mover {matches[0].name}: {e}", flush=True)

    def disconnect(self) -> None:
        """Desconecta la conexión VPN activa y mueve el config usado a la carpeta ar/."""
        if self.current_servidor:
            subprocess.run(
                ["nmcli", "connection", "down", self.current_servidor],
                capture_output=True
            )
            time.sleep(1)
            # Mover el .ovpn usado a la subcarpeta ar/ para no reutilizarlo
            # en esta ejecución y mantener un registro de cuáles ya se usaron
            nombre_ovpn = self.current_servidor.replace(".", "_") + ".ovpn"
            src = self.configs_dir / nombre_ovpn
            used_dir = self.configs_dir / "ar"
            if src.exists():
                used_dir.mkdir(exist_ok=True)
                dst = used_dir / nombre_ovpn
                src.rename(dst)
                print(f"  [VPN] Config movido a {used_dir.name}/", flush=True)
        self.current_servidor = ""
        self.current_pais     = ""
        self.current_ip       = ""


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO DE CARGA DE SOCIOS
# ══════════════════════════════════════════════════════════════════════════════

def cargar_socios(input_csv: Path, excel_path: Path, sheet_name: str) -> pd.DataFrame:
    """
    Carga los socios a procesar.
    - Si input_csv existe: lee el CSV de pendientes (solo BLOQUEADO + SIN_DATOS).
    - Si no: cae al Excel original.
    """
    if input_csv and Path(input_csv).exists():
        print(f"  Fuente: CSV de pendientes → {input_csv.name}")
        df = pd.read_csv(input_csv)
        df.columns = [str(c).strip() for c in df.columns]
        df = df.rename(columns={
            "nro_socio":      "nro_socio",
            "dni_consultado": "dni",
            "apellido_socio": "apellido",
            "nombre_socio":   "nombre",
            "fecha_nac_socio":"fecha_nac",
            "edad_socio":     "edad",
        })
    else:
        print(f"  Fuente: Excel → {excel_path.name} [{sheet_name}]")
        df = pd.read_excel(excel_path, sheet_name=sheet_name, header=2)
        df.columns = [str(c).strip() for c in df.columns]
        df = df.rename(columns={
            "Nº Socio": "nro_socio", "DNI": "dni",
            "Apellido": "apellido",  "Nombre": "nombre",
            "F. Nac.":  "fecha_nac", "Edad": "edad",
        })

    columnas_requeridas = ["nro_socio", "dni", "apellido", "nombre", "fecha_nac", "edad"]
    faltantes = [c for c in columnas_requeridas if c not in df.columns]
    if faltantes:
        raise ValueError(f"Faltan columnas: {faltantes}. Encontradas: {list(df.columns)}")

    df["dni"] = pd.to_numeric(df["dni"], errors="coerce")
    df = df.dropna(subset=["dni"])
    df["dni"] = df["dni"].astype(int).astype(str)
    df["nro_socio"] = pd.to_numeric(df["nro_socio"], errors="coerce").fillna(0).astype(int)
    return df[columnas_requeridas].drop_duplicates(subset=["dni"]).reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO DE CONSULTA WEB
# ══════════════════════════════════════════════════════════════════════════════

def get_hidden_fields(soup: BeautifulSoup) -> dict[str, str]:
    hidden: dict[str, str] = {}
    for field in soup.find_all("input", {"type": "hidden"}):
        name  = str(field.get("name", ""))
        value = str(field.get("value", ""))
        if name:
            hidden[name] = value
    return hidden



def consultar_dni(session: requests.Session, dni: str) -> str:
    headers = {
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
        "Cache-Control":   "no-cache",
        "Pragma":          "no-cache",
        "Sec-Ch-Ua":       '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile":   "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest":  "document",
        "Sec-Fetch-Mode":  "navigate",
        "Sec-Fetch-Site":  "same-origin",
        "Sec-Fetch-User":  "?1",
        "Upgrade-Insecure-Requests": "1",
        "Content-Type":    "application/x-www-form-urlencoded",
        "Origin":          URL.rstrip("/"),
        "Referer":         URL,
    }
    get_resp = session.get(URL, headers=headers, timeout=20)
    get_resp.raise_for_status()
    (BASE_DIR / "debug_get.html").write_text(get_resp.text, encoding="utf-8")
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
    (BASE_DIR / "debug_post.html").write_text(post_resp.text, encoding="utf-8")
    return post_resp.text

def armar_nombre_busqueda(apellido: str, nombre: str) -> str:
    """
    Arma el texto de búsqueda considerando patrones argentinos de nombres.
    - Apellido es solo "DE" (o "DE LA", etc.): el nombre tiene el apellido compuesto
    - Apellido termina en " DE": apellido del marido + nombre propio + DE
    - Caso normal: NOMBRE APELLIDO
    """
    import re
    apellido = str(apellido).strip()
    nombre   = str(nombre).strip()
    if re.match(r"^DE(\s+(LA|LOS|LAS|DEL|L[AO]))?$", apellido, re.IGNORECASE):
        return nombre
    if re.search(r"\bDE$", apellido, re.IGNORECASE):
        sin_de = re.sub(r"\s*DE$", "", apellido, flags=re.IGNORECASE).strip()
        partes = sin_de.split()
        if len(partes) >= 2:
            return f"{' '.join(partes[1:])} {nombre}".strip()
        return f"{nombre} {sin_de}".strip()
    return f"{nombre} {apellido}".strip()


def consultar_nombre(session: requests.Session, nombre_busqueda: str) -> str:
    """Consulta busca-datos.com.ar por nombre en lugar de DNI."""
    headers = {
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
        "Cache-Control":   "no-cache",
        "Pragma":          "no-cache",
        "Sec-Ch-Ua":       '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile":   "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest":  "document",
        "Sec-Fetch-Mode":  "navigate",
        "Sec-Fetch-Site":  "same-origin",
        "Sec-Fetch-User":  "?1",
        "Upgrade-Insecure-Requests": "1",
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
        "txtBusqueda":           nombre_busqueda,
        "BTNobtener":            "Consultar Ahora",
    }
    post_resp = session.post(URL, headers=headers, data=payload, timeout=20)
    post_resp.raise_for_status()
    (BASE_DIR / "debug_post.html").write_text(post_resp.text, encoding="utf-8")
    return post_resp.text



#  MÓDULO DE PARSEO
# ══════════════════════════════════════════════════════════════════════════════

def parsear_tarjeta(contenedor) -> dict:
    """
    Parsea un div.det-contenedor de la nueva estructura del sitio.
    Extrae nombre, edad, CUIT y estado (VIVO/FALLECIDO).
    """
    # Nombre y edad desde det-nombre-destacado
    nombre_div = contenedor.find(class_="det-nombre-destacado")
    texto_raw  = nombre_div.get_text(separator=" ", strip=True) if nombre_div else ""

    # Edad entre paréntesis
    edad_match = re.search(r"\((\d+)\s*años?\)", texto_raw)
    edad_sitio = edad_match.group(1) if edad_match else ""

    # Nombre limpio
    nombre_limpio = re.sub(r"\(\d+\s*años?\)", "", texto_raw).strip()
    # Quitar símbolo de fallecido (✝ u otros)
    nombre_limpio = re.sub(r"[^\w\sáéíóúüñÁÉÍÓÚÜÑ,.]", "", nombre_limpio).strip()

    # Estado: buscar det-cruz dentro del contenedor
    cruz = contenedor.find(class_="det-cruz")
    if cruz and "fallecido" in cruz.get("title", "").lower():
        estado = "FALLECIDO"
    elif cruz or "✝" in texto_raw or "†" in texto_raw:
        estado = "FALLECIDO"
    else:
        estado = "VIVO"

    # CUIT desde botón de compra (data-url o onclick)
    cuit = ""
    btn = contenedor.find("a", id=re.compile(r"btnComprarInforme"))
    if btn:
        data_url = btn.get("data-url", "") or btn.get("onclick", "")
        m = re.search(r"cuit=(\d+)", data_url)
        if m:
            raw = m.group(1)
            cuit = f"{raw[:2]}-{raw[2:10]}-{raw[10:]}"

    # Sexo por prefijo CUIT
    prefijo = cuit[:2] if cuit else ""
    sexo = "Femenino" if prefijo in ("23", "27") else "Masculino"

    return {
        "cuit":                  cuit,
        "sexo":                  sexo,
        "nombre_completo_sitio": nombre_limpio,
        "edad_sitio":            edad_sitio,
        "estado":                estado,
        "localidades":           "",
    }


def parsear_html(html: str, dni: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")

    # Detectar bloqueo real de Cloudflare (página de error, no solo scripts de CDN)
    # El sitio siempre tiene scripts de Cloudflare — solo es bloqueo si hay título o
    # mensaje específico de error de CF
    es_bloqueo_cf = (
        "cf-ray" in html.lower() and "just a moment" in html.lower()
    ) or "error 1020" in html.lower() or "access denied" in html.lower()
    if es_bloqueo_cf:
        return [{"dni_consultado": dni,
                 "estado_consulta": "BLOQUEADO",
                 "mensaje": "Página de error de Cloudflare — IP bloqueada o rate limit"}]

    resultados = []

    # ── Estructura nueva: div.det-contenedor ──────────────────────────────────
    contenedores = soup.find_all(class_="det-contenedor")
    tarjetas_nuevas = [
        c for c in contenedores
        if c.find(class_="det-nombre-destacado")
        and c.find(class_="det-nombre-destacado").get_text(strip=True)
    ]
    for c in tarjetas_nuevas:
        r = parsear_tarjeta(c)
        r["dni_consultado"]  = dni
        r["estado_consulta"] = "OK"
        r["mensaje"]         = ""
        resultados.append(r)

    # ── Estructura antigua: id="IDCUIT..." ────────────────────────────────────
    tarjetas_old = soup.find_all(id=re.compile(r"^IDCUIT"))
    for tag in tarjetas_old:
        r = _parsear_tarjeta_old(tag)
        r["dni_consultado"]  = dni
        r["estado_consulta"] = "OK"
        r["mensaje"]         = ""
        resultados.append(r)

    # Si encontramos algo en cualquiera de las dos estructuras, devolver
    if resultados:
        return resultados

    # ── Sin resultados — determinar si es SIN_DATOS o BLOQUEADO ──────────────
    tiene_form = soup.find("input", {"name": "txtBusqueda"}) is not None
    tiene_pnl  = soup.find(id="PNL03") is not None

    lbl       = soup.find(id="LBLcompletarDATOS")
    tiene_msg = lbl is not None or bool(
        soup.find_all(string=re.compile(r"NO hay datos|no hay datos", re.I))
    )

    if tiene_pnl or tiene_msg:
        # El sitio respondió con PNL03 o con mensaje explícito de sin datos
        msg = lbl.get_text(strip=True) if lbl else "Sin resultados para este DNI"
        if not lbl:
            for tag in soup.find_all(string=re.compile(r"NO hay datos|no hay datos", re.I)):
                msg = str(tag).strip()
                break
        return [{"dni_consultado": dni, "estado_consulta": "SIN_DATOS", "mensaje": msg}]

    # Tiene formulario pero sin PNL03 ni mensaje — bloqueo silencioso
    if tiene_form:
        return [{"dni_consultado": dni,
                 "estado_consulta": "BLOQUEADO",
                 "mensaje": "El sitio devolvió el formulario vacío — bloqueo silencioso"}]

    return [{"dni_consultado": dni,
             "estado_consulta": "BLOQUEADO",
             "mensaje": "El sitio no devolvió el formulario ni resultados"}]


def _parsear_tarjeta_old(tag) -> dict:
    """Parser de compatibilidad para la estructura antigua con id=IDCUIT*."""
    cuit_id  = tag.get("id", "")
    digitos  = cuit_id.replace("IDCUIT", "")
    prefijo  = digitos[:2]
    dni_num  = digitos[2:10]
    verif    = digitos[10:]
    cuit     = f"{prefijo}-{dni_num}-{verif}"
    sexo     = "Femenino" if prefijo in ("23", "27") else "Masculino"
    primer_div = tag.find("div")
    texto_raw  = primer_div.get_text(separator=" ", strip=True) if primer_div else ""
    nombre_limpio = re.sub(r"\(\d+\s*años?\)", "", texto_raw)
    nombre_limpio = re.sub(r"[^\w\sáéíóúüñÁÉÍÓÚÜÑ,.]", "", nombre_limpio).strip()
    edad_match = re.search(r"\((\d+)\s*años?\)", texto_raw)
    edad_sitio = edad_match.group(1) if edad_match else ""
    cruz = tag.find(class_="det-cruz")
    if cruz and "fallecido" in cruz.get("title", "").lower():
        estado = "FALLECIDO"
    elif "fallecido" in texto_raw.lower():
        estado = "FALLECIDO"
    else:
        estado = "VIVO"
    locs = []
    for d in tag.find_all("div"):
        txt = d.get_text(strip=True)
        if re.search(r"[A-ZÁÉÍÓÚ][a-záéíóú].*,\s*[A-ZÁÉÍÓÚ]", txt) and            "DNI" not in txt and "CUIT" not in txt and "años" not in txt:
            locs.append(re.sub(r"^[^\w]+", "", txt))
    return {"cuit": cuit, "sexo": sexo, "nombre_completo_sitio": nombre_limpio,
            "edad_sitio": edad_sitio, "estado": estado, "localidades": " | ".join(locs)}



# ══════════════════════════════════════════════════════════════════════════════
#  CONTROL DE PROGRESO
# ══════════════════════════════════════════════════════════════════════════════

def dnis_ya_procesados(output_csv: Path) -> set[str]:
    """
    Devuelve los DNIs que ya fueron procesados exitosamente.
    Los que tienen estado SIN_DATOS o ERROR_RED se excluyen
    para que sean reprocesados en la próxima ejecución.
    """
    if not output_csv.exists():
        return set()
    procesados = set()
    reintentar = set()
    with open(output_csv, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            dni    = row.get("dni_consultado", "")
            estado = row.get("estado_consulta", "")
            if estado in ("ERROR_RED", "BLOQUEADO", "SIN_TARJETAS"):
                reintentar.add(dni)
            else:
                procesados.add(dni)
    # Los que tienen resultado exitoso en al menos una fila no se reprocesán
    # aunque también tengan filas con error
    return procesados - reintentar


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    if os.geteuid() != 0:
        print("⚠  Este script necesita permisos de administrador para manejar VPN.")
        print("   Ejecutalo con:  sudo python consulta_fallecidos_vpn.py")
        sys.exit(1)

    # Verificar que existan los archivos necesarios
    if ROTATE_EVERY > 0:
        if not CREDENTIALS_FILE.exists():
            print(f"✗ No se encontró el archivo de credenciales: {CREDENTIALS_FILE}")
            print("  Crealo con tu usuario OpenVPN en la línea 1 y la contraseña en la línea 2.")
            sys.exit(1)
        if not PROTON_CONFIGS_DIR.exists():
            print(f"✗ No se encontró la carpeta de configs: {PROTON_CONFIGS_DIR}")
            print("  Descargá los .ovpn desde account.protonvpn.com/downloads")
            sys.exit(1)

    print("=" * 65)
    print("  Reprocesamiento BLOQUEADO/SIN_DATOS — GEBA  [ProtonVPN]")
    print("=" * 65)

    # ── Cargar socios desde CSV de pendientes (o Excel como fallback) ──────────
    df = cargar_socios(INPUT_CSV, EXCEL_PATH, SHEET_NAME)
    total = len(df)
    print(f"  {total} socios en la fuente de entrada")

    # ── Determinar cuáles ya tienen resultado exitoso en OUTPUT_CSV ────────────
    ya_procesados = dnis_ya_procesados(OUTPUT_CSV)

    # Limpiar filas previas con BLOQUEADO/ERROR del OUTPUT para no duplicar
    if OUTPUT_CSV.exists():
        with open(OUTPUT_CSV, encoding="utf-8") as f:
            filas = list(csv.DictReader(f))
        filas_limpias = [r for r in filas
                         if r.get("estado_consulta", "") not in
                         ("ERROR_RED", "BLOQUEADO", "SIN_TARJETAS")]
        if len(filas_limpias) < len(filas):
            print(f"  {len(filas) - len(filas_limpias)} filas bloqueadas eliminadas del output para reprocesar.")
            with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
                writer_clean = csv.DictWriter(f, fieldnames=FIELDNAMES,
                                              extrasaction="ignore", lineterminator="\n")
                writer_clean.writeheader()
                writer_clean.writerows(filas_limpias)

    # Pendientes = los del CSV de entrada que aún no tienen resultado exitoso
    pendientes_df = df[~df["dni"].isin(ya_procesados)].reset_index(drop=True)
    print(f"  {len(ya_procesados)} ya resueltos — {len(pendientes_df)} pendientes\n")

    # ── Inicializar cliente VPN ────────────────────────────────────────────────
    vpn: Optional[ProtonVPNClient] = None
    if ROTATE_EVERY > 0:
        vpn = ProtonVPNClient(
            configs_dir=PROTON_CONFIGS_DIR,
            credentials_file=CREDENTIALS_FILE,
            failed_servers_file=FAILED_SERVERS_FILE,
            country_filter=COUNTRY_FILTER,
        )
        vpn.connect()

    def cleanup(signum=None, frame=None):
        print("\n  [VPN] Desconectando...")
        if vpn:
            vpn.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT,  cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    escribir_hdr = not OUTPUT_CSV.exists()
    procesados   = len(ya_procesados)
    errores      = 0
    consultas_en_esta_vpn = 0

    # Lista de un elemento para que rotar_vpn pueda mutar la referencia
    # y todos los usos posteriores vean la sesión nueva correctamente.
    _session = [requests.Session()]

    # Cola de reintentos: DNIs bloqueados que se reintentan tras rotar VPN.
    cola_reintento: list[dict] = []

    # Cooldown: cuántos items normales procesar tras una rotación antes de
    # sacar reintentos de la cola (deja que Cloudflare "olvide" la IP).
    COOLDOWN_TRAS_ROTACION = 3
    cooldown_restante = 0

    def rotar_vpn(motivo: str) -> bool:
        """Rota la VPN y crea una sesión HTTP limpia. Devuelve True si tuvo éxito."""
        nonlocal consultas_en_esta_vpn, cooldown_restante
        print(f"  [VPN] Rotando servidor ({motivo})...")
        _session[0].close()
        _session[0] = requests.Session()   # sesión limpia, sin cookies de la IP anterior
        if vpn:
            vpn.disconnect()
            try:
                vpn.connect()
                consultas_en_esta_vpn = 0
                cooldown_restante = COOLDOWN_TRAS_ROTACION
                print(f"  [VPN] Cooldown: {COOLDOWN_TRAS_ROTACION} consultas normales antes de reintentar bloqueados.")
                return True
            except RuntimeError as e:
                print(f"  [VPN] {e}")
                print("  [VPN] Sin servidores disponibles — deteniendo.")
                return False
        return True

    try:
        with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=FIELDNAMES,
                                    extrasaction="ignore", lineterminator="\n")
            if escribir_hdr:
                writer.writeheader()

            # Iterador principal: pendientes del CSV + cola de reintentos intercalada
            iterrows = list(pendientes_df.iterrows())
            idx = 0  # puntero sobre iterrows

            while idx < len(iterrows) or cola_reintento:

                # ── Rotar VPN por cuota ────────────────────────────────────────
                if vpn and ROTATE_EVERY > 0 and consultas_en_esta_vpn >= ROTATE_EVERY:
                    if not rotar_vpn(f"cada {ROTATE_EVERY} consultas"):
                        break

                # ── Decidir si procesar un reintento o el siguiente de la lista ─
                # Durante cooldown post-rotación, procesar items normales primero
                # para que Cloudflare no asocie la nueva IP con el patrón de antes.
                if cola_reintento and cooldown_restante <= 0:
                    row = cola_reintento.pop(0)
                    es_reintento = True
                elif idx < len(iterrows):
                    _, row = iterrows[idx]
                    idx += 1
                    es_reintento = False
                    if cooldown_restante > 0:
                        cooldown_restante -= 1
                elif cola_reintento:
                    # Sin más items normales, procesar reintentos aunque el cooldown no terminó
                    row = cola_reintento.pop(0)
                    es_reintento = True
                else:
                    break

                nro_socio = row["nro_socio"]
                dni       = str(row["dni"])
                apellido  = str(row["apellido"])
                nombre    = str(row["nombre"])

                vpn_servidor = vpn.current_servidor if vpn else ""
                vpn_pais     = vpn.current_pais     if vpn else ""

                prefijo_log = "↩ REINTENTO" if es_reintento else f"  [{procesados+1}/{total}]"
                print(
                    f"{prefijo_log} {apellido}, {nombre} (DNI {dni})"
                    + (f" [{vpn_pais}]" if vpn_servidor else "")
                    + " ...",
                    end=" ", flush=True
                )

                base = {
                    "nro_socio":       str(nro_socio),
                    "dni_consultado":  dni,
                    "apellido_socio":  apellido,
                    "nombre_socio":    nombre,
                    "fecha_nac_socio": str(row.get("fecha_nac", "")),
                    "edad_socio":      str(row.get("edad", "")),
                    "vpn_servidor":    vpn_servidor,
                    "vpn_pais":        vpn_pais,
                    "vpn_ip":          vpn.current_ip if vpn else "",
                }

                # ── Consulta con reintentos de red ─────────────────────────────
                html = None
                for intento in range(1, REINTENTOS + 1):
                    try:
                        html = consultar_dni(_session[0], dni)
                        break
                    except Exception as e:
                        if intento < REINTENTOS:
                            print(f"\n    ⚠ intento {intento} fallido: {e} — reintentando...",
                                  end=" ")
                            time.sleep(5)
                        else:
                            writer.writerow({**base,
                                             "estado_consulta": "ERROR_RED",
                                             "mensaje": str(e)})
                            csvfile.flush()
                            errores += 1
                            print("ERROR_RED")

                if html is None:
                    if not es_reintento:
                        procesados += 1
                    consultas_en_esta_vpn += 1
                    time.sleep(random.uniform(PAUSA_MIN, PAUSA_MAX))
                    continue

                # Debug
                soup_dbg       = BeautifulSoup(html, "html.parser")
                nuevas_dbg     = [c for c in soup_dbg.find_all(class_="det-contenedor")
                                  if c.find(class_="det-nombre-destacado")
                                  and c.find(class_="det-nombre-destacado").get_text(strip=True)]
                viejas_dbg     = soup_dbg.find_all(id=re.compile(r"^IDCUIT"))
                form_dbg       = soup_dbg.find("input", {"name": "txtBusqueda"}) is not None
                pnl_dbg        = soup_dbg.find(id="PNL03") is not None
                cf_dbg         = ("cf-ray" in html.lower() and "just a moment" in html.lower()) or "error 1020" in html.lower()
                print(f"    [DBG] html={len(html)}b cf={cf_dbg} form={form_dbg} pnl={pnl_dbg} "
                      f"nuevas={len(nuevas_dbg)} viejas={len(viejas_dbg)}", flush=True)

                registros = parsear_html(html, dni)

                # ── Si SIN_DATOS: reintentar por nombre ───────────────────────
                if (len(registros) == 1 and
                        registros[0].get("estado_consulta") == "SIN_DATOS"):
                    nombre_busqueda = armar_nombre_busqueda(apellido, nombre)
                    print(f"    [SIN_DATOS] Reintentando por nombre: {nombre_busqueda!r}",
                          end=" ", flush=True)
                    try:
                        html_nombre = consultar_nombre(_session[0], nombre_busqueda)
                        registros_nombre = parsear_html(html_nombre, dni)
                        if registros_nombre and registros_nombre[0].get("estado_consulta") == "OK":
                            registros = registros_nombre
                            print("→ encontrado", flush=True)
                        else:
                            print("→ sin resultados", flush=True)
                    except Exception as e:
                        print(f"→ error: {e}", flush=True)

                # ── Evaluar resultado ──────────────────────────────────────────
                hay_bloqueo    = any(r.get("estado_consulta") == "BLOQUEADO" for r in registros)
                hay_ok         = any(r.get("estado_consulta") == "OK" for r in registros)

                if hay_bloqueo and vpn:
                    # ── BLOQUEADO: registrar servidor actual y rotar ───────────
                    print("BLOQUEADO → rotando VPN y reintentando después...", flush=True)
                    if vpn.current_servidor:
                        vpn._persist_failed(
                            vpn.current_servidor,
                            motivo="sitio bloqueó la IP (Cloudflare / rate limit)"
                        )
                    if rotar_vpn("bloqueo detectado"):
                        # Reinsertar este DNI al frente de la cola
                        cola_reintento.insert(0, row)
                        print(f"  [COLA] {apellido}, {nombre} (DNI {dni}) encolado para reintento con nueva IP")
                    else:
                        # Sin más VPN: guardar como bloqueado y seguir
                        for reg in registros:
                            writer.writerow({**base, **reg})
                        csvfile.flush()
                        if not es_reintento:
                            procesados += 1
                    # No incrementar consultas_en_esta_vpn — la rotación ya lo reseteó
                    time.sleep(random.uniform(PAUSA_MIN, PAUSA_MAX))
                    continue

                # ── Resultado válido (OK, SIN_DATOS sin reintento posible) ─────
                estado_resumen = []
                for reg in registros:
                    writer.writerow({**base, **reg})
                    ec = reg.get("estado_consulta", "")
                    if ec == "SIN_DATOS":
                        estado_resumen.append("SIN_DATOS")
                    elif ec == "SIN_TARJETAS":
                        estado_resumen.append("SIN_TARJETAS")
                    else:
                        nom = reg.get("nombre_completo_sitio", "?")
                        est = reg.get("estado", "?")
                        estado_resumen.append(f"{nom} [{est}]")
                csvfile.flush()
                print(" / ".join(estado_resumen))

                if not es_reintento:
                    procesados += 1
                consultas_en_esta_vpn += 1
                time.sleep(random.uniform(PAUSA_MIN, PAUSA_MAX))

    finally:
        if vpn:
            vpn.disconnect()

    print(f"\n{'='*65}")
    print(f"  Completado: {procesados} procesados, {errores} con error de red")
    print(f"  Resultado: {OUTPUT_CSV.resolve()}")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    main()
