from django.db import DatabaseError, connection
from django.http import JsonResponse
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .exceptions import error_body


class LivenessView(APIView):
    """Liveness: the process is up and serving. Never touches the database, so the
    platform's health checks (every few seconds, forever, on an always-on service)
    don't keep the database busy or count against its usage limits."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    @extend_schema(
        responses={200: inline_serializer("Liveness", {"status": serializers.CharField()})}
    )
    def get(self, request):
        return Response({"status": "ok"})


class HealthView(APIView):
    """Readiness: the app can reach its database (SELECT 1). 503 if it can't."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

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


def not_found(request, exception=None):
    return JsonResponse(error_body("not_found", "Not found."), status=404)


def server_error(request):
    return JsonResponse(error_body("server_error", "Internal server error."), status=500)
