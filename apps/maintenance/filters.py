from django_filters import rest_framework as filters

from .models import MaintenanceRequest
from .selectors import overdue_q


class MaintenanceRequestFilter(filters.FilterSet):
    # Plain number filters (not model choice filters) so an out-of-scope id
    # yields an empty page rather than a "does not exist" error.
    property = filters.NumberFilter(field_name="unit__property_id")
    unit = filters.NumberFilter(field_name="unit_id")
    assigned_to = filters.NumberFilter(field_name="assigned_to_id")
    overdue = filters.BooleanFilter(method="filter_overdue")
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
        return queryset.filter(overdue_q()) if value else queryset.exclude(overdue_q())
