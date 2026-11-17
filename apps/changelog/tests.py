from datetime import date
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User

from .models import PartnerTrack, Release, RoadmapItem
from .templatetags.changelog import notes

# admin pages need the static files manifest in production settings; tests skip it
PLAIN_STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=PLAIN_STATIC)
class ReleaseNotesPageTests(TestCase):
    def setUp(self):
        Release.objects.create(
            released_on=date(2026, 10, 14),
            build="4ba921d",
            title="Certificates in dcr-status",
            summary="Reads certificates.\n- `certificate` field\n- statuses <pending>",
            frontend_impact="Show the certificate.",
            issues="#16",
        )
        Release.objects.create(
            released_on=date(2026, 10, 1), title="Hidden", summary="x", published=False
        )
        RoadmapItem.objects.create(title="First end-to-end submit", status="waiting")
        RoadmapItem.objects.create(title="Certificates", status="done")
        PartnerTrack.objects.create(
            partner="TESOBE (DCR)", topic="Document link entity", status="waiting_partner"
        )

    def test_superuser_sees_everything(self):
        admin = User.objects.create_superuser("root", "r@example.org", "pw-12345678")
        self.client.force_login(admin)
        response = self.client.get(reverse("release-notes"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Certificates in dcr-status", html)
        self.assertNotIn("Hidden", html)
        self.assertIn("First end-to-end submit", html)
        self.assertIn("Document link entity", html)
        self.assertIn("operator-platform/issues/16", html)
        self.assertIn("&lt;pending&gt;", html)  # text is escaped

    def test_reader_account_sees_only_the_release_notes(self):
        call_command(
            "release_notes_reader", username="luka", password="pw-12345678", stdout=StringIO()
        )
        reader = User.objects.get(username="luka")
        self.assertTrue(reader.is_staff)
        self.assertFalse(reader.is_superuser)
        self.client.force_login(reader)
        self.assertEqual(self.client.get(reverse("release-notes")).status_code, 200)
        index = self.client.get(reverse("admin:index")).content.decode()
        self.assertIn(reverse("release-notes"), index)
        self.assertNotIn("Recently updated projects", index)
        self.assertEqual(
            self.client.get(reverse("admin:projects_project_changelist")).status_code, 403
        )

    def test_staff_without_rights_and_anonymous_are_refused(self):
        self.assertEqual(self.client.get(reverse("release-notes")).status_code, 302)
        staff = User.objects.create_user("staff", password="pw-12345678", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("release-notes")).status_code, 403)


class NotesFilterTests(TestCase):
    def test_bullets_code_and_issue_links(self):
        html = notes("Intro line\n- one `code`\n- two #12\n\nAfter")
        self.assertEqual(
            html,
            "<p>Intro line</p><ul><li>one <code>code</code></li><li>two "
            '<a href="https://codeberg.org/OGCR/operator-platform/issues/12" target="_blank" '
            'rel="noopener">#12</a></li></ul><p>After</p>',
        )
