from datetime import date
from unittest import mock

from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.dcr.exceptions import DCRError
from apps.operators.models import Operator, OperatorMembership
from apps.parcels.models import Parcel, ParcelOwnerVerification, ProjectParcel

from .models import ActivityPlan, MonitoringPlan, Project

SQUARE = {
    "type": "Polygon",
    "coordinates": [[[13.405, 52.52], [13.406, 52.52], [13.406, 52.521], [13.405, 52.52]]],
}


class ProjectBase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", dcr_user_id="dcr-user-1")
        self.other = User.objects.create_user(username="bob", dcr_user_id="dcr-user-2")
        self.operator = Operator.objects.create(
            legal_name="Agreena", email="c@example.org", country_code="DE"
        )
        OperatorMembership.objects.create(operator=self.operator, user=self.user)
        self.client.force_authenticate(self.user)

    def make_project(self, **extra):
        defaults = dict(
            owner=self.user,
            operator=self.operator,
            name="Forest A",
            start_date=date(2027, 1, 1),
            end_date=date(2031, 12, 31),
        )
        defaults.update(extra)
        return Project.objects.create(**defaults)


class ProjectCrudTests(ProjectBase):
    def test_create_with_polygon_geometry(self):
        response = self.client.post(
            reverse("project-list"),
            {
                "name": "Forest A",
                "operator": self.operator.pk,
                "geometry": SQUARE,
                "cobenefits": ["biodiversity"],
                "start_date": "2027-01-01",
                "end_date": "2031-12-31",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["status"], "draft")
        self.assertEqual(body["dcr_sync_status"], "local")
        self.assertEqual(body["geometry"]["type"], "MultiPolygon")
        self.assertFalse(body["has_plan"])

    def test_cannot_use_foreign_operator(self):
        foreign = Operator.objects.create(legal_name="X", email="x@example.org", country_code="FR")
        response = self.client.post(
            reverse("project-list"), {"name": "P", "operator": foreign.pk}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_plan_subresource(self):
        project = self.make_project()
        url = reverse("project-plan", args=[project.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        response = self.client.put(
            url, {"expected_net_benefit": 6, "iacs_codes": ["DE-1"]}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.content)
        response = self.client.patch(url, {"expected_net_benefit": 7}, format="json")
        self.assertEqual(response.json()["expected_net_benefit"], 7)
        self.assertTrue(
            self.client.get(reverse("project-detail", args=[project.pk])).json()["has_plan"]
        )

    def test_only_own_projects_listed(self):
        Project.objects.create(owner=self.other, name="Not mine")
        self.make_project()
        names = [row["name"] for row in self.client.get(reverse("project-list")).json()["results"]]
        self.assertEqual(names, ["Forest A"])


class SubmitTests(ProjectBase):
    def auth_header(self):
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION='DirectLogin token="tok-1"')

    def fake_client(self):
        client = mock.MagicMock()
        counter = {"n": 0}

        def create(entity, payload, token):
            counter["n"] += 1
            return {f"{entity}_id": f"{entity}-{counter['n']}", **payload}

        client.create_entity.side_effect = create
        client.update_entity.side_effect = lambda e, i, p, t: {f"{e}_id": i, **p}
        client.entity_id_from.side_effect = lambda data, entity: data.get(f"{entity}_id")
        client.entity_fields.return_value = None
        client.entity_exists.return_value = None
        client.current_user.return_value = {"user_id": "dcr-user-1", "username": "alice"}
        return client

    def test_missing_fields_are_reported(self):
        project = self.make_project(start_date=None)
        response = self.client.post(reverse("project-submit", args=[project.pk]))
        self.assertEqual(response.status_code, 400)
        self.assertIn("Fill start_date", response.json()["problems"])

    @mock.patch("apps.dcr.authentication.get_client")
    @mock.patch("apps.projects.services.get_client")
    def test_submit_pushes_entities_in_order(self, services_client, auth_client):
        client = self.fake_client()
        services_client.return_value = client
        auth_client.return_value = client
        project = self.make_project(geometry=None)
        ActivityPlan.objects.create(
            project=project,
            methodology_quantification_baseline="Baseline per CRCF method",
            expected_total_carbon_removals=10,
            expected_total_soil_emissions=2,
            expected_total_ghg_emissions_associated=2,
            expected_net_benefit=6,
        )
        parcel = Parcel.objects.create(
            operator=self.operator,
            name="Field",
            geometry=(
                "SRID=4326;MULTIPOLYGON(((13.405 52.52,13.406 52.52,13.406 52.521,13.405 52.52)))"
            ),
        )
        ParcelOwnerVerification.objects.create(
            parcel=parcel, status_code="verified", authority="Cadastre"
        )
        ProjectParcel.objects.create(project=project, parcel=parcel, amount=6)
        self.auth_header()

        response = self.client.post(reverse("project-submit", args=[project.pk]))
        self.assertEqual(response.status_code, 200, response.content)
        created = [call.args[0] for call in client.create_entity.call_args_list]
        self.assertEqual(
            created,
            [
                "operator",
                "user_operator_relationship",
                "activity",
                "activity_plan",
                "parcel",
                "activity_parcel_verification",  # owner verification is the certifier's
            ],
        )
        project.refresh_from_db()
        parcel.refresh_from_db()
        payloads = {c.args[0]: c.args[1] for c in client.create_entity.call_args_list}
        # ids are generated by us and sent on create
        self.assertTrue(project.dcr_id.startswith("act_"))
        self.assertEqual(payloads["activity"]["activity_id"], project.dcr_id)
        self.assertEqual(payloads["activity"]["operator_id"], project.operator.dcr_id)
        self.assertTrue(project.operator.dcr_id.startswith("op_"))
        self.assertEqual(payloads["activity_plan"]["activity_id"], project.dcr_id)
        self.assertEqual(payloads["activity_parcel_verification"]["parcel_id"], parcel.dcr_id)
        self.assertEqual(payloads["activity_parcel_verification"]["activity_id"], project.dcr_id)
        self.assertEqual(payloads["activity_parcel_verification"]["status_code"], "in_progress")
        self.assertEqual(payloads["parcel"]["coordinate_reference_system"], "EPSG:4326")
        # schema unknown -> the legacy activity back-reference update still happens
        updated = [c.args[0] for c in client.update_entity.call_args_list]
        self.assertEqual(updated, ["activity"])
        self.assertEqual(
            client.update_entity.call_args_list[0].args[2]["activity_plan_id"], project.plan.dcr_id
        )
        self.assertEqual(project.status, "submitted")
        self.assertEqual(project.plan.dcr_sync_status, "synced")

    @mock.patch("apps.dcr.authentication.get_client")
    @mock.patch("apps.projects.services.get_client")
    def test_payload_is_trimmed_to_the_live_schema(self, services_client, auth_client):
        client = self.fake_client()
        schemas = {
            "activity": {
                "activity_id",
                "name",
                "operator_id",
                "start_date",
                "end_date",
                "type",
                "certification_scheme_id",
            },
            "monitoring_plan": {"monitoring_plan_id", "activity_id", "monitoring_frequency"},
        }
        client.entity_fields.side_effect = lambda entity: schemas.get(entity)
        services_client.return_value = client
        auth_client.return_value = client
        project = self.make_project(
            geometry=None, certification_scheme_id="cs-1", certification_scheme_name="Scheme"
        )
        ActivityPlan.objects.create(
            project=project,
            methodology_quantification_baseline="Baseline",
            expected_total_carbon_removals=10,
            expected_total_soil_emissions=2,
            expected_total_ghg_emissions_associated=2,
            expected_net_benefit="6.5",
        )
        MonitoringPlan.objects.create(
            project=project,
            monitoring_frequency=12,
            monitored_data_parameters=[{"name": "soc", "unit": "%", "scope": "soil"}],
            measurement_methods_procedures_accuracy_calibration="x",
            quality_assessment_or_quality_control_procedures="x",
            responsibility_for_collection_and_archiving="x",
        )
        self.auth_header()

        response = self.client.post(reverse("project-submit", args=[project.pk]))
        self.assertEqual(response.status_code, 200, response.content)
        payloads = {c.args[0]: c.args[1] for c in client.create_entity.call_args_list}
        self.assertEqual(payloads["activity"]["certification_scheme_id"], "cs-1")
        self.assertNotIn("certification_scheme", payloads["activity"])
        self.assertNotIn("activity_plan_id", payloads["activity"])
        # new schema drops the back-reference: no second activity update
        self.assertEqual(client.update_entity.call_count, 0)
        self.assertEqual(
            payloads["monitoring_plan"]["activity_id"],
            project.refresh_from_db() or Project.objects.get(pk=project.pk).dcr_id,
        )
        self.assertEqual(payloads["activity_plan"]["expected_net_benefit"], 6.5)

    @mock.patch("apps.dcr.authentication.get_client")
    @mock.patch("apps.projects.services.get_client")
    def test_dcr_failure_marks_step_and_keeps_project_editable(self, services_client, auth_client):
        client = self.fake_client()
        client.create_entity.side_effect = [
            {"operator_id": "operator-1"},
            {"user_operator_relationship_id": "rel-1"},
            DCRError("OBP-20006: User is missing role CanCreateDynamicEntity_Systemactivity", 403),
        ]
        services_client.return_value = client
        auth_client.return_value = client
        project = self.make_project()
        self.auth_header()

        response = self.client.post(reverse("project-submit", args=[project.pk]))
        self.assertEqual(response.status_code, 502, response.content)
        self.assertEqual(response.json()["step"], "activity")
        project.refresh_from_db()
        self.assertEqual(project.status, "draft")
        self.assertEqual(project.dcr_sync_status, "failed")
        self.assertEqual(project.operator.dcr_sync_status, "synced")


class ReadinessTests(ProjectBase):
    def test_readiness_reports_errors_and_warnings(self):
        project = self.make_project(start_date=None)
        response = self.client.get(reverse("project-readiness", args=[project.pk]))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertFalse(body["ready"])
        self.assertIn("Fill start_date", body["errors"])
        self.assertIn("Fill the activity plan", body["warnings"])
        self.assertIn("Link at least one parcel", body["warnings"])
        project.start_date = date(2027, 1, 1)
        project.save()
        body = self.client.get(reverse("project-readiness", args=[project.pk])).json()
        self.assertTrue(body["ready"])
        self.assertEqual(body["errors"], [])
        self.assertTrue(any(c["code"] == "geometry" and not c["ok"] for c in body["checks"]))


class DCRStatusTests(ProjectBase):
    def auth_header(self):
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION='DirectLogin token="tok-1"')

    def test_not_submitted_is_409(self):
        project = self.make_project()
        self.assertEqual(
            self.client.post(reverse("project-dcr-status", args=[project.pk])).status_code, 409
        )
        snapshot = self.client.get(reverse("project-dcr-status", args=[project.pk])).json()
        self.assertEqual(snapshot["status"], "draft")
        self.assertIsNone(snapshot["checked_at"])

    @mock.patch("apps.dcr.authentication.get_client")
    @mock.patch("apps.projects.services.get_client")
    def test_refresh_mirrors_verification(self, services_client, auth_client):
        project = self.make_project(status="submitted", dcr_id="act-1")
        parcel = Parcel.objects.create(
            operator=self.operator,
            name="Field",
            dcr_id="par-1",
            geometry=(
                "SRID=4326;MULTIPOLYGON(((13.405 52.52,13.406 52.52,13.406 52.521,13.405 52.52)))"
            ),
        )
        link = ProjectParcel.objects.create(project=project, parcel=parcel, dcr_id="apv-1")
        client = mock.MagicMock()
        client.current_user.return_value = {"user_id": "dcr-user-1", "username": "alice"}
        client.entity_exists.return_value = None

        def list_entities(entity, token, params=None):
            return {
                "activity_verification": [
                    {"activity_id": "other", "status_code": "failed"},
                    {
                        "activity_id": "act-1",
                        "status_code": "verified",
                        "status_message": "ok",
                        "permanent_net_carbon_removal_benefit": 12,
                    },
                ],
                "activity_parcel_verification": [
                    {
                        "activity_parcel_verification_id": "apv-1",
                        "activity_id": "act-1",
                        "parcel_id": "par-1",
                        "status_code": "verified",
                        "amount": 7,
                    }
                ],
                "parcel_owner_verification": [
                    {
                        "parcel_owner_verification_id": "pov-9",
                        "parcel_id": "par-1",
                        "status_code": "verified",
                        "authority": "Cadastre FR",
                        "parcel_owner_legal_name": "J. Dupont",
                    }
                ],
            }[entity]

        client.list_entities.side_effect = list_entities
        services_client.return_value = client
        auth_client.return_value = client
        self.auth_header()

        response = self.client.post(reverse("project-dcr-status", args=[project.pk]))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["status"], "accepted")
        self.assertEqual(body["verification"]["permanent_net_carbon_removal_benefit"], 12)
        self.assertEqual(body["parcels"][0]["status_code"], "verified")
        self.assertEqual(body["parcels"][0]["amount"], 7)
        self.assertEqual(body["parcels"][0]["owner_verification"]["authority"], "Cadastre FR")
        self.assertIsNotNone(body["checked_at"])
        link.refresh_from_db()
        self.assertEqual(link.amount, 7)
        self.assertEqual(parcel.owner_verification.dcr_id, "pov-9")

    @mock.patch("apps.dcr.authentication.get_client")
    @mock.patch("apps.projects.services.get_client")
    def test_missing_role_is_502_with_step(self, services_client, auth_client):
        project = self.make_project(status="submitted", dcr_id="act-1")
        client = mock.MagicMock()
        client.current_user.return_value = {"user_id": "dcr-user-1", "username": "alice"}
        client.list_entities.side_effect = DCRError("OBP-20006: missing role", 403)
        services_client.return_value = client
        auth_client.return_value = client
        self.auth_header()
        response = self.client.post(reverse("project-dcr-status", args=[project.pk]))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["step"], "activity_verification")


