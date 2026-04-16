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
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver import ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

try:  # pragma: no cover - depende del entorno
    import pyodbc  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    pyodbc = None  # type: ignore


URL = "https://servicioswww.anses.gob.ar/C2-ConstaCUIL"
ERROR_TEXT = "ACERCATE A UNA OFICINA DE ANSES CON DOCUMENTACIÓN QUE ACREDITE IDENTIDAD"
SUCCESS_TEXT = "DESCARGAR CONSTANCIA"
VALIDATION_ERROR_TEXTS = (
    "completá este campo",
    "ingresá",
    "verificá",
    "dato inválido",
)

DOCUMENTO_UNICO_OPTIONS = ("Documento Único", "Documento Unico")
DOCUMENTO_IDENTIDAD_OPTIONS = ("Documento de Identidad", "Documento identidad")
LIBRETA_CIVICA_OPTIONS = ("Libreta Cívica", "Libreta Civica")
LIBRETA_ENROLAMIENTO_OPTIONS = ("Libreta de Enrolamiento", "Libreta Enrolamiento")


@dataclass(frozen=True)
class PersonData:
    """Datos mínimos requeridos por el formulario de ANSES."""

    doc_nro: int
    nombre: str
    apellido: str
    sexo: str
    fecha_nacimiento: date
    fecha_nacimiento_original: str


def _parse_fecha_nacimiento(fecha_nac: object) -> tuple[date, str] | None:
    """
    Normaliza la fecha de nacimiento obtenida desde MSSQL y conserva su formato original.

    Devuelve una tupla con:
      - fecha normalizada como `date`
      - valor original formateado en string para trazabilidad
    """
    if isinstance(fecha_nac, datetime):
        fecha = fecha_nac.date()
        return fecha, fecha_nac.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(fecha_nac, date):
        return fecha_nac, fecha_nac.isoformat()
    if isinstance(fecha_nac, str):
        raw = fecha_nac.strip()
        if not raw:
            return None
        formatos = ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y", "%Y%m%d", "%d%m%Y")
        for fmt in formatos:
            try:
                return datetime.strptime(raw, fmt).date(), raw
            except ValueError:
                continue
    return None


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
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
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


def complete_form(
    driver: webdriver.Chrome,
    wait: WebDriverWait,
    person: PersonData,
) -> None:
    """
    Completa el formulario con los datos indicados por el usuario.

    Args:
        driver: Instancia del navegador.
        wait: Instancia de espera explícita.
    """
    driver.get(URL)

    # Tipo de documento: selector con id "TipoDocumento"
    tipo_documento = wait.until(EC.element_to_be_clickable((By.ID, "TipoDocumento")))
    selector = Select(tipo_documento)
    doc_type_label = _select_doc_type(selector=selector, person=person)
    print(f"DNI {person.doc_nro}: tipo documento ANSES='{doc_type_label}'")

    numero_doc = wait.until(EC.visibility_of_element_located((By.ID, "NumeroDoc")))
    numero_doc.clear()
    numero_doc.send_keys(str(person.doc_nro))

    nombre = wait.until(EC.visibility_of_element_located((By.ID, "Nombre")))
    nombre.clear()
    nombre.send_keys(person.nombre)

    apellido = wait.until(EC.visibility_of_element_located((By.ID, "Apellido")))
    apellido.clear()
    apellido.send_keys(person.apellido)

    sexo = (person.sexo or "").upper().strip()
    sexo_id = "SexoF" if sexo == "F" else "SexoM"
    sexo_element = wait.until(EC.element_to_be_clickable((By.ID, sexo_id)))
    sexo_element.click()

    fecha_nacimiento = wait.until(EC.visibility_of_element_located((By.ID, "FechaNacimiento")))
    fecha_nacimiento.clear()
    fecha_form_input = person.fecha_nacimiento.strftime("%d/%m/%Y")
    print(
        f"DNI {person.doc_nro}: fecha_nac origen='{person.fecha_nacimiento_original}' "
        f"-> carga ANSES='{fecha_form_input}'"
    )
    fecha_nacimiento.send_keys(fecha_form_input)

    submit_button = wait.until(EC.element_to_be_clickable((By.ID, "submit")))
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", submit_button)
    try:
        submit_button.click()
    except Exception:
        driver.execute_script("arguments[0].click();", submit_button)


def _try_select_by_visible_text(selector: Select, labels: tuple[str, ...]) -> str | None:
    for label in labels:
        try:
            selector.select_by_visible_text(label)
            return label
        except Exception:
            continue
    return None


