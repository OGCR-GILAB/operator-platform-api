import shutil
import tempfile
from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.operators.models import Operator, OperatorMembership
from apps.parcels.models import Parcel
from apps.projects.models import Project

from .models import Document

MEDIA = tempfile.mkdtemp(prefix="ogcr-docs-")


@override_settings(MEDIA_ROOT=MEDIA)
class DocumentTests(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.user = User.objects.create_user(username="alice", dcr_user_id="dcr-1")
        self.other = User.objects.create_user(username="bob", dcr_user_id="dcr-2")
        self.operator = Operator.objects.create(
            legal_name="Agreena", email="c@example.org", country_code="DE"
        )
        OperatorMembership.objects.create(operator=self.operator, user=self.user)
        self.project = Project.objects.create(
            owner=self.user,
            operator=self.operator,
            name="P",
            start_date=date(2027, 1, 1),
            end_date=date(2028, 1, 1),
        )
        self.parcel = Parcel.objects.create(
            operator=self.operator,
            geometry=(
                "SRID=4326;MULTIPOLYGON(((13.405 52.52,13.406 52.52,13.406 52.521,13.405 52.52)))"
            ),
        )
        self.client.force_authenticate(self.user)

    def upload(self, name="deed.pdf", content=b"%PDF-1.4 test", **fields):
        data = {
            "file": SimpleUploadedFile(name, content, content_type="application/pdf"),
            "kind": "ownership_proof",
            "title": "Deed",
        }
        data.update(fields)
        return self.client.post(reverse("document-list"), data, format="multipart")

    def test_upload_download_and_checksum(self):
        response = self.upload(parcel=self.parcel.pk)
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["operator"], self.operator.pk)
        self.assertEqual(body["size"], len(b"%PDF-1.4 test"))
        self.assertEqual(len(body["sha256"]), 64)
        self.assertTrue(body["is_current"])
        response = self.client.get(reverse("document-download", args=[body["id"]]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 test")
        self.assertEqual(response["X-Checksum-SHA256"], body["sha256"])

    def test_exactly_one_parent_required(self):
        self.assertEqual(self.upload().status_code, 400)
        self.assertEqual(
            self.upload(project=self.project.pk, parcel=self.parcel.pk).status_code, 400
        )

    def test_type_and_size_limits(self):
        self.assertEqual(self.upload(name="virus.exe", project=self.project.pk).status_code, 400)
        with override_settings(DOCUMENTS={"MAX_SIZE_MB": 1, "ALLOWED_EXTENSIONS": {"pdf"}}):
            response = self.upload(content=b"x" * (1024 * 1024 + 1), project=self.project.pk)
        self.assertEqual(response.status_code, 400)

    def test_versioning(self):
        first = self.upload(project=self.project.pk).json()
        response = self.client.post(
            reverse("document-versions", args=[first["id"]]),
            {
                "file": SimpleUploadedFile(
                    "deed-v2.pdf", b"%PDF-1.4 v2", content_type="application/pdf"
                )
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.content)
        second = response.json()
        self.assertEqual(second["version"], 2)
        self.assertEqual(second["supersedes"], first["id"])
        self.assertEqual(second["title"], "Deed")
        current = [d["id"] for d in self.client.get(reverse("document-list")).json()["results"]]
        self.assertEqual(current, [second["id"]])
        everything = self.client.get(reverse("document-list") + "?all_versions=1").json()["results"]
        self.assertEqual(len(everything), 2)
        # deleting the current version restores the previous one
        self.assertEqual(
            self.client.delete(reverse("document-detail", args=[second["id"]])).status_code, 204
        )
        self.assertTrue(Document.objects.get(pk=first["id"]).is_current)

    def test_access_is_limited_to_members(self):
        doc = self.upload(parcel=self.parcel.pk).json()
        self.client.force_authenticate(self.other)
        self.assertEqual(
            self.client.get(reverse("document-detail", args=[doc["id"]])).status_code, 404
        )
        self.assertEqual(self.upload(parcel=self.parcel.pk).status_code, 400)

    def new_version(self, doc_id):
        return self.client.post(
            reverse("document-versions", args=[doc_id]),
            {"file": SimpleUploadedFile("v.pdf", b"%PDF-1.4 v2", content_type="application/pdf")},
            format="multipart",
        )

    def test_documents_after_submit_until_the_verdict_is_final(self):
        doc = self.upload(project=self.project.pk).json()
        self.project.status = Project.Status.SUBMITTED
        self.project.save()
        # certifiers fetch documents during the audit: adding and new versions still work
        added = self.upload(project=self.project.pk, title="Extra evidence")
        self.assertEqual(added.status_code, 201, added.content)
        self.assertFalse(added.json()["is_deletable"])
        self.assertEqual(self.new_version(doc["id"]).status_code, 201)
        # but nothing is deleted after submission
        current = self.client.get(reverse("document-list") + f"?project={self.project.pk}").json()
        some = current["results"][0]["id"]
        self.assertEqual(
            self.client.delete(reverse("document-detail", args=[some])).status_code, 409
        )

        # a final verdict closes the activity's documents
        self.project.dcr_verification = {"status_code": "verified"}
        self.project.save()
        self.assertEqual(self.upload(project=self.project.pk).status_code, 400)
        self.assertEqual(self.new_version(some).status_code, 409)
        patch = self.client.patch(
            reverse("document-detail", args=[some]), {"title": "x"}, format="json"
        )
        self.assertEqual(patch.status_code, 409)

    def test_parcel_documents_follow_its_activities(self):
        from apps.parcels.models import ProjectParcel

        ProjectParcel.objects.create(project=self.project, parcel=self.parcel)
        self.parcel.dcr_sync_status = "synced"
        self.parcel.dcr_id = "parcel-1"
        self.parcel.save()
        self.project.status = Project.Status.SUBMITTED
        self.project.save()
        doc = self.upload(parcel=self.parcel.pk)
        self.assertEqual(doc.status_code, 201, doc.content)
        self.assertEqual(
            self.client.delete(reverse("document-detail", args=[doc.json()["id"]])).status_code,
            409,
        )
        self.project.dcr_certificates = [{"certification_status": "active"}]
        self.project.save()
        self.assertEqual(self.upload(parcel=self.parcel.pk).status_code, 400)
