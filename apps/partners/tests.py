import shutil
import tempfile
from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.documents.models import Document
from apps.operators.models import Operator
from apps.parcels.models import Parcel, ParcelOwnerVerification, ProjectParcel
from apps.projects.models import Project

from .models import PartnerApiKey

MEDIA = tempfile.mkdtemp(prefix="ogcr-partner-")
INSIDE = "SRID=4326;MULTIPOLYGON(((13.405 52.52,13.406 52.52,13.406 52.521,13.405 52.52)))"
FAR_AWAY = "SRID=4326;MULTIPOLYGON(((20.4 44.8,20.41 44.8,20.41 44.81,20.4 44.8)))"
SEARCH_AREA = {
    "type": "Polygon",
    "coordinates": [
        [[13.40, 52.51], [13.41, 52.51], [13.41, 52.53], [13.40, 52.53], [13.40, 52.51]]
    ],
}


@override_settings(MEDIA_ROOT=MEDIA)
class PartnerApiTests(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.key, self.raw = PartnerApiKey.generate("verification-platform")
        self.user = User.objects.create_user(username="alice", dcr_user_id="dcr-1")
        self.operator = Operator.objects.create(
            legal_name="Agreena", email="c@example.org", country_code="DE"
        )
        self.submitted = Project.objects.create(
            owner=self.user,
            operator=self.operator,
            name="Submitted",
            status="submitted",
            dcr_id="act-1",
            start_date=date(2027, 1, 1),
            end_date=date(2028, 1, 1),
        )
        self.draft = Project.objects.create(owner=self.user, operator=self.operator, name="Draft")
        self.inside = Parcel.objects.create(operator=self.operator, name="Inside", geometry=INSIDE)
        self.inside_draft_only = Parcel.objects.create(
            operator=self.operator, name="Draft only", geometry=INSIDE
        )
        self.far = Parcel.objects.create(operator=self.operator, name="Far", geometry=FAR_AWAY)
        ProjectParcel.objects.create(project=self.submitted, parcel=self.inside, amount=5)
        ProjectParcel.objects.create(project=self.draft, parcel=self.inside_draft_only)
        ProjectParcel.objects.create(project=self.submitted, parcel=self.far)
        ParcelOwnerVerification.objects.create(
            parcel=self.inside, status_code="verified", authority="Cadastre"
        )
        self.parcel_doc = Document.objects.create(
            parcel=self.inside,
            operator=self.operator,
            uploaded_by=self.user,
            kind="ownership_proof",
            title="Deed",
            file=SimpleUploadedFile("deed.pdf", b"%PDF deed"),
            original_name="deed.pdf",
            content_type="application/pdf",
            size=9,
            sha256="a" * 64,
        )
        Document.objects.create(
            project=self.submitted,
            operator=self.operator,
            uploaded_by=self.user,
            kind="methodology",
            title="Method",
            file=SimpleUploadedFile("m.pdf", b"%PDF m"),
            original_name="m.pdf",
            content_type="application/pdf",
            size=6,
            sha256="b" * 64,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {self.raw}")

    def test_key_required(self):
        self.client.credentials()
        self.assertEqual(self.client.get(reverse("partner-ping")).status_code, 401)
        self.client.credentials(HTTP_AUTHORIZATION="Api-Key ogcr_wrong")
        self.assertEqual(self.client.get(reverse("partner-ping")).status_code, 401)
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {self.raw}")
        self.assertEqual(
            self.client.get(reverse("partner-ping")).json()["partner"], "verification-platform"
        )
        self.key.refresh_from_db()
        self.assertIsNotNone(self.key.last_used_at)

    def test_search_returns_intersecting_submitted_parcels_with_documents(self):
        response = self.client.post(
            reverse("partner-parcel-search"), {"geometry": SEARCH_AREA}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["type"], "FeatureCollection")
        self.assertEqual([f["properties"]["name"] for f in body["features"]], ["Inside"])
        props = body["features"][0]["properties"]
        self.assertEqual(props["operator"]["legal_name"], "Agreena")
        self.assertEqual(props["owner_verification"]["authority"], "Cadastre")
        self.assertEqual(props["projects"][0]["dcr_id"], "act-1")
        self.assertEqual(props["projects"][0]["amount"], 5)
        scopes = sorted((d["scope"], d["title"]) for d in props["documents"])
        self.assertEqual(scopes, [("parcel", "Deed"), ("project", "Method")])
        self.assertTrue(props["documents"][0]["download_url"].endswith("/download/"))

    def test_search_leaves_out_neighbours_that_only_touch(self):
        # a neighbour sharing the search area's east border, and one overlapping it by a sliver
        box = "SRID=4326;MULTIPOLYGON((({w} 52.51,13.42 52.51,13.42 52.53,{w} 52.53,{w} 52.51)))"
        touching = box.format(w=13.41)
        sliver = box.format(w=13.40995)  # 0.5% of the parcel lies in the search area
        for name, geometry in (("Touching", touching), ("Sliver", sliver)):
            parcel = Parcel.objects.create(operator=self.operator, name=name, geometry=geometry)
            ProjectParcel.objects.create(project=self.submitted, parcel=parcel)
        url = reverse("partner-parcel-search")
        body = self.client.post(url, {"geometry": SEARCH_AREA}, format="json").json()
        self.assertEqual([f["properties"]["name"] for f in body["features"]], ["Inside"])
        self.assertEqual(body["features"][0]["properties"]["overlap"], 1.0)
        # a small search area inside a parcel still finds it
        small = {
            "type": "Polygon",
            "coordinates": [[[13.415, 52.52], [13.416, 52.52], [13.416, 52.521], [13.415, 52.52]]],
        }
        body = self.client.post(url, {"geometry": small}, format="json").json()
        self.assertEqual(
            sorted(f["properties"]["name"] for f in body["features"]), ["Sliver", "Touching"]
        )
        # min_overlap 0: every intersecting parcel, as before
        body = self.client.post(
            url, {"geometry": SEARCH_AREA, "min_overlap": 0}, format="json"
        ).json()
        self.assertEqual(
            sorted(f["properties"]["name"] for f in body["features"]),
            ["Inside", "Sliver", "Touching"],
        )

    def test_include_unsubmitted(self):
        response = self.client.post(
            reverse("partner-parcel-search"),
            {"geometry": SEARCH_AREA, "include_unsubmitted": True},
            format="json",
        )
        names = sorted(f["properties"]["name"] for f in response.json()["features"])
        self.assertEqual(names, ["Draft only", "Inside"])

    def test_partner_download(self):
        response = self.client.get(reverse("partner-document-download", args=[self.parcel_doc.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"%PDF deed")

    def test_activity_by_registry_id(self):
        self.inside.dcr_id = "parcel-1"
        self.inside.save()
        response = self.client.get(reverse("partner-activity", args=["act-1"]))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["name"], "Submitted")
        self.assertEqual(body["operator"]["legal_name"], "Agreena")
        self.assertEqual([d["title"] for d in body["documents_by_kind"]["methodology"]], ["Method"])
        self.assertEqual(body["parcels"]["count"], 2)
        inside = next(
            f["properties"]
            for f in body["parcels"]["features"]
            if f["properties"]["name"] == "Inside"
        )
        # parcel features carry only their own documents here; project ones sit at the top
        self.assertEqual([d["title"] for d in inside["documents"]], ["Deed"])
        self.assertEqual(list(inside["documents_by_kind"]), ["ownership_proof"])
        self.assertEqual(inside["dcr_id"], "parcel-1")

        no_docs = self.client.get(
            reverse("partner-activity", args=["act-1"]) + "?include_documents=false"
        ).json()
        self.assertEqual(no_docs["documents"], [])
        self.assertEqual(no_docs["parcels"]["features"][0]["properties"]["documents"], [])

        self.draft.dcr_id = "act-draft"
        self.draft.save()
        self.assertEqual(
            self.client.get(reverse("partner-activity", args=["act-draft"])).status_code, 404
        )
        self.assertEqual(
            self.client.get(reverse("partner-activity", args=["nope"])).status_code, 404
        )

    def test_parcel_by_registry_id(self):
        self.inside.dcr_id = "OPP-TEST_parcel_1"
        self.inside.save()
        self.inside_draft_only.dcr_id = "parcel-draft"
        self.inside_draft_only.save()
        response = self.client.get(reverse("partner-parcel", args=["OPP-TEST_parcel_1"]))
        self.assertEqual(response.status_code, 200, response.content)
        props = response.json()["properties"]
        self.assertEqual(props["name"], "Inside")
        self.assertEqual(props["owner_verification"]["authority"], "Cadastre")
        self.assertEqual(sorted(props["documents_by_kind"]), ["methodology", "ownership_proof"])

        url = reverse("partner-parcel", args=["parcel-draft"])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(url + "?include_unsubmitted=true").status_code, 200)
        # the search route is not shadowed by the registry-id route
        search = self.client.post(
            reverse("partner-parcel-search"), {"geometry": SEARCH_AREA}, format="json"
        )
        self.assertEqual(search.status_code, 200)

    def test_end_user_token_cannot_use_partner_api(self):
        self.client.credentials()
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(reverse("partner-ping")).status_code, 403)


class PartnerIndexTests(APITestCase):
    def test_index_and_docs_are_public(self):
        response = self.client.get(reverse("partner-index"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("parcel_search", response.json()["endpoints"])
        response = self.client.get(reverse("partner-docs"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Api-Key", response.content.decode())


HEADER = (
    "Département;Code Direction;Code Commune;Nom de la commune;Préfixe;Section;N° plan;"
    "N° voirie;Indice de répétition;Code voie MAJIC;Code voie Rivoli;Nature voie;Nom voie;"
    "Contenance;SUF;Nature culture;Contenance;Code droit - par;N° Majic - par;N° SIREN - par;"
    "Groupe personne - par;Forme juridique - par;Forme juridique abrégée - par;Dénomination - par"
)
ROWS = [
    # two fiscal subdivisions of ZA 93, same owner: one row after loading
    "45;0;025;BAZOCHES;;ZA;0093;;;00114;B114;;MAUREPAS;160040;J;T - Terre;80020;P - Propriétaire;"
    "PBBKGM;424108801;0 - Personnes morales non remarquables ;6534;GFA;GFA DES VARENNES",
    "45;0;025;BAZOCHES;;ZA;0093;;;00114;B114;;MAUREPAS;160040;K;T - Terre;80020;P - Propriétaire;"
    "PBBKGM;424108801;0 - Personnes morales non remarquables ;6534;GFA;GFA DES VARENNES",
    # a single-letter section and a manager next to the owner
    "72;0;040;X;;B;0101;;;;;;;100;;T - Terre;100;P - Propriétaire;A;214500258;4 - Commune;7210;"
    "COM;COMMUNE DE X",
    "72;0;040;X;;B;0101;;;;;;;100;;T - Terre;100;G - Gérant, mandataire, gestionnaire;B;;0 - x;;;"
    "AGENCE Y",
    # overseas departments are not loaded
    "971;1;101;X;;CR;0087;;;;;;;100;;T - Terre;100;P - Propriétaire;D;222;4 - Commune;;COM;DOM",
    # an absorbed commune's prefix
    "45;0;025;BAZOCHES;172;ZA;0021;;;;;;;100;;T - Terre;100;P - Propriétaire;C;111;4 - Commune;;"
    "COM;OLD",
]


class RegistryOwnerTests(APITestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ogcr-registry-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        _, self.raw = PartnerApiKey.generate("verification-platform")
        user = User.objects.create_user(username="alice", dcr_user_id="dcr-1")
        operator = Operator.objects.create(
            legal_name="Op", email="o@example.org", country_code="FR"
        )
        project = Project.objects.create(
            owner=user, operator=operator, name="Submitted", status="submitted", dcr_id="act-9"
        )
        self.za93 = Parcel.objects.create(
            operator=operator, cadastral_reference="45025000ZA0093", geometry=INSIDE, dcr_id="p-93"
        )
        self.private = Parcel.objects.create(
            operator=operator, cadastral_reference="45025000ZA0090", geometry=INSIDE
        )
        self.foreign = Parcel.objects.create(
            operator=operator, cadastral_reference="", geometry=INSIDE
        )
        for parcel in (self.za93, self.private, self.foreign):
            ProjectParcel.objects.create(project=project, parcel=parcel)
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {self.raw}")

    def write_zip(self, rows, name="PM_26_NB_450.csv"):
        import zipfile
        from pathlib import Path

        path = Path(self.tmp) / "parcels.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(f"Fichier des parcelles/{name}", "﻿" + "\n".join([HEADER, *rows]))
        return path

    def load(self, rows, vintage=2026):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command(
            "load_registry_owners", file=[str(self.write_zip(rows))], vintage=vintage, stdout=out
        )
        return out.getvalue()

    def test_parse_builds_the_cadastral_id_and_merges_subdivisions(self):
        from apps.parcels import registry

        rows = list(registry.parse_csv([HEADER, *ROWS]))
        self.assertEqual(
            [(r["idu"], r["right_code"], r["legal_name"]) for r in rows],
            [
                ("45025000ZA0093", "P", "GFA DES VARENNES"),
                ("720400000B0101", "P", "COMMUNE DE X"),
                ("720400000B0101", "G", "AGENCE Y"),
                ("45025172ZA0021", "P", "OLD"),
            ],
        )
        self.assertEqual(rows[0]["person_group"], "Personnes morales non remarquables")
        self.assertEqual(rows[0]["siren"], "424108801")
        with self.assertRaises(ValueError):
            list(registry.parse_csv(["a;b;c", *ROWS]))
        self.assertEqual(registry.normalize_idu(" 45025000za0093 "), "45025000ZA0093")
        self.assertIsNone(registry.normalize_idu("1234"))

    def test_partner_api_returns_registry_owners_next_to_the_declaration(self):
        url = reverse("partner-activity", args=["act-9"])
        before = self.client.get(url).json()["parcels"]["features"]
        self.assertTrue(all(f["properties"]["registry_owners"] is None for f in before))

        self.assertIn("Loaded 4 rows of vintage 2026", self.load(ROWS))
        features = {
            f["id"]: f["properties"] for f in self.client.get(url).json()["parcels"]["features"]
        }
        block = features[self.za93.pk]["registry_owners"]
        self.assertEqual(block["vintage"], 2026)
        self.assertEqual(block["cadastral_reference"], "45025000ZA0093")
        self.assertEqual(
            block["owners"],
            [
                {
                    "legal_name": "GFA DES VARENNES",
                    "siren": "424108801",
                    "legal_form": "GFA",
                    "person_group": "Personnes morales non remarquables",
                    "right_code": "P",
                    "right_label": "Propriétaire",
                    "suggested_owner_type": "legal_person",
                }
            ],
        )
        self.assertIsNone(block["matches_declared"])  # nothing declared yet
        # a French parcel without a legal-person holder: checked, nothing found
        self.assertEqual(features[self.private.pk]["registry_owners"]["owners"], [])
        # no French cadastral id: nothing to compare
        self.assertIsNone(features[self.foreign.pk]["registry_owners"])

        parcel = self.client.get(reverse("partner-parcel", args=["p-93"])).json()
        self.assertEqual(parcel["properties"]["registry_owners"]["owners"][0]["siren"], "424108801")
        search = self.client.post(
            reverse("partner-parcel-search"), {"geometry": SEARCH_AREA}, format="json"
        ).json()
        self.assertEqual(
            {
                f["id"]: bool(f["properties"]["registry_owners"]["owners"])
                for f in search["features"]
                if f["properties"]["registry_owners"]
            },
            {self.za93.pk: True, self.private.pk: False},
        )

    def test_a_new_vintage_replaces_the_old_one(self):
        from django.core.management.base import CommandError

        from apps.parcels.models import RegistryOwner

        self.load(ROWS, vintage=2025)
        self.load(ROWS[:1], vintage=2026)
        self.assertEqual(list(RegistryOwner.objects.values_list("vintage", flat=True)), [2026])
        with self.assertRaises(CommandError):
            self.load([])  # an empty file does not wipe what is loaded
        self.assertEqual(RegistryOwner.objects.count(), 1)
