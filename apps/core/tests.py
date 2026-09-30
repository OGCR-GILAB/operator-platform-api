from django.test import TestCase, override_settings
from django.urls import reverse


class HealthViewTests(TestCase):
    def test_health_returns_ok(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["database"], "ok")


class ReferenceTests(TestCase):
    """Reference data is public: no login in setUp on purpose."""

    def test_reference_lists(self):
        body = self.client.get(reverse("reference")).json()
        self.assertIn(
            {"value": "PERMANENT_REMOVAL", "label": "Permanent carbon removal"},
            body["activity_types"],
        )
        self.assertEqual(
            next(u for u in body["unit_types"] if u["value"] == "Soil Emission Reduction")[
                "activity_type"
            ],
            "CARBON_FARMING",
        )
        self.assertIn("ownership_proof", [k["value"] for k in body["document_kinds"]])

    def test_countries(self):
        body = self.client.get(reverse("reference-countries")).json()
        self.assertIn({"code": "DE", "name": "Germany"}, body)


PLAIN_STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=PLAIN_STATIC)
class AdminSmokeTests(TestCase):
    """Every admin page renders for a superuser (catches theme/config regressions)."""

    def setUp(self):
        from apps.accounts.models import User

        self.client.force_login(User.objects.create_superuser(username="root", password="x"))

    def test_dashboard_and_changelists_render(self):
        from django.contrib import admin as django_admin

        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)
        for model in django_admin.site._registry:
            app, name = model._meta.app_label, model._meta.model_name
            changelist = self.client.get(reverse(f"admin:{app}_{name}_changelist"))
            self.assertEqual(changelist.status_code, 200, (app, name))
            add = self.client.get(reverse(f"admin:{app}_{name}_add"))
            # 403 = the ModelAdmin deliberately disables adding (e.g. axes access attempts)
            self.assertIn(add.status_code, (200, 403), (app, name))


@override_settings(STORAGES=PLAIN_STATIC)
class HardeningTests(TestCase):
    def test_oversized_body_is_rejected_before_reading(self):
        response = self.client.generic(
            "POST",
            reverse("health"),
            data=b"",
            content_type="application/octet-stream",
            CONTENT_LENGTH=str(500 * 1024 * 1024),
        )
        self.assertEqual(response.status_code, 413)

    @override_settings(MAX_REQUEST_BODY_BYTES=1000)
    def test_slightly_oversized_body_is_drained_and_rejected(self):
        response = self.client.generic(
            "POST", reverse("health"), data=b"x" * 1500, content_type="application/octet-stream"
        )
        self.assertEqual(response.status_code, 413)
        self.assertIn("exceeds", response.json()["detail"])

    def test_geometry_vertex_cap(self):
        from rest_framework.exceptions import ValidationError

        from apps.core.serializers import MultiPolygonGeometryField

        n = 200
        ring = [[13.0 + i * 1e-6, 52.0] for i in range(n)] + [[13.0, 52.0]]
        with override_settings(MAX_GEOMETRY_VERTICES=100):
            with self.assertRaises(ValidationError):
                MultiPolygonGeometryField().to_internal_value(
                    {"type": "Polygon", "coordinates": [ring]}
                )

    @override_settings(AXES_FAILURE_LIMIT=3)
    def test_admin_login_locks_after_failures(self):
        from apps.accounts.models import User

        User.objects.create_superuser(username="root", password="correct-horse-9!")
        for _ in range(3):
            self.client.post(reverse("admin:login"), {"username": "root", "password": "wrong"})
        response = self.client.post(
            reverse("admin:login"), {"username": "root", "password": "correct-horse-9!"}
        )
        self.assertNotEqual(response.status_code, 302, "login must be locked out")


class RootRedirectTests(TestCase):
    def test_root_and_api_root_go_to_docs(self):
        for url in ("/", "/api/"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertEqual(response["Location"], "/api/docs/")
