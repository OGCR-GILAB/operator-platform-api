from unittest import mock

from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.models import User

from .client import DCRClient
from .exceptions import DCRAuthError, DCRError, DCRUnavailable

PROFILE = {
    "user_id": "dcr-user-1",
    "email": "alice@example.org",
    "provider": "https://dcr.example",
    "provider_id": "alice",
    "username": "alice",
}


class DirectLoginAuthenticationTests(APITestCase):
    def setUp(self):
        cache.clear()

    def auth_header(self, token="tok-123"):
        return {"HTTP_AUTHORIZATION": f'DirectLogin token="{token}"'}

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.get(reverse("auth-me"))
        self.assertEqual(response.status_code, 401)
        self.assertTrue(response["WWW-Authenticate"].startswith("DirectLogin"))

    @mock.patch("apps.dcr.authentication.get_client")
    def test_valid_token_creates_local_user(self, get_client):
        get_client.return_value.current_user.return_value = PROFILE

        response = self.client.get(reverse("auth-me"), **self.auth_header())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["username"], "alice")
        self.assertEqual(response.json()["dcr_user_id"], "dcr-user-1")
        self.assertTrue(User.objects.filter(dcr_user_id="dcr-user-1").exists())

        # second call is served from cache, DCR is not called again
        self.client.get(reverse("auth-me"), **self.auth_header())
        self.assertEqual(get_client.return_value.current_user.call_count, 1)

    @mock.patch("apps.dcr.authentication.get_client")
    def test_me_reports_dcr_roles_and_capabilities(self, get_client):
        profile = {
            **PROFILE,
            "entitlements": {
                "list": [
                    {"role_name": "CanCreateDynamicEntity_Systemoperator", "bank_id": ""},
                    {"role_name": "CanGetDynamicEntity_Systemcertification_scheme", "bank_id": ""},
                    # bank-scoped grant: must not count for system-level entities
                    {"role_name": "CanCreateDynamicEntity_Systemactivity", "bank_id": "OGCR"},
                ]
            },
        }
        get_client.return_value.current_user.return_value = profile
        body = self.client.get(reverse("auth-me"), **self.auth_header()).json()
        self.assertEqual(body["dcr"]["provider"], "https://dcr.example")
        self.assertIn("CanCreateDynamicEntity_Systemoperator", body["dcr"]["roles"])
        self.assertTrue(body["dcr"]["capabilities"]["reference"]["allowed"])
        self.assertFalse(body["dcr"]["capabilities"]["submit"]["allowed"])
        # missing roles are reported in the current (bank-level) naming
        self.assertIn(
            "CanCreateDynamicEntityRecord_activity",
            body["dcr"]["capabilities"]["submit"]["missing_roles"],
        )
        self.assertNotIn("CanCreateDynamicEntity_Systemactivity", body["dcr"]["roles"])
        self.assertEqual(
            body["dcr"]["bank_scoped_roles"], {"OGCR": ["CanCreateDynamicEntity_Systemactivity"]}
        )

    @mock.patch("apps.dcr.authentication.get_client")
    def test_bank_level_roles_at_platform_bank_count(self, get_client):
        from apps.dcr.roles import SUBMIT

        rows = []
        for action, entities in SUBMIT:
            entity = entities if isinstance(entities, str) else entities[0]
            rows.append(
                {"role_name": f"Can{action}DynamicEntityRecord_{entity}", "bank_id": "ogcr"}
            )
        get_client.return_value.current_user.return_value = {
            **PROFILE,
            "entitlements": {"list": rows},
        }
        body = self.client.get(reverse("auth-me"), **self.auth_header()).json()
        self.assertTrue(
            body["dcr"]["capabilities"]["submit"]["allowed"], body["dcr"]["capabilities"]
        )
        self.assertIn("CanCreateDynamicEntityRecord_activity", body["dcr"]["roles"])

    @mock.patch("apps.dcr.authentication.get_client")
    def test_bearer_form_is_accepted(self, get_client):
        get_client.return_value.current_user.return_value = PROFILE
        response = self.client.get(reverse("auth-me"), HTTP_AUTHORIZATION="Bearer tok-123")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["username"], "alice")

    @mock.patch("apps.dcr.authentication.get_client")
    def test_invalid_token_is_401(self, get_client):
        get_client.return_value.current_user.side_effect = DCRAuthError()
        response = self.client.get(reverse("auth-me"), **self.auth_header("bad"))
        self.assertEqual(response.status_code, 401)

    @mock.patch("apps.dcr.authentication.get_client")
    def test_dcr_down_is_503(self, get_client):
        get_client.return_value.current_user.side_effect = DCRUnavailable()
        response = self.client.get(reverse("auth-me"), **self.auth_header())
        self.assertEqual(response.status_code, 503)


