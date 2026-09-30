from django.core.management.base import BaseCommand

from apps.partners.models import PartnerApiKey


class Command(BaseCommand):
    help = "Create an API key for a partner platform; the key is printed once"

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True, help="e.g. verification-platform")
        parser.add_argument("--notes", default="")

    def handle(self, *args, **options):
        key, raw = PartnerApiKey.generate(options["name"], notes=options["notes"])
        self.stdout.write(f"Created key '{key.name}' (prefix {key.prefix}).")
        self.stdout.write("Send this to the partner over a secure channel; it is not stored:")
        self.stdout.write(raw)
