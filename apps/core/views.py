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


def not_found(request, exception=None):
    return JsonResponse(error_body("not_found", "Not found."), status=404)


def server_error(request):
    return JsonResponse(error_body("server_error", "Internal server error."), status=500)
