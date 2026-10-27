from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import IsTenant

from . import selectors
from .filters import MaintenanceRequestFilter
from .models import MaintenanceRequest
from .serializers import (
    CommentSerializer,
    MaintenanceRequestCreateSerializer,
    MaintenanceRequestSerializer,
    MaintenanceRequestUpdateSerializer,
    RequestEventSerializer,
    TransitionSerializer,
)
from .services.comments import add_comment
from .services.requests import create_request, update_request
from .services.transitions import apply_transition


class MaintenanceRequestViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Every lookup goes through requests_visible_to(), so a request outside the
    caller's scope is a 404 on detail, events, comments and transition alike."""

    serializer_class = MaintenanceRequestSerializer
    filterset_class = MaintenanceRequestFilter

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # schema generation
            return MaintenanceRequest.objects.none()
        return selectors.with_priority_rank(selectors.requests_visible_to(self.request.user))

    def get_permissions(self):
        if self.action == "create":
            return [IsTenant()]
        return [IsAuthenticated()]

    def get_object(self):
        # Scope only; list filters/ordering don't apply to single-object lookups.
        return get_object_or_404(self.get_queryset(), pk=self.kwargs["pk"])

    @extend_schema(
        request=MaintenanceRequestCreateSerializer,
        responses={201: MaintenanceRequestSerializer},
    )
    def create(self, request):
        serializer = MaintenanceRequestCreateSerializer(
            data=request.data, context=self.get_serializer_context()
        )
        serializer.is_valid(raise_exception=True)
        req = create_request(tenant=request.user, **serializer.validated_data)
        return Response(MaintenanceRequestSerializer(req).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        request=MaintenanceRequestUpdateSerializer, responses=MaintenanceRequestSerializer
    )
    def partial_update(self, request, pk=None):
        req = self.get_object()
        serializer = MaintenanceRequestUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        req = update_request(request_id=req.pk, user=request.user, **serializer.validated_data)
        return Response(MaintenanceRequestSerializer(req).data)

    @extend_schema(request=TransitionSerializer, responses=MaintenanceRequestSerializer)
    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        req = self.get_object()
        serializer = TransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        req = apply_transition(request_id=req.pk, actor=request.user, **serializer.validated_data)
        return Response(MaintenanceRequestSerializer(req).data)

    @extend_schema(responses=RequestEventSerializer(many=True))
    @action(detail=True, methods=["get"])
    def events(self, request, pk=None):
        req = self.get_object()
        page = self.paginate_queryset(req.events.all())
        return self.get_paginated_response(RequestEventSerializer(page, many=True).data)

    @extend_schema(methods=["GET"], responses=CommentSerializer(many=True))
    @extend_schema(methods=["POST"], request=CommentSerializer, responses={201: CommentSerializer})
    @action(detail=True, methods=["get", "post"])
    def comments(self, request, pk=None):
        req = self.get_object()
        if request.method == "POST":
            serializer = CommentSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            comment = add_comment(request=req, author=request.user, **serializer.validated_data)
            return Response(CommentSerializer(comment).data, status=status.HTTP_201_CREATED)
        page = self.paginate_queryset(selectors.comments_visible_to(request.user, req))
        return self.get_paginated_response(CommentSerializer(page, many=True).data)

    @extend_schema(responses=CommentSerializer)
    @action(detail=True, methods=["get"], url_path=r"comments/(?P<comment_id>\d+)")
    def comment_detail(self, request, pk=None, comment_id=None):
        req = self.get_object()
        comment = get_object_or_404(selectors.comments_visible_to(request.user, req), pk=comment_id)
        return Response(CommentSerializer(comment).data)
