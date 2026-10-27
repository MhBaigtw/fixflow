from datetime import timedelta

import pytest
from django.utils import timezone

from apps.maintenance.models import AppendOnlyError, Comment, MaintenanceRequest, RequestEvent

from .factories import MaintenanceRequestFactory

pytestmark = pytest.mark.django_db
S = MaintenanceRequest.Status


@pytest.fixture
def event():
    req = MaintenanceRequestFactory()
    return RequestEvent.objects.create(
        request=req, actor=req.created_by, from_status=None, to_status=S.SUBMITTED
    )


def test_event_cannot_be_updated_via_save(event):
    event.note = "tampered"
    with pytest.raises(AppendOnlyError):
        event.save()


def test_event_cannot_be_deleted(event):
    with pytest.raises(AppendOnlyError):
        event.delete()


def test_event_queryset_update_and_delete_blocked(event):
    with pytest.raises(AppendOnlyError):
        RequestEvent.objects.filter(pk=event.pk).update(note="tampered")
    with pytest.raises(AppendOnlyError):
        RequestEvent.objects.all().delete()
    event.refresh_from_db()
    assert event.note == ""


@pytest.mark.parametrize(
    "status,due_offset,expected",
    [
        (S.SUBMITTED, -1, True),
        (S.IN_PROGRESS, -1, True),
        (S.SUBMITTED, +1, False),
        (S.RESOLVED, -1, False),
        (S.CLOSED, -1, False),
        (S.CANCELLED, -1, False),
    ],
)
def test_is_overdue(status, due_offset, expected):
    req = MaintenanceRequestFactory(
        status=status, sla_due_at=timezone.now() + timedelta(hours=due_offset)
    )
    assert req.is_overdue is expected


def test_str(event):
    req = event.request
    assert req.title in str(req)
    assert "submitted" in str(event)
    comment = Comment.objects.create(request=req, author=req.created_by, body="hi")
    assert str(req.pk) in str(comment)
