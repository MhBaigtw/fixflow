from rest_framework import serializers

from .models import Property, Tenancy, Unit


class PropertySerializer(serializers.ModelSerializer):
    class Meta:
        model = Property
        fields = ["id", "name", "address", "manager", "created_at"]
        read_only_fields = ["id", "manager", "created_at"]


class UnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = Unit
        fields = ["id", "property", "label"]
        read_only_fields = ["id", "property"]

    def validate_label(self, value):
        property_ = self.context["property"]
        if Unit.objects.filter(property=property_, label=value).exists():
            raise serializers.ValidationError("A unit with this label already exists here.")
        return value


class TenancySerializer(serializers.ModelSerializer):
    tenant_email = serializers.EmailField(source="tenant.email", read_only=True)
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = Tenancy
        fields = ["id", "unit", "tenant", "tenant_email", "start_date", "end_date", "is_active"]
        read_only_fields = fields


class AttachTenantSerializer(serializers.Serializer):
    email = serializers.EmailField()
    start_date = serializers.DateField(required=False)