class VocabularyTests(ProjectBase):
    def test_unit_type_sets_activity_type_and_country_is_validated(self):
        response = self.client.post(
            reverse("project-list"),
            {"name": "P", "operator": self.operator.pk, "unit_types": "Permanent Removal"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["type"], "PERMANENT_REMOVAL")
        response = self.client.post(
            reverse("project-list"),
            {"name": "P", "unit_types": "Permanent Removal", "type": "CARBON_FARMING"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            reverse("project-list"), {"name": "P", "country_code": "XX"}, format="json"
        )
        self.assertEqual(response.status_code, 400)


class LinkEntityTests(ProjectBase):
    @mock.patch("apps.dcr.authentication.get_client")
    @mock.patch("apps.projects.services.get_client")
    def test_new_parcel_activity_entity_is_used_when_declared(self, services_client, auth_client):
        client = SubmitTests.fake_client(self)
        client.entity_exists.side_effect = lambda e: e == "parcel_activity"
        services_client.return_value = client
        auth_client.return_value = client
        project = self.make_project(geometry=None)
        parcel = Parcel.objects.create(
            operator=self.operator,
            name="Field",
            geometry=(
                "SRID=4326;MULTIPOLYGON(((13.405 52.52,13.406 52.52,13.406 52.521,13.405 52.52)))"
            ),
        )
        ProjectParcel.objects.create(project=project, parcel=parcel)
        SubmitTests.auth_header(self)
        response = self.client.post(reverse("project-submit", args=[project.pk]))
        self.assertEqual(response.status_code, 200, response.content)
        created = [c.args[0] for c in client.create_entity.call_args_list]
        self.assertIn("parcel_activity", created)
        self.assertNotIn("activity_parcel_verification", created)
        payload = {c.args[0]: c.args[1] for c in client.create_entity.call_args_list}[
            "parcel_activity"
        ]
        self.assertEqual(payload["activity_type"], "CARBON_FARMING")
        self.assertTrue(payload["parcel_activity_id"].startswith("pa_"))
