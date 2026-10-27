import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken


@pytest.fixture
def anon():
    return APIClient()


@pytest.fixture
def client_for():
    """Return an APIClient authenticated with a real JWT access token for `user`."""

    def make(user):
        client = APIClient()
        token = RefreshToken.for_user(user).access_token
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client

    return make


class Workflow(dict):
    """People and places for maintenance tests; keys double as actor names."""

    def make(self, status="submitted", **kwargs):
        """A request on `unit`, created by `creator`, already in `status`."""
        from django.utils import timezone

        from apps.maintenance.models import MaintenanceRequest

        from .factories import MaintenanceRequestFactory

        S = MaintenanceRequest.Status
        fields = {"unit": self["unit"], "created_by": self["creator"], "status": status}
        if status in (S.ASSIGNED, S.IN_PROGRESS, S.RESOLVED, S.CLOSED):
            fields["assigned_to"] = self["contractor"]
        if status in (S.RESOLVED, S.CLOSED):
            fields["resolved_at"] = timezone.now()
        if status == S.CLOSED:
            fields["closed_at"] = timezone.now()
        fields.update(kwargs)
        return MaintenanceRequestFactory(**fields)


@pytest.fixture
def wf(db):
    from .factories import (
        ContractorFactory,
        ManagerFactory,
        PropertyFactory,
        TenancyFactory,
        TenantFactory,
        UnitFactory,
    )

    manager = ManagerFactory()
    prop = PropertyFactory(manager=manager)
    unit = UnitFactory(property=prop, label="1A")
    other_unit = UnitFactory(property=prop, label="1B")
    creator = TenantFactory()
    roommate = TenantFactory()
    outsider = TenantFactory()
    TenancyFactory(unit=unit, tenant=creator)
    TenancyFactory(unit=unit, tenant=roommate)
    TenancyFactory(unit=other_unit, tenant=outsider)
    return Workflow(
        manager=manager,
        other_manager=ManagerFactory(),
        property=prop,
        unit=unit,
        other_unit=other_unit,
        creator=creator,
        roommate=roommate,
        outsider=outsider,
        contractor=ContractorFactory(),
        other_contractor=ContractorFactory(),
    )


def assert_error(response, status_code, code=None):
    """Assert the standard error shape and return the error object."""
    assert response.status_code == status_code, response.content
    body = response.json()
    assert set(body) == {"error"}, body
    error = body["error"]
    assert set(error) == {"code", "message", "details"}, error
    assert isinstance(error["code"], str) and error["code"]
    assert isinstance(error["message"], str) and error["message"]
    assert isinstance(error["details"], dict)
    if code is not None:
        assert error["code"] == code, error
    return error
