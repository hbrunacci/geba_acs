"""Lectura y edición de la ficha del socio en xSys (tabla ``Clientes``).

Es el único lugar de la app que ESCRIBE en xSys. Todo lo demás es espejo de sólo
lectura. Por eso acá hay más ceremonia que en el resto: lista blanca de columnas,
validación por tipo, avisos antes de guardar y auditoría campo por campo.

Tres cosas que conviene tener presentes antes de tocar esto:

1. ``Clientes`` tiene 156 columnas y la enorme mayoría son contables, de AFIP o de
   logística —el ERP es genérico y el club es un cliente más—. Acá se expone la
   ficha del SOCIO: identificación, datos personales, contacto, domicilio,
   condición de socio, credencial y observaciones. Agregar una columna es sumar
   un ``Campo`` a ``CAMPOS``; lo que no está en esa lista no se puede escribir ni
   aunque llegue en el JSON.

2. Un UPDATE dispara ``tri_clientes``, que llama a ``SP_Clientes_Validar`` y puede
   abortar con un error propio. Ese mensaje se devuelve tal cual al operador: es
   la validación del ERP y decirle "error al guardar" a secas sería esconderla.
   También dispara ``CT_Clientes_CD_Clientes_Novedades``, que es el canal por el
   que las bajas y altas llegan a los lectores faciales.

3. Hay campos que parecen inocentes y no lo son. ``Id_Tipo_Cli`` decide la cuota y
   por qué puertas pasa; ``Activo`` corta el acceso; ``Id_Cliente_Ref`` arma el
   grupo familiar y con él la facturación de los integrantes. Van marcados con
   ``riesgo`` para que la pantalla los muestre con la advertencia a la vista.
"""

from __future__ import annotations

import datetime as _dt
import threading
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from xsys.services.mssql import connect


class FichaError(Exception):
    """Error de validación o de escritura sobre la ficha. El texto se le muestra al operador."""


class FichaConfirmacion(Exception):
    """El cambio es válido pero tiene una consecuencia que hay que confirmar antes.

    Existe porque un aviso mostrado DESPUÉS de guardar no sirve de nada. El caso
    que la motiva es el documento repetido: si dos fichas tienen el mismo número,
    en el molinete gana una sola y la otra persona queda afuera sin que nadie
    entienda por qué. El operador tiene que ver eso antes, no después.
    """

    def __init__(self, motivos: list[str]):
        super().__init__(" ".join(motivos))
        self.motivos = motivos


@dataclass(frozen=True)
class Campo:
    col: str                      # columna real en Clientes
    label: str
    grupo: str
    tipo: str = "texto"           # texto | entero | fecha | bool | opcion
    editable: bool = True
    maxlen: int | None = None
    catalogo: str | None = None   # clave de CATALOGOS, sólo para tipo "opcion"
    numerico: bool = False        # opción cuyo valor es numérico (smallint) y no char
    ayuda: str = ""
    riesgo: str = ""              # advertencia que la pantalla muestra junto al campo


GRUPO_IDENT = "Identificación"
GRUPO_PERSONA = "Datos personales"
GRUPO_CONTACTO = "Contacto"
GRUPO_DOMICILIO = "Domicilio"
GRUPO_SOCIO = "Condición de socio"
GRUPO_CREDENCIAL = "Credencial"
GRUPO_NOTAS = "Observaciones"
GRUPO_SISTEMA = "Sistema"


