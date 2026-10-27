from rest_framework.exceptions import PermissionDenied

from apps.maintenance.models import Comment


def add_comment(*, request, author, body, is_internal=False):
    if is_internal and author.is_tenant:
        raise PermissionDenied("Tenants cannot post internal comments.")
    return Comment.objects.create(
        request=request, author=author, body=body, is_internal=is_internal
    )
