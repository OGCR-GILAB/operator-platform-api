import shutil
import tempfile
from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.documents.models import Document
from apps.operators.models import Operator
from apps.parcels.models import Parcel, ParcelOwnerVerification, ProjectParcel
from apps.projects.models import Project

from .models import PartnerApiKey

MEDIA = tempfile.mkdtemp(prefix="ogcr-partner-")
INSIDE = "SRID=4326;MULTIPOLYGON(((13.405 52.52,13.406 52.52,13.406 52.521,13.405 52.52)))"
FAR_AWAY = "SRID=4326;MULTIPOLYGON(((20.4 44.8,20.41 44.8,20.41 44.81,20.4 44.8)))"
SEARCH_AREA = {
    "type": "Polygon",
    "coordinates": [
        [[13.40, 52.51], [13.41, 52.51], [13.41, 52.53], [13.40, 52.53], [13.40, 52.51]]
    ],
}


@override_settings(MEDIA_ROOT=MEDIA)
class PartnerApiTests(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.key, self.raw = PartnerApiKey.generate("verification-platform")
        self.user = User.objects.create_user(username="alice", dcr_user_id="dcr-1")
        self.operator = Operator.objects.create(
            legal_name="Agreena", email="c@example.org", country_code="DE"
        )
        self.submitted = Project.objects.create(
            owner=self.user,
            operator=self.operator,
            name="Submitted",
            status="submitted",
            dcr_id="act-1",
            start_date=date(2027, 1, 1),
            end_date=date(2028, 1, 1),
        )
        self.draft = Project.objects.create(owner=self.user, operator=self.operator, name="Draft")
        self.inside = Parcel.objects.create(operator=self.operator, name="Inside", geometry=INSIDE)
        self.inside_draft_only = Parcel.objects.create(
            operator=self.operator, name="Draft only", geometry=INSIDE
        )
        self.far = Parcel.objects.create(operator=self.operator, name="Far", geometry=FAR_AWAY)
        ProjectParcel.objects.create(project=self.submitted, parcel=self.inside, amount=5)
        ProjectParcel.objects.create(project=self.draft, parcel=self.inside_draft_only)
        ProjectParcel.objects.create(project=self.submitted, parcel=self.far)
        ParcelOwnerVerification.objects.create(
            parcel=self.inside, status_code="verified", authority="Cadastre"
        )
        self.parcel_doc = Document.objects.create(
            parcel=self.inside,
            operator=self.operator,
            uploaded_by=self.user,
            kind="ownership_proof",
            title="Deed",
            file=SimpleUploadedFile("deed.pdf", b"%PDF deed"),
            original_name="deed.pdf",
            content_type="application/pdf",
            size=9,
            sha256="a" * 64,
        )
        Document.objects.create(
            project=self.submitted,
            operator=self.operator,
            uploaded_by=self.user,
            kind="methodology",
            title="Method",
            file=SimpleUploadedFile("m.pdf", b"%PDF m"),
            original_name="m.pdf",
            content_type="application/pdf",
            size=6,
            sha256="b" * 64,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {self.raw}")

    def test_key_required(self):
        self.client.credentials()
        self.assertEqual(self.client.get(reverse("partner-ping")).status_code, 401)
        self.client.credentials(HTTP_AUTHORIZATION="Api-Key ogcr_wrong")
        self.assertEqual(self.client.get(reverse("partner-ping")).status_code, 401)
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {self.raw}")
        self.assertEqual(
            self.client.get(reverse("partner-ping")).json()["partner"], "verification-platform"
        )
        self.key.refresh_from_db()
        self.assertIsNotNone(self.key.last_used_at)

    def test_search_returns_intersecting_submitted_parcels_with_documents(self):
        response = self.client.post(
            reverse("partner-parcel-search"), {"geometry": SEARCH_AREA}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["type"], "FeatureCollection")
        self.assertEqual([f["properties"]["name"] for f in body["features"]], ["Inside"])
        props = body["features"][0]["properties"]
        self.assertEqual(props["operator"]["legal_name"], "Agreena")
        self.assertEqual(props["owner_verification"]["authority"], "Cadastre")
        self.assertEqual(props["projects"][0]["dcr_id"], "act-1")
        self.assertEqual(props["projects"][0]["amount"], 5)
        scopes = sorted((d["scope"], d["title"]) for d in props["documents"])
        self.assertEqual(scopes, [("parcel", "Deed"), ("project", "Method")])
        self.assertTrue(props["documents"][0]["download_url"].endswith("/download/"))

    def test_include_unsubmitted(self):
        response = self.client.post(
            reverse("partner-parcel-search"),
            {"geometry": SEARCH_AREA, "include_unsubmitted": True},
            format="json",
        )
        names = sorted(f["properties"]["name"] for f in response.json()["features"])
        self.assertEqual(names, ["Draft only", "Inside"])

    def test_partner_download(self):
        response = self.client.get(reverse("partner-document-download", args=[self.parcel_doc.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"%PDF deed")

    def test_end_user_token_cannot_use_partner_api(self):
        self.client.credentials()
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(reverse("partner-ping")).status_code, 403)


class PartnerIndexTests(APITestCase):
    def test_index_and_docs_are_public(self):
        response = self.client.get(reverse("partner-index"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("parcel_search", response.json()["endpoints"])
        response = self.client.get(reverse("partner-docs"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Api-Key", response.content.decode())
