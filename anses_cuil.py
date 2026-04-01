#!/usr/bin/env python3
"""
Script para consultar y descargar la constancia de CUIL desde ANSES usando Selenium.

Requisitos:
    pip install selenium

Ejemplos:
    python anses_cuil.py
    python anses_cuil.py --headless
    python anses_cuil.py --download-dir ./descargas --timeout 30

Notas:
- Usa Selenium Manager, por lo que Selenium 4.6+ puede descargar/gestionar el driver automáticamente.
- El campo de fecha es de tipo HTML "date", por lo que se envía en formato YYYY-MM-DD.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver import ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait


URL = "https://servicioswww.anses.gob.ar/C2-ConstaCUIL"
ERROR_TEXT = "ACERCATE A UNA OFICINA DE ANSES CON DOCUMENTACIÓN QUE ACREDITE IDENTIDAD"
SUCCESS_TEXT = "DESCARGAR CONSTANCIA"


def build_driver(download_dir: Path, headless: bool) -> webdriver.Chrome:
    """
    Crea y configura el driver de Chrome.

    Args:
        download_dir: Carpeta donde se descargarán los archivos.
        headless: Indica si se ejecuta en modo headless.

    Returns:
        Una instancia configurada de webdriver.Chrome.
    """
    download_dir.mkdir(parents=True, exist_ok=True)

    options = ChromeOptions()
    options.add_argument("--window-size=1400,1200")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--lang=es-AR")
    options.add_experimental_option(
        "prefs",
        {
            "download.default_directory": str(download_dir.resolve()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "plugins.always_open_pdf_externally": True,
            "safebrowsing.enabled": True,
        },
    )

    if headless:
        options.add_argument("--headless=new")

    driver = webdriver.Chrome(service=ChromeService(), options=options)

    # Habilita descargas en headless para Chrome.
    driver.execute_cdp_cmd(
        "Page.setDownloadBehavior",
        {
            "behavior": "allow",
            "downloadPath": str(download_dir.resolve()),
        },
    )
    return driver


def complete_form(driver: webdriver.Chrome, wait: WebDriverWait) -> None:
    """
    Completa el formulario con los datos indicados por el usuario.

    Args:
        driver: Instancia del navegador.
        wait: Instancia de espera explícita.
    """
    driver.get(URL)

    # Tipo de documento: selector con id "TipoDocumento"
    tipo_documento = wait.until(EC.element_to_be_clickable((By.ID, "TipoDocumento")))
    Select(tipo_documento).select_by_visible_text("Documento Único")

    numero_doc = wait.until(EC.visibility_of_element_located((By.ID, "NumeroDoc")))
    numero_doc.clear()
    numero_doc.send_keys("28206285")

    nombre = wait.until(EC.visibility_of_element_located((By.ID, "Nombre")))
    nombre.clear()
    nombre.send_keys("Romina")

    apellido = wait.until(EC.visibility_of_element_located((By.ID, "Apellido")))
    apellido.clear()
    apellido.send_keys("Areco")

    sexo_m = wait.until(EC.element_to_be_clickable((By.ID, "SexoF")))
    sexo_m.click()

    fecha_nacimiento = wait.until(
        EC.visibility_of_element_located((By.ID, "FechaNacimiento"))
    )
    fecha_nacimiento.clear()
    fecha_nacimiento.send_keys("1980-07-09")

    submit_button = wait.until(EC.element_to_be_clickable((By.ID, "submit")))
    submit_button.click()


def wait_for_result(driver: webdriver.Chrome, wait: WebDriverWait) -> str:
    """
    Espera el resultado de la consulta.

    Returns:
        'error' si aparece el mensaje de error,
        'success' si aparece el botón de descarga.

    Raises:
        TimeoutException: Si no se detecta un resultado dentro del tiempo configurado.
    """
    wait.until(
        lambda d: ERROR_TEXT.lower() in d.page_source.lower()
        or SUCCESS_TEXT.lower() in d.page_source.lower()
    )

    page = driver.page_source.lower()
    if ERROR_TEXT.lower() in page:
        return "error"
    if SUCCESS_TEXT.lower() in page:
        return "success"

    raise TimeoutException("No se pudo determinar el resultado de la consulta.")


def download_constancia(driver: webdriver.Chrome, wait: WebDriverWait) -> None:
    """
    Hace clic en el botón de descarga de la constancia.

    Args:
        driver: Instancia del navegador.
        wait: Instancia de espera explícita.
    """
    download_button = wait.until(
        EC.element_to_be_clickable(
            (
                By.XPATH,
                "//button[contains(., 'DESCARGAR CONSTANCIA')]"
                " | //a[contains(., 'DESCARGAR CONSTANCIA')]",
            )
        )
    )
    download_button.click()


def parse_args() -> argparse.Namespace:
    """
    Parsea los argumentos de línea de comandos.

    Returns:
        Namespace con los parámetros del script.
    """
    parser = argparse.ArgumentParser(
        description="Consulta y descarga la constancia de CUIL desde ANSES."
    )
    parser.add_argument(
        "--download-dir",
        type=Path,
        default=Path.cwd() / "descargas_anses",
        help="Directorio donde se descargará la constancia.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=25,
        help="Tiempo máximo de espera en segundos.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Ejecuta Chrome sin interfaz gráfica.",
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="Solo consulta; no hace clic en 'Descargar constancia'.",
    )
    return parser.parse_args()


def main() -> int:
    """
    Punto de entrada principal del script.

    Returns:
        Código de salida del proceso.
    """
    args = parse_args()
    driver = build_driver(download_dir=args.download_dir, headless=args.headless)
    wait = WebDriverWait(driver, args.timeout)

    try:
        complete_form(driver, wait)
        result = wait_for_result(driver, wait)

        if result == "error":
            print("ERROR: ANSES devolvió el mensaje de validación de identidad.")
            print(ERROR_TEXT)
            return 1

        print("OK: La constancia fue generada correctamente.")

        if not args.no_download:
            download_constancia(driver, wait)
            print(f"Descarga iniciada en: {args.download_dir.resolve()}")

        return 0

    except TimeoutException as exc:
        print(f"ERROR: Tiempo de espera agotado. Detalle: {exc}")
        return 2
    except Exception as exc:  # pragma: no cover
        print(f"ERROR INESPERADO: {exc}")
        return 3
    finally:
        driver.quit()


if __name__ == "__main__":
    sys.exit(main())
