"""Role checks. A wrong role on an allowed resource type is a 403.

Object scoping (which rows a user can see) is done by selectors, which
turn out-of-scope objects into 404s.
"""

from rest_framework.permissions import BasePermission


class IsManager(BasePermission):
    message = "Only property managers can do this."

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_manager


class IsTenant(BasePermission):
    message = "Only tenants can do this."

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_tenant
