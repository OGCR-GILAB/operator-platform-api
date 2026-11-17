from datetime import date
from unittest import mock

from django.urls import reverse
from django.utils import timezone
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

    @mock.patch("apps.dcr.authentication.get_client")
    @mock.patch("apps.projects.services.get_client")
    def test_submit_stops_when_dcr_reports_missing_roles(self, services_client, auth_client):
        client = self.fake_client()
        client.my_role.side_effect = lambda token, role, bank: {
            "role_name": role,
            "held_by": {
                "user": role != "CanCreateDynamicEntityRecord_activity_plan",
                "consumer": False,
            },
        }
        services_client.return_value = client
        auth_client.return_value = client
        project = self.make_project()
        self.auth_header()
        response = self.client.post(reverse("project-submit", args=[project.pk]))
        self.assertEqual(response.status_code, 403, response.content)
        body = response.json()
        self.assertEqual(body["step"], "permissions")
        self.assertEqual(body["missing_roles"], ["CanCreateDynamicEntityRecord_activity_plan"])
        client.create_entity.assert_not_called()

        readiness = self.client.get(reverse("project-readiness", args=[project.pk])).json()
        self.assertIn("dcr.roles", [c["code"] for c in readiness["checks"] if not c["ok"]])

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
            cadastral_reference="45025000ZA0090",
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
                # parcels and their links are registered by the certification platform
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
        self.assertFalse(parcel.dcr_id)
        # no drawn boundary: the activity covers its parcels
        self.assertEqual(payloads["activity"]["multipolygon_coordinates"]["type"], "MultiPolygon")
        # schema unknown -> the legacy activity back-reference update still happens
        updated = [c.args[0] for c in client.update_entity.call_args_list]
        self.assertEqual(updated, ["activity"])
        self.assertEqual(
            client.update_entity.call_args_list[0].args[2]["activity_plan_id"], project.plan.dcr_id
        )
        self.assertEqual(project.status, "submitted")
        self.assertEqual(project.plan.dcr_sync_status, "synced")
        # the submission date is stored at the first submit and sent from then on
        project.plan.refresh_from_db()
        self.assertIsNotNone(project.plan.date_submitted)
        self.assertEqual(
            payloads["activity_plan"]["date_submitted"], project.plan.date_submitted.isoformat()
        )

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
            cadastral_reference="45025000ZA0090",
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
                "certificate_of_compliance": [
                    {"activity_id": "other", "certification_status": "certified"},
                    {
                        "certificate_of_compliance_id": "coc-old",
                        "activity_id": "act-1",
                        "certification_status": "expired",
                        "issue_date": "2025-01-10",
                    },
                    {
                        "certificate_of_compliance_id": "coc-new",
                        "activity_id": "act-1",
                        "certification_status": "certified",
                        "issue_date": "2026-10-01",
                        "expiry_date": "2031-09-30",
                    },
                ],
                "audit_report": [
                    {"audit_report_id": "ar-1", "activity_id": "act-1", "audit_date": "2026-09-20"}
                ],
            }.get(entity, [])

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
        self.assertEqual(body["parcels"][0]["name"], "Field")
        self.assertTrue(body["parcels"][0]["has_verdict"])
        self.assertEqual(body["parcels"][0]["owner_verification"]["authority"], "Cadastre FR")
        self.assertIsNotNone(body["checked_at"])
        self.assertEqual(body["certificate"]["certificate_of_compliance_id"], "coc-new")
        self.assertEqual(
            [c["certificate_of_compliance_id"] for c in body["certificates"]],
            ["coc-new", "coc-old"],
        )
        self.assertEqual(body["audit_reports"][0]["audit_report_id"], "ar-1")
        link.refresh_from_db()
        self.assertEqual(link.amount, 7)
        self.assertEqual(parcel.owner_verification.dcr_id, "pov-9")

        # certificates are optional reads: a DCR error keeps what was mirrored before
        def without_certificates(entity, token, params=None):
            if entity in ("certificate_of_compliance", "audit_report"):
                raise DCRError("OBP-20006: missing role", 403)
            return list_entities(entity, token, params)

        client.list_entities.side_effect = without_certificates
        body = self.client.post(reverse("project-dcr-status", args=[project.pk])).json()
        self.assertEqual(body["certificate"]["certificate_of_compliance_id"], "coc-new")
        self.assertEqual(len(body["audit_reports"]), 1)

    @mock.patch("apps.dcr.authentication.get_client")
    @mock.patch("apps.projects.services.get_client")
    def test_pending_verification_keeps_submitted(self, services_client, auth_client):
        project = self.make_project(status="submitted", dcr_id="act-2")
        client = mock.MagicMock()
        client.current_user.return_value = {"user_id": "dcr-user-1", "username": "alice"}
        client.list_entities.side_effect = lambda entity, token, params=None: (
            [{"activity_id": "act-2", "status_code": "under_review"}]
            if entity == "activity_verification"
            else []
        )
        services_client.return_value = client
        auth_client.return_value = client
        self.auth_header()
        body = self.client.post(reverse("project-dcr-status", args=[project.pk])).json()
        self.assertEqual(body["status"], "submitted")
        self.assertEqual(body["verification"]["status_code"], "under_review")
        self.assertIsNone(body["certificate"])

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


