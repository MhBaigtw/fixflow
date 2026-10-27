from rest_framework import serializers

from apps.properties.models import Unit
from apps.properties.selectors import active_tenancies

from .models import Comment, MaintenanceRequest, RequestEvent
from .services.requests import EDITABLE_FIELDS
from .services.transitions import ACTIONS


class MaintenanceRequestSerializer(serializers.ModelSerializer):
    property = serializers.IntegerField(source="unit.property_id", read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = MaintenanceRequest
        fields = [
            "id",
            "unit",
            "property",
            "created_by",
            "title",
            "description",
            "category",
            "priority",
            "status",
            "assigned_to",
            "sla_due_at",
            "is_overdue",
            "resolved_at",
            "closed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class MaintenanceRequestCreateSerializer(serializers.ModelSerializer):
    # Only units the tenant actively rents are valid; any other id gets the same
    # "does not exist" error, so unit ids outside your scope aren't confirmed.
    unit = serializers.PrimaryKeyRelatedField(queryset=Unit.objects.none())

    class Meta:
        model = MaintenanceRequest
        fields = ["unit", "title", "description", "category", "priority"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None and request.user.is_authenticated:
            self.fields["unit"].queryset = Unit.objects.filter(
                id__in=active_tenancies(request.user).values("unit_id")
            )


class MaintenanceRequestUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaintenanceRequest
        fields = list(EDITABLE_FIELDS)
        extra_kwargs = {f: {"required": False} for f in EDITABLE_FIELDS}

    def validate(self, attrs):
        unknown = sorted(set(self.initial_data) - set(EDITABLE_FIELDS))
        if unknown:
            raise serializers.ValidationError(
                {f: ["This field cannot be edited."] for f in unknown}
            )
        if not attrs:
            raise serializers.ValidationError("Provide at least one field to change.")
        return attrs


class TransitionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=ACTIONS)
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=2000)
    contractor_id = serializers.IntegerField(required=False, allow_null=True)


class RequestEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = RequestEvent
        fields = ["id", "from_status", "to_status", "actor", "note", "created_at"]
        read_only_fields = fields


class CommentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Comment
        fields = ["id", "author", "body", "is_internal", "created_at"]
        read_only_fields = ["id", "author", "created_at"]
