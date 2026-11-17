import tempfile
import time
from pathlib import Path

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.parcels import registry
from apps.parcels.models import RegistryOwner

COLUMNS = (
    "idu",
    "right_code",
    "right_label",
    "siren",
    "legal_name",
    "legal_form",
    "legal_form_code",
    "person_group",
    "vintage",
)


class Command(BaseCommand):
    help = (
        "Load the DGFiP file of parcels owned by legal persons (France) into RegistryOwner, "
        "replacing the vintage loaded before. Downloads the published files unless --file is given."
    )

    def add_arguments(self, parser):
        parser.add_argument("--vintage", type=int, default=max(registry.FILES))
        parser.add_argument(
            "--file",
            action="append",
            default=[],
            help="A downloaded zip or CSV (repeatable) instead of downloading the vintage's files.",
        )
        parser.add_argument(
            "--download-dir",
            default="",
            help="Keep the downloaded zips here (default: a temporary directory, removed after).",
        )

    def handle(self, *args, **options):
        vintage = options["vintage"]
        files = [Path(p) for p in options["file"]]
        if files:
            missing = [str(p) for p in files if not p.is_file()]
            if missing:
                raise CommandError(f"Not found: {', '.join(missing)}")
            return self.load(files, vintage)
        urls = registry.FILES.get(vintage)
        if not urls:
            raise CommandError(f"No published files known for {vintage}; pass --file.")
        if options["download_dir"]:
            target = Path(options["download_dir"])
            target.mkdir(parents=True, exist_ok=True)
            return self.load(self.download(urls, target), vintage)
        with tempfile.TemporaryDirectory(prefix="registry-owners-") as tmp:
            return self.load(self.download(urls, Path(tmp)), vintage)

    def download(self, urls, target: Path) -> list[Path]:
        paths = []
        for index, url in enumerate(urls, start=1):
            path = target / f"{url.rstrip('/').rsplit('/', 1)[-1]}.zip"
            if path.is_file() and path.stat().st_size:
                self.stdout.write(f"using {path}")
            else:
                self.stdout.write(f"downloading {index}/{len(urls)}: {url}")
                with requests.get(url, stream=True, timeout=120) as response:
                    response.raise_for_status()
                    with open(path, "wb") as out:
                        for chunk in response.iter_content(1 << 20):
                            out.write(chunk)
            paths.append(path)
        return paths

    def load(self, paths: list[Path], vintage: int) -> None:
        table = RegistryOwner._meta.db_table
        started = time.monotonic()
        total = 0
        with transaction.atomic(), connection.cursor() as cursor:
            # the old vintage stays visible until the new one is committed
            cursor.execute(f"DELETE FROM {table}")
            sql = f"COPY {table} ({', '.join(COLUMNS)}) FROM STDIN"
            with cursor.cursor.copy(sql) as copy:
                for name, rows in registry.iter_files(paths):
                    count = 0
                    for row in rows:
                        copy.write_row([*(row[c] for c in COLUMNS[:-1]), vintage])
                        count += 1
                    total += count
                    self.stdout.write(f"{name}: {count} rows")
            if not total:
                # raised inside the transaction, so the vintage loaded before stays
                raise CommandError("No rows read; nothing was replaced.")
        with connection.cursor() as cursor:
            cursor.execute(f"ANALYZE {table}")
        minutes = (time.monotonic() - started) / 60
        self.stdout.write(
            self.style.SUCCESS(f"Loaded {total} rows of vintage {vintage} in {minutes:.1f} min.")
        )