class RegistryParcelTests(ProjectBase):
    def make_parcel(self, reference, **extra):
        return Parcel.objects.create(
            operator=self.operator,
            name=reference,
            cadastral_reference=reference,
            geometry=(
                "SRID=4326;MULTIPOLYGON(((13.405 52.52,13.406 52.52,13.406 52.521,13.405 52.52)))"
            ),
            **extra,
        )

    @mock.patch("apps.dcr.authentication.get_client")
    @mock.patch("apps.projects.services.get_client")
    def test_submit_sends_no_parcels(self, services_client, auth_client):
        client = SubmitTests.fake_client(self)
        client.entity_exists.side_effect = lambda e: e == "parcel_activity"
        services_client.return_value = client
        auth_client.return_value = client
        project = self.make_project(geometry=None)
        ProjectParcel.objects.create(project=project, parcel=self.make_parcel("45025000ZA0090"))
        SubmitTests.auth_header(self)
        response = self.client.post(reverse("project-submit", args=[project.pk]))
        self.assertEqual(response.status_code, 200, response.content)
        touched = {c.args[0] for c in client.create_entity.call_args_list} | {
            c.args[0] for c in client.update_entity.call_args_list
        }
        self.assertFalse(touched & {"parcel", "parcel_activity", "activity_parcel_verification"})

    @mock.patch("apps.dcr.authentication.get_client")
    @mock.patch("apps.projects.services.get_client")
    def test_refresh_adopts_parcels_the_certifier_registered(self, services_client, auth_client):
        project = self.make_project(status="submitted", dcr_id="act-1")
        ours = self.make_parcel("45025000ZA0090")
        already = self.make_parcel("45025000ZA0093", dcr_id="cert_p_93")
        for parcel in (ours, already):
            ProjectParcel.objects.create(project=project, parcel=parcel)
        client = mock.MagicMock()
        client.current_user.return_value = {"user_id": "dcr-user-1", "username": "alice"}
        client.entity_exists.side_effect = lambda e: e == "parcel_activity"
        registry = {
            "cert_p_90": {"parcel_id": "cert_p_90", "cadastral_reference": "45025000ZA0090"},
            "cert_p_93": {"parcel_id": "cert_p_93", "cadastral_reference": "45025000ZA0093"},
            "cert_p_xx": {"parcel_id": "cert_p_xx", "cadastral_reference": "41084000AI0308"},
        }

        def list_entities(entity, token, params=None):
            if entity == "parcel_activity":
                return [
                    {"activity_id": "act-1", "parcel_id": "cert_p_90"},
                    {"activity_id": "act-1", "parcel_id": "cert_p_93"},
                    {"activity_id": "act-1", "parcel_id": "cert_p_xx"},
                    {"activity_id": "other", "parcel_id": "cert_p_90"},
                ]
            if entity == "activity_verification":
                return [
                    {"activity_id": "act-1", "status_code": "under_review"},
                    {"activity_id": "act-1", "parcel_id": "cert_p_90", "status_code": "verified"},
                ]
            return []

        def get_entity(entity, record_id, token):
            if entity == "parcel" and record_id in registry:
                return dict(registry[record_id])
            if entity == "activity" and record_id == "act-1":
                return {"activity_id": "act-1"}
            raise DCRError("OBP-09005: Entity not found", 404)

        client.list_entities.side_effect = list_entities
        client.get_entity.side_effect = get_entity
        services_client.return_value = client
        auth_client.return_value = client
        SubmitTests.auth_header(self)

        body = self.client.post(reverse("project-dcr-status", args=[project.pk])).json()
        ours.refresh_from_db()
        self.assertEqual(ours.dcr_id, "cert_p_90")
        self.assertEqual(ours.dcr_sync_status, "synced")
        rows = {row["cadastral_reference"]: row for row in body["parcels"]}
        self.assertEqual(rows["45025000ZA0090"]["status_code"], "verified")
        # a parcel of the certifier that we do not have is ignored
        self.assertEqual(Parcel.objects.filter(dcr_id="cert_p_xx").count(), 0)


VOCABULARIES = {
    "technologies_practices_processes": {"AGROFORESTRY", "COVER_CROPPING"},
    "co_benefits": {"sample"},
    "sustainable_development_goals": {"CLIMATE_ACTION", "LIFE_ON_LAND"},
}