class LoginProxyTests(APITestCase):
    def setUp(self):
        cache.clear()

    @mock.patch("apps.dcr.views.get_client")
    def test_login_returns_token_and_user(self, get_client):
        client = get_client.return_value
        client.direct_login.return_value = "tok-abc"
        client.current_user.return_value = PROFILE

        response = self.client.post(
            reverse("auth-login"), {"username": "alice", "password": "pw"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["token"], "tok-abc")
        self.assertEqual(response.json()["user"]["username"], "alice")
        client.direct_login.assert_called_once_with("alice", "pw")

    @mock.patch("apps.dcr.views.get_client")
    def test_bad_credentials_are_401(self, get_client):
        get_client.return_value.direct_login.side_effect = DCRAuthError("Invalid login credentials")
        response = self.client.post(
            reverse("auth-login"), {"username": "alice", "password": "wrong"}, format="json"
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "dcr_auth_failed")


class CertificationSchemeTests(APITestCase):
    def setUp(self):
        cache.clear()

    @mock.patch("apps.dcr.reference.get_client")
    @mock.patch("apps.dcr.authentication.get_client")
    def test_schemes_are_proxied_and_cached(self, auth_client, ref_client):
        auth_client.return_value.current_user.return_value = PROFILE
        dcr = ref_client.return_value
        # the public (bank-level) list is unavailable: the user's token is used instead
        dcr.list_public_entities.side_effect = DCRError("not found", 404)
        dcr.list_entities.return_value = [
            {
                "certification_scheme_id": "cs-1",
                "name": "CRCF Scheme",
                "scheme_version_number": "v1.3",
            }
        ]
        headers = {"HTTP_AUTHORIZATION": 'DirectLogin token="tok-123"'}
        response = self.client.get(reverse("dcr-certification-schemes"), **headers)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()[0]["name"], "CRCF Scheme")
        self.client.get(reverse("dcr-certification-schemes"), **headers)
        self.assertEqual(dcr.list_entities.call_count, 1)
        dcr.list_entities.assert_called_with("certification_scheme", "tok-123")

    @mock.patch("apps.dcr.reference.get_client")
    @mock.patch("apps.dcr.authentication.get_client")
    def test_public_list_is_preferred(self, auth_client, ref_client):
        auth_client.return_value.current_user.return_value = PROFILE
        dcr = ref_client.return_value
        dcr.list_public_entities.return_value = [{"certification_scheme_id": "cs", "name": "P"}]
        headers = {"HTTP_AUTHORIZATION": 'DirectLogin token="tok-123"'}
        body = self.client.get(reverse("dcr-certification-schemes"), **headers).json()
        self.assertEqual(body[0]["name"], "P")
        dcr.list_public_entities.assert_called_once_with("certification_scheme")
        dcr.list_entities.assert_not_called()

    @mock.patch("apps.dcr.reference.get_client")
    @mock.patch("apps.dcr.authentication.get_client")
    def test_scheme_name_comes_from_legal_name(self, auth_client, ref_client):
        # DCR renamed `name` to `legal_name` and added e-mail and logo (Nov 2026)
        auth_client.return_value.current_user.return_value = PROFILE
        ref_client.return_value.list_public_entities.return_value = [
            {
                "certification_scheme_id": "cs-2",
                "legal_name": "TestWald Registry",
                "email": "info@example.org",
                "logo": "https://example.org/logo.svg",
            },
            {"certification_scheme_id": "cs-3"},
        ]
        headers = {"HTTP_AUTHORIZATION": 'DirectLogin token="tok-123"'}
        response = self.client.get(reverse("dcr-certification-schemes"), **headers)
        self.assertEqual(response.status_code, 200, response.content)
        first, second = response.json()
        self.assertEqual(first["name"], "TestWald Registry")
        self.assertEqual(first["legal_name"], "TestWald Registry")
        self.assertEqual(first["logo"], "https://example.org/logo.svg")
        self.assertEqual(second["name"], "cs-3")


