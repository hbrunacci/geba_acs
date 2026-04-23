# API-3000 Python Wrapper

Wrapper inicial en Python para comunicarse con placas Intelektron API-3000 usando la librería nativa `libitkcom.so.0.0.0` vía `ctypes`.

## Estado del proyecto

Esta base está pensada para:

- cargar la librería nativa desde Linux
- exponer una API Python más cómoda
- mapear estructuras y errores principales
- permitir las primeras pruebas reales contra una placa

**Importante:** esta primera versión está preparada a partir de:
- documentación técnica del API-3000
- código VBA de ejemplo
- símbolos exportados por `libitkcom.so.0.0.0`

Hay partes que están razonablemente inferidas, pero **todavía necesitan validación real contra hardware**.

---

## Estructura

```text
api3000_wrapper/
├── README.md
├── pyproject.toml
├── examples/
│   └── basic_usage.py
└── api3000/
    ├── __init__.py
    ├── client.py
    ├── constants.py
    ├── errors.py
    ├── native.py
    └── structs.py
```

---

## Requisitos

- Python 3.11 o superior
- Linux x86_64
- archivo `libitkcom.so.0.0.0`

---

## Paso 1: crear entorno virtual

```bash
python3 -m venv .venv
source .venv/bin/activate
```

---

## Paso 2: copiar la librería nativa

Copiá `libitkcom.so.0.0.0` a una de estas ubicaciones:

### Opción A: dejarla fuera del proyecto y pasar la ruta explícita
```python
client = Api3000Client(
    lib_path="/ruta/completa/libitkcom.so.0.0.0",
    source_node=1,
    packet_protocol=PacketProtocol.NEXT,
)
```

### Opción B: exportar variable de entorno
```bash
export API3000_LIB_PATH=/ruta/completa/libitkcom.so.0.0.0
```

### Opción C: instalarla en una ruta visible por el loader
Por ejemplo:
```bash
sudo cp libitkcom.so.0.0.0 /usr/local/lib/
sudo ldconfig
```

---

## Paso 3: instalar el proyecto en modo editable

Desde la raíz del proyecto:

```bash
pip install -e .
```

---

## Paso 4: probar carga de la librería

```bash
python -c "from api3000 import Api3000Client; print('ok')"
```

---

## Paso 5: conexión TCP/IP a la placa

El ejemplo VBA usa strings del tipo:

```text
192.168.250.241:3001
```

y la documentación del equipo indica que el puerto host por defecto para TCP/IP es `3001`.

Ejemplo de uso:

```python
from api3000 import Api3000Client, PacketProtocol

with Api3000Client(
    lib_path="/ruta/libitkcom.so.0.0.0",
    source_node=1,
    packet_protocol=PacketProtocol.NEXT,
    conn_string="192.168.0.10:3001",
    timeout=5000,
) as client:
    dt = client.get_time(dest_node=1)
    print(dt)
```

---

## Paso 6: probar lectura de hora

El método más simple para validar el wrapper es:

1. `init`
2. `open`
3. `get_time`
4. `close`
5. `uninit`

Si eso funciona, el puente Python <-> `.so` está bien encaminado.

---

## Paso 7: operaciones siguientes sugeridas

Una vez validada la conexión:

- `list_users`
- `add_user`
- `edit_user`
- `delete_user`
- `list_marks`

---

## Notas importantes sobre callbacks

La función `itk_open` acepta callbacks para:

- eventos de usuario
- eventos de entradas auxiliares
- eventos de conexión

En esta primera versión se envían callbacks nulos por defecto, porque es la forma más simple y estable para arrancar.

Cuando quieras, el siguiente paso es agregar una capa Python para registrar callbacks reales con `ctypes.CFUNCTYPE`.

---

## Limitaciones actuales

- no se validó todavía contra hardware real
- no se implementaron todavía callbacks de eventos
- no se expusieron todavía todas las funciones de la `.so`
- las estructuras con strings fijos fueron mapeadas siguiendo el VBA, pero conviene validarlas con pruebas reales

---

## Orden recomendado para seguir

1. validar `get_time`
2. validar `set_time`
3. validar `list_users`
4. validar `list_marks`
5. agregar callbacks
6. agregar biometría
7. agregar operaciones sobre archivos SD

---

## Debug sugerido

Inicializá la librería con un archivo de log:

```python
client = Api3000Client(
    lib_path="/ruta/libitkcom.so.0.0.0",
    log_path="itkcom_python.log",
    log_level=5,
)
```

Luego revisá el log para comparar la comunicación con el ejemplo VBA.

---

## Próximo paso recomendado

Primero probá el ejemplo de `examples/basic_usage.py`.

Si falla, revisá:

- ruta de la `.so`
- `conn_string`
- `source_node`
- `dest_node`
- protocolo (`IN1` o `NEXT`)
- reachability de red