@mock.patch(
    "apps.projects.dcr_links.vocabulary_ids", side_effect=lambda name: VOCABULARIES.get(name)
)
class LinkSyncTests(ProjectBase):
    def fake_client(self, entities=None):
        client = mock.MagicMock()
        declared = entities or {
            "activity_technologies_practices_processes",
            "activity_co_benefit",
            "activity_sustainable_development_goal",
            "activity_media",
        }
        client.entity_exists.side_effect = lambda e: e in declared
        client.entity_fields.return_value = None
        counter = {"n": 0}

        def create(entity, payload, token):
            counter["n"] += 1
            if entity == "activity_media":
                return {entity: payload}
            return {entity: {f"{entity}_id": f"uuid-{counter['n']}", **payload}}

        client.create_entity.side_effect = create
        client.entity_id_from.side_effect = lambda data, entity: data[entity].get(f"{entity}_id")
        return client

    def test_links_follow_the_project(self, _vocabularies):
        from . import dcr_links

        project = self.make_project(
            dcr_id="act-1",
            technologies_practices_processes=["AGROFORESTRY", "my own practice"],
            cobenefits=["biodiversity"],
            sustainable_development_goals=["CLIMATE_ACTION"],
            media_links=[
                "https://example.org/video",
                {"link": "https://example.org/article", "title": "Article", "type": "article"},
            ],
        )
        client = self.fake_client()
        dcr_links.sync_links(client, "tok", project)

        created = [(c.args[0], c.args[1]) for c in client.create_entity.call_args_list]
        entities = [entity for entity, _ in created]
        self.assertEqual(
            entities,
            [
                "activity_technologies_practices_processes",
                "activity_sustainable_development_goal",
                "activity_media",
                "activity_media",
            ],
        )
        # only DCR vocabulary ids become links; free text stays on the activity
        self.assertEqual(
            created[0][1],
            {"activity_id": "act-1", "technologies_practices_processes_id": "AGROFORESTRY"},
        )
        article = created[3][1]
        self.assertEqual(article["title"], "Article")
        self.assertEqual(article["activity_media_type"], "article")
        self.assertTrue(article["activity_media_id"].startswith("am_"))
        project.refresh_from_db()
        self.assertEqual(
            project.dcr_links["activity_technologies_practices_processes"],
            {"AGROFORESTRY": "uuid-1"},
        )
        self.assertEqual(len(project.dcr_links["activity_media"]), 2)

        # resubmit: one practice swapped, one SDG removed, the article retitled
        project.technologies_practices_processes = ["COVER_CROPPING"]
        project.sustainable_development_goals = []
        project.media_links = [
            {"link": "https://example.org/article", "title": "Article v2", "type": "article"}
        ]
        project.save()
        client = self.fake_client()
        dcr_links.sync_links(client, "tok", project)
        deleted = [(c.args[0], c.args[1]) for c in client.delete_entity.call_args_list]
        self.assertIn(("activity_technologies_practices_processes", "uuid-1"), deleted)
        self.assertIn(("activity_sustainable_development_goal", "uuid-2"), deleted)
        self.assertEqual(sum(1 for e, _ in deleted if e == "activity_media"), 1)
        updated = client.update_entity.call_args_list
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0].args[2]["title"], "Article v2")
        project.refresh_from_db()
        self.assertEqual(
            list(project.dcr_links["activity_technologies_practices_processes"]),
            ["COVER_CROPPING"],
        )
        self.assertEqual(project.dcr_links["activity_sustainable_development_goal"], {})

    def test_singular_entity_names_are_picked_up(self, _vocabularies):
        from . import dcr_links

        project = self.make_project(
            dcr_id="act-1",
            technologies_practices_processes=["AGROFORESTRY"],
            dcr_links={
                "activity_technologies_practices_processes": {"AGROFORESTRY": "old"},
                "sustainable_development_goals": {"CLIMATE_ACTION": "older"},
            },
        )
        client = self.fake_client(
            {"activity_technology_practice_process", "activity_sustainable_development_goal"}
        )
        client.entity_fields.side_effect = lambda e: {
            "activity_technology_practice_process": {
                "activity_id",
                "technology_practice_process_id",
            }
        }.get(e)
        dcr_links.sync_links(client, "tok", project)
        created = [(c.args[0], c.args[1]) for c in client.create_entity.call_args_list]
        self.assertEqual(
            created,
            [
                (
                    "activity_technology_practice_process",
                    {"activity_id": "act-1", "technology_practice_process_id": "AGROFORESTRY"},
                )
            ],
        )
        client.delete_entity.assert_not_called()  # records under the old names went with them
        project.refresh_from_db()
        self.assertEqual(set(project.dcr_links), {"activity_technology_practice_process"})

    def test_failure_keeps_what_was_created(self, _vocabularies):
        from . import dcr_links

        project = self.make_project(
            dcr_id="act-1",
            technologies_practices_processes=["AGROFORESTRY"],
            sustainable_development_goals=["CLIMATE_ACTION"],
        )
        client = self.fake_client()
        create = client.create_entity.side_effect

        def failing(entity, payload, token):
            if entity == "activity_sustainable_development_goal":
                raise DCRError("OBP-20006: missing role", 403)
            return create(entity, payload, token)

        client.create_entity.side_effect = failing
        with self.assertRaises(dcr_links.LinkError) as ctx:
            dcr_links.sync_links(client, "tok", project)
        self.assertEqual(ctx.exception.step, "activity_sustainable_development_goal")
        project.refresh_from_db()
        self.assertIn(
            "AGROFORESTRY", project.dcr_links["activity_technologies_practices_processes"]
        )

    def test_readiness_warns_about_values_outside_the_vocabulary(self, _vocabularies):
        project = self.make_project(
            technologies_practices_processes=["AGROFORESTRY", "my own practice"]
        )
        body = self.client.get(reverse("project-readiness", args=[project.pk])).json()
        warning = next(
            c for c in body["checks"] if c["code"] == "technologies_practices_processes.vocabulary"
        )
        self.assertFalse(warning["ok"])
        self.assertIn("my own practice", warning["message"])

    def test_list_fields_are_validated(self, _vocabularies):
        project = self.make_project()
        url = reverse("project-detail", args=[project.pk])
        ok = self.client.patch(
            url,
            {
                "sustainable_development_goals": ["CLIMATE_ACTION", "CLIMATE_ACTION"],
                "media_links": [
                    "https://example.org/a",
                    {"link": "https://example.org/b", "published_date": "2026-10-01"},
                ],
            },
            format="json",
        )
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(ok.json()["sustainable_development_goals"], ["CLIMATE_ACTION"])
        self.assertNotIn("dcr_links", ok.json())
        bad = self.client.patch(
            url, {"media_links": [{"title": "no link"}, "not a url"]}, format="json"
        )
        self.assertEqual(bad.status_code, 400)
        bad = self.client.patch(url, {"cobenefits": "biodiversity"}, format="json")
        self.assertEqual(bad.status_code, 400)