CAMPOS: tuple[Campo, ...] = (
    # --- sólo lectura: los maneja el ERP -------------------------------------
    Campo("Id_Cliente", "Id de cliente", GRUPO_SISTEMA, "entero", editable=False),
    Campo("Id_Cliente_Externo", "Nº de socio", GRUPO_SISTEMA, editable=False,
          ayuda="Lo asigna xSys por trigger cuando se crea la ficha."),
    Campo("Ult_Cuota_Paga", "Última cuota paga", GRUPO_SISTEMA, "fecha", editable=False,
          ayuda="La mantiene el trigger Tri_Ult_Cuota_Paga a partir de los pagos."),
    Campo("Fecha_Modif", "Última modificación", GRUPO_SISTEMA, "fecha", editable=False),

    # --- identificación -------------------------------------------------------
    Campo("Id_Tipo_Doc", "Tipo de documento", GRUPO_IDENT, "opcion", catalogo="tipo_doc", maxlen=3),
    Campo("Doc_Nro", "Nº de documento", GRUPO_IDENT, "entero",
          ayuda="Es lo que el socio tipea en el molinete. Sin esto no puede identificarse."),
    Campo("Cuit", "CUIT", GRUPO_IDENT, maxlen=19),
    Campo("Pasap_Nro", "Pasaporte", GRUPO_IDENT, maxlen=50),
    Campo("Legajo", "Legajo", GRUPO_IDENT, maxlen=30),

    # --- datos personales -----------------------------------------------------
    Campo("Apellido", "Apellido", GRUPO_PERSONA, maxlen=100),
    Campo("Nombre", "Nombre", GRUPO_PERSONA, maxlen=100),
    Campo("Razon_Social", "Razón social", GRUPO_PERSONA, maxlen=100,
          ayuda="Se usa en las fichas que no son personas (empresas, concesiones)."),
    Campo("Sexo", "Sexo", GRUPO_PERSONA, "opcion", catalogo="sexo", maxlen=1),
    Campo("Fecha_Nac", "Fecha de nacimiento", GRUPO_PERSONA, "fecha",
          ayuda="Define la categoría por edad en la facturación de la cuota."),
    Campo("Estado_Civil", "Estado civil", GRUPO_PERSONA, "opcion", catalogo="estado_civil", maxlen=1),
    Campo("Tipo_Persona", "Tipo de persona", GRUPO_PERSONA, "opcion", catalogo="tipo_persona", maxlen=1,
          ayuda="Sólo las 'F' (física) cuentan como integrantes de un grupo familiar."),

    # --- contacto -------------------------------------------------------------
    Campo("Email", "Email", GRUPO_CONTACTO, maxlen=600),
    Campo("Email_Web", "Email del portal web", GRUPO_CONTACTO, maxlen=300),
    Campo("Tel_Movil", "Celular", GRUPO_CONTACTO, maxlen=20),
    Campo("telefono", "Teléfonos", GRUPO_CONTACTO, maxlen=900),
    Campo("Flag_Comunic_Email", "Acepta avisos por email", GRUPO_CONTACTO, "bool"),
    Campo("Flag_Comunic_Sms", "Acepta avisos por SMS", GRUPO_CONTACTO, "bool"),

    # --- domicilio ------------------------------------------------------------
    Campo("Direccion", "Calle", GRUPO_DOMICILIO, maxlen=100),
    Campo("Nro", "Número", GRUPO_DOMICILIO, "entero"),
    Campo("Depto", "Piso / depto", GRUPO_DOMICILIO, maxlen=10),
    Campo("Cp", "Código postal", GRUPO_DOMICILIO, maxlen=10),
    Campo("Localidad_Descrip", "Localidad", GRUPO_DOMICILIO, maxlen=200),
    Campo("Provincia_Descrip", "Provincia", GRUPO_DOMICILIO, maxlen=200),
    Campo("Entre_Calle_1", "Entre calle", GRUPO_DOMICILIO, maxlen=35),
    Campo("Entre_Calle_2", "Y calle", GRUPO_DOMICILIO, maxlen=35),

    # --- condición de socio ---------------------------------------------------
    Campo("Id_Tipo_Cli", "Categoría", GRUPO_SOCIO, "opcion", catalogo="categoria", numerico=True,
          riesgo="Decide cuánto paga de cuota y por qué accesos puede pasar."),
    Campo("Activo", "Activo", GRUPO_SOCIO, "bool",
          riesgo="En 0 deja de pasar por los molinetes y sale de la lista blanca del facial."),
    Campo("Id_Estado_Cliente", "Estado", GRUPO_SOCIO, "opcion", catalogo="estado_cliente", numerico=True),
    Campo("Id_Motivo_Est", "Motivo del estado", GRUPO_SOCIO, "opcion", catalogo="motivo_est", numerico=True),
    Campo("Fecha_Alta", "Fecha de alta", GRUPO_SOCIO, "fecha"),
    Campo("Fecha_Baja", "Fecha de baja", GRUPO_SOCIO, "fecha"),
    Campo("Id_Cliente_Ref", "Titular del grupo familiar", GRUPO_SOCIO, "entero",
          ayuda="Id de cliente del titular. 0 = es titular (o no tiene grupo).",
          riesgo="Mueve al socio de grupo familiar y con él su cuota: la cuota de los "
                 "integrantes se factura en el cupón del titular."),
    Campo("Id_Cond_Vta", "Condición de venta", GRUPO_SOCIO, "opcion", catalogo="cond_vta", maxlen=10,
          ayuda="GFCP cobra a cada integrante por separado en vez de agrupar en el titular."),

    # --- credencial -----------------------------------------------------------
    Campo("Credencial_Nro", "Nº de credencial", GRUPO_CREDENCIAL, maxlen=30,
          riesgo="Es la tarjeta con la que abre los molinetes."),
    Campo("Credencial_Entrega", "Fecha de entrega", GRUPO_CREDENCIAL, "fecha"),

    # --- notas ----------------------------------------------------------------
    Campo("Observacion", "Observaciones", GRUPO_NOTAS, maxlen=4000),
)

