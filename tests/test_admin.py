import pytest
from django.test import Client

from apps.accounts.models import User
from apps.maintenance.models import RequestEvent

pytestmark = pytest.mark.django_db

CHANGELISTS = [
    "accounts/user",
    "properties/property",
    "properties/unit",
    "properties/tenancy",
    "maintenance/maintenancerequest",
    "maintenance/requestevent",
    "maintenance/comment",
]


@pytest.fixture
def admin_client(wf):
    admin = User.objects.create_superuser("admin@example.com", "pw")
    client = Client()
    client.force_login(admin)
    return client


@pytest.mark.parametrize("path", CHANGELISTS)
def test_every_model_is_registered(admin_client, path):
    assert admin_client.get(f"/admin/{path}/").status_code == 200


def test_request_and_event_admin_are_read_only(admin_client, wf):
    req = wf.make()
    event = RequestEvent.objects.create(request=req, actor=wf["creator"], to_status="submitted")
    assert (
        admin_client.get(f"/admin/maintenance/maintenancerequest/{req.id}/change/").status_code
        == 200
    )
    assert admin_client.get("/admin/maintenance/maintenancerequest/add/").status_code == 403
    assert admin_client.get("/admin/maintenance/requestevent/add/").status_code == 403
    resp = admin_client.post(
        f"/admin/maintenance/requestevent/{event.id}/change/", {"note": "tampered"}
    )
    assert resp.status_code == 403
    event.refresh_from_db()
    assert event.note == ""
