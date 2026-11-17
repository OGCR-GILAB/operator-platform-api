"""
Refresh the DCR verification status of every submitted activity, as the service account.

Meant to run on a schedule (cron, Kubernetes CronJob), so verdicts, amendment requests and
certificates written by the certification platform show up without anyone pressing refresh.
`--every N` keeps running and repeats every N seconds instead.
"""

import time

from django.core.management.base import BaseCommand, CommandError

from apps.dcr.client import get_client
from apps.dcr.exceptions import DCRError
from apps.projects import services
from apps.projects.models import Project

SYNCED_STATUSES = (
    Project.Status.SUBMITTED,
    Project.Status.CHANGES_REQUESTED,
    Project.Status.ACCEPTED,
    Project.Status.REJECTED,
)


class Command(BaseCommand):
    help = "Refresh dcr-status of all submitted activities using the DCR service account."

    def add_arguments(self, parser):
        parser.add_argument("--project", type=int, help="only this project id")
        parser.add_argument("--every", type=int, help="repeat every N seconds (stay running)")

    def handle(self, *args, **options):
        while True:
            self.run_once(options.get("project"))
            if not options.get("every"):
                return
            time.sleep(options["every"])

    def run_once(self, project_id=None):
        try:
            token = get_client().service_token()
        except DCRError as exc:
            raise CommandError(f"DCR service account unavailable: {exc}") from exc

        projects = Project.objects.exclude(dcr_id="").filter(status__in=SYNCED_STATUSES)
        if project_id:
            projects = projects.filter(pk=project_id)
        checked = changed = failed = 0
        for project in projects.order_by("pk"):
            before = (project.status, (project.dcr_verification or {}).get("status_code"))
            certificates = len(project.dcr_certificates or [])
            try:
                snapshot = services.refresh_dcr_status(project, token)
            except (services.SubmitError, services.InvalidTransition) as exc:
                failed += 1
                self.stderr.write(f"project {project.pk} ({project.dcr_id}): {exc}")
                continue
            checked += 1
            after = (snapshot["status"], (snapshot["verification"] or {}).get("status_code"))
            if after != before or len(snapshot["certificates"]) != certificates:
                changed += 1
                self.stdout.write(
                    f"project {project.pk} ({project.dcr_id}): {before[0]}/{before[1]} -> "
                    f"{after[0]}/{after[1]}, {len(snapshot['certificates'])} certificate(s)"
                )
        self.stdout.write(f"dcr-status sync: {checked} checked, {changed} changed, {failed} failed")
