from django.db import DatabaseError, connection
from django.http import JsonResponse
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .exceptions import error_body


class HealthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        responses={
            200: inline_serializer(
                "Health",
                {"status": serializers.CharField(), "database": serializers.CharField()},
            )
        }
    )
    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except DatabaseError:
            return Response(
                error_body(
                    "service_unavailable",
                    "Database is unavailable.",
                    {"database": "unavailable"},
                ),
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"status": "ok", "database": "ok"})


class ClientIPDiagnosticView(APIView):
    """TEMPORARY (Checkpoint 2, Part F): shows how the caller's own request reaches the
    app, to set NUM_PROXIES correctly on Render. Authenticated; reveals only the
    caller's own address chain. Remove after the live check (see docs/decisions.md)."""

    throttle_classes = []

    @extend_schema(exclude=True)
    def get(self, request):
        from rest_framework.settings import api_settings
        from rest_framework.throttling import ScopedRateThrottle

        return Response(
            {
                "remote_addr": request.META.get("REMOTE_ADDR"),
                "x_forwarded_for": request.META.get("HTTP_X_FORWARDED_FOR"),
                "cf_connecting_ip": request.META.get("HTTP_CF_CONNECTING_IP"),
                "true_client_ip": request.META.get("HTTP_TRUE_CLIENT_IP"),
                "num_proxies": api_settings.NUM_PROXIES,
                "throttle_ident": ScopedRateThrottle().get_ident(request),
            }
        )


def not_found(request, exception=None):
    return JsonResponse(error_body("not_found", "Not found."), status=404)


def server_error(request):
    return JsonResponse(error_body("server_error", "Internal server error."), status=500)