class SyncDCRStatusCommandTests(ProjectBase):
    @mock.patch("apps.projects.management.commands.sync_dcr_status.get_client")
    @mock.patch("apps.projects.services.get_client")
    def test_submitted_projects_are_refreshed_with_the_service_account(
        self, services_client, command_client
    ):
        from io import StringIO

        from django.core.management import call_command

        command_client.return_value.service_token.return_value = "service-tok"
        client = mock.MagicMock()
        client.entity_exists.return_value = None
        client.list_entities.side_effect = lambda entity, token, params=None: (
            [{"activity_id": "act-1", "status_code": "verified"}]
            if entity == "activity_verification"
            else []
        )
        services_client.return_value = client
        submitted = self.make_project(status="submitted", dcr_id="act-1")
        draft = self.make_project(name="Draft", dcr_id="act-2")

        out = StringIO()
        call_command("sync_dcr_status", stdout=out, stderr=StringIO())
        submitted.refresh_from_db()
        draft.refresh_from_db()
        self.assertEqual(submitted.status, "accepted")
        self.assertIsNotNone(submitted.dcr_status_checked_at)
        self.assertIsNone(draft.dcr_status_checked_at)
        self.assertIn("1 checked, 1 changed, 0 failed", out.getvalue())
        tokens = {c.args[1] for c in client.list_entities.call_args_list}
        self.assertEqual(tokens, {"service-tok"})

        client.list_entities.side_effect = DCRError("down", 503)
        out, err = StringIO(), StringIO()
        call_command("sync_dcr_status", stdout=out, stderr=err)
        self.assertIn("0 checked, 0 changed, 1 failed", out.getvalue())
        self.assertIn("act-1", err.getvalue())


class ActivityBoundaryTests(ProjectBase):
    def test_parcels_outline_is_sent_when_no_boundary_is_drawn(self):
        from . import dcr_mapping

        project = self.make_project()
        self.assertNotIn("multipolygon_coordinates", dcr_mapping.activity_payload(project))
        for name, x in (("A", 13.405), ("B", 13.407)):
            parcel = Parcel.objects.create(
                operator=self.operator,
                name=name,
                geometry=(
                    f"SRID=4326;MULTIPOLYGON((({x} 52.52,{x + 0.001} 52.52,"
                    f"{x + 0.001} 52.521,{x} 52.52)))"
                ),
            )
            ProjectParcel.objects.create(project=project, parcel=parcel)
        geometry = dcr_mapping.activity_payload(project)["multipolygon_coordinates"]
        self.assertEqual(geometry["type"], "MultiPolygon")
        self.assertEqual(len(geometry["coordinates"]), 2)
        project.refresh_from_db()
        self.assertIsNone(project.geometry)  # local data is not changed
        checks = self.client.get(reverse("project-readiness", args=[project.pk])).json()["checks"]
        boundary = next(c for c in checks if c["code"] == "geometry")
        self.assertIn("outline of its parcels", boundary["message"])


class ActivityPlanPayloadTests(ProjectBase):
    def test_legal_parcel_ownership_text_is_not_sent(self):
        from . import dcr_mapping

        project = self.make_project()
        plan = ActivityPlan.objects.create(
            project=project, legal_parcel_ownership="Owned by the operator"
        )
        self.assertNotIn("legal_parcel_ownership", dcr_mapping.activity_plan_payload(plan))


class DCRStatusParcelVerdictTests(ProjectBase):
    def test_link_without_its_own_verdict(self):
        project = self.make_project(status="submitted", dcr_id="act-9")
        parcel = Parcel.objects.create(
            operator=self.operator,
            name="North field",
            cadastral_reference="45025000ZA0090",
            geometry=(
                "SRID=4326;MULTIPOLYGON(((13.405 52.52,13.406 52.52,13.406 52.521,13.405 52.52)))"
            ),
        )
        # what the parcel_activity create returned: no status of its own
        ProjectParcel.objects.create(
            project=project, parcel=parcel, dcr_response={"parcel_activity_id": "pa-1"}
        )
        row = self.client.get(reverse("project-dcr-status", args=[project.pk])).json()["parcels"][0]
        self.assertEqual(row["name"], "North field")
        self.assertEqual(row["cadastral_reference"], "45025000ZA0090")
        self.assertFalse(row["has_verdict"])


class VerificationStatusCodeTests(ProjectBase):
    @mock.patch("apps.dcr.authentication.get_client")
    @mock.patch("apps.projects.services.get_client")
    def test_denied_rejects_and_closes_documents(self, services_client, auth_client):
        project = self.make_project(status="submitted", dcr_id="act-3")
        client = mock.MagicMock()
        client.current_user.return_value = {"user_id": "dcr-user-1", "username": "alice"}
        client.list_entities.side_effect = lambda entity, token, params=None: (
            [{"activity_id": "act-3", "status_code": "denied"}]
            if entity == "activity_verification"
            else []
        )
        services_client.return_value = client
        auth_client.return_value = client
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION='DirectLogin token="tok-1"')
        body = self.client.post(reverse("project-dcr-status", args=[project.pk])).json()
        self.assertEqual(body["status"], "rejected")
        project.refresh_from_db()
        self.assertFalse(project.accepts_documents)


