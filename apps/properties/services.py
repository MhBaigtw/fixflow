from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.core.exceptions import Conflict

from .models import Tenancy

DUPLICATE_TENANCY = "This tenant already has an active tenancy on this unit."


def attach_tenant(*, unit, email, start_date=None):
    tenant = User.objects.filter(email__iexact=email).first()
    if tenant is None:
        raise ValidationError({"email": ["No user with this email exists."]})
    if not tenant.is_tenant:
        raise ValidationError({"email": ["This user does not have the tenant role."]})
    if Tenancy.objects.filter(unit=unit, tenant=tenant, end_date__isnull=True).exists():
        raise ValidationError({"email": [DUPLICATE_TENANCY]})
    try:
        # Savepoint so a lost race doesn't poison an outer transaction.
        with transaction.atomic():
            return Tenancy.objects.create(
                unit=unit,
                tenant=tenant,
                start_date=start_date or timezone.localdate(),
            )
    except IntegrityError as exc:
        raise ValidationError({"email": [DUPLICATE_TENANCY]}) from exc


def end_tenancy(*, tenancy):
    if tenancy.end_date is not None:
        raise Conflict("This tenancy has already ended.", code="tenancy_already_ended")
    tenancy.end_date = timezone.localdate()
    tenancy.save(update_fields=["end_date"])
    return tenancy
