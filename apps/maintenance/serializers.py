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
            "requested_priority",
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
    """Creating tenant (while submitted): any of these fields. Property manager
    (submitted to in_progress): `priority` only."""

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
    priority = serializers.ChoiceField(
        choices=MaintenanceRequest.Priority.choices,
        required=False,
        help_text="Optional, 'triage' only: set the working priority while triaging.",
    )


class RequestEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = RequestEvent
        fields = [
            "id",
            "event_type",
            "from_status",
            "to_status",
            "actor",
            "note",
            "changes",
            "created_at",
        ]
        read_only_fields = fields


class StatsQuerySerializer(serializers.Serializer):
    property = serializers.IntegerField(
        required=False, help_text="Limit to one of your properties (others give zeros)."
    )
    created_after = serializers.DateTimeField(
        required=False, help_text="Only requests created at or after this time (ISO 8601)."
    )
    created_before = serializers.DateTimeField(
        required=False, help_text="Only requests created at or before this time (ISO 8601)."
    )


class OpenByStatusSerializer(serializers.Serializer):
    submitted = serializers.IntegerField()
    triaged = serializers.IntegerField()
    assigned = serializers.IntegerField()
    in_progress = serializers.IntegerField()


class MedianHoursSerializer(serializers.Serializer):
    plumbing = serializers.FloatField(allow_null=True)
    electrical = serializers.FloatField(allow_null=True)
    heating = serializers.FloatField(allow_null=True)
    appliance = serializers.FloatField(allow_null=True)
    pest = serializers.FloatField(allow_null=True)
    structural = serializers.FloatField(allow_null=True)
    other = serializers.FloatField(allow_null=True)


class StatsSerializer(serializers.Serializer):
    open_by_status = OpenByStatusSerializer(help_text="Count of requests in each open status.")
    overdue = serializers.IntegerField(
        help_text="Requests not resolved, closed or cancelled whose sla_due_at has passed."
    )
    median_hours_to_resolve = MedianHoursSerializer(
        help_text=(
            "Per category: median of (resolved_at - created_at) in hours, over requests "
            "currently resolved or closed (Postgres percentile_cont(0.5), i.e. the mean of "
            "the two middle values for an even count). A reopened-then-re-resolved request "
            "counts from creation to its final resolution. null = no resolved requests."
        )
    )


class CommentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Comment
        fields = ["id", "author", "body", "is_internal", "created_at"]
        read_only_fields = ["id", "author", "created_at"]