POR_COL: dict[str, Campo] = {c.col: c for c in CAMPOS}
EDITABLES: dict[str, Campo] = {c.col: c for c in CAMPOS if c.editable}
GRUPOS: tuple[str, ...] = (
    GRUPO_IDENT, GRUPO_PERSONA, GRUPO_CONTACTO, GRUPO_DOMICILIO,
    GRUPO_SOCIO, GRUPO_CREDENCIAL, GRUPO_NOTAS, GRUPO_SISTEMA,
)

# Opciones que no viven en una tabla de catálogo: son letras sueltas en la columna.
OPCIONES_FIJAS: dict[str, list[dict]] = {
    "sexo": [
        {"valor": "M", "texto": "Masculino"},
        {"valor": "F", "texto": "Femenino"},
        {"valor": "N", "texto": "Sin especificar"},
    ],
    "estado_civil": [
        {"valor": "S", "texto": "Soltero/a"},
        {"valor": "C", "texto": "Casado/a"},
        {"valor": "D", "texto": "Divorciado/a"},
        {"valor": "V", "texto": "Viudo/a"},
        {"valor": "U", "texto": "Unión convivencial"},
        {"valor": "N", "texto": "Sin especificar"},
    ],
    "tipo_persona": [
        {"valor": "F", "texto": "Física"},
        {"valor": "J", "texto": "Jurídica"},
        {"valor": "A", "texto": "Adherente"},
        {"valor": "E", "texto": "Extranjera"},
        {"valor": "I", "texto": "Indistinta"},
    ],
}

# Catálogos que sí salen de xSys: (tabla, columna id, columna descripción).
CATALOGOS_SQL: dict[str, tuple[str, str, str]] = {
    "tipo_doc": ("Documentos_Tipos", "Id_Tipo_Doc", "Descripcion"),
    "categoria": ("Clientes_Tipos", "Id_Tipo_Cli", "Descripcion"),
    "estado_cliente": ("Clientes_Estados", "Id_Estado_Cliente", "Descripcion"),
    "motivo_est": ("Tab_Motivos_Est", "Id_Motivo_Est", "Descripcion"),
    "cond_vta": ("Cbtes_Cond_Vtas", "Id_Cond_Vta", "Descripcion"),
}

_CACHE_SEGUNDOS = 600
_cache: dict = {"armado_en": None, "catalogos": None}
_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# catálogos
# ---------------------------------------------------------------------------

