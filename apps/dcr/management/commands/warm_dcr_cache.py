"""
Load the DCR entity catalogue (dynamic resource docs) into the shared cache.

Reading the docs takes tens of seconds, so the container does it at start-up instead of
the first submit paying for it. Failures are logged, never fatal.
"""

from django.core.cache import cache
from django.core.management.base import BaseCommand

from apps.dcr.client import get_client


class Command(BaseCommand):
    help = "Warm the cached DCR entity catalogue used to shape submit payloads."

    def handle(self, *args, **options):
        client = get_client()
        if not client.base_url:
            self.stdout.write("DCR_BASE_URL not set; nothing to warm")
            return
        cache.delete(f"dcr:entity_fields:{client.bank_id or 'system'}")
        exists = client.entity_exists("activity")
        if exists is None:
            self.stderr.write("DCR entity catalogue could not be read")
        else:
            self.stdout.write(f"DCR entity catalogue cached (activity declared: {exists})")
