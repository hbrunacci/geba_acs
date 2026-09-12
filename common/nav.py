"""Estructura del menú lateral.

El menú vivía en el HTML como veinte ``<li>`` con su propio ``{% if %}`` de rol y
su propia comparación de ``url_name`` para pintarse activo. Funcionaba, pero
agregar una pantalla obligaba a tocar tres lugares del template y era fácil que
un grupo quedara visible sin ningún hijo adentro, o abierto cuando no
correspondía.

Acá el menú es un dato: una lista de ítems y grupos con el rol que cada uno
necesita. El context processor la resuelve contra el usuario y la URL actual, y
el template sólo recorre el resultado. Agregar una pantalla es agregar una línea.

CÓMO SE LEEN LOS ROLES
----------------------
Cada ítem declara el rol MÍNIMO que lo ve, con los mismos criterios de
``common.roles``:

    admin           superusuario o grupo Administrador
    puertas         admin o grupo Configuración de Puertas
    socios          puertas o grupo socios
    tableros        superusuario, staff o grupo tableros (NO alcanza admin)
    concesionarios  superusuario, staff o grupo concesionarios (NO alcanza admin)

Un grupo NO declara rol propio: se muestra si al menos uno de sus hijos es
visible para ese usuario. Así un grupo no puede quedar vacío en pantalla, que es
justamente lo que pasaba antes con "Socios" para quien sólo tenía el rol de
puertas.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from common.roles import (
    es_admin,
    puede_concesionarios,
    puede_config_puertas,
    puede_socios,
    puede_tableros,
)

CHEQUEOS = {
    "admin": es_admin,
    "puertas": puede_config_puertas,
    "socios": puede_socios,
    "tableros": puede_tableros,
    "concesionarios": puede_concesionarios,
}


@dataclass(frozen=True)
class Item:
    """Una pantalla del menú.

    ``url`` es el nombre de ruta para ``{% url %}``; ``activo_en`` es el
    ``url_name`` con el que se compara para pintarlo seleccionado, que no es lo
    mismo cuando la ruta tiene namespace (``common:dashboard`` resuelve al
    ``url_name`` ``dashboard``).
    """

    titulo: str
    url: str
    rol: str = "admin"
    activo_en: str = ""
    nueva_pestania: bool = False

    @property
    def marca(self) -> str:
        return self.activo_en or self.url.split(":")[-1]

    def visible(self, user) -> bool:
        return CHEQUEOS[self.rol](user)


@dataclass(frozen=True)
class Grupo:
    """Un desplegable con pantallas adentro."""

    titulo: str
    items: tuple[Item, ...] = field(default_factory=tuple)

    def hijos_visibles(self, user) -> list[Item]:
        return [i for i in self.items if i.visible(user)]


# --------------------------------------------------------------------------- #
# El menú
# --------------------------------------------------------------------------- #
# El orden es el de uso, no el alfabético: arriba lo que se mira todos los días
# (el resumen y los tableros), en el medio el trabajo de mostrador y de puertas,
# y al final lo que sólo toca sistemas.

MENU: tuple = (
    Item("Resumen", "common:dashboard", rol="admin", activo_en="dashboard"),

    Grupo("Socios", (
        Item("Fichas de socios", "xsys_socios_fichas", rol="socios"),
        Item("Avisos a socios", "avisos_pendientes", rol="socios"),
        Item("Personas y documentación", "people_configuration_console", rol="admin"),
        Item("Socios (espejo xSys)", "xsys_socio_console", rol="admin"),
    )),

    Grupo("ACS", (
        Item("Puertas y zonas", "access_topology_console", rol="admin"),
        Item("Molinetes por puerta", "xsys_molinetes_config", rol="puertas"),
        Item("Visor de puerta", "xsys_puerta_monitor", rol="puertas",
             nueva_pestania=True),
        Item("¿Por qué no entra?", "xsys_diagnostico", rol="puertas"),
        Item("Diagnóstico de facial", "diag_facial_console", rol="puertas"),
        Item("Eventos", "events_console", rol="admin"),
        Item("Reportes de accesos", "access_reports_console", rol="admin"),
        Item("Movimientos externos", "external_access_console", rol="admin"),
        Item("Estacionamiento", "parking_movements_console", rol="admin"),
    )),

    Item("Tableros", "xsys_tableros", rol="tableros"),

    Grupo("Concesionarios", (
        Item("Listado", "concesionarios_listado", rol="concesionarios"),
        Item("Ingresos al club", "concesionarios_ingresos", rol="concesionarios"),
        Item("Empresas y documentos", "concesionarios_empresas", rol="concesionarios"),
        Item("Horarios de ingreso", "concesionarios_horarios", rol="concesionarios"),
    )),

    Grupo("Sistema", (
        # Biostar era su propio desplegable. Se aplanó a dos entradas en vez de
        # anidar un desplegable dentro de otro: el CSS del sidebar tiene un solo
        # nivel de sublista, y un tercer nivel quedaría sin estilo.
        Item("Biostar · Dispositivos", "biostar_devices_console", rol="admin"),
        Item("Biostar · Personas", "biostar_users_console", rol="admin"),
        Item("Equipos Intelektron", "intelektron_admin", rol="admin"),
        Item("Consola API3000", "api3000_test_console", rol="admin"),
        Item("Salud de pollers", "pollers_dashboard", rol="admin"),
        Item("Verificar situación ANSES", "anses_verification_console", rol="admin"),
    )),
)


def construir(user, url_actual: str = "") -> list[dict]:
    """Devuelve el menú ya filtrado por rol y con el ítem actual marcado.

    El template no decide nada: recorre esto y lo dibuja. Un grupo cuyo hijo es
    el ítem actual viene con ``abierto=True`` para que el desplegable arranque
    desplegado y no haya que buscar dónde estamos parados.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return []

    salida: list[dict] = []
    for entrada in MENU:
        if isinstance(entrada, Item):
            if entrada.visible(user):
                salida.append({
                    "tipo": "item",
                    "titulo": entrada.titulo,
                    "url": entrada.url,
                    "activo": entrada.marca == url_actual,
                    "nueva_pestania": entrada.nueva_pestania,
                })
            continue

        hijos = entrada.hijos_visibles(user)
        if not hijos:
            continue
        hijos_dict = [{
            "titulo": i.titulo,
            "url": i.url,
            "activo": i.marca == url_actual,
            "nueva_pestania": i.nueva_pestania,
        } for i in hijos]
        salida.append({
            "tipo": "grupo",
            "titulo": entrada.titulo,
            "items": hijos_dict,
            "abierto": any(h["activo"] for h in hijos_dict),
        })
    return salida