class AmendmentFlowTests(ProjectBase):
    def setUp(self):
        super().setUp()
        self.project = self.make_project(
            status="submitted", dcr_id="act-7", dcr_sync_status="synced"
        )
        self.keep = Parcel.objects.create(
            operator=self.operator,
            name="Keep",
            cadastral_reference="45025000ZA0093",
            dcr_id="par-keep",
            dcr_sync_status="synced",
            geometry=(
                "SRID=4326;MULTIPOLYGON(((13.405 52.52,13.406 52.52,13.406 52.521,13.405 52.52)))"
            ),
        )
        ProjectParcel.objects.create(
            project=self.project, parcel=self.keep, dcr_id="pa-keep", dcr_sync_status="synced"
        )
        self.reports = [{"audit_report_id": "ar-1", "activity_id": "act-7"}]
        self.verification = {
            "activity_id": "act-7",
            "status_code": "validation_amendment_required",
            "status_message": "Please add the title deed of parcel ZA 93",
        }
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION='DirectLogin token="tok-1"')

    def fake_client(self):
        client = mock.MagicMock()
        client.current_user.return_value = {"user_id": "dcr-user-1", "username": "alice"}
        client.entity_exists.return_value = None
        client.entity_fields.return_value = None
        client.create_entity.side_effect = lambda e, p, t: {f"{e}_id": p.get(f"{e}_id"), **p}
        client.update_entity.side_effect = lambda e, i, p, t: {f"{e}_id": i, **p}
        client.entity_id_from.side_effect = lambda data, entity: data.get(f"{entity}_id")

        def list_entities(entity, token, params=None):
            if entity == "activity_verification":
                return [dict(self.verification)]
            if entity == "audit_report":
                return [dict(r) for r in self.reports]
            if entity == "parcel_activity":
                return [
                    {
                        "parcel_activity_id": "pa-keep",
                        "activity_id": "act-7",
                        "parcel_id": "par-keep",
                    },
                    {
                        "parcel_activity_id": "pa-gone",
                        "activity_id": "act-7",
                        "parcel_id": "par-gone",
                    },
                ]
            return []

        client.list_entities.side_effect = list_entities
        return client

    def refresh(self):
        return self.client.post(reverse("project-dcr-status", args=[self.project.pk])).json()

    @mock.patch("apps.dcr.authentication.get_client")
    @mock.patch("apps.projects.services.get_client")
    def test_request_edit_resubmit_and_a_new_request(self, services_client, auth_client):
        client = self.fake_client()
        services_client.return_value = client
        auth_client.return_value = client

        # 1. the certifier asks for changes: the activity reopens for the operator
        body = self.refresh()
        self.assertEqual(body["status"], "changes_requested")
        self.assertEqual(body["amendment"]["stage"], "validation")
        self.assertEqual(body["amendment"]["message"], "Please add the title deed of parcel ZA 93")
        edit = self.client.patch(
            reverse("project-detail", args=[self.project.pk]), {"summary": "Amended"}, format="json"
        )
        self.assertEqual(edit.status_code, 200, edit.content)
        doc_rules = self.project.__class__.objects.get(pk=self.project.pk)
        self.assertTrue(doc_rules.accepts_documents)

        # 2. resubmit: an update of the same records; parcel links are the certifier's
        response = self.client.post(reverse("project-submit", args=[self.project.pk]))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "submitted")
        client.delete_entity.assert_not_called()
        updated = {c.args[0] for c in client.update_entity.call_args_list}
        self.assertIn("activity", updated)

        # 3. the same request on DCR does not reopen the activity again
        body = self.refresh()
        self.assertEqual(body["status"], "submitted")
        self.assertIsNone(body["amendment"])

        # 3b. the certifier repeats the identical request: only its new audit report differs
        self.reports.append({"audit_report_id": "ar-2", "activity_id": "act-7"})
        self.assertEqual(self.refresh()["status"], "changes_requested")
        self.client.post(reverse("project-submit", args=[self.project.pk]))
        self.assertEqual(self.refresh()["status"], "submitted")

        # 4. the certifier moves on, then asks again (verification stage)
        self.verification = {
            **self.verification,
            "status_code": "under_review",
            "status_message": "",
        }
        self.assertEqual(self.refresh()["status"], "submitted")
        self.verification = {
            **self.verification,
            "status_code": "verification_amendment_required",
            "status_message": "Monitoring report missing",
        }
        body = self.refresh()
        self.assertEqual(body["status"], "changes_requested")
        self.assertEqual(body["amendment"]["stage"], "verification")

    @mock.patch("apps.dcr.authentication.get_client")
    @mock.patch("apps.projects.services.get_client")
    def test_documents_while_changes_are_requested(self, services_client, auth_client):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.documents.models import Document

        client = self.fake_client()
        services_client.return_value = client
        auth_client.return_value = client
        self.project.submitted_at = timezone.now()
        self.project.save()
        doc = Document.objects.create(
            project=self.project,
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
        self.refresh()
        doc.refresh_from_db()
        self.assertFalse(doc.is_frozen)  # new versions can answer the request
        self.assertFalse(doc.is_deletable)  # but what the certifier saw stays


class UnlinkAfterSubmitTests(ProjectBase):
    def test_parcel_can_be_dropped_while_changes_are_requested(self):
        project = self.make_project(status="changes_requested", dcr_id="act-8")
        parcel = Parcel.objects.create(
            operator=self.operator,
            name="Drop me",
            geometry=(
                "SRID=4326;MULTIPOLYGON(((13.405 52.52,13.406 52.52,13.406 52.521,13.405 52.52)))"
            ),
        )
        ProjectParcel.objects.create(
            project=project, parcel=parcel, dcr_id="pa-1", dcr_sync_status="synced"
        )
        url = reverse("project-unlink-parcel", args=[project.pk, parcel.pk])
        self.assertEqual(self.client.delete(url).status_code, 204)
        project.status = "submitted"
        project.save()
        other = Parcel.objects.create(
            operator=self.operator, name="Other", geometry=parcel.geometry
        )
        ProjectParcel.objects.create(project=project, parcel=other)
        url = reverse("project-unlink-parcel", args=[project.pk, other.pk])
        self.assertEqual(self.client.delete(url).status_code, 409)


class RegistryResetTests(ProjectBase):
    """Records may disappear from DCR (registry resets); a resubmit registers them again."""

    def setUp(self):
        super().setUp()
        geometry = (
            "SRID=4326;MULTIPOLYGON(((13.405 52.52,13.406 52.52,13.406 52.521,13.405 52.52)))"
        )
        self.parcel = Parcel.objects.create(
            operator=self.operator,
            name="P",
            cadastral_reference="45025000ZA0021",
            geometry=geometry,
            dcr_id="par-1",
            dcr_sync_status="synced",
        )
        self.project = self.make_project(
            status="changes_requested",
            dcr_id="act-1",
            dcr_sync_status="synced",
            dcr_links={"activity_technologies_practices_processes": {"AGROFORESTRY": "old"}},
        )
        ProjectParcel.objects.create(project=self.project, parcel=self.parcel)
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION='DirectLogin token="tok-1"')

    def fake_client(self, existing):
        client = mock.MagicMock()
        client.current_user.return_value = {"user_id": "dcr-user-1", "username": "alice"}
        client.entity_exists.return_value = None
        client.entity_fields.return_value = None
        client.list_entities.return_value = []

        def get_entity(entity, record_id, token):
            if (entity, record_id) in existing:
                return {f"{entity}_id": record_id}
            raise DCRError("OBP-09005: Entity not found", 404)

        def update(entity, record_id, payload, token):
            if (entity, record_id) not in existing:
                raise DCRError("OBP-09005: Entity not found", 404)
            return {f"{entity}_id": record_id, **payload}

        client.get_entity.side_effect = get_entity
        client.update_entity.side_effect = update
        client.create_entity.side_effect = lambda e, p, t: {f"{e}_id": p.get(f"{e}_id"), **p}
        client.entity_id_from.side_effect = lambda data, entity: data.get(f"{entity}_id")
        return client

    @mock.patch("apps.dcr.authentication.get_client")
    @mock.patch("apps.projects.services.get_client")
    def test_missing_records_are_created_again(self, services_client, auth_client):
        client = self.fake_client(existing=set())
        services_client.return_value = client
        auth_client.return_value = client
        response = self.client.post(reverse("project-submit", args=[self.project.pk]))
        self.assertEqual(response.status_code, 200, response.content)
        created = {c.args[0] for c in client.create_entity.call_args_list}
        self.assertTrue({"operator", "activity"} <= created, created)
        self.assertNotIn("parcel", created)
        self.project.refresh_from_db()
        self.assertNotIn("old", str(self.project.dcr_links))

    @mock.patch("apps.dcr.authentication.get_client")
    @mock.patch("apps.projects.services.get_client")
    def test_existing_parcel_is_not_overwritten(self, services_client, auth_client):
        client = self.fake_client(existing={("activity", "act-1"), ("parcel", "par-1")})
        services_client.return_value = client
        auth_client.return_value = client
        response = self.client.post(reverse("project-submit", args=[self.project.pk]))
        self.assertEqual(response.status_code, 200, response.content)
        touched = {c.args[0] for c in client.update_entity.call_args_list} | {
            c.args[0] for c in client.create_entity.call_args_list
        }
        self.assertNotIn("parcel", touched)

    @mock.patch("apps.dcr.authentication.get_client")
    @mock.patch("apps.projects.services.get_client")
    def test_a_parcel_gone_from_dcr_is_local_again(self, services_client, auth_client):
        from apps.parcels.models import ParcelOwner, ParcelOwnerVerification

        owner = ParcelOwner.objects.create(
            parcel=self.parcel,
            legal_name="GAEC du Moulin",
            owner_type="legal_person",
            country_code="FR",
            dcr_id="po_1",
            dcr_sync_status="synced",
            supporting_document_dcr_id="sd_1",
        )
        ParcelOwnerVerification.objects.create(
            parcel=self.parcel, dcr_id="pov_1", status_code="draft"
        )
        link = self.project.parcel_links.get()
        link.status_code = "verified"
        link.amount = 12
        link.save()
        client = self.fake_client(existing=set())
        services_client.return_value = client
        auth_client.return_value = client
        response = self.client.post(reverse("project-submit", args=[self.project.pk]))
        self.assertEqual(response.status_code, 200, response.content)
        self.parcel.refresh_from_db()
        self.assertEqual((self.parcel.dcr_id, self.parcel.dcr_sync_status), ("", "local"))
        owner.refresh_from_db()
        self.assertEqual(owner.dcr_sync_status, "local")
        self.assertEqual(owner.dcr_id, "po_1")  # the same id is used when it is sent again
        self.assertFalse(ParcelOwnerVerification.objects.filter(parcel=self.parcel).exists())
        link.refresh_from_db()
        self.assertEqual((link.status_code, link.amount), ("in_progress", None))
        self.assertNotIn("parcel_owner", {c.args[0] for c in client.create_entity.call_args_list})