def _leer_catalogos(cur) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {k: list(v) for k, v in OPCIONES_FIJAS.items()}
    for clave, (tabla, col_id, col_desc) in CATALOGOS_SQL.items():
        cur.execute(f"SELECT {col_id}, {col_desc} FROM {tabla}")
        filas = []
        for valor, texto in cur.fetchall():
            valor = "" if valor is None else str(valor).strip()
            texto = (texto or "").strip() or valor
            filas.append({"valor": valor, "texto": texto})
        filas.sort(key=lambda f: f["texto"].upper())
        out[clave] = filas
    return out


def catalogos(*, refrescar: bool = False) -> dict[str, list[dict]]:
    """Opciones de los campos de tipo ``opcion``. Se cachean: casi nunca cambian."""
    ahora = timezone.now()
    with _cache_lock:
        armado = _cache["armado_en"]
        vigente = (
            not refrescar
            and _cache["catalogos"] is not None
            and armado is not None
            and (ahora - armado).total_seconds() < _CACHE_SEGUNDOS
        )
        if vigente:
            return _cache["catalogos"]
    conn = connect(settings.MSSQL_XSYS)
    try:
        datos = _leer_catalogos(conn.cursor())
    finally:
        conn.close()
    with _cache_lock:
        _cache["armado_en"] = ahora
        _cache["catalogos"] = datos
    return datos


def invalidar_cache() -> None:
    with _cache_lock:
        _cache["armado_en"] = None
        _cache["catalogos"] = None


def definicion() -> list[dict]:
    """La ficha tal como la dibuja la pantalla: campos ordenados por grupo."""
    cats = catalogos()
    salida = []
    for grupo in GRUPOS:
        campos = [c for c in CAMPOS if c.grupo == grupo]
        if not campos:
            continue
        salida.append({
            "grupo": grupo,
            "campos": [
                {
                    "col": c.col,
                    "label": c.label,
                    "tipo": c.tipo,
                    "editable": c.editable,
                    "maxlen": c.maxlen,
                    "ayuda": c.ayuda,
                    "riesgo": c.riesgo,
                    "opciones": cats.get(c.catalogo or "", []) if c.tipo == "opcion" else None,
                }
                for c in campos
            ],
        })
    return salida


# ---------------------------------------------------------------------------
# lectura
# ---------------------------------------------------------------------------

def _valor_publico(campo: Campo, crudo):
    if crudo is None:
        return None
    if campo.tipo == "fecha":
        if isinstance(crudo, _dt.datetime):
            return crudo.date().isoformat()
        if isinstance(crudo, _dt.date):
            return crudo.isoformat()
        return str(crudo)
    if campo.tipo == "bool":
        return bool(int(crudo))
    if campo.tipo == "entero":
        return int(crudo)
    return str(crudo).strip()


def leer(id_cliente: int, cur=None) -> dict | None:
    """Devuelve los valores de la ficha listos para el formulario, o None si no existe."""
    cols = [c.col for c in CAMPOS]
    sql = "SELECT " + ", ".join(f"[{c}]" for c in cols) + " FROM Clientes WHERE Id_Cliente = ?"
    propio = cur is None
    conn = None
    if propio:
        conn = connect(settings.MSSQL_XSYS)
        cur = conn.cursor()
    try:
        cur.execute(sql, (int(id_cliente),))
        fila = cur.fetchone()
    finally:
        if propio and conn is not None:
            conn.close()
    if fila is None:
        return None
    return {c: _valor_publico(POR_COL[c], v) for c, v in zip(cols, fila)}


# ---------------------------------------------------------------------------
# validación
# ---------------------------------------------------------------------------

