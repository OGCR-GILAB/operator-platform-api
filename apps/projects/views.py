from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.exceptions import BadGateway, Conflict
from apps.core.mixins import OneToOneSubresourceMixin
from apps.parcels.serializers import ProjectParcelSerializer

from . import services
from .models import Project
from .serializers import (
    ActivityPlanSerializer,
    DCRStatusSerializer,
    MonitoringPlanSerializer,
    ProjectSerializer,
    ReadinessSerializer,
    SubmissionErrorSerializer,
)


class ProjectViewSet(OneToOneSubresourceMixin, viewsets.ModelViewSet):
    """
    Projects (DCR activities) of the current user (staff sees all).

    Lifecycle: draft -> ready -> submitted -> accepted | rejected.
    Nothing is sent to DCR until `submit`.
    """

    queryset = Project.objects.none()  # real queryset comes from get_queryset()
    serializer_class = ProjectSerializer
    filterset_fields = ["status", "operator", "type", "dcr_sync_status"]
    search_fields = ["name", "summary", "description"]
    ordering_fields = ["created_at", "updated_at", "name", "status"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Project.objects.none()
        queryset = Project.objects.select_related("owner", "operator", "plan", "monitoring_plan")
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(owner=self.request.user)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    def perform_update(self, serializer):
        if not serializer.instance.is_editable:
            raise Conflict("Project is no longer editable after submission")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.status != Project.Status.DRAFT or instance.is_synced:
            raise Conflict("Only unsubmitted draft projects can be deleted")
        instance.delete()

    # ----------------------------------------------------------- sub-resources

    @extend_schema(request=ActivityPlanSerializer, responses={200: ActivityPlanSerializer})
    @action(detail=True, methods=["get", "put", "patch"], url_path="plan")
    def plan(self, request, pk=None):
        project = self.get_object()
        return self.one_to_one(
            request, ActivityPlanSerializer, "plan", "project", project.is_editable
        )

    @extend_schema(request=MonitoringPlanSerializer, responses={200: MonitoringPlanSerializer})
    @action(detail=True, methods=["get", "put", "patch"], url_path="monitoring-plan")
    def monitoring_plan(self, request, pk=None):
        project = self.get_object()
        return self.one_to_one(
            request, MonitoringPlanSerializer, "monitoring_plan", "project", project.is_editable
        )

    # --------------------------------------------------------------- parcels

    @extend_schema(
        request=ProjectParcelSerializer, responses={200: ProjectParcelSerializer(many=True)}
    )
    @action(detail=True, methods=["get", "post"])
    def parcels(self, request, pk=None):
        """GET the parcels linked to the project; POST {parcel, status_message?} links one."""
        project = self.get_object()
        links = project.parcel_links.select_related("parcel", "parcel__operator")
        if request.method == "GET":
            return Response(ProjectParcelSerializer(links, many=True).data)
        if not project.is_editable:
            raise Conflict("Project is no longer editable after submission")
        serializer = ProjectParcelSerializer(
            data=request.data, context={"request": request, "project": project}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(project=project)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses={204: None})
    @action(detail=True, methods=["delete"], url_path=r"parcels/(?P<parcel_id>[0-9]+)")
    def unlink_parcel(self, request, pk=None, parcel_id=None):
        project = self.get_object()
        if not project.is_editable:
            raise Conflict("Project is no longer editable after submission")
        link = project.parcel_links.filter(parcel_id=parcel_id).first()
        if link is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if link.is_synced:
            raise Conflict("Link is already registered on DCR")
        link.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------- lifecycle

    def _transition(self, func, *args, **kwargs):
        project = self.get_object()
        try:
            project = func(project, *args, **kwargs)
        except services.InvalidTransition as exc:
            raise Conflict(str(exc)) from exc
        return Response(self.get_serializer(project).data)

    @extend_schema(
        responses={
            200: DCRStatusSerializer,
            409: SubmissionErrorSerializer,
            502: SubmissionErrorSerializer,
        }
    )
    @action(detail=True, methods=["get", "post"], url_path="dcr-status")
    def dcr_status(self, request, pk=None):
        """GET: last known verification outcome. POST: refresh it from DCR and mirror locally."""
        project = self.get_object()
        if request.method == "GET":
            return Response(services.dcr_status_snapshot(project))
        dcr_token = request.auth if isinstance(request.auth, str) else None
        try:
            return Response(services.refresh_dcr_status(project, dcr_token))
        except services.InvalidTransition as exc:
            raise Conflict(str(exc)) from exc
        except services.SubmitError as exc:
            error = BadGateway({"detail": exc.detail, "step": exc.step})
            if exc.step == "auth":
                error.status_code = status.HTTP_401_UNAUTHORIZED
            raise error from exc

    @extend_schema(responses={200: ReadinessSerializer})
    @action(detail=True, methods=["get"])
    def readiness(self, request, pk=None):
        """Submission checklist: blocking errors and non-blocking warnings."""
        return Response(services.readiness(self.get_object()))

    @extend_schema(request=None, responses={200: ProjectSerializer})
    @action(detail=True, methods=["post"])
    def ready(self, request, pk=None):
        return self._transition(services.mark_ready)

    @extend_schema(request=None, responses={200: ProjectSerializer})
    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        return self._transition(services.reopen)

    @extend_schema(
        request=None,
        responses={
            200: ProjectSerializer,
            400: SubmissionErrorSerializer,
            502: SubmissionErrorSerializer,
        },
    )
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        """Push the project (operator, activity, plans) to DCR on behalf of the user."""
        dcr_token = request.auth if isinstance(request.auth, str) else None
        try:
            return self._transition(services.submit_project, dcr_token, user=request.user)
        except services.NotReadyForSubmission as exc:
            return Response(
                {"detail": "Project is not ready for submission", "problems": exc.problems},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except services.SubmitError as exc:
            error = BadGateway({"detail": exc.detail, "step": exc.step})
            if exc.step == "auth":
                error.status_code = status.HTTP_401_UNAUTHORIZED
            raise error from exc