class ActivityGoneFromDCRTests(ProjectBase):
    @mock.patch("apps.dcr.authentication.get_client")
    @mock.patch("apps.projects.services.get_client")
    def test_refresh_reopens_an_activity_missing_on_dcr(self, services_client, auth_client):
        project = self.make_project(
            status="submitted", dcr_id="act-9", dcr_verification={"status_code": "validated"}
        )
        client = mock.MagicMock()
        client.current_user.return_value = {"user_id": "dcr-user-1", "username": "alice"}
        client.get_entity.side_effect = DCRError("OBP-09005: Entity not found", 404)
        services_client.return_value = client
        auth_client.return_value = client
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION='DirectLogin token="tok-1"')
        body = self.client.post(reverse("project-dcr-status", args=[project.pk])).json()
        self.assertEqual(body["status"], "ready")
        self.assertIsNone(body["verification"])
        project.refresh_from_db()
        self.assertTrue(project.is_editable)


class ParcelOwnerTests(ProjectBase):
    def setUp(self):
        super().setUp()
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.documents.models import Document

        self.project = self.make_project(status="submitted", dcr_id="act-1")
        self.parcel = Parcel.objects.create(
            operator=self.operator,
            name="ZA 90",
            cadastral_reference="45025000ZA0090",
            geometry=(
                "SRID=4326;MULTIPOLYGON(((13.405 52.52,13.406 52.52,13.406 52.521,13.405 52.52)))"
            ),
        )
        ProjectParcel.objects.create(project=self.project, parcel=self.parcel)
        self.deed = Document.objects.create(
            parcel=self.parcel,
            operator=self.operator,
            uploaded_by=self.user,
            kind="ownership_proof",
            title="Title deed ZA 90",
            file=SimpleUploadedFile("deed.pdf", b"%PDF deed"),
            original_name="deed.pdf",
            content_type="application/pdf",
            size=9,
            sha256="d" * 64,
        )
        self.url = reverse("parcel-owner", args=[self.parcel.pk])

    def declare(self, **extra):
        body = {
            "legal_name": "GAEC du Moulin",
            "owner_type": "legal_person",
            "country_code": "fr",
            "document": self.deed.pk,
            **extra,
        }
        return self.client.put(self.url, body, format="json")

    def test_declaration_is_validated(self):
        self.assertEqual(self.client.get(self.url).status_code, 404)
        readiness = self.client.get(reverse("project-readiness", args=[self.project.pk])).json()
        self.assertIn(
            f"parcel.{self.parcel.pk}.owner",
            [c["code"] for c in readiness["checks"] if not c["ok"]],
        )

        self.assertEqual(self.declare(country_code="XX").status_code, 400)
        self.assertEqual(self.declare(owner_type="tenant").status_code, 400)
        other = Parcel.objects.create(
            operator=self.operator,
            name="other",
            geometry=(
                "SRID=4326;MULTIPOLYGON(((13.405 52.52,13.406 52.52,13.406 52.521,13.405 52.52)))"
            ),
        )
        self.deed.parcel = other
        self.deed.save()
        self.assertEqual(self.declare().status_code, 400)  # a document of another parcel
        self.deed.parcel = self.parcel
        self.deed.save()

        response = self.declare()
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["country_code"], "FR")
        self.assertTrue(body["is_complete"])
        self.assertEqual(body["dcr_sync_status"], "local")  # parcel not registered yet
        readiness = self.client.get(reverse("project-readiness", args=[self.project.pk])).json()
        self.assertNotIn(
            f"parcel.{self.parcel.pk}.owner",
            [c["code"] for c in readiness["checks"] if not c["ok"]],
        )

    @mock.patch("apps.dcr.authentication.get_client")
    @mock.patch("apps.projects.services.get_client")
    def test_owner_reaches_dcr_once_the_parcel_is_registered(self, services_client, auth_client):
        from django.test import override_settings

        self.assertEqual(self.declare().status_code, 201)
        client = mock.MagicMock()
        client.current_user.return_value = {"user_id": "dcr-user-1", "username": "alice"}
        client.entity_exists.side_effect = lambda e: e == "parcel_activity"
        client.entity_fields.return_value = None
        registry = {("activity", "act-1"): {"activity_id": "act-1"}}
        registry[("parcel", "cert_p_90")] = {
            "parcel_id": "cert_p_90",
            "cadastral_reference": "45025000ZA0090",
        }

        def get_entity(entity, record_id, token):
            if (entity, record_id) in registry:
                return dict(registry[(entity, record_id)])
            raise DCRError("OBP-09005: Entity not found", 404)

        def create(entity, payload, token):
            registry[(entity, payload[f"{entity}_id"])] = dict(payload)
            return dict(payload)

        def update(entity, record_id, payload, token):
            registry[(entity, record_id)] = dict(payload)
            return dict(payload)

        def list_entities(entity, token, params=None):
            if entity == "parcel_activity":
                return [{"activity_id": "act-1", "parcel_id": "cert_p_90"}]
            return []

        client.get_entity.side_effect = get_entity
        client.create_entity.side_effect = create
        client.update_entity.side_effect = update
        client.entity_id_from.side_effect = lambda data, entity: data.get(f"{entity}_id")
        client.list_entities.side_effect = list_entities
        services_client.return_value = client
        auth_client.return_value = client
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION='DirectLogin token="tok-1"')

        with override_settings(PUBLIC_API_URL="https://api.example.org"):
            body = self.client.post(reverse("project-dcr-status", args=[self.project.pk])).json()
        created = [c.args[0] for c in client.create_entity.call_args_list]
        self.assertEqual(created, ["supporting_document", "parcel_owner"])
        document = client.create_entity.call_args_list[0].args[1]
        self.assertEqual(
            document["url"],
            f"https://api.example.org/api/partner/documents/{self.deed.pk}/download/",
        )
        self.assertEqual(document["sha256_of_document"], "d" * 64)
        import hashlib

        digest = hashlib.sha256(document["url"].encode()).hexdigest()
        self.assertEqual(document["supporting_document_id"], f"sd_{digest}")
        owner = client.create_entity.call_args_list[1].args[1]
        self.assertEqual(owner["parcel_id"], "cert_p_90")
        self.assertEqual(owner["owner_type"], "legal_person")
        self.assertEqual(owner["country_id"], "FR")
        self.assertEqual(owner["supporting_document_id"], f"sd_{digest}")
        self.assertTrue(owner["parcel_owner_id"].startswith("po_"))
        row = body["parcels"][0]["owner"]
        self.assertEqual(row["dcr_sync_status"], "synced")

        # nothing changed: nothing is sent again
        client.create_entity.reset_mock()
        with override_settings(PUBLIC_API_URL="https://api.example.org"):
            self.client.post(reverse("project-dcr-status", args=[self.project.pk]))
        client.create_entity.assert_not_called()
        client.update_entity.assert_not_called()

        # an edit is sent at once, as an update of the same record
        with override_settings(PUBLIC_API_URL="https://api.example.org"):
            response = self.declare(legal_name="Jean Dupont", owner_type="natural_person")
        self.assertEqual(response.status_code, 200, response.content)
        updated = client.update_entity.call_args_list
        self.assertEqual([c.args[0] for c in updated], ["parcel_owner"])
        self.assertEqual(updated[0].args[1], owner["parcel_owner_id"])
        self.assertEqual(updated[0].args[2]["legal_name"], "Jean Dupont")
        client.create_entity.assert_not_called()  # the document record already exists

    @mock.patch("apps.dcr.authentication.get_client")
    @mock.patch("apps.projects.services.get_client")
    def test_a_failure_is_recorded_and_does_not_stop_the_refresh(
        self, services_client, auth_client
    ):
        self.parcel.dcr_id = "cert_p_90"
        self.parcel.save()
        self.assertEqual(self.declare().status_code, 201)
        client = mock.MagicMock()
        client.current_user.return_value = {"user_id": "dcr-user-1", "username": "alice"}
        client.entity_exists.return_value = None
        client.entity_fields.return_value = None
        client.list_entities.return_value = []

        def get_entity(entity, record_id, token):
            if entity == "activity":
                return {"activity_id": record_id}
            raise DCRError("OBP-20006: missing role", 403)

        client.get_entity.side_effect = get_entity
        services_client.return_value = client
        auth_client.return_value = client
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION='DirectLogin token="tok-1"')
        response = self.client.post(reverse("project-dcr-status", args=[self.project.pk]))
        self.assertEqual(response.status_code, 200, response.content)
        row = response.json()["parcels"][0]["owner"]
        self.assertEqual(row["dcr_sync_status"], "failed")
        self.assertIn("missing role", row["error"])

    @mock.patch("apps.dcr.authentication.get_client")
    @mock.patch("apps.projects.services.get_client")
    def test_documents_added_after_submit_are_linked_to_the_owner_verification(
        self, services_client, auth_client
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        from apps.documents.models import Document

        self.parcel.dcr_id = "cert_p_90"
        self.parcel.save()
        extract = Document.objects.create(
            parcel=self.parcel,
            operator=self.operator,
            uploaded_by=self.user,
            kind="cadastral_extract",
            title="Extract ZA 90",
            file=SimpleUploadedFile("extract.pdf", b"%PDF extract"),
            original_name="extract.pdf",
            content_type="application/pdf",
            size=12,
            sha256="e" * 64,
        )
        with override_settings(PUBLIC_API_URL="https://api.example.org"):
            from apps.projects import dcr_mapping

            extract_sd = dcr_mapping.supporting_document_id(extract)
            deed_url = dcr_mapping.supporting_document_url(self.deed)
        # what the certification platform made at submit: the deed under an id of its own,
        # linked to its verification
        registry = {
            ("activity", "act-1"): {"activity_id": "act-1"},
            ("supporting_document", "cert_sd_deed"): {
                "supporting_document_id": "cert_sd_deed",
                "url": deed_url,
            },
        }
        pov_links = [
            {
                "parcel_owner_verification_supporting_document_id": "cert_sdlink_1",
                "supporting_document_id": "cert_sd_deed",
                "parcel_owner_verification_id": "cert_pov_90",
            }
        ]
        client = mock.MagicMock()
        client.current_user.return_value = {"user_id": "dcr-user-1", "username": "alice"}
        client.entity_exists.return_value = None
        client.entity_fields.return_value = None

        def get_entity(entity, record_id, token):
            if (entity, record_id) in registry:
                return dict(registry[(entity, record_id)])
            raise DCRError("OBP-09005: Entity not found", 404)

        def create(entity, payload, token):
            if entity == "parcel_owner_verification_supporting_document":
                pov_links.append(dict(payload))
            else:
                registry[(entity, payload[f"{entity}_id"])] = dict(payload)
            return dict(payload)

        def list_entities(entity, token, params=None):
            if entity == "parcel_owner_verification":
                return [
                    {
                        "parcel_owner_verification_id": "cert_pov_90",
                        "parcel_id": "cert_p_90",
                        "status_code": "draft",
                    }
                ]
            if entity == "parcel_owner_verification_supporting_document":
                return list(pov_links)
            return []

        client.get_entity.side_effect = get_entity
        client.create_entity.side_effect = create
        client.list_entities.side_effect = list_entities
        services_client.return_value = client
        auth_client.return_value = client
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION='DirectLogin token="tok-1"')

        with override_settings(PUBLIC_API_URL="https://api.example.org"):
            response = self.client.post(reverse("project-dcr-status", args=[self.project.pk]))
        self.assertEqual(response.status_code, 200, response.content)
        created = [c.args[0] for c in client.create_entity.call_args_list]
        self.assertEqual(
            created, ["supporting_document", "parcel_owner_verification_supporting_document"]
        )
        link = pov_links[-1]
        self.assertEqual(link["supporting_document_id"], extract_sd)
        self.assertEqual(link["parcel_owner_verification_id"], "cert_pov_90")
        self.assertTrue(
            link["parcel_owner_verification_supporting_document_id"].startswith("sdlink_")
        )

        # linked once: the next refresh sends nothing
        client.create_entity.reset_mock()
        with override_settings(PUBLIC_API_URL="https://api.example.org"):
            self.client.post(reverse("project-dcr-status", args=[self.project.pk]))
        client.create_entity.assert_not_called()


class OwnerVerificationMirrorTests(ProjectBase):
    def test_a_replaced_verification_record_takes_over(self):
        from apps.parcels.models import ParcelOwnerVerification
        from apps.projects import services

        parcel = Parcel.objects.create(
            operator=self.operator,
            name="P",
            geometry=(
                "SRID=4326;MULTIPOLYGON(((13.405 52.52,13.406 52.52,13.406 52.521,13.405 52.52)))"
            ),
            dcr_id="cert_p_1",
        )
        ParcelOwnerVerification.objects.create(parcel=parcel, dcr_id="old_pov", status_code="draft")
        services._mirror_owner_verification(
            parcel,
            {
                "parcel_owner_verification_id": "new_pov",
                "parcel_id": "cert_p_1",
                "status_code": "draft",
            },
        )
        self.assertEqual(ParcelOwnerVerification.objects.get(parcel=parcel).dcr_id, "new_pov")
