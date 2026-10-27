"""Read-side scoping for properties. Out-of-scope rows simply don't exist (404)."""

from .models import Property, Tenancy, Unit


def properties_for_manager(user):
    return Property.objects.filter(manager=user)


def units_for_manager(user):
    return Unit.objects.filter(property__manager=user)


def tenancies_for_manager(user):
    return Tenancy.objects.filter(unit__property__manager=user)


def active_tenancies(user):
    return Tenancy.objects.filter(tenant=user, end_date__isnull=True)
