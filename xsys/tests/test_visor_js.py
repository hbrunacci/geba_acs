"""Chequeos estáticos del JavaScript del visor de molinetes.

El visor corre en kioscos colgados en la pared, y una de esas pantallas es un
Chrome 49 sobre Windows XP. Ahí un error de JS no degrada nada: corta el script y
deja la pantalla en blanco, sin que nadie lo note hasta que alguien avisa que "no
se ve". Ya pasó dos veces seguidas:

- ``padStart`` (Chrome 57+) en el navegador de días, y
- ``verDia`` usada en once lugares sin declarar en ninguno.

Las dos se detectan leyendo el archivo, así que se chequean acá.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    import esprima
except ImportError:  # pragma: no cover - depende del entorno
    esprima = None

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "xsys" / "puerta_monitor.html"

# Globals del navegador que el visor puede usar sin declarar. Si hace falta uno
# nuevo, se agrega acá; la lista corta es justamente la que hace útil el test.
GLOBALES = {
    "window", "document", "location", "console", "localStorage", "navigator",
    "fetch", "setInterval", "setTimeout", "clearTimeout", "clearInterval",
    "JSON", "Math", "Date", "Array", "Object", "String", "Number", "Boolean",
    "RegExp", "parseInt", "parseFloat", "isNaN", "isFinite", "encodeURIComponent",
    "decodeURIComponent", "Uint8Array", "Error", "alert", "confirm", "Promise",
    "crypto", "msCrypto", "undefined", "NaN", "Infinity",
}

# Métodos que NO existen en Chrome 49 (el piso real del parque de pantallas).
METODOS_PROHIBIDOS = {
    "padStart": "Chrome 57+",
    "padEnd": "Chrome 57+",
    "trimStart": "Chrome 66+",
    "trimEnd": "Chrome 66+",
    "flat": "Chrome 69+",
    "flatMap": "Chrome 69+",
    "finally": "Promise.finally, Chrome 63+",
    "entries": "Object.entries, Chrome 54+",
    "values": "Object.values, Chrome 54+",
    "fromEntries": "Chrome 73+",
    "matchAll": "Chrome 73+",
    "replaceAll": "Chrome 85+",
    "at": "Chrome 92+",
}

# Nodos de sintaxis ES6+: no los entiende un navegador viejo y, peor, un error de
# sintaxis anula el archivo ENTERO antes de ejecutar una sola línea.
NODOS_ES6 = {
    "ArrowFunctionExpression": "arrow function",
    "TemplateLiteral": "template literal",
    "ClassDeclaration": "class",
    "ClassExpression": "class",
    "SpreadElement": "spread (...)",
    "RestElement": "rest (...)",
    "AssignmentPattern": "parámetro con valor por defecto",
    "ObjectPattern": "destructuring de objeto",
    "ArrayPattern": "destructuring de array",
    "ForOfStatement": "for...of",
}


def _script() -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
    bloques = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
    assert len(bloques) == 1, f"se esperaba un solo <script>, hay {len(bloques)}"
    return bloques[0]


def _recorrer(nodo, visita, padre=None):
    if not hasattr(nodo, "type"):
        return
    visita(nodo, padre)
    for attr in dir(nodo):
        if attr.startswith("_"):
            continue
        valor = getattr(nodo, attr, None)
        if isinstance(valor, list):
            for hijo in valor:
                _recorrer(hijo, visita, nodo)
        elif hasattr(valor, "type"):
            _recorrer(valor, visita, nodo)


@unittest.skipIf(esprima is None, "falta esprima (está en requirements.txt)")
class VisorJavaScriptTests(unittest.TestCase):
    """No tocan la base: leen el template y analizan el JS."""

    @classmethod
    def setUpClass(cls):
        cls.codigo = _script()
        cls.arbol = esprima.parseScript(cls.codigo)

    def test_el_javascript_es_sintacticamente_valido(self):
        # setUpClass ya lo parsea: si el archivo estuviera roto, ni llegaríamos.
        self.assertGreater(len(self.codigo), 1000)

    def test_no_hay_variables_sin_declarar(self):
        """Leer una variable no declarada corta el script (así se cayó el visor)."""
        declaradas: set[str] = set()
        leidas: dict[str, int] = {}

        def visita(n, padre):
            t = n.type
            if t == "VariableDeclarator" and getattr(n.id, "name", None):
                declaradas.add(n.id.name)
            elif t in ("FunctionDeclaration", "FunctionExpression"):
                if getattr(n, "id", None) and getattr(n.id, "name", None):
                    declaradas.add(n.id.name)
                for p in (n.params or []):
                    if getattr(p, "name", None):
                        declaradas.add(p.name)
            elif t == "CatchClause" and getattr(n.param, "name", None):
                declaradas.add(n.param.name)
            elif t == "Identifier":
                # No contar nombres de propiedad (a.b) ni claves de objeto ({b: 1}).
                es_propiedad = (
                    padre is not None and padre.type == "MemberExpression"
                    and padre.property is n and not padre.computed
                )
                es_clave = padre is not None and padre.type == "Property" and padre.key is n
                if not es_propiedad and not es_clave:
                    leidas.setdefault(n.name, 0)
                    leidas[n.name] += 1

        _recorrer(self.arbol, visita)
        faltantes = sorted(set(leidas) - declaradas - GLOBALES)
        self.assertEqual(
            faltantes, [],
            "variables usadas sin declarar en puerta_monitor.html: "
            f"{faltantes}. Declarálas con var, o si es un global del navegador "
            "agregalo a GLOBALES en este test.",
        )

    def test_no_se_usan_metodos_que_no_existen_en_chrome_49(self):
        encontrados = []

        def visita(n, padre):
            if n.type == "MemberExpression" and not n.computed:
                nombre = getattr(n.property, "name", None)
                if nombre in METODOS_PROHIBIDOS:
                    encontrados.append(f"{nombre} ({METODOS_PROHIBIDOS[nombre]})")

        _recorrer(self.arbol, visita)
        self.assertEqual(
            sorted(set(encontrados)), [],
            "el visor corre en un Chrome 49 (Windows XP) y estos métodos no "
            "existen ahí; usá un equivalente ES5.",
        )

    def test_no_se_usa_sintaxis_es6(self):
        encontrados = []

        def visita(n, padre):
            if n.type in NODOS_ES6:
                encontrados.append(NODOS_ES6[n.type])
            elif n.type == "VariableDeclaration" and n.kind in ("let", "const"):
                encontrados.append(f"{n.kind} (usar var)")

        _recorrer(self.arbol, visita)
        self.assertEqual(
            sorted(set(encontrados)), [],
            "el visor tiene que seguir en ES5: un error de sintaxis anula el "
            "archivo entero y la pantalla queda en blanco.",
        )
