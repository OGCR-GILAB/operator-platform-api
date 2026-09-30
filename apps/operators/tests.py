from unittest import mock

from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.models import User

from .models import Operator, OperatorMembership


class OperatorTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", dcr_user_id="dcr-1")
        self.other = User.objects.create_user(username="bob", dcr_user_id="dcr-2")
        self.client.force_authenticate(self.user)

    def test_create_makes_creator_a_member(self):
        response = self.client.post(
            reverse("operator-list"),
            {"legal_name": "Agreena", "email": "c@example.org", "country_code": "de"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["country_code"], "DE")
        self.assertEqual(response.json()["my_relationship"], "Managing Director")
        self.assertEqual(response.json()["dcr_sync_status"], "local")
        self.assertTrue(OperatorMembership.objects.filter(user=self.user).exists())

    def test_only_member_operators_are_listed(self):
        Operator.objects.create(legal_name="Foreign", email="f@example.org", country_code="FR")
        mine = Operator.objects.create(legal_name="Mine", email="m@example.org", country_code="RS")
        OperatorMembership.objects.create(operator=mine, user=self.user)
        response = self.client.get(reverse("operator-list"))
        names = [row["legal_name"] for row in response.json()["results"]]
        self.assertEqual(names, ["Mine"])


class MemberTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", dcr_user_id="dcr-1")
        self.bob = User.objects.create_user(
            username="bob", email="bob@example.org", dcr_user_id="dcr-2"
        )
        self.operator = Operator.objects.create(
            legal_name="Mine", email="m@example.org", country_code="RS"
        )
        OperatorMembership.objects.create(operator=self.operator, user=self.user)
        self.client.force_authenticate(self.user)

    def test_add_list_and_remove_member(self):
        url = reverse("operator-members", args=[self.operator.pk])
        response = self.client.post(
            url, {"email": "bob@example.org", "relationship": "Accountant"}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["username"], "bob")
        self.assertEqual(len(self.client.get(url).json()), 2)
        self.assertEqual(self.client.post(url, {"username": "bob"}, format="json").status_code, 409)
        self.assertEqual(
            self.client.post(url, {"username": "nobody"}, format="json").status_code, 400
        )
        response = self.client.delete(
            reverse("operator-remove-member", args=[self.operator.pk, self.bob.pk])
        )
        self.assertEqual(response.status_code, 204)
        # the last member cannot be removed
        response = self.client.delete(
            reverse("operator-remove-member", args=[self.operator.pk, self.user.pk])
        )
        self.assertEqual(response.status_code, 409)


class OperatorDCRRecordTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", dcr_user_id="dcr-1")
        self.operator = Operator.objects.create(
            legal_name="Mine", email="m@example.org", country_code="RS"
        )
        OperatorMembership.objects.create(operator=self.operator, user=self.user)

    def test_unregistered_operator_is_404(self):
        self.client.force_authenticate(self.user)
        response = self.client.get(reverse("operator-dcr", args=[self.operator.pk]))
        self.assertEqual(response.status_code, 404)

    @mock.patch("apps.operators.views.get_client")
    @mock.patch("apps.dcr.authentication.get_client")
    def test_live_record_with_wallet(self, auth_client, ops_client):
        auth_client.return_value.current_user.return_value = {
            "user_id": "dcr-1",
            "username": "alice",
        }
        ops_client.return_value.get_entity.return_value = {
            "operator_id": "op-9",
            "legal_name": "Mine",
            "ogcr_wallet_address": "0xabc",
        }
        self.operator.dcr_id = "op-9"
        self.operator.save()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer tok-1")
        response = self.client.get(reverse("operator-dcr", args=[self.operator.pk]))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["ogcr_wallet_address"], "0xabc")
        ops_client.return_value.get_entity.assert_called_once_with("operator", "op-9", "tok-1")