def _normalizar(campo: Campo, valor):
    """Convierte lo que llegó del formulario al valor que va a la base.

    Vacío siempre es NULL: en la ficha "sin dato" y "cadena vacía" son lo mismo,
    y dejar '' donde el ERP espera NULL hace que después no matcheen los ISNULL.
    """
    if valor is None:
        return None
    if isinstance(valor, str):
        valor = valor.strip()
        if valor == "":
            return None

    if campo.tipo == "entero":
        try:
            return int(str(valor).replace(".", "").replace(" ", ""))
        except (TypeError, ValueError):
            raise FichaError(f"{campo.label}: '{valor}' no es un número entero.")

    if campo.tipo == "bool":
        if isinstance(valor, bool):
            return 1 if valor else 0
        texto = str(valor).strip().lower()
        if texto in ("1", "true", "si", "sí"):
            return 1
        if texto in ("0", "false", "no"):
            return 0
        raise FichaError(f"{campo.label}: '{valor}' no es sí/no.")

    if campo.tipo == "fecha":
        if isinstance(valor, _dt.datetime):
            return valor
        if isinstance(valor, _dt.date):
            return _dt.datetime(valor.year, valor.month, valor.day)
        try:
            d = _dt.date.fromisoformat(str(valor)[:10])
        except ValueError:
            raise FichaError(f"{campo.label}: '{valor}' no es una fecha AAAA-MM-DD.")
        return _dt.datetime(d.year, d.month, d.day)

    if campo.tipo == "opcion":
        texto = str(valor).strip()
        permitidos = {o["valor"] for o in catalogos().get(campo.catalogo or "", [])}
        if texto not in permitidos:
            raise FichaError(f"{campo.label}: '{texto}' no es una opción válida.")
        if campo.numerico:
            try:
                return int(texto)
            except ValueError:
                raise FichaError(f"{campo.label}: '{texto}' no es un valor numérico.")
        return texto

    texto = str(valor)
    if campo.maxlen and len(texto) > campo.maxlen:
        raise FichaError(f"{campo.label}: no puede superar {campo.maxlen} caracteres.")
    return texto


def _a_confirmar(cur, id_cliente: int, nuevos: dict) -> list[str]:
    """Consecuencias que hay que aceptar ANTES de escribir, no enterarse después."""
    motivos: list[str] = []

    if nuevos.get("Doc_Nro"):
        cur.execute(
            "SELECT TOP 5 Id_Cliente, RTRIM(ISNULL(Apellido,'')) + ' ' + RTRIM(ISNULL(Nombre,'')), "
            "       ISNULL(Activo,0) "
            "FROM Clientes WHERE Doc_Nro = ? AND Id_Cliente <> ?",
            (nuevos["Doc_Nro"], id_cliente),
        )
        otros = cur.fetchall()
        if otros:
            activas = [r for r in otros if r[2]]
            detalle = "; ".join(
                f"{r[0]} {(r[1] or '').strip() or 'sin nombre'}"
                f"{' (activa)' if r[2] else ' (de baja)'}"
                for r in otros
            )
            motivos.append(
                f"Ese documento ya está cargado en otras {len(otros)} ficha(s): {detalle}. "
                + (
                    "Al tipearlo en un molinete, xSys resuelve una sola ficha y prioriza la "
                    "activa: la otra persona puede quedar afuera."
                    if activas
                    else "Las otras están de baja, así que en el molinete va a ganar ésta."
                )
            )

    if nuevos.get("Activo") == 0:
        motivos.append(
            "Al quedar inactivo deja de pasar por los molinetes y sale de la lista blanca "
            "del reconocimiento facial."
        )

    return motivos


def _avisos(cur, id_cliente: int, nuevos: dict, actual: dict) -> list[str]:
    """Cosas que no impiden guardar pero el operador tiene que saber."""
    avisos: list[str] = []

    activo = nuevos.get("Activo", actual.get("Activo"))
    baja = nuevos.get("Fecha_Baja", actual.get("Fecha_Baja"))
    if "Activo" in nuevos:
        if nuevos["Activo"] == 0 and not baja:
            avisos.append("Queda inactivo sin fecha de baja cargada.")
        if nuevos["Activo"] == 1 and baja:
            avisos.append("Queda activo pero conserva la fecha de baja.")
    if activo == 1 and "Id_Tipo_Cli" in nuevos:
        avisos.append("Cambió la categoría de un socio activo: revisá que la cuota sea la esperada.")

    return avisos


