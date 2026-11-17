"""
Create (or update) a local admin account that can only read the release notes.

The account is separate from DCR accounts: it logs into /admin/ with a local password and
belongs to the "Release notes" group, which has view rights on the release notes models only.
"""

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

from apps.accounts.models import User

GROUP = "Release notes"
MODELS = ("release", "roadmapitem", "partnertrack")


def release_notes_group() -> Group:
    group, _ = Group.objects.get_or_create(name=GROUP)
    perms = Permission.objects.filter(
        content_type__app_label="changelog", codename__in=[f"view_{m}" for m in MODELS]
    )
    group.permissions.set(perms)
    return group


class Command(BaseCommand):
    help = "Create or update a local admin account that can only read the release notes."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--email", default="")
        parser.add_argument("--first-name", default="")
        parser.add_argument("--last-name", default="")
        parser.add_argument("--password", help="set or reset the local password")

    def handle(self, *args, **options):
        group = release_notes_group()
        user, created = User.objects.get_or_create(username=options["username"])
        if created and not options["password"]:
            user.set_unusable_password()
        user.is_staff = True
        for field in ("email", "first_name", "last_name"):
            if options[field]:
                setattr(user, field, options[field])
        if options["password"]:
            user.set_password(options["password"])
        user.save()
        user.groups.add(group)
        self.stdout.write(
            f"{user.username}: {'created' if created else 'updated'}, group '{GROUP}', "
            f"password {'set' if options['password'] else 'unchanged'}"
        )
