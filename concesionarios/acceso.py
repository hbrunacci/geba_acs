"""Validación en el molinete de las personas cargadas sólo en la app.

``intelektron_online`` pregunta acá ANTES que a xSys. Si la lectura (DNI o
credencial) es de una ``PersonaLocal``, la decisión sale de esta base y xSys no
se consulta: esa persona no existe allá y la cascada de Clever la rechazaría
por "no registrado". Si no es de una persona local se devuelve ``None`` y el
molinete sigue con la validación de siempre.

Qué se mira, en orden, para una ENTRADA:

1. que tenga una concesión cargada;
2. que la concesión esté activa y dentro de sus fechas de alta y baja;
3. que la empresa esté activa;
4. que no tenga vencido un documento que habilita el ingreso;
5. que esté dentro de su horario (el propio o el de la empresa).

Una SALIDA se deja pasar siempre que la persona esté identificada: el que está
adentro tiene que poder irse aunque se le haya terminado el horario.

Corre en el hilo de la librería nativa con la placa esperando la respuesta:
son tres o cuatro consultas por índice y nada más.
"""

from __future__ import annotations

from datetime import datetime

from access_control.services.validador_local import DecisionLocal, es_numerico

VALIDADOR = "concesion"
# Las lecturas numéricas por debajo de esto son documentos; arriba, los QR de
# Intelektron (10 dígitos que empiezan con 9). Mismo corte que xSys.
TOPE_DOCUMENTO = 3_000_000_000


class DecisionConcesion(DecisionLocal):
    """Decisión tomada con la base local de concesionarios."""

    validador = VALIDADOR


def documento_de_lectura(tag: str) -> int | None:
    """El número de documento que trae la lectura, si trae uno.

    Además del número pelado acepta el código PDF417 del DNI argentino, que
    separa los campos con '@': en el formato actual el DNI es el quinto campo
    (trámite@apellido@nombre@sexo@DNI@...) y en el viejo, que arranca con '@',
    el segundo.
    """
    dato = (tag or "").strip()
    if "@" in dato:
        campos = [c.strip() for c in dato.split("@")]
        candidatos = [campos[1]] if dato.startswith("@") and len(campos) > 1 else []
        if len(campos) > 4:
            candidatos.append(campos[4])
        for c in candidatos:
            if c.isdigit() and 6 <= len(c) <= 9:
                return int(c)
        return None
    limpio = dato.replace(".", "").replace(",", "")
    if limpio.isdigit() and 0 < int(limpio) < TOPE_DOCUMENTO:
        return int(limpio)
    return None


def identificar(tag: str):
    """La ``PersonaLocal`` de la lectura, o ``None``."""
    from concesionarios.models import PersonaLocal

    dato = (tag or "").strip()
    if not dato:
        return None
    doc = documento_de_lectura(dato)
    if doc is not None:
        persona = PersonaLocal.objects.filter(doc_nro=doc).first()
        if persona:
            return persona
    if "@" in dato or len(dato) > 32:
        return None
    # Credencial Mifare: hex. Una numérica también puede ser credencial (xSys
    # prueba documento y después credencial; acá lo mismo).
    if es_numerico(dato) and doc is None:
        return None
    return PersonaLocal.objects.filter(credencial=dato.upper()).exclude(credencial="").first()


def documento_que_frena(id_cliente: int, hoy):
    """El documento vencido que impide entrar, o ``None``.

    Frena un tipo que ``bloquea_acceso`` sólo si NINGÚN documento de ese tipo
    está vigente: la ART vieja vencida no frena a quien ya cargó la nueva.
    """
    from concesionarios.models import Documento

    por_tipo: dict[int, list] = {}
    for doc in (Documento.objects.filter(id_cliente=id_cliente, tipo__bloquea_acceso=True,
                                         tipo__activo=True)
                .select_related("tipo").order_by("-fecha_vencimiento")):
        por_tipo.setdefault(doc.tipo_id, []).append(doc)
    for docs in por_tipo.values():
        if all(d.estado(hoy) == Documento.VENCIDO for d in docs):
            return docs[0]
    return None


def decidir(*, tag: str, tipo_acceso: str, cuando: datetime, id_acceso: int | None = None
            ) -> DecisionConcesion | None:
    """Decide la lectura si es de una persona local; si no, ``None``."""
    import time

    from concesionarios.models import Concesionario

    inicio = time.perf_counter()
    persona = identificar(tag)
    if persona is None:
        return None
    traza = [f"persona_local={persona.id_cliente}"]

    def rta(habilitado: bool, mensaje: str, detalle: str = "") -> DecisionConcesion:
        return DecisionConcesion(
            habilitado=habilitado, mensaje=mensaje[:60], info="OBSER" if habilitado else "ADVER",
            id_cliente=persona.id_cliente, id_cliente_ref=0, tiempo_mensaje=1,
            duracion_ms=(time.perf_counter() - inicio) * 1000, id_motivo=0, detalle=detalle[:200],
            id_acceso=int(id_acceso or 0), traza=traza,
        )

    conc = (Concesionario.objects.select_related("empresa", "horario", "empresa__horario")
            .filter(id_cliente=persona.id_cliente).first())
    if (tipo_acceso or "E").upper() == "S":
        return rta(True, f"SALIDA {conc.empresa.nombre}" if conc else "SALIDA")
    if conc is None:
        return rta(False, "SIN CONCESION ASIGNADA")

    hoy = cuando.date()
    empresa = conc.empresa.nombre
    if not conc.activo or (conc.fecha_baja and conc.fecha_baja <= hoy):
        return rta(False, f"BAJA EN {empresa}")
    if conc.fecha_alta and conc.fecha_alta > hoy:
        return rta(False, f"HABILITADO DESDE {conc.fecha_alta:%d/%m/%Y}")
    if not conc.empresa.activa:
        return rta(False, f"EMPRESA INACTIVA {empresa}")
    vencido = documento_que_frena(persona.id_cliente, hoy)
    if vencido is not None:
        detalle = (f"vencida el {vencido.fecha_vencimiento:%d/%m/%Y}"
                   if vencido.fecha_vencimiento else "")
        return rta(False, f"{vencido.tipo.nombre} VENCIDA".upper(), detalle)
    if not conc.permite_horario(cuando):
        horario = conc.horario_vigente
        return rta(False, "FUERA DE HORARIO", horario.resumen if horario else "")
    return rta(True, f"CONCESIONARIO {empresa}")


__all__ = ["DecisionConcesion", "VALIDADOR", "decidir", "documento_de_lectura", "documento_que_frena",
           "identificar"]