def _select_doc_type(selector: Select, person: PersonData) -> str:
    """Selecciona tipo de documento para ANSES según longitud de documento y sexo."""
    sexo = (person.sexo or "").upper().strip()
    if person.doc_nro < 10_000_000:
        prioritized_labels = (
            LIBRETA_CIVICA_OPTIONS
            if sexo == "F"
            else LIBRETA_ENROLAMIENTO_OPTIONS
        )
        selected = _try_select_by_visible_text(selector, prioritized_labels)
        if selected:
            return selected

        selected = _try_select_by_visible_text(selector, DOCUMENTO_IDENTIDAD_OPTIONS)
        if selected:
            return selected

    selected = _try_select_by_visible_text(selector, DOCUMENTO_UNICO_OPTIONS)
    if selected:
        return selected

    raise RuntimeError(
        "No se encontró una opción válida en 'Tipo de documento' para completar el formulario ANSES."
    )


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
        or any(text in d.page_source.lower() for text in VALIDATION_ERROR_TEXTS)
    )

    page = driver.page_source.lower()
    if ERROR_TEXT.lower() in page:
        return "error"
    if SUCCESS_TEXT.lower() in page:
        return "success"
    if any(text in page for text in VALIDATION_ERROR_TEXTS):
        raise RuntimeError(
            "ANSES devolvió un mensaje de validación en pantalla. "
            "Revisá nombre, apellido, sexo y fecha de nacimiento."
        )

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
    parser.add_argument(
        "--from-mssql",
        action="store_true",
        help="Obtiene 10+ personas activas >90 años desde MSSQL y ejecuta ANSES.",
    )
    parser.add_argument(
        "--mssql-limit",
        type=int,
        default=10,
        help="Cantidad de personas a traer de MSSQL cuando se usa --from-mssql.",
    )
    parser.add_argument(
        "--dni",
        action="append",
        type=int,
        dest="dnis",
        help="DNI puntual a consultar. Repetir el flag para múltiples DNIs.",
    )
    return parser.parse_args()


def _mssql_config_from_env() -> dict[str, str | int]:
    port = int(os.getenv("MSSQL_CLIENT_LOOKUP_PORT", os.getenv("MSSQL_ACCESS_LOG_PORT", "1433")))
    return {
        "HOST": os.getenv("MSSQL_CLIENT_LOOKUP_HOST", os.getenv("MSSQL_ACCESS_LOG_HOST", "192.168.0.6")),
        "PORT": port,
        "DATABASE": os.getenv("MSSQL_CLIENT_LOOKUP_DATABASE", os.getenv("MSSQL_ACCESS_LOG_DATABASE", "xsys_geba")),
        "USER": os.getenv("MSSQL_CLIENT_LOOKUP_USER", os.getenv("MSSQL_ACCESS_LOG_USER", "sa")),
        "PASSWORD": os.getenv("MSSQL_CLIENT_LOOKUP_PASSWORD", os.getenv("MSSQL_ACCESS_LOG_PASSWORD", "kvy2012*.")),
        "TABLE": os.getenv("MSSQL_CLIENT_LOOKUP_TABLE", "Clientes"),
        "DRIVER": os.getenv("MSSQL_CLIENT_LOOKUP_DRIVER", os.getenv("MSSQL_ACCESS_LOG_DRIVER", "{ODBC Driver 18 for SQL Server}")),
    }


def _connection_string(config: dict[str, str | int]) -> str:
    server = f"{config['HOST']},{config['PORT']}"
    return (
        f"DRIVER={config['DRIVER']};"
        f"SERVER={server};"
        f"DATABASE={config['DATABASE']};"
        f"UID={config['USER']};"
        f"PWD={config['PASSWORD']};"
    )


def fetch_people_from_mssql(limit: int) -> list[PersonData]:
    if pyodbc is None:
        raise RuntimeError("El paquete pyodbc no está instalado.")
    if limit <= 0:
        raise RuntimeError("--mssql-limit debe ser un entero positivo.")

    config = _mssql_config_from_env()
    query = f"""
    SELECT TOP {limit}
        Doc_Nro,
        Nombre,
        Apellido,
        Sexo,
        Fecha_Nac
    FROM {config["TABLE"]}
    WHERE Activo = 1
      AND Fecha_Nac IS NOT NULL
      AND Doc_Nro IS NOT NULL
      AND DATEDIFF(YEAR, Fecha_Nac, CAST(GETDATE() AS date)) > 90
    ORDER BY Fecha_Nac ASC
    """
    try:
        connection = pyodbc.connect(_connection_string(config))  # type: ignore[union-attr]
        cursor = connection.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
    except Exception as exc:
        raise RuntimeError(f"No se pudo consultar MSSQL: {exc}") from exc
    finally:
        try:
            connection.close()
        except Exception:
            pass

    people: list[PersonData] = []
    for doc_nro, nombre, apellido, sexo, fecha_nac in rows:
        if not doc_nro or not nombre or not apellido or not fecha_nac:
            continue
        parsed_date = _parse_fecha_nacimiento(fecha_nac)
        if parsed_date is None:
            continue
        fecha_final, fecha_original = parsed_date
        people.append(
            PersonData(
                doc_nro=int(doc_nro),
                nombre=str(nombre).strip(),
                apellido=str(apellido).strip(),
                sexo=str(sexo or "M"),
                fecha_nacimiento=fecha_final,
                fecha_nacimiento_original=fecha_original,
            )
        )
    return people