class OnboardingTests(APITestCase):
    def setUp(self):
        cache.clear()

    @mock.patch("apps.dcr.views.get_client")
    def test_register_creates_dcr_user_and_local_mirror(self, get_client):
        get_client.return_value.create_user.return_value = {**PROFILE, "user_id": "dcr-new"}
        response = self.client.post(
            reverse("auth-register"),
            {
                "email": "alice@example.org",
                "password": "Str0ng!Pass",
                "first_name": "Alice",
                "last_name": "A",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["user"]["dcr_user_id"], "dcr-new")
        get_client.return_value.create_user.assert_called_once_with(
            email="alice@example.org",
            username="alice@example.org",
            password="Str0ng!Pass",
            first_name="Alice",
            last_name="A",
            mobile_phone_number="",
        )
        self.assertEqual(User.objects.get(dcr_user_id="dcr-new").first_name, "Alice")

    @mock.patch("apps.dcr.views.get_client")
    def test_register_conflict_is_passed_through(self, get_client):
        get_client.return_value.create_user.side_effect = DCRError(
            "OBP-30208: Username already exists", 409
        )
        response = self.client.post(
            reverse("auth-register"),
            {"email": "alice@example.org", "password": "x", "first_name": "A", "last_name": "B"},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "dcr_rejected")

    @mock.patch("apps.dcr.views.get_client")
    def test_validate_email(self, get_client):
        response = self.client.post(reverse("auth-validate-email"), {"token": "jwt"}, format="json")
        self.assertEqual(response.status_code, 200)
        get_client.return_value.validate_email.assert_called_once_with("jwt")
        get_client.return_value.validate_email.side_effect = DCRError(
            "OBP-20106: invalid token", 404
        )
        response = self.client.post(reverse("auth-validate-email"), {"token": "bad"}, format="json")
        self.assertEqual(response.status_code, 400)

    @mock.patch("apps.dcr.views.get_client")
    def test_password_reset_is_generic(self, get_client):
        client = get_client.return_value
        client.users_by_email.return_value = [
            {"username": "bob", "email": "B@example.org", "user_id": "74a8ebcc-user-bob"},
            {"username": "other", "email": "someone@else.org", "user_id": "x"},
        ]
        response = self.client.post(
            reverse("auth-password-reset"), {"email": "b@example.org"}, format="json"
        )
        self.assertEqual(response.status_code, 202)
        # DCR needs username, e-mail and the user's id, looked up on DCR by e-mail
        client.users_by_email.assert_called_once_with("b@example.org")
        client.send_password_reset.assert_called_once_with(
            "bob", "B@example.org", "74a8ebcc-user-bob"
        )
        # a username narrows it down
        client.send_password_reset.reset_mock()
        response = self.client.post(
            reverse("auth-password-reset"),
            {"email": "b@example.org", "username": "nobody"},
            format="json",
        )
        self.assertEqual(response.status_code, 202)
        client.send_password_reset.assert_not_called()
        # unknown e-mail (DCR answers 404): same answer, nothing sent
        client.users_by_email.side_effect = DCRError("not found", 404)
        response = self.client.post(
            reverse("auth-password-reset"), {"email": "c@example.org"}, format="json"
        )
        self.assertEqual(response.status_code, 202)
        client.send_password_reset.assert_not_called()
        # DCR refusing the reset (e.g. a user not validated yet): same answer
        client.users_by_email.side_effect = None
        client.send_password_reset.side_effect = DCRError("not found", 404)
        response = self.client.post(
            reverse("auth-password-reset"), {"email": "b@example.org"}, format="json"
        )
        self.assertEqual(response.status_code, 202)

    @mock.patch("apps.dcr.views.get_client")
    def test_password_reset_falls_back_to_local_users(self, get_client):
        from apps.accounts.models import User

        User.objects.create(
            username="alice", email="A@example.org", dcr_user_id="74a8ebcc-user-alice"
        )
        client = get_client.return_value
        # the service account lost CanGetAnyUser: users who signed in here still get the e-mail
        client.users_by_email.side_effect = DCRError("OBP-20006: missing role", 403)
        response = self.client.post(
            reverse("auth-password-reset"), {"email": "a@example.org"}, format="json"
        )
        self.assertEqual(response.status_code, 202)
        client.send_password_reset.assert_called_once_with(
            "alice", "A@example.org", "74a8ebcc-user-alice"
        )

    def test_password_reset_without_service_account_is_503(self):
        from django.conf import settings
        from django.test import override_settings

        unconfigured = {**settings.DCR, "SERVICE_USERNAME": "", "SERVICE_PASSWORD": ""}
        with override_settings(DCR=unconfigured):
            response = self.client.post(
                reverse("auth-password-reset"), {"email": "a@example.org"}, format="json"
            )
        self.assertEqual(response.status_code, 503)


class ClientParsingTests(APITestCase):
    def test_entity_id_is_read_from_wrapped_and_flat_responses(self):
        wrapped = {"operator": {"operator_id": "5e37-uuid", "legal_name": "X"}}
        self.assertEqual(DCRClient.entity_id_from(wrapped, "operator"), "5e37-uuid")
        self.assertEqual(DCRClient.entity_id_from({"operator_id": "flat"}, "operator"), "flat")
        self.assertIsNone(DCRClient.entity_id_from({"operator": {}}, "operator"))
        self.assertEqual(
            DCRClient.unwrap_list({"operator_list": [{"a": 1}]}, "operator"), [{"a": 1}]
        )


class CertificationSchemeFallbackTests(APITestCase):
    def setUp(self):
        cache.clear()

    @mock.patch("apps.dcr.reference.get_client")
    @mock.patch("apps.dcr.authentication.get_client")
    def test_samples_when_dcr_denies_access(self, auth_client, ref_client):
        auth_client.return_value.current_user.return_value = PROFILE
        ref_client.return_value.list_public_entities.side_effect = DCRError("missing", 404)
        ref_client.return_value.list_entities.side_effect = DCRError("OBP-20006: missing role", 403)
        headers = {"HTTP_AUTHORIZATION": 'DirectLogin token="tok-123"'}
        response = self.client.get(reverse("dcr-certification-schemes"), **headers)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertGreaterEqual(len(body), 5)
        self.assertTrue(all(row["source"] == "sample" for row in body))
        self.assertIn("EU CRCF Carbon Farming Scheme", [row["name"] for row in body])

    @mock.patch("apps.dcr.reference.get_client")
    @mock.patch("apps.dcr.authentication.get_client")
    def test_dcr_rows_are_marked_as_dcr(self, auth_client, ref_client):
        auth_client.return_value.current_user.return_value = PROFILE
        ref_client.return_value.list_public_entities.return_value = [
            {"certification_scheme_id": "cs-1", "name": "Real"}
        ]
        headers = {"HTTP_AUTHORIZATION": 'DirectLogin token="tok-123"'}
        body = self.client.get(reverse("dcr-certification-schemes"), **headers).json()
        self.assertEqual(body[0]["source"], "dcr")


class BankLevelClientTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_entity_paths_follow_the_bank(self):
        bank = DCRClient(base_url="https://dcr.test", bank_id="ogcr")
        self.assertEqual(
            bank.entity_url("activity", "a1"),
            "https://dcr.test/obp/dynamic-entity/banks/ogcr/activity/a1",
        )
        self.assertEqual(
            bank.entity_url("country", public=True),
            "https://dcr.test/obp/dynamic-entity/banks/ogcr/public/country",
        )
        system = DCRClient(base_url="https://dcr.test", bank_id="")
        self.assertEqual(
            system.entity_url("activity"), "https://dcr.test/obp/dynamic-entity/activity"
        )

    def test_catalogue_reads_bank_level_resource_docs(self):
        docs = {
            "resource_docs": [
                {
                    "request_verb": "POST",
                    "request_url": "/banks/ogcr/activity",
                    "example_request_body": {"activity_id": "", "name": ""},
                },
                {
                    "request_verb": "POST",
                    "request_url": "/banks/other/activity",
                    "example_request_body": {"wrong": ""},
                },
                {
                    "request_verb": "GET",
                    "request_url": "/banks/ogcr/public/country",
                    "example_request_body": None,
                },
            ]
        }
        session = mock.Mock()
        session.request.return_value = mock.Mock(status_code=200, content=b"x", json=lambda: docs)
        client = DCRClient(base_url="https://dcr.test", bank_id="ogcr", session=session)
        self.assertEqual(client.entity_fields("activity"), {"activity_id", "name"})
        self.assertFalse(client.entity_exists("country"))
        params = session.request.call_args.kwargs["params"]
        self.assertEqual(params, {"content": "dynamic", "bank-id": "ogcr"})


class VocabularyTests(APITestCase):
    def setUp(self):
        cache.clear()

    @mock.patch("apps.dcr.reference.get_client")
    def test_vocabularies_come_from_dcr_public_lists(self, get_client):
        rows = {
            "country": [
                {"country_id": "FR", "country_name": "France"},
                {"country_id": "AT", "country_name": "Austria"},
            ],
            "sustainable_development_goal": [
                {
                    "sustainable_development_goal_id": "NO_POVERTY",
                    "sustainable_development_goal_name": "No Poverty",
                    "sustainable_development_goal_link": "https://x/1.png",
                }
            ],
            "technologies_practices_processes": [
                {
                    "technologies_practices_processes_id": "AGROFORESTRY",
                    "practice_name": "Agroforestry",
                }
            ],
        }

        def public(entity):
            if entity == "technology_practice_process":  # rename not live yet
                raise DCRError("OBP-09005: Entity not found", 404)
            if entity not in rows:
                raise DCRError("down", 500)
            return rows[entity]

        get_client.return_value.list_public_entities.side_effect = public
        body = self.client.get(reverse("reference-vocabularies")).json()
        self.assertEqual([c["value"] for c in body["countries"]], ["AT", "FR"])
        self.assertEqual(body["sustainable_development_goals"][0]["icon"], "https://x/1.png")
        self.assertEqual(body["technologies_practices_processes"][0]["label"], "Agroforestry")
        self.assertEqual(body["co_benefits"], [])
        self.assertEqual(body["sources"]["co_benefits"], "unavailable")
        self.assertEqual(body["sources"]["countries"], "dcr")

        countries = self.client.get(reverse("reference-countries")).json()
        self.assertEqual(countries[0], {"code": "AT", "name": "Austria"})

    def test_countries_fall_back_to_iso_list(self):
        body = self.client.get(reverse("reference-countries")).json()
        self.assertIn({"code": "DE", "name": "Germany"}, body)
        vocab = self.client.get(reverse("reference-vocabularies")).json()
        self.assertEqual(vocab["sources"]["countries"], "local")
        self.assertIn({"value": "DE", "label": "Germany"}, vocab["countries"])


SPECIAL = "[\\x21-\\x2f\\x3a-\\x40\\x5b-\\x60\\x7b-\\x7e]"
POLICY = {
    "description": "10 to 16 characters with all classes, or a passphrase of 17+.",
    "policies": [
        {
            "description": "short",
            "min_length": 10,
            "max_length": 16,
            "required_character_classes": [{"name": "digit", "regex": "[0-9]"}],
            "regex": f"^(?=.*[0-9])(?=.*[a-z])(?=.*[A-Z])(?=.*{SPECIAL})[\\x21-\\x7e]{{10,16}}$",
        },
        {
            "description": "long",
            "min_length": 17,
            "max_length": 512,
            "required_character_classes": [],
            "regex": "^[\\x21-\\x7e]{17,512}$",
        },
    ],
}


class PasswordAndValidationTests(APITestCase):
    def setUp(self):
        cache.clear()

    @mock.patch("apps.dcr.reference.get_client")
    def test_password_policy_is_served_and_enforced(self, ref_client):
        ref_client.return_value.password_config.return_value = POLICY
        body = self.client.get(reverse("auth-password-policy")).json()
        self.assertEqual(len(body["policies"]), 2)

        weak = {
            "email": "a@example.org",
            "password": "short1A!",
            "first_name": "A",
            "last_name": "B",
        }
        response = self.client.post(reverse("auth-register"), weak, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.json())

        from .reference import password_satisfies

        self.assertTrue(password_satisfies("Str0ng!Pass", POLICY))
        self.assertTrue(password_satisfies("correct-horse-battery-staple", POLICY))
        self.assertFalse(password_satisfies("nouppercase1!", POLICY))

    def test_password_policy_unavailable_is_503(self):
        self.assertEqual(self.client.get(reverse("auth-password-policy")).status_code, 503)

    @mock.patch("apps.dcr.views.get_client")
    def test_register_passes_mobile_phone(self, get_client):
        get_client.return_value.create_user.return_value = {**PROFILE, "user_id": "dcr-phone"}
        data = {
            "email": "p@example.org",
            "password": "Str0ng!Pass",
            "first_name": "P",
            "last_name": "Q",
            "mobile_phone_number": "+33 6 12 34 56 78",
        }
        response = self.client.post(reverse("auth-register"), data, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        kwargs = get_client.return_value.create_user.call_args.kwargs
        self.assertEqual(kwargs["mobile_phone_number"], "+33 6 12 34 56 78")
        data["mobile_phone_number"] = "0612"
        response = self.client.post(reverse("auth-register"), data, format="json")
        self.assertEqual(response.status_code, 400)

    @mock.patch("apps.dcr.views.get_client")
    def test_resend_validation_email_is_generic(self, get_client):
        url = reverse("auth-resend-validation-email")
        response = self.client.post(url, {"email": "a@example.org"}, format="json")
        self.assertEqual(response.status_code, 202)
        get_client.return_value.resend_validation_email.assert_called_once_with(
            "a@example.org", "a@example.org"
        )
        get_client.return_value.resend_validation_email.side_effect = DCRError("nope", 404)
        response = self.client.post(url, {"email": "b@example.org"}, format="json")
        self.assertEqual(response.status_code, 202)


def role_answer(held):
    def answer(token, role, bank):
        if held is None:
            raise DCRError("not found", 404)
        return {
            "role_name": role,
            "bank_id": bank,
            "held_by": {"user": role in held, "consumer": False},
        }

    return answer


def submit_roles(skip=()):
    from .roles import SUBMIT

    held = {f"Can{a}DynamicEntityRecord_{e if isinstance(e, str) else e[0]}" for a, e in SUBMIT}
    return held - set(skip)


class LivePermissionTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.headers = {"HTTP_AUTHORIZATION": 'DirectLogin token="tok-9"'}

    @mock.patch("apps.dcr.client.get_client")
    @mock.patch("apps.dcr.authentication.get_client")
    def test_permissions_are_checked_live(self, auth_client, dcr_client):
        auth_client.return_value.current_user.return_value = PROFILE
        held = submit_roles(skip=["CanCreateDynamicEntityRecord_activity_plan"])
        dcr_client.return_value.my_role.side_effect = role_answer(held)
        body = self.client.get(reverse("dcr-permissions"), **self.headers).json()
        self.assertEqual(body["source"], "live")
        self.assertEqual(body["bank_id"], "ogcr")
        submit = body["capabilities"]["submit"]
        self.assertFalse(submit["allowed"])
        self.assertEqual(submit["missing_roles"], ["CanCreateDynamicEntityRecord_activity_plan"])
        self.assertFalse(body["capabilities"]["status"]["allowed"])
        calls = dcr_client.return_value.my_role.call_count
        self.client.get(reverse("dcr-permissions"), **self.headers)
        self.assertEqual(dcr_client.return_value.my_role.call_count, calls)  # cached
        self.client.get(reverse("dcr-permissions") + "?refresh=1", **self.headers)
        self.assertEqual(dcr_client.return_value.my_role.call_count, 2 * calls)

    @mock.patch("apps.dcr.client.get_client")
    @mock.patch("apps.dcr.authentication.get_client")
    def test_profile_fallback_when_dcr_cannot_answer(self, auth_client, dcr_client):
        auth_client.return_value.current_user.return_value = PROFILE
        dcr_client.return_value.my_role.side_effect = role_answer(None)
        body = self.client.get(reverse("dcr-permissions"), **self.headers).json()
        self.assertEqual(body["source"], "profile")
        self.assertFalse(body["capabilities"]["submit"]["allowed"])


class MobilePhoneTests(APITestCase):
    def setUp(self):
        cache.clear()

    @mock.patch("apps.dcr.views.get_client")
    @mock.patch("apps.dcr.authentication.get_client")
    def test_mobile_phone_is_set_on_dcr(self, auth_client, view_client):
        auth_client.return_value.current_user.return_value = PROFILE
        view_client.return_value.current_user.return_value = {
            **PROFILE,
            "mobile_phone_number": "+381 64 123 4567",
            "mobile_phone_number_is_validated": False,
        }
        headers = {"HTTP_AUTHORIZATION": 'DirectLogin token="tok-5"'}
        url = reverse("auth-mobile-phone")
        response = self.client.put(
            url, {"mobile_phone_number": "+381 64 123 4567"}, format="json", **headers
        )
        self.assertEqual(response.status_code, 200, response.content)
        view_client.return_value.update_mobile_phone_number.assert_called_once_with(
            "tok-5", "+381 64 123 4567"
        )
        dcr = response.json()["dcr"]
        self.assertEqual(dcr["mobile_phone_number"], "+381 64 123 4567")
        self.assertFalse(dcr["mobile_phone_number_is_validated"])
        self.assertEqual(dcr["bank_id"], "ogcr")
        bad = self.client.put(url, {"mobile_phone_number": "abc"}, format="json", **headers)
        self.assertEqual(bad.status_code, 400)
