from django_filters import rest_framework as filters

from .models import MaintenanceRequest
from .selectors import overdue_q


class MaintenanceRequestFilter(filters.FilterSet):
    # Plain number filters (not model choice filters) so an out-of-scope id
    # yields an empty page rather than a "does not exist" error.
    property = filters.NumberFilter(field_name="unit__property_id")
    unit = filters.NumberFilter(field_name="unit_id")
    assigned_to = filters.NumberFilter(field_name="assigned_to_id")
    # A choice filter (not BooleanFilter) so a typo like ?overdue=yes is a 400
    # instead of being silently ignored.
    overdue = filters.ChoiceFilter(
        choices=[("true", "true"), ("false", "false")], method="filter_overdue"
    )
    created_after = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_before = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="lte")
    ordering = filters.OrderingFilter(
        fields=(
            ("created_at", "created_at"),
            ("priority_rank", "priority"),
            ("sla_due_at", "sla_due_at"),
        )
    )

    class Meta:
        model = MaintenanceRequest
        fields = ["status", "priority", "category"]

    def filter_overdue(self, queryset, name, value):
        if value == "true":
            return queryset.filter(overdue_q())
        return queryset.exclude(overdue_q())
