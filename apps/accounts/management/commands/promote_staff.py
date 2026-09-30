from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User


class Command(BaseCommand):
    help = "Give a DCR-mirrored user access to the Django admin (staff, optionally superuser)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--username", required=True, help="local username (as shown by /api/auth/me/)"
        )
        parser.add_argument("--superuser", action="store_true")
        parser.add_argument(
            "--password", help="set a local password so the user can log into /admin/"
        )
        parser.add_argument(
            "--revoke", action="store_true", help="remove staff and superuser flags"
        )

    def handle(self, *args, **options):
        try:
            user = User.objects.get(username=options["username"])
        except User.DoesNotExist as exc:
            raise CommandError("User not found; they must log in through the API once") from exc
        if options["revoke"]:
            user.is_staff = user.is_superuser = False
        else:
            user.is_staff = True
            user.is_superuser = user.is_superuser or options["superuser"]
        if options["password"]:
            user.set_password(options["password"])
        user.save()
        self.stdout.write(
            f"{user.username}: staff={user.is_staff} superuser={user.is_superuser} "
            f"password={'set' if options['password'] else 'unchanged'}"
        )