def fetch_people_by_dnis(dnis: list[int]) -> list[PersonData]:
    if pyodbc is None:
        raise RuntimeError("El paquete pyodbc no está instalado.")
    if not dnis:
        return []
    clean_dnis = sorted({int(dni) for dni in dnis if int(dni) > 0})
    placeholders = ", ".join("?" for _ in clean_dnis)
    config = _mssql_config_from_env()
    query = f"""
    SELECT
        Doc_Nro,
        Nombre,
        Apellido,
        Sexo,
        Fecha_Nac
    FROM {config["TABLE"]}
    WHERE Activo = 1
      AND Fecha_Nac IS NOT NULL
      AND Doc_Nro IN ({placeholders})
      AND DATEDIFF(YEAR, Fecha_Nac, CAST(GETDATE() AS date)) > 90
    ORDER BY Fecha_Nac ASC
    """
    try:
        connection = pyodbc.connect(_connection_string(config))  # type: ignore[union-attr]
        cursor = connection.cursor()
        cursor.execute(query, clean_dnis)
        rows = cursor.fetchall()
    except Exception as exc:
        raise RuntimeError(f"No se pudo consultar MSSQL por DNI: {exc}") from exc
    finally:
        try:
            connection.close()
        except Exception:
            pass

    people: list[PersonData] = []
    for doc_nro, nombre, apellido, sexo, fecha_nac in rows:
        if not doc_nro or not nombre or not apellido or not fecha_nac:
            continue
        parsed_date = _parse_fecha_nacimiento(fecha_nac)
        if parsed_date is None:
            continue
        fecha_final, fecha_original = parsed_date
        people.append(
            PersonData(
                doc_nro=int(doc_nro),
                nombre=str(nombre).strip(),
                apellido=str(apellido).strip(),
                sexo=str(sexo or "M"),
                fecha_nacimiento=fecha_final,
                fecha_nacimiento_original=fecha_original,
            )
        )
    return people


def main() -> int:
    """
    Punto de entrada principal del script.

    Returns:
        Código de salida del proceso.
    """
    args = parse_args()
    # Asegura que la carpeta de salida exista incluso antes de inicializar Selenium.
    args.download_dir.mkdir(parents=True, exist_ok=True)

    if args.dnis:
        try:
            people = fetch_people_by_dnis(args.dnis)
        except Exception as exc:
            print(f"ERROR: {exc}")
            return 2
        if not people:
            print("No se encontraron personas activas de más de 90 años para los DNIs indicados.")
            return 1
    elif args.from_mssql:
        try:
            people = fetch_people_from_mssql(args.mssql_limit)
        except Exception as exc:
            print(f"ERROR: {exc}")
            return 2
        if not people:
            print("No se encontraron personas activas de más de 90 años.")
            return 1
    else:
        people = [
            PersonData(
                doc_nro=28206285,
                nombre="Romina",
                apellido="Areco",
                sexo="F",
                fecha_nacimiento=date(1980, 7, 9),
                fecha_nacimiento_original="1980-07-09",
            )
        ]

    driver = build_driver(download_dir=args.download_dir, headless=args.headless)
    wait = WebDriverWait(driver, args.timeout)
    errors = 0

    try:
        for person in people:
            complete_form(driver, wait, person)
            result = wait_for_result(driver, wait)

            if result == "error":
                errors += 1
                print(f"ERROR DNI {person.doc_nro}: {ERROR_TEXT}")
                continue

            print(f"OK DNI {person.doc_nro}: constancia generada.")
            if not args.no_download:
                download_constancia(driver, wait)
                print(f"Descarga iniciada en: {args.download_dir.resolve()}")

        print(f"Resultado final: {len(people) - errors}/{len(people)} exitosos.")
        return 0 if errors == 0 else 1

    except TimeoutException as exc:
        snapshot_path = args.download_dir / "anses_timeout_snapshot.html"
        try:
            args.download_dir.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(driver.page_source, encoding="utf-8")
            extra = f" Se guardó snapshot HTML en: {snapshot_path.resolve()}"
        except Exception:
            extra = ""
        print(
            "ERROR: Tiempo de espera agotado."
            f" URL actual: {driver.current_url}. Detalle: {exc}.{extra}"
        )
        return 2
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 2
    except Exception as exc:  # pragma: no cover
        print(f"ERROR INESPERADO: {exc}")
        return 3
    finally:
        driver.quit()


if __name__ == "__main__":
    sys.exit(main())
