"""
Team-facing release notes shown in the admin: what each build changed, what is planned
next and where each partner thread stands. Edited in the admin; read on /admin/release-notes/.
Text fields accept plain text with `- ` bullet lines and `code` in backticks.
"""

from django.db import models


class Release(models.Model):
    released_on = models.DateField()
    build = models.CharField(
        max_length=64, blank=True, help_text="Commit(s) or 'config' for a configuration change"
    )
    title = models.CharField(max_length=200)
    summary = models.TextField(help_text="What changed and why")
    frontend_impact = models.TextField(
        blank=True, help_text="What the frontend has to do; empty when nothing"
    )
    issues = models.CharField(
        max_length=200, blank=True, help_text="Frontend issue numbers, e.g. #10, #11"
    )
    published = models.BooleanField(default=True)

    class Meta:
        ordering = ["-released_on", "-id"]

    def __str__(self) -> str:
        return f"{self.released_on} {self.title}"


class RoadmapItem(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        IN_PROGRESS = "in_progress", "In progress"
        WAITING = "waiting", "Waiting on a partner"
        DONE = "done", "Done"

    class Area(models.TextChoices):
        BACKEND = "backend", "Backend"
        FRONTEND = "frontend", "Frontend"
        INTEGRATION = "integration", "Integration"
        OPERATIONS = "operations", "Operations"

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    area = models.CharField(max_length=16, choices=Area.choices, default=Area.BACKEND)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNED)
    target = models.CharField(max_length=64, blank=True, help_text="e.g. 'week of 19 Oct'")
    position = models.PositiveIntegerField(default=0, help_text="Lower comes first")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self) -> str:
        return self.title


class PartnerTrack(models.Model):
    class Status(models.TextChoices):
        AGREED = "agreed", "Agreed"
        WAITING_PARTNER = "waiting_partner", "Waiting on partner"
        WAITING_US = "waiting_us", "Waiting on us"
        OPEN = "open", "Open question"
        DONE = "done", "Done"

    partner = models.CharField(max_length=100, help_text="e.g. TESOBE (DCR)")
    topic = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    detail = models.TextField(blank=True)
    next_step = models.CharField(max_length=300, blank=True)
    position = models.PositiveIntegerField(default=0, help_text="Lower comes first")
    updated_on = models.DateField(auto_now=True)

    class Meta:
        ordering = ["partner", "position", "id"]

    def __str__(self) -> str:
        return f"{self.partner}: {self.topic}"
