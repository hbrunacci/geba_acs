from __future__ import annotations

from rest_framework import permissions, serializers, status, views
from rest_framework.response import Response

from access_control.services.intelectron.api3000_service import (
    Api3000CommandError,
    Api3000ConnectionError,
    Api3000GatewayError,
    Api3000Service,
)


class Api3000CommandSerializer(serializers.Serializer):
    ip = serializers.IPAddressField(required=True)
    port = serializers.IntegerField(required=False, default=3001, min_value=1, max_value=65535)
    dest_node = serializers.IntegerField(required=False, default=1, min_value=1)
    command = serializers.CharField(required=True, trim_whitespace=True)
    params = serializers.DictField(required=False, default=dict)


class Api3000PingSerializer(serializers.Serializer):
    ip = serializers.IPAddressField(required=True)
    port = serializers.IntegerField(required=False, default=3001, min_value=1, max_value=65535)
    dest_node = serializers.IntegerField(required=False, default=1, min_value=1)


class Api3000PingAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = Api3000PingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        service = Api3000Service()
        payload = serializer.validated_data

        try:
            result = service.ping(
                ip=payload["ip"],
                port=payload["port"],
                dest_node=payload["dest_node"],
            )
        except Api3000ConnectionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_504_GATEWAY_TIMEOUT)
        except Api3000GatewayError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        except Api3000CommandError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(result, status=status.HTTP_200_OK)


class Api3000CommandAPI(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = Api3000CommandSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        payload = serializer.validated_data
        service = Api3000Service()

        try:
            result = service.execute_command(
                ip=payload["ip"],
                port=payload["port"],
                dest_node=payload["dest_node"],
                command=payload["command"],
                params=payload.get("params") or {},
            )
        except Api3000CommandError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Api3000ConnectionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_504_GATEWAY_TIMEOUT)
        except Api3000GatewayError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        return Response(result, status=status.HTTP_200_OK)
