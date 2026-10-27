from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, mixins, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsManager

from . import selectors, services
from .models import Property, Unit
from .serializers import (
    AttachTenantSerializer,
    PropertySerializer,
    TenancySerializer,
    UnitSerializer,
)


class PropertyViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = PropertySerializer
    permission_classes = [IsManager]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # schema generation
            return Property.objects.none()
        return selectors.properties_for_manager(self.request.user)

    def perform_create(self, serializer):
        serializer.save(manager=self.request.user)


class UnitListCreateView(generics.ListCreateAPIView):
    serializer_class = UnitSerializer
    permission_classes = [IsManager]

    def get_property(self):
        return get_object_or_404(
            selectors.properties_for_manager(self.request.user), pk=self.kwargs["property_id"]
        )

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Unit.objects.none()
        return self.get_property().units.all()

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if not getattr(self, "swagger_fake_view", False):
            context["property"] = self.get_property()
        return context

    def perform_create(self, serializer):
        serializer.save(property=serializer.context["property"])


class UnitTenancyView(APIView):
    permission_classes = [IsManager]

    def get_unit(self):
        return get_object_or_404(
            selectors.units_for_manager(self.request.user), pk=self.kwargs["unit_id"]
        )

    @extend_schema(responses=TenancySerializer(many=True))
    def get(self, request, unit_id):
        unit = self.get_unit()
        return Response(TenancySerializer(unit.tenancies.all(), many=True).data)

    @extend_schema(request=AttachTenantSerializer, responses={201: TenancySerializer})
    def post(self, request, unit_id):
        unit = self.get_unit()
        serializer = AttachTenantSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tenancy = services.attach_tenant(unit=unit, **serializer.validated_data)
        return Response(TenancySerializer(tenancy).data, status=status.HTTP_201_CREATED)


class EndTenancyView(APIView):
    permission_classes = [IsManager]

    @extend_schema(request=None, responses=TenancySerializer)
    def post(self, request, tenancy_id):
        tenancy = get_object_or_404(selectors.tenancies_for_manager(request.user), pk=tenancy_id)
        services.end_tenancy(tenancy=tenancy)
        return Response(TenancySerializer(tenancy).data)