def _validar_grupo(cur, id_cliente: int, ref: int | None) -> None:
    """El titular tiene que existir, no ser el propio socio y ser titular él mismo."""
    if ref in (None, 0):
        return
    if ref == id_cliente:
        raise FichaError("Un socio no puede ser titular de sí mismo.")
    cur.execute(
        "SELECT ISNULL(Id_Cliente_Ref,0), ISNULL(Activo,0), "
        "       RTRIM(ISNULL(Apellido,'')) + ' ' + RTRIM(ISNULL(Nombre,'')) "
        "FROM Clientes WHERE Id_Cliente = ?",
        (ref,),
    )
    fila = cur.fetchone()
    if fila is None:
        raise FichaError(f"No existe la ficha {ref} para usarla como titular.")
    if fila[0]:
        raise FichaError(
            f"La ficha {ref} ({fila[2].strip()}) es a su vez integrante del grupo {fila[0]}. "
            "El titular tiene que ser cabeza de grupo."
        )
    cur.execute("SELECT COUNT(*) FROM Clientes WHERE Id_Cliente_Ref = ? AND ISNULL(Activo,0) = 1",
                (id_cliente,))
    if cur.fetchone()[0]:
        raise FichaError(
            "Este socio ya tiene integrantes a cargo: si pasa a ser integrante de otro grupo, "
            "los suyos quedan colgados. Primero reasignalos."
        )


# ---------------------------------------------------------------------------
# escritura
# ---------------------------------------------------------------------------

def _texto_auditoria(campo: Campo, valor) -> str:
    if valor is None:
        return ""
    if campo.tipo == "bool":
        return "sí" if int(valor) else "no"
    if campo.tipo == "fecha":
        return valor.date().isoformat() if isinstance(valor, _dt.datetime) else str(valor)
    if campo.tipo == "opcion":
        for o in catalogos().get(campo.catalogo or "", []):
            if o["valor"] == str(valor).strip():
                return o["texto"]
    return str(valor)[:255]


def guardar(id_cliente: int, cambios: dict, *, usuario: str = "", ip: str = "",
            confirmado: bool = False) -> dict:
    """Aplica los cambios sobre ``Clientes`` y deja la auditoría.

    Devuelve ``{"aplicados": [...], "avisos": [...], "ficha": {...}}``. Si no hay
    nada que cambiar no toca la base: guardar sin editar no debe ensuciar
    ``Fecha_Modif`` ni disparar el canal de novedades del facial.

    Si el cambio tiene consecuencias que hay que aceptar primero (documento
    repetido, baja del socio) levanta ``FichaConfirmacion`` sin escribir nada; el
    llamador vuelve a pedirlo con ``confirmado=True``.
    """
    id_cliente = int(id_cliente)
    desconocidos = [c for c in cambios if c not in EDITABLES]
    if desconocidos:
        raise FichaError("Campos no editables: " + ", ".join(sorted(desconocidos)))

    conn = connect(settings.MSSQL_XSYS)
    try:
        cur = conn.cursor()
        actual_crudo = _leer_crudo(cur, id_cliente)
        if actual_crudo is None:
            raise FichaError(f"No existe la ficha {id_cliente} en xSys.")

        nuevos: dict = {}
        for col, valor in cambios.items():
            campo = EDITABLES[col]
            nuevo = _normalizar(campo, valor)
            if _igual(campo, actual_crudo.get(col), nuevo):
                continue
            nuevos[col] = nuevo

        if not nuevos:
            return {"aplicados": [], "avisos": [], "ficha": leer(id_cliente, cur)}

        if "Id_Cliente_Ref" in nuevos:
            _validar_grupo(cur, id_cliente, nuevos["Id_Cliente_Ref"])

        if not confirmado:
            motivos = _a_confirmar(cur, id_cliente, nuevos)
            if motivos:
                raise FichaConfirmacion(motivos)

        avisos = _avisos(cur, id_cliente, nuevos, actual_crudo)

        cols = list(nuevos)
        sets = ", ".join(f"[{c}] = ?" for c in cols) + ", [Fecha_Modif] = GETDATE()"
        params = [nuevos[c] for c in cols] + [id_cliente]
        try:
            cur.execute(f"UPDATE Clientes SET {sets} WHERE Id_Cliente = ?", params)
            if cur.rowcount != 1:
                raise FichaError(f"El UPDATE afectó {cur.rowcount} filas; se cancela.")
            conn.commit()
        except FichaError:
            conn.rollback()
            raise
        except Exception as exc:  # el trigger SP_Clientes_Validar aborta con su propio mensaje
            conn.rollback()
            raise FichaError(f"xSys rechazó el cambio: {_mensaje_sql(exc)}") from exc

        aplicados = [
            {
                "campo": c,
                "etiqueta": EDITABLES[c].label,
                "anterior": _texto_auditoria(EDITABLES[c], actual_crudo.get(c)),
                "nuevo": _texto_auditoria(EDITABLES[c], nuevos[c]),
            }
            for c in cols
        ]
        _auditar(id_cliente, aplicados, usuario=usuario, ip=ip)
        _refrescar_espejo(cur, id_cliente)
        return {"aplicados": aplicados, "avisos": avisos, "ficha": leer(id_cliente, cur)}
    finally:
        conn.close()


