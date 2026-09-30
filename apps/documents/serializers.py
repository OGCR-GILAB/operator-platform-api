import hashlib
from pathlib import PurePosixPath

from django.conf import settings
from rest_framework import serializers
from rest_framework.reverse import reverse

from apps.parcels.models import Parcel
from apps.projects.models import Project

from .models import Document


def _is_member(user, operator) -> bool:
    return operator is not None and operator.memberships.filter(user=user).exists()


def user_can_access(user, project=None, parcel=None) -> bool:
    if user.is_staff:
        return True
    if project is not None:
        return project.owner_id == user.id or _is_member(user, project.operator)
    if parcel is not None:
        return _is_member(user, parcel.operator)
    return False


def sha256_of(uploaded_file) -> str:
    digest = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        digest.update(chunk)
    uploaded_file.seek(0)
    return digest.hexdigest()


class DocumentSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True)
    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.all(), required=False, allow_null=True
    )
    parcel = serializers.PrimaryKeyRelatedField(
        queryset=Parcel.objects.all(), required=False, allow_null=True
    )
    uploaded_by_username = serializers.CharField(source="uploaded_by.username", read_only=True)
    download_url = serializers.SerializerMethodField()
    is_frozen = serializers.BooleanField(read_only=True)

    class Meta:
        model = Document
        fields = [
            "id",
            "project",
            "parcel",
            "operator",
            "kind",
            "title",
            "description",
            "file",
            "original_name",
            "content_type",
            "size",
            "sha256",
            "version",
            "supersedes",
            "is_current",
            "is_frozen",
            "forward_status",
            "forwarded_at",
            "forward_reference",
            "uploaded_by",
            "uploaded_by_username",
            "uploaded_at",
            "download_url",
        ]
        read_only_fields = [
            "operator",
            "original_name",
            "content_type",
            "size",
            "sha256",
            "version",
            "supersedes",
            "is_current",
            "forward_status",
            "forwarded_at",
            "forward_reference",
            "uploaded_by",
            "uploaded_at",
        ]

    def get_download_url(self, obj) -> str:
        request = self.context.get("request")
        return reverse("document-download", args=[obj.pk], request=request)

    def validate_file(self, uploaded):
        cfg = settings.DOCUMENTS
        max_bytes = cfg["MAX_SIZE_MB"] * 1024 * 1024
        if uploaded.size > max_bytes:
            raise serializers.ValidationError(f"File exceeds {cfg['MAX_SIZE_MB']} MB")
        if uploaded.size == 0:
            raise serializers.ValidationError("File is empty")
        ext = PurePosixPath(uploaded.name).suffix.lower().lstrip(".")
        if ext not in cfg["ALLOWED_EXTENSIONS"]:
            allowed = ", ".join(sorted(cfg["ALLOWED_EXTENSIONS"]))
            raise serializers.ValidationError(
                f"File type .{ext} is not allowed (allowed: {allowed})"
            )
        return uploaded

    def validate(self, attrs):
        if self.instance is not None:
            # metadata edits only; parent and file are fixed for an existing document
            attrs.pop("project", None)
            attrs.pop("parcel", None)
            return attrs
        project, parcel = attrs.get("project"), attrs.get("parcel")
        if bool(project) == bool(parcel):
            raise serializers.ValidationError(
                "Attach the document to exactly one project or parcel"
            )
        user = self.context["request"].user
        if not user_can_access(user, project=project, parcel=parcel):
            raise serializers.ValidationError("You do not have access to this project or parcel")
        if project is not None and not project.is_editable:
            raise serializers.ValidationError("Project is no longer editable after submission")
        return attrs

    def create(self, validated_data):
        uploaded = validated_data["file"]
        project, parcel = validated_data.get("project"), validated_data.get("parcel")
        validated_data.update(
            uploaded_by=self.context["request"].user,
            operator=project.operator if project else parcel.operator,
            original_name=PurePosixPath(uploaded.name).name[:255],
            content_type=getattr(uploaded, "content_type", "") or "",
            size=uploaded.size,
            sha256=sha256_of(uploaded),
        )
        return super().create(validated_data)


class DocumentVersionSerializer(serializers.Serializer):
    """Upload a new version of an existing document; metadata is inherited unless given."""

    file = serializers.FileField()
    title = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True)

    validate_file = DocumentSerializer.validate_file
