from __future__ import annotations

from api3000 import Api3000Client, PacketProtocol


def main() -> None:
    # Ajustar estos valores antes de ejecutar.
    lib_path = "/ruta/a/libitkcom.so.0.0.0"
    conn_string = "192.168.0.10:3001"
    source_node = 1
    dest_node = 1

    with Api3000Client(
        lib_path=lib_path,
        source_node=source_node,
        packet_protocol=PacketProtocol.NEXT,
        conn_string=conn_string,
        timeout=5000,
        log_path="itkcom_python.log",
        log_level=5,
    ) as client:
        print("Version lib:", client.lib_version())
        current_dt = client.get_time_as_datetime(dest_node=dest_node)
        print("Fecha/hora del equipo:", current_dt.isoformat())

        # Primera prueba recomendada:
        users, count = client.list_users(dest_node=dest_node, start_position=0, records_to_list=1)
        print("Usuarios leidos:", count)
        if count:
            print("Primer usuario access_id:", users[0].access_id)
            print("Primer usuario nombre:", users[0].get_user_name())


if __name__ == "__main__":
    main()
