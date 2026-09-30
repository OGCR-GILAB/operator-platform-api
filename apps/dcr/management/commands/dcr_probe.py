"""
Probe how DCR handles dynamic entity creation with a real account.

Answers two open questions against the live platform:
  1. which entitlements the account holds (CanCreateDynamicEntity_System*)
  2. whether DCR assigns entity ids itself or expects the client to send them

Usage:
  manage.py dcr_probe --username U --password P            # read-only checks
  manage.py dcr_probe --username U --password P --create   # also create + delete a test operator
"""

import json

from django.core.management.base import BaseCommand

from apps.dcr.client import get_client
from apps.dcr.exceptions import DCRError


class Command(BaseCommand):
    help = "Probe DCR entitlements and dynamic entity id handling with a real account"

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--password", required=True)
        parser.add_argument(
            "--create", action="store_true", help="create and delete a test operator"
        )

    def handle(self, *args, **options):
        client = get_client()
        token = client.direct_login(options["username"], options["password"])
        me = client.current_user(token)
        self.stdout.write(f"user_id={me.get('user_id')} username={me.get('username')}")

        roles = sorted(
            e.get("role_name", "") for e in (me.get("entitlements") or {}).get("list", [])
        )
        self.stdout.write(f"entitlements ({len(roles)}): {', '.join(roles) or '-'}")

        for entity in ("certification_scheme", "operator", "activity"):
            try:
                rows = client.list_entities(entity, token)
                self.stdout.write(f"GET {entity}: {len(rows)} rows")
            except DCRError as exc:
                self.stdout.write(f"GET {entity}: {exc.status_code} {exc}")

        if not options["create"]:
            return

        payload = {
            "legal_name": "dcr_probe test",
            "email": "probe@example.org",
            "country_code": "RS",
        }
        self.stdout.write("POST operator without id:")
        self._try_create(client, token, "operator", payload)
        self.stdout.write("POST operator with client id:")
        self._try_create(
            client, token, "operator", {"operator_id": "op_probe_client_id", **payload}
        )

    def _try_create(self, client, token, entity, payload):
        try:
            response = client.create_entity(entity, payload, token)
        except DCRError as exc:
            self.stdout.write(
                f"  rejected: {exc.status_code} {exc} {json.dumps(exc.payload)[:300]}"
            )
            return
        entity_id = client.entity_id_from(response, entity)
        self.stdout.write(f"  created id={entity_id} response={json.dumps(response)[:300]}")
        if entity_id:
            try:
                client.delete_entity(entity, entity_id, token)
                self.stdout.write("  deleted again")
            except DCRError as exc:
                self.stdout.write(f"  delete failed: {exc.status_code} {exc}")
