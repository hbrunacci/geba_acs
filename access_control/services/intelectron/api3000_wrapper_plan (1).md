# Wrapper Python para Intelektron API-3000 usando `libitkcom.dll`

## Objetivo

Construir una primera versión de una librería Python que funcione como **wrapper** de la DLL `libitkcom.dll`, reutilizando la API ya expuesta por el ejemplo VBA.

La idea de esta primera etapa es:

1. **Hablar con la DLL desde Python** usando `ctypes`.
2. Exponer una interfaz Python prolija y tipada.
3. Separar claramente:
   - constantes
   - errores
   - estructuras
   - wrapper de bajo nivel
   - cliente de alto nivel
4. Dejar la base lista para, en una etapa posterior, reemplazar el uso de la DLL por una implementación nativa del protocolo.

---

# 1. Qué se dedujo del ejemplo VBA

Del proyecto VBA se observa que el protocolo real no está implementado en VBA, sino dentro de `libitkcom.dll`. El código VBA declara funciones externas y estructuras para invocarlas.

Los archivos relevantes del ejemplo son:

- `itkcom_api.bas`: declaraciones de funciones exportadas por la DLL.
- `itkcom_structs.bas`: estructuras usadas por la DLL.
- `itkcom_defines.bas`: constantes de configuración.
- `itkcom_error.bas`: códigos de error.
- `itkcom.log`: tráfico real de algunas operaciones.

Esto permite construir un wrapper Python bastante fiel a la API original.

---

# 2. Alcance de esta primera versión

Esta versión inicial del wrapper va a cubrir lo mínimo útil para empezar a trabajar:

- inicialización de la librería
- apertura y cierre de conexión
- lectura y escritura de hora
- consulta básica de información del nodo
- listado de marcaciones
- alta / edición / baja / listado de usuarios
- manejo base de errores

Más adelante se puede extender para:

- biometría
- SD card
- schedules
- relés
- mensajes de display
- configuración avanzada

---

# 3. Estructura recomendada del proyecto

Crear una carpeta de proyecto con esta estructura:

```text
api3000_wrapper/
├── pyproject.toml
├── README.md
├── .gitignore
├── examples/
│   ├── get_time.py
│   ├── list_marks.py
│   └── add_user.py
├── src/
│   └── api3000/
│       ├── __init__.py
│       ├── constants.py
│       ├── errors.py
│       ├── structs.py
│       ├── dll_wrapper.py
│       ├── client.py
│       └── utils.py
└── vendor/
    └── libitkcom.dll
```

---

# 4. Requisitos previos

## 4.1. Sistema operativo

Como esta primera etapa depende de `libitkcom.dll`, el wrapper quedará inicialmente orientado a **Windows**.

## 4.2. Python

Usar **Python 3.11 o superior**.

## 4.3. Arquitectura

Es importante que coincidan:

- arquitectura de Python
- arquitectura de la DLL

Ejemplo:

- Python 64 bits + DLL 64 bits
- Python 32 bits + DLL 32 bits

Si no coinciden, `ctypes` no va a poder cargar la DLL.

## 4.4. Ubicación de la DLL

Guardar `libitkcom.dll` dentro de:

```text
vendor/libitkcom.dll
```

---

# 5. Crear el proyecto paso a paso

## Paso 1: crear la carpeta raíz

```bash
mkdir api3000_wrapper
cd api3000_wrapper
```

## Paso 2: crear entorno virtual

### En Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### En CMD

```cmd
python -m venv .venv
.venv\Scripts\activate.bat
```

## Paso 3: crear estructura de carpetas

```bash
mkdir examples
mkdir vendor
mkdir src
mkdir src\api3000
```

## Paso 4: crear `pyproject.toml`

