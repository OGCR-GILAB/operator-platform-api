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
        self.assertIn(
            "CanCreateDynamicEntity_Systemactivity",
            body["dcr"]["capabilities"]["submit"]["missing_roles"],
        )
        self.assertNotIn("CanCreateDynamicEntity_Systemactivity", body["dcr"]["roles"])
        self.assertEqual(
            body["dcr"]["bank_scoped_roles"], {"OGCR": ["CanCreateDynamicEntity_Systemactivity"]}
        )

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
        ref_client.return_value.list_entities.return_value = [
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
        self.assertEqual(ref_client.return_value.list_entities.call_count, 1)
        ref_client.return_value.list_entities.assert_called_with("certification_scheme", "tok-123")


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
        response = self.client.post(
            reverse("auth-password-reset"), {"email": "a@example.org"}, format="json"
        )
        self.assertEqual(response.status_code, 202)
        get_client.return_value.send_password_reset.assert_called_once_with(
            "a@example.org", "a@example.org"
        )
        get_client.return_value.send_password_reset.side_effect = DCRError("not found", 404)
        response = self.client.post(
            reverse("auth-password-reset"), {"email": "b@example.org"}, format="json"
        )
        self.assertEqual(response.status_code, 202)

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
        ref_client.return_value.list_entities.return_value = [
            {"certification_scheme_id": "cs-1", "name": "Real"}
        ]
        headers = {"HTTP_AUTHORIZATION": 'DirectLogin token="tok-123"'}
        body = self.client.get(reverse("dcr-certification-schemes"), **headers).json()
        self.assertEqual(body[0]["source"], "dcr")
