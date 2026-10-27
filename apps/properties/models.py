from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Property(models.Model):
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=500)
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="managed_properties",
        limit_choices_to={"role": "manager"},
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["id"]
        verbose_name_plural = "properties"

    def __str__(self):
        return self.name


class Unit(models.Model):
    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="units")
    label = models.CharField(max_length=50)

    class Meta:
        ordering = ["property_id", "label"]
        constraints = [
            models.UniqueConstraint(
                fields=["property", "label"], name="unique_unit_label_per_property"
            ),
        ]

    def __str__(self):
        return f"{self.property} / {self.label}"


class Tenancy(models.Model):
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="tenancies")
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="tenancies",
        limit_choices_to={"role": "tenant"},
    )
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        verbose_name_plural = "tenancies"
        constraints = [
            models.UniqueConstraint(
                fields=["unit", "tenant"],
                condition=Q(end_date__isnull=True),
                name="unique_active_tenancy_per_unit_tenant",
            ),
        ]

    def __str__(self):
        return f"{self.tenant} @ {self.unit}"

    @property
    def is_active(self):
        return self.end_date is None