def _leer_crudo(cur, id_cliente: int) -> dict | None:
    cols = [c.col for c in CAMPOS]
    cur.execute(
        "SELECT " + ", ".join(f"[{c}]" for c in cols) + " FROM Clientes WHERE Id_Cliente = ?",
        (id_cliente,),
    )
    fila = cur.fetchone()
    if fila is None:
        return None
    salida = {}
    for col, v in zip(cols, fila):
        campo = POR_COL[col]
        if isinstance(v, str):
            v = v.strip() or None
        elif campo.tipo == "bool" and v is not None:
            v = int(v)
        elif campo.tipo == "entero" and v is not None:
            v = int(v)
        salida[col] = v
    return salida


def _igual(campo: Campo, viejo, nuevo) -> bool:
    if viejo is None and nuevo is None:
        return True
    if viejo is None or nuevo is None:
        return False
    if campo.tipo == "fecha":
        a = viejo.date() if isinstance(viejo, _dt.datetime) else viejo
        b = nuevo.date() if isinstance(nuevo, _dt.datetime) else nuevo
        return a == b
    return str(viejo).strip() == str(nuevo).strip()


def _mensaje_sql(exc: Exception) -> str:
    """Saca el texto útil del error de pyodbc, que viene con prefijos de driver."""
    texto = str(exc)
    marca = "[SQL Server]"
    if marca in texto:
        texto = texto.split(marca)[-1]
    return texto.strip().strip("()'\" ")[:400]


def _auditar(id_cliente: int, aplicados: list[dict], *, usuario: str, ip: str) -> None:
    from xsys.models import XsysSocioEdicion

    with transaction.atomic():
        XsysSocioEdicion.objects.bulk_create([
            XsysSocioEdicion(
                id_cliente=id_cliente,
                campo=a["campo"][:60],
                etiqueta=a["etiqueta"][:80],
                valor_anterior=(a["anterior"] or "")[:255],
                valor_nuevo=(a["nuevo"] or "")[:255],
                usuario=(usuario or "")[:150],
                ip=(ip or "")[:45],
            )
            for a in aplicados
        ])


def _refrescar_espejo(cur, id_cliente: int) -> None:
    """Trae la ficha recién guardada al espejo local, sin esperar al sync.

    Si falla no se rompe el guardado: el cambio en xSys ya está hecho y el sync
    periódico lo va a levantar igual. Sólo se pierde ver el dato nuevo en la
    grilla hasta el próximo ciclo.
    """
    try:
        from xsys.services.sync import XsysSyncService

        XsysSyncService().sync_socios_by_ids(cur, [id_cliente], only_active=False)
    except Exception:  # pragma: no cover - defensivo, depende de red/datos
        import logging

        logging.getLogger(__name__).warning(
            "No se pudo refrescar el espejo local del socio %s tras editarlo", id_cliente
        )
