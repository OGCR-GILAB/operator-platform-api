from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.exceptions import BadGateway, Conflict
from apps.dcr.client import get_client
from apps.dcr.exceptions import DCRError

from .models import Operator, OperatorMembership
from .serializers import (
    AddMemberSerializer,
    OperatorDCRSerializer,
    OperatorMembershipSerializer,
    OperatorSerializer,
)


class OperatorViewSet(viewsets.ModelViewSet):
    """
    Operators the current user belongs to (staff sees all).

    Creating an operator makes the creator its first member. The operator is pushed to
    DCR only as part of a project submission.
    """

    serializer_class = OperatorSerializer
    search_fields = ["legal_name", "email"]
    ordering_fields = ["legal_name", "created_at", "updated_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Operator.objects.none()
        queryset = Operator.objects.prefetch_related("memberships")
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(memberships__user=self.request.user).distinct()

    def perform_destroy(self, instance):
        if instance.projects.exists():
            raise Conflict("Operator still has projects")
        if instance.is_synced:
            raise Conflict("Operator is already registered on DCR and cannot be deleted locally")
        instance.delete()

    # ---------------------------------------------------------------- members

    @extend_schema(
        request=AddMemberSerializer, responses={200: OperatorMembershipSerializer(many=True)}
    )
    @action(detail=True, methods=["get", "post"])
    def members(self, request, pk=None):
        """GET the members of the operator; POST {username|email, relationship} adds one."""
        operator = self.get_object()
        if request.method == "GET":
            memberships = operator.memberships.select_related("user")
            return Response(OperatorMembershipSerializer(memberships, many=True).data)
        serializer = AddMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        if operator.memberships.filter(user=user).exists():
            raise Conflict("User is already a member of this operator")
        membership = OperatorMembership.objects.create(
            operator=operator, user=user, relationship=serializer.validated_data["relationship"]
        )
        return Response(
            OperatorMembershipSerializer(membership).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(request=None, responses={204: None})
    @action(detail=True, methods=["delete"], url_path=r"members/(?P<user_id>[0-9]+)")
    def remove_member(self, request, pk=None, user_id=None):
        operator = self.get_object()
        membership = operator.memberships.filter(user_id=user_id).first()
        if membership is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if membership.is_synced:
            raise Conflict("Membership is already registered on DCR")
        if operator.memberships.count() == 1:
            raise Conflict("An operator must keep at least one member")
        membership.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # -------------------------------------------------------------- DCR record

    @extend_schema(responses={200: OperatorDCRSerializer, 404: None, 502: None})
    @action(detail=True, methods=["get"], url_path="dcr")
    def dcr(self, request, pk=None):
        """
        The operator as stored on DCR (live read), including the `ogcr_wallet_address`
        the registry assigns. 404 until the operator has been registered by a submit.
        """
        operator = self.get_object()
        if not operator.dcr_id:
            return Response(
                {"detail": "Operator is not registered on DCR yet (submit a project first)"},
                status=status.HTTP_404_NOT_FOUND,
            )
        dcr_token = request.auth if isinstance(request.auth, str) else None
        if not dcr_token:
            return Response(
                {"detail": "A DCR token is required"}, status=status.HTTP_401_UNAUTHORIZED
            )
        try:
            record = get_client().get_entity("operator", operator.dcr_id, dcr_token)
        except DCRError as exc:
            raise BadGateway({"detail": str(exc), "step": "operator"}) from exc
        return Response(
            {
                "dcr_id": operator.dcr_id,
                "dcr_synced_at": operator.dcr_synced_at,
                "fetched_at": timezone.now(),
                "record": record,
                "ogcr_wallet_address": record.get("ogcr_wallet_address", ""),
            }
        )
