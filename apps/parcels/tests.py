from datetime import date

from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.operators.models import Operator, OperatorMembership
from apps.projects.models import Project

from .models import Parcel, ProjectParcel

# ~1 ha square near Berlin, as a plain Polygon
SQUARE = {
    "type": "Polygon",
    "coordinates": [
        [
            [13.4050, 52.5200],
            [13.4065, 52.5200],
            [13.4065, 52.5209],
            [13.4050, 52.5209],
            [13.4050, 52.5200],
        ]
    ],
}


class ParcelBase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", dcr_user_id="dcr-user-1")
        self.operator = Operator.objects.create(
            legal_name="Agreena", email="c@example.org", country_code="DE"
        )
        OperatorMembership.objects.create(operator=self.operator, user=self.user)
        self.foreign = Operator.objects.create(
            legal_name="Other", email="o@example.org", country_code="FR"
        )
        self.client.force_authenticate(self.user)

    def create_parcel(self, **extra):
        payload = {
            "operator": self.operator.pk,
            "name": "Field 1",
            "geometry": SQUARE,
            "iacs_codes": ["DE-1"],
        }
        payload.update(extra)
        return self.client.post(reverse("parcel-list"), payload, format="json")


class ParcelTests(ParcelBase):
    def test_create_computes_area_and_promotes_polygon(self):
        response = self.create_parcel()
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["geometry"]["type"], "MultiPolygon")
        self.assertAlmostEqual(body["area_ha"], 1.0, delta=0.1)
        self.assertEqual(body["dcr_sync_status"], "local")

    def test_cannot_create_for_foreign_operator(self):
        response = self.create_parcel(operator=self.foreign.pk)
        self.assertEqual(response.status_code, 400)

    def test_geojson_collection(self):
        self.create_parcel()
        response = self.client.get(reverse("parcel-geojson"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["type"], "FeatureCollection")
        self.assertEqual(len(response.json()["features"]), 1)

    def test_owner_verification_subresource(self):
        parcel_id = self.create_parcel().json()["id"]
        url = reverse("parcel-owner-verification", args=[parcel_id])
        response = self.client.put(
            url, {"status_code": "verified", "authority": "Cadastre FR"}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.client.get(url).json()["authority"], "Cadastre FR")


class ProjectParcelLinkTests(ParcelBase):
    def setUp(self):
        super().setUp()
        self.project = Project.objects.create(
            owner=self.user,
            operator=self.operator,
            name="P",
            start_date=date(2027, 1, 1),
            end_date=date(2028, 1, 1),
        )
        self.parcel = Parcel.objects.get(pk=self.create_parcel().json()["id"])

    def test_link_and_unlink(self):
        url = reverse("project-parcels", args=[self.project.pk])
        response = self.client.post(url, {"parcel": self.parcel.pk, "amount": 6}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["status_code"], "in_progress")
        self.assertEqual(response.json()["parcel_detail"]["name"], "Field 1")
        self.assertEqual(len(self.client.get(url).json()), 1)
        # duplicate link is rejected
        self.assertEqual(
            self.client.post(url, {"parcel": self.parcel.pk}, format="json").status_code, 400
        )
        # parcel cannot be deleted while linked
        self.assertEqual(
            self.client.delete(reverse("parcel-detail", args=[self.parcel.pk])).status_code, 409
        )
        response = self.client.delete(
            reverse("project-unlink-parcel", args=[self.project.pk, self.parcel.pk])
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(ProjectParcel.objects.exists())

    def test_parcel_of_other_operator_cannot_be_linked(self):
        other = Parcel.objects.create(operator=self.foreign, geometry=self.parcel.geometry)
        url = reverse("project-parcels", args=[self.project.pk])
        self.assertEqual(
            self.client.post(url, {"parcel": other.pk}, format="json").status_code, 400
        )


class RegistryOwnersTests(ParcelBase):
    def setUp(self):
        super().setUp()
        from .models import RegistryOwner

        self.parcel = Parcel.objects.create(
            operator=self.operator,
            cadastral_reference="45025000ZA0093",
            geometry="SRID=4326;MULTIPOLYGON(((1 47,1.01 47,1.01 47.01,1 47)))",
        )
        self.url = reverse("parcel-registry-owners", args=[self.parcel.pk])
        common = {"idu": "45025000ZA0093", "vintage": 2026, "right_label": ""}
        RegistryOwner.objects.create(
            **common,
            right_code="P",
            legal_name="GFA DES VARENNES",
            legal_form="GFA",
            person_group="Personnes morales non remarquables",
        )
        RegistryOwner.objects.create(
            **common, right_code="G", legal_name="COMMUNE DE BAZOCHES", person_group="Commune"
        )

    def test_registry_owners_with_suggestion_and_match(self):
        from .models import ParcelOwner

        body = self.client.get(self.url).json()
        self.assertEqual(body["vintage"], 2026)
        self.assertEqual(
            [(o["legal_name"], o["suggested_owner_type"]) for o in body["owners"]],
            [("COMMUNE DE BAZOCHES", "public_entity"), ("GFA DES VARENNES", "legal_person")],
        )
        self.assertIsNone(body["matches_declared"])

        owner = ParcelOwner.objects.create(
            parcel=self.parcel,
            legal_name="Gfa des Varennes.",
            owner_type="legal_person",
            country_code="FR",
        )
        self.assertTrue(self.client.get(self.url).json()["matches_declared"])
        # only owners (right P) count, not a manager
        owner.legal_name = "Commune de Bazoches"
        owner.save()
        self.assertFalse(self.client.get(self.url).json()["matches_declared"])

    def test_no_registry_data(self):
        other = Parcel.objects.create(
            operator=self.operator,
            cadastral_reference="DE-123",
            geometry="SRID=4326;MULTIPOLYGON(((1 47,1.01 47,1.01 47.01,1 47)))",
        )
        url = reverse("parcel-registry-owners", args=[other.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        foreign = Parcel.objects.create(
            operator=self.foreign,
            cadastral_reference="45025000ZA0093",
            geometry="SRID=4326;MULTIPOLYGON(((1 47,1.01 47,1.01 47.01,1 47)))",
        )
        url = reverse("parcel-registry-owners", args=[foreign.pk])
        self.assertEqual(self.client.get(url).status_code, 404)  # another operator's parcel

    def test_list_rows_carry_owner_completeness_and_registry_match(self):
        from .models import ParcelOwner

        project = Project.objects.create(owner=self.user, operator=self.operator, name="P")
        ProjectParcel.objects.create(project=project, parcel=self.parcel)
        plain = Parcel.objects.create(
            operator=self.operator,
            cadastral_reference="45025000ZA0076",
            geometry="SRID=4326;MULTIPOLYGON(((1 47,1.01 47,1.01 47.01,1 47)))",
        )

        def rows():
            return {r["id"]: r for r in self.client.get(reverse("parcel-list")).json()["results"]}

        row = rows()[self.parcel.pk]
        self.assertIsNone(row["owner_is_complete"])
        self.assertIsNone(row["registry_match"])
        ParcelOwner.objects.create(
            parcel=self.parcel,
            legal_name="GFA des Varennes",
            owner_type="legal_person",
            country_code="FR",
        )
        ParcelOwner.objects.create(
            parcel=plain, legal_name="J. Dupont", owner_type="natural_person", country_code="FR"
        )
        found = rows()
        self.assertFalse(found[self.parcel.pk]["owner_is_complete"])  # no deed yet
        self.assertTrue(found[self.parcel.pk]["registry_match"])
        self.assertIsNone(found[plain.pk]["registry_match"])  # no legal-person owner to compare
        links = self.client.get(reverse("project-parcels", args=[project.pk])).json()
        self.assertTrue(links[0]["parcel_detail"]["registry_match"])
        self.assertFalse(links[0]["parcel_detail"]["owner_is_complete"])

    def test_overseas_parcels_are_not_covered(self):
        overseas = Parcel.objects.create(
            operator=self.operator,
            cadastral_reference="97101000CR0087",
            geometry="SRID=4326;MULTIPOLYGON(((1 47,1.01 47,1.01 47.01,1 47)))",
        )
        url = reverse("parcel-registry-owners", args=[overseas.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