Contenido sugerido:

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "api3000-wrapper"
version = "0.1.0"
description = "Wrapper Python para Intelektron API-3000 usando libitkcom.dll"
readme = "README.md"
requires-python = ">=3.11"
authors = [
  { name = "Hernan" }
]
dependencies = []

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]
```

## Paso 5: crear `.gitignore`

```gitignore
.venv/
__pycache__/
*.pyc
*.pyo
*.pyd
*.log
build/
dist/
*.egg-info/
```

> Evaluar si querés versionar o no la DLL. Si no querés incluirla en git, agregá también:

```gitignore
vendor/*.dll
```

---

# 6. Archivos Python a crear

## 6.1. `src/api3000/__init__.py`

Este archivo expone la API pública del paquete.

Contenido sugerido:

```python
from .client import Api3000Client
from .errors import Api3000Error, Api3000DllError

__all__ = [
    "Api3000Client",
    "Api3000Error",
    "Api3000DllError",
]
```

---

## 6.2. `src/api3000/constants.py`

Acá conviene concentrar:

- protocolos (`IN1`, `NEXT`)
- adaptadores si hicieran falta
- tamaños máximos
- valores por defecto
- flags relevantes

Contenido inicial sugerido:

```python
from __future__ import annotations

from enum import IntEnum


class PacketProtocol(IntEnum):
    """Protocolos observados en el ejemplo VBA."""

    IN1 = 1
    NEXT = 2


DEFAULT_PORT = 3001
DEFAULT_TIMEOUT_MS = 5000

MAX_USER_NAME_LEN = 40
MAX_USER_MSG_LEN = 40
MAX_TEMPLATE_SIZE = 16384
MAX_MARK_BUFFER = 1024
```

> Los límites exactos de algunos campos deben confirmarse luego contra el comportamiento real de la DLL.

---

## 6.3. `src/api3000/errors.py`

Mapear los errores de la DLL a excepciones Python.

Contenido sugerido:

```python
from __future__ import annotations


class Api3000Error(Exception):
    """Excepción base del paquete."""


class Api3000DllError(Api3000Error):
    """Error devuelto por la DLL."""

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


ERROR_MESSAGES: dict[int, str] = {
    1: "Operación exitosa.",
    5: "El usuario ya existe.",
    12: "El biométrico no está presente.",
    106: "El usuario no existe.",
    1001: "Connection string inválido.",
    1009: "Dirección IP inválida.",
}


def raise_for_code(code: int) -> None:
    """Lanza excepción si el código informado por la DLL representa error."""
    if code == 1:
        return
    message = ERROR_MESSAGES.get(code, "Error desconocido devuelto por la DLL.")
    raise Api3000DllError(code=code, message=message)
```

> A medida que vayas encontrando más códigos en `itkcom_error.bas`, amplialos en este diccionario.

---

## 6.4. `src/api3000/structs.py`

Este archivo debe contener las estructuras `ctypes.Structure` equivalentes a las definidas en `itkcom_structs.bas`.

## Importante

En VBA, algunos tipos aparecen como:

- `Byte`
- `Integer`
- `Long`
- `String * N`

En Python con `ctypes` normalmente se mapearán así:

- `Byte` -> `ctypes.c_ubyte`
- `Integer` -> `ctypes.c_short`
- `Long` -> `ctypes.c_long` o `ctypes.c_int32` según validación
- `String * N` -> `ctypes.c_char * N`

Como este punto depende mucho de alineación y tamaño real, conviene arrancar con un subconjunto mínimo.

Contenido base sugerido:

```python
from __future__ import annotations

import ctypes


class ITK_DATE_TIME(ctypes.Structure):
    _fields_ = [
        ("year", ctypes.c_short),
        ("month", ctypes.c_ubyte),
        ("day", ctypes.c_ubyte),
        ("hour", ctypes.c_ubyte),
        ("minute", ctypes.c_ubyte),
        ("second", ctypes.c_ubyte),
    ]


class ITK_USER_INFO(ctypes.Structure):
    _fields_ = [
        ("access_id", ctypes.c_char * 16),
        ("password", ctypes.c_char * 16),
        ("status", ctypes.c_ubyte),
        ("access_ctl", ctypes.c_long),
        ("panic_code", ctypes.c_ubyte),
        ("bio_count", ctypes.c_ubyte),
        ("bio_level", ctypes.c_ubyte),
        ("sec_level", ctypes.c_ubyte),
        ("user_name", ctypes.c_char * 64),
        ("user_msg", ctypes.c_char * 64),
        ("user_id", ctypes.c_long),
        ("schedule_id", ctypes.c_long),
        ("anti_passback", ctypes.c_ubyte),
    ]


class ITK_MARK_INFO(ctypes.Structure):
    _fields_ = [
        ("access_id", ctypes.c_char * 16),
        ("date_time", ITK_DATE_TIME),
        ("source", ctypes.c_ubyte),
        ("event", ctypes.c_short),
    ]
```

## Recomendación clave

Antes de avanzar con todas las estructuras, validar una por una comparando:

- tamaño esperado en VBA
- tamaño real en `ctypes.sizeof(...)`

Crear un pequeño script de prueba:

```python
import ctypes
from api3000.structs import ITK_DATE_TIME, ITK_USER_INFO

print(ctypes.sizeof(ITK_DATE_TIME))
print(ctypes.sizeof(ITK_USER_INFO))
```

Si hubiera problemas de padding, probar con:

```python
_pack_ = 1
```

pero **no lo fuerces** en todas las estructuras sin comprobarlo.

---

## 6.5. `src/api3000/utils.py`

Este módulo debe tener utilidades pequeñas y reutilizables.

Contenido sugerido:

```python
from __future__ import annotations

from datetime import datetime

from .structs import ITK_DATE_TIME


def encode_fixed_string(value: str, size: int) -> bytes:
    """Codifica un string ASCII en un buffer fijo terminado en nulos."""
    raw = value.encode("ascii", errors="ignore")[: max(0, size - 1)]
    return raw.ljust(size, b"\x00")



def decode_fixed_string(value: bytes) -> str:
    """Convierte un buffer fijo terminado en nulos a string Python."""
    return value.split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip()



def datetime_to_itk(value: datetime) -> ITK_DATE_TIME:
    """Convierte datetime Python a estructura ITK_DATE_TIME."""
    return ITK_DATE_TIME(
        year=value.year,
        month=value.month,
        day=value.day,
        hour=value.hour,
        minute=value.minute,
        second=value.second,
    )



def itk_to_datetime(value: ITK_DATE_TIME) -> datetime:
    """Convierte ITK_DATE_TIME a datetime Python."""
    return datetime(
        year=value.year,
        month=value.month,
        day=value.day,
        hour=value.hour,
        minute=value.minute,
        second=value.second,
    )
```

---

## 6.6. `src/api3000/dll_wrapper.py`

Este es el núcleo del wrapper de bajo nivel.

Responsabilidades:

- cargar la DLL
- declarar `argtypes` y `restype`
- envolver cada función exportada
- validar errores
- no mezclar lógica de negocio

Contenido base sugerido:

```python
from __future__ import annotations

import ctypes
from pathlib import Path

from .errors import raise_for_code
from .structs import ITK_DATE_TIME


class Api3000DllWrapper:
    """Wrapper de bajo nivel sobre libitkcom.dll."""

    def __init__(self, dll_path: str | Path) -> None:
        self.dll_path = Path(dll_path)
        if not self.dll_path.exists():
            raise FileNotFoundError(f"No se encontró la DLL: {self.dll_path}")

        self._dll = ctypes.WinDLL(str(self.dll_path))
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        """Configura firmas de funciones exportadas por la DLL."""
        self._dll.itk_init.argtypes = []
        self._dll.itk_init.restype = ctypes.c_int

        self._dll.itk_uninit.argtypes = []
        self._dll.itk_uninit.restype = ctypes.c_int

        self._dll.itk_open.argtypes = [ctypes.c_char_p, ctypes.c_ubyte]
        self._dll.itk_open.restype = ctypes.c_int

        self._dll.itk_close.argtypes = [ctypes.c_int]
        self._dll.itk_close.restype = ctypes.c_int

        self._dll.itk_get_time.argtypes = [ctypes.c_int, ctypes.POINTER(ITK_DATE_TIME)]
        self._dll.itk_get_time.restype = ctypes.c_int

        self._dll.itk_set_time.argtypes = [ctypes.c_int, ctypes.POINTER(ITK_DATE_TIME)]
        self._dll.itk_set_time.restype = ctypes.c_int

    def init(self) -> None:
        code = self._dll.itk_init()
        raise_for_code(code)

    def uninit(self) -> None:
        code = self._dll.itk_uninit()
        raise_for_code(code)

    def open(self, connection_string: str, protocol: int) -> int:
        handle = self._dll.itk_open(connection_string.encode("ascii"), protocol)
        if handle <= 0:
            raise RuntimeError(f"No se pudo abrir conexión. Handle devuelto: {handle}")
        return int(handle)

    def close(self, handle: int) -> None:
        code = self._dll.itk_close(handle)
        raise_for_code(code)

    def get_time(self, handle: int) -> ITK_DATE_TIME:
        value = ITK_DATE_TIME()
        code = self._dll.itk_get_time(handle, ctypes.byref(value))
        raise_for_code(code)
        return value

    def set_time(self, handle: int, value: ITK_DATE_TIME) -> None:
        code = self._dll.itk_set_time(handle, ctypes.byref(value))
        raise_for_code(code)
```

## Notas importantes

1. La firma exacta de cada función debe compararse con el VBA.
2. Si una función devuelve directamente un código de error, usar `raise_for_code`.
3. Si devuelve un handle, no asumir todavía que `<= 0` siempre es error sin validar con pruebas reales.

---

## 6.7. `src/api3000/client.py`

Este archivo debe exponer la interfaz Python de alto nivel.

Acá sí conviene:

- usar `datetime`
- convertir estructuras en objetos Python
- ofrecer métodos cómodos
- manejar contexto (`with`)

Contenido base sugerido:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .constants import PacketProtocol
from .dll_wrapper import Api3000DllWrapper
from .utils import datetime_to_itk, itk_to_datetime


@dataclass(slots=True)
class ConnectionConfig:
    host: str
    port: int = 3001
    protocol: PacketProtocol = PacketProtocol.NEXT

    def to_connection_string(self) -> str:
        return f"{self.host}:{self.port}"


class Api3000Client:
    """Cliente de alto nivel para placas API-3000 usando la DLL de Intelektron."""

    def __init__(self, dll_path: str | Path, config: ConnectionConfig) -> None:
        self.config = config
        self._wrapper = Api3000DllWrapper(dll_path=dll_path)
        self._handle: int | None = None
        self._initialized = False

    def connect(self) -> None:
        if not self._initialized:
            self._wrapper.init()
            self._initialized = True

        self._handle = self._wrapper.open(
            connection_string=self.config.to_connection_string(),
            protocol=int(self.config.protocol),
        )

    def disconnect(self) -> None:
        if self._handle is not None:
            self._wrapper.close(self._handle)
            self._handle = None

        if self._initialized:
            self._wrapper.uninit()
            self._initialized = False

    def get_time(self) -> datetime:
        self._ensure_connected()
        value = self._wrapper.get_time(self._handle)
        return itk_to_datetime(value)

    def set_time(self, value: datetime) -> None:
        self._ensure_connected()
        self._wrapper.set_time(self._handle, datetime_to_itk(value))

    def _ensure_connected(self) -> None:
        if self._handle is None:
            raise RuntimeError("El cliente no está conectado.")

    def __enter__(self) -> "Api3000Client":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.disconnect()
```

---

# 7. Primer ejemplo funcional

## `examples/get_time.py`

```python
from pathlib import Path

from api3000.client import Api3000Client, ConnectionConfig
from api3000.constants import PacketProtocol


def main() -> None:
    dll_path = Path("vendor/libitkcom.dll")
    config = ConnectionConfig(
        host="192.168.250.241",
        port=3001,
        protocol=PacketProtocol.NEXT,
    )

    with Api3000Client(dll_path=dll_path, config=config) as client:
        current_time = client.get_time()
        print(f"Hora del equipo: {current_time}")


if __name__ == "__main__":
    main()
```

---

# 8. Cómo instalar el paquete en modo editable

Desde la raíz del proyecto:

```bash
pip install -e .
```

Esto permite importar `api3000` sin reinstalar en cada cambio.

---

# 9. Cómo probar el wrapper paso a paso

## Prueba 1: carga de la DLL

Crear un script temporal:

```python
from pathlib import Path
from api3000.dll_wrapper import Api3000DllWrapper

wrapper = Api3000DllWrapper(Path("vendor/libitkcom.dll"))
print("DLL cargada correctamente")
```

Si falla, revisar:

- path incorrecto
- mismatch 32/64 bits
- dependencias faltantes de la DLL

## Prueba 2: init / uninit

```python
from pathlib import Path
from api3000.dll_wrapper import Api3000DllWrapper

wrapper = Api3000DllWrapper(Path("vendor/libitkcom.dll"))
wrapper.init()
print("init ok")
wrapper.uninit()
print("uninit ok")
```

## Prueba 3: open / close

```python
from pathlib import Path
from api3000.constants import PacketProtocol
from api3000.dll_wrapper import Api3000DllWrapper

wrapper = Api3000DllWrapper(Path("vendor/libitkcom.dll"))
wrapper.init()
handle = wrapper.open("192.168.250.241:3001", int(PacketProtocol.NEXT))
print(f"handle: {handle}")
wrapper.close(handle)
wrapper.uninit()
```

## Prueba 4: get_time

Una vez validadas las anteriores, ejecutar `examples/get_time.py`.

---

# 10. Orden recomendado de implementación

Para no trabarte, implementá en este orden:

1. `errors.py`
2. `constants.py`
3. `structs.py` con **solo `ITK_DATE_TIME`**
4. `dll_wrapper.py` con:
   - `itk_init`
   - `itk_uninit`
   - `itk_open`
   - `itk_close`
   - `itk_get_time`
   - `itk_set_time`
5. `client.py`
6. ejemplo `get_time.py`
7. recién después agregar:
   - `ITK_USER_INFO`
   - `itk_add_user`
   - `itk_edit_user`
   - `itk_del_user`
   - `itk_list_users`
8. después histórico:
   - `ITK_MARK_INFO`
   - `itk_list_marks`

---

# 11. Cómo extender luego para usuarios

Cuando la base funcione, agregar en `dll_wrapper.py` las firmas de usuario.

La idea es exponer luego en `client.py` métodos como:

```python
def add_user(self, user: UserInfo) -> None: ...
def edit_user(self, user: UserInfo) -> None: ...
def delete_user(self, access_id: str) -> None: ...
def list_users(self) -> list[UserInfo]: ...
```

Y usar una `dataclass` Python para no exponer `ctypes.Structure` en la interfaz pública:

```python
from dataclasses import dataclass


@dataclass(slots=True)
class UserInfo:
    access_id: str
    password: str = ""
    status: int = 1
    access_ctl: int = 0
    panic_code: int = 0
    bio_count: int = 0
    bio_level: int = 0
    sec_level: int = 0
    user_name: str = ""
    user_msg: str = ""
    user_id: int = 0
    schedule_id: int = 0
    anti_passback: int = 0
```

---

# 12. Cómo extender luego para marcaciones

La idea es exponer algo así:

```python
from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class MarkInfo:
    access_id: str
    timestamp: datetime
    source: int
    event: int
```

Y un método:

```python
def list_marks(self) -> list[MarkInfo]: ...
```

---

# 13. Buenas prácticas para este wrapper

## 13.1. No mezclar `ctypes` con lógica de negocio

- `dll_wrapper.py`: puro bajo nivel
- `client.py`: API cómoda para usar

## 13.2. Centralizar conversiones

Toda conversión entre:

- `datetime` <-> `ITK_DATE_TIME`
- `str` <-> `c_char * N`

conviene dejarla en `utils.py`.

## 13.3. Documentar qué está confirmado y qué es inferencia

En varios puntos estamos infiriendo tamaños y tipos.

Usar comentarios del tipo:

```python
# TODO: validar este tamaño contra comportamiento real de la DLL.
```

## 13.4. Agregar logging opcional

Más adelante conviene instrumentar logs para registrar:

- carga de DLL
- conexión
- códigos de retorno
- tiempos de operación

---

# 14. Problemas esperables

## Error al cargar la DLL

Causas probables:

- arquitectura incompatible
- falta alguna DLL dependiente
- ruta incorrecta

## Strings truncados

Causas probables:

- tamaño fijo incorrecto
- codificación ASCII
- buffer mal rellenado

## Estructuras incorrectas

Causas probables:

- padding/alignment
- tipo incorrecto (`c_short` vs `c_int`)
- longitud incorrecta en campos `char[]`

## El handle abre pero algunas operaciones fallan

Causas probables:

- protocolo incorrecto (`IN1` vs `NEXT`)
- equipo no accesible
- timeouts no configurados
- firma incorrecta de la función en `ctypes`

---

# 15. Próximo incremento recomendado

Una vez que funcione `get_time`, el siguiente bloque más útil sería:

1. `itk_get_info`
2. `itk_list_marks`
3. `itk_add_user`
4. `itk_edit_user`
5. `itk_del_user`
6. `itk_list_users`

Ese conjunto ya te deja una librería realmente aprovechable para operar con la placa.

---

# 16. Resumen ejecutivo

## Qué hay que crear

- estructura de proyecto Python
- módulo de constantes
- módulo de errores
- estructuras `ctypes`
- wrapper de bajo nivel para la DLL
- cliente de alto nivel
- ejemplos de uso

## Por dónde arrancar

1. cargar DLL
2. init/uninit
3. open/close
4. get_time/set_time
5. recién después usuarios y marcaciones

## Decisión técnica correcta para esta etapa

La mejor primera versión es un **wrapper fino sobre `libitkcom.dll`**, no una reimplementación del protocolo.

Eso te permite:

- avanzar rápido
- validar el modelo de objetos
- entender bien la API real
- dejar el camino abierto para implementar después el protocolo nativo en Python

---

# 17. Lista concreta de archivos mínimos para la v0.1

```text
src/api3000/__init__.py
src/api3000/constants.py
src/api3000/errors.py
src/api3000/structs.py
src/api3000/utils.py
src/api3000/dll_wrapper.py
src/api3000/client.py
examples/get_time.py
pyproject.toml
README.md
```

---

# 18. Siguiente paso sugerido

Implementar primero la **v0.1 mínima operativa** con:

- conexión
- lectura de hora
- escritura de hora

y probarla contra una placa real.

Una vez validado eso, avanzar con usuarios y marcaciones.
