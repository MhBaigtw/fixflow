from datetime import timedelta

import factory
from django.utils import timezone

from apps.accounts.models import User
from apps.maintenance.models import MaintenanceRequest
from apps.properties.models import Property, Tenancy, Unit

PASSWORD = "test-pass-123"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    role = User.Role.TENANT
    password = factory.PostGenerationMethodCall("set_password", PASSWORD)

    @factory.post_generation
    def _save(obj, create, extracted, **kwargs):
        if create:
            obj.save()


class ManagerFactory(UserFactory):
    email = factory.Sequence(lambda n: f"manager{n}@example.com")
    role = User.Role.MANAGER


class TenantFactory(UserFactory):
    email = factory.Sequence(lambda n: f"tenant{n}@example.com")
    role = User.Role.TENANT


class ContractorFactory(UserFactory):
    email = factory.Sequence(lambda n: f"contractor{n}@example.com")
    role = User.Role.CONTRACTOR


class PropertyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Property

    name = factory.Sequence(lambda n: f"Property {n}")
    address = factory.Sequence(lambda n: f"{n} Main Street")
    manager = factory.SubFactory(ManagerFactory)


class UnitFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Unit

    property = factory.SubFactory(PropertyFactory)
    label = factory.Sequence(lambda n: f"U{n}")


class TenancyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Tenancy

    unit = factory.SubFactory(UnitFactory)
    tenant = factory.SubFactory(TenantFactory)
    start_date = factory.LazyFunction(timezone.localdate)
    end_date = None


class MaintenanceRequestFactory(factory.django.DjangoModelFactory):
    """Inserts a request row directly, in any state. Writes no events: use the
    services when a test needs the audit trail."""

    class Meta:
        model = MaintenanceRequest

    unit = factory.SubFactory(UnitFactory)
    created_by = factory.SubFactory(TenantFactory)
    title = factory.Sequence(lambda n: f"Request {n}")
    description = "Something is broken."
    category = MaintenanceRequest.Category.PLUMBING
    priority = MaintenanceRequest.Priority.ROUTINE
    requested_priority = factory.LazyAttribute(lambda o: o.priority)
    status = MaintenanceRequest.Status.SUBMITTED
    created_at = factory.LazyFunction(timezone.now)
    sla_due_at = factory.LazyAttribute(lambda o: o.created_at + timedelta(days=7))
