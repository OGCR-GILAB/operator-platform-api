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
