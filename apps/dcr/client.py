"""
Thin HTTP client for the DCR platform (Open Bank Project style API).

Authentication model:
  * the application is registered on DCR as a *consumer* (consumer key / secret)
  * users log in with DirectLogin -> DCR returns a token
  * every further call carries  Authorization: DirectLogin token="<token>"
"""

import logging

import requests
from django.conf import settings

from .exceptions import DCRAuthError, DCRConfigurationError, DCRError, DCRUnavailable

logger = logging.getLogger(__name__)


class DCRClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_version: str | None = None,
        consumer_key: str | None = None,
        timeout: int | None = None,
        session: requests.Session | None = None,
    ):
        cfg = settings.DCR
        self.base_url = (base_url if base_url is not None else cfg["BASE_URL"]).rstrip("/")
        self.api_version = api_version or cfg["API_VERSION"]
        self.consumer_key = consumer_key if consumer_key is not None else cfg["CONSUMER_KEY"]
        self.timeout = timeout or cfg["TIMEOUT"]
        self.session = session or requests.Session()

    # ------------------------------------------------------------------ auth

    @property
    def api_root(self) -> str:
        return f"{self.base_url}/obp/{self.api_version}"

    def direct_login(self, username: str, password: str) -> str:
        """POST /my/logins/direct -> DirectLogin token."""
        if not self.base_url or not self.consumer_key:
            raise DCRConfigurationError()
        header = (
            f'DirectLogin username="{username}",password="{password}",'
            f'consumer_key="{self.consumer_key}"'
        )
        data = self._request(
            "POST", f"{self.base_url}/my/logins/direct", headers={"Authorization": header}
        )
        token = data.get("token") if isinstance(data, dict) else None
        if not token:
            raise DCRAuthError("DCR did not return a token")
        return token

    def current_user(self, token: str) -> dict:
        """GET /obp/<version>/users/current for the given token."""
        return self.get("/users/current", token=token)

    # ------------------------------------------------------------ onboarding

    def create_user(
        self, email: str, username: str, password: str, first_name: str, last_name: str
    ) -> dict:
        """POST /obp/<version>/users (no authentication). Returns the DCR user."""
        payload = {
            "email": email,
            "username": username,
            "password": password,
            "first_name": first_name,
            "last_name": last_name,
        }
        return self._request("POST", self._api_url("/users"), json=payload)

    def validate_email(self, token: str) -> dict:
        """POST /obp/<version>/users/email-validation with the token from the e-mail."""
        return self._request(
            "POST", self._api_url("/users/email-validation"), json={"token": token}
        )

    def service_token(self) -> str:
        """DirectLogin token of the configured service account (cached)."""
        from django.core.cache import cache

        cfg = settings.DCR
        if not cfg["SERVICE_USERNAME"] or not cfg["SERVICE_PASSWORD"]:
            raise DCRConfigurationError("DCR service account is not configured")
        key = "dcr:service_token"
        token = cache.get(key)
        if not token:
            token = self.direct_login(cfg["SERVICE_USERNAME"], cfg["SERVICE_PASSWORD"])
            cache.set(key, token, 50 * 60)
        return token

    def send_password_reset(self, username: str, email: str) -> dict:
        """POST management/user/reset-password-url as the service account; DCR e-mails the link."""
        return self.post(
            "/management/user/reset-password-url",
            token=self.service_token(),
            json={"username": username, "email": email},
        )

    # ------------------------------------------------------- dynamic entities
    # OGCR domain objects (operator, activity, parcel, ...) are OBP dynamic entities
    # served under /obp/dynamic-entity/<entity> (no API version in the path).
    # Writes require the entitlement CanCreateDynamicEntity_System<entity> etc.

    DYNAMIC_ENTITY_PATH = "/obp/dynamic-entity"

    def entity_url(self, entity: str, entity_id: str | None = None) -> str:
        url = f"{self.base_url}{self.DYNAMIC_ENTITY_PATH}/{entity}"
        return f"{url}/{entity_id}" if entity_id else url

    def list_entities(self, entity: str, token: str, params: dict | None = None) -> list:
        data = self._request("GET", self.entity_url(entity), token=token, params=params)
        return self.unwrap_list(data, entity)

    def get_entity(self, entity: str, entity_id: str, token: str) -> dict:
        data = self._request("GET", self.entity_url(entity, entity_id), token=token)
        return self.unwrap_entity(data, entity)

    def create_entity(self, entity: str, payload: dict, token: str) -> dict:
        data = self._request("POST", self.entity_url(entity), token=token, json=payload)
        return self.unwrap_entity(data, entity)

    def update_entity(self, entity: str, entity_id: str, payload: dict, token: str) -> dict:
        data = self._request("PUT", self.entity_url(entity, entity_id), token=token, json=payload)
        return self.unwrap_entity(data, entity)

    def delete_entity(self, entity: str, entity_id: str, token: str) -> None:
        self._request("DELETE", self.entity_url(entity, entity_id), token=token)

    def entity_exists(self, entity: str) -> bool | None:
        """Whether DCR declares the entity at all; None when the docs cannot be read."""
        catalogue = self._entity_catalogue()
        return None if catalogue is None else entity in catalogue

    def entity_fields(self, entity: str) -> set[str] | None:
        """
        Property names DCR currently accepts for an entity (from the dynamic resource docs,
        cached for an hour). None when the docs cannot be read, in which case callers send
        everything they have.
        """
        catalogue = self._entity_catalogue()
        names = (catalogue or {}).get(entity)
        return set(names) if names else None

    def _entity_catalogue(self) -> dict[str, list[str]] | None:
        from django.core.cache import cache

        key = "dcr:entity_fields"
        fields = cache.get(key)
        if fields is None:
            try:
                data = self._request(
                    "GET",
                    f"{self.base_url}/obp/{self.api_version}/resource-docs/{self.api_version}/obp",
                    params={"content": "dynamic"},
                )
            except DCRError as exc:
                logger.warning("could not read DCR resource docs: %s", exc)
                return None
            fields = {}
            for doc in data.get("resource_docs", []) if isinstance(data, dict) else []:
                url = doc.get("request_url", "")
                if doc.get("request_verb") == "POST" and url.count("/") == 1:
                    fields[url.strip("/")] = sorted((doc.get("example_request_body") or {}).keys())
            cache.set(key, fields, 3600)
        return fields

    @staticmethod
    def unwrap_list(data, entity: str) -> list:
        """OBP wraps lists as {"<entity>_list": [...]}; tolerate a bare list too."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in (f"{entity}_list", "list", "results"):
                if isinstance(data.get(key), list):
                    return data[key]
            for value in data.values():
                if isinstance(value, list):
                    return value
        return []

    @staticmethod
    def unwrap_entity(data, entity: str):
        """DCR wraps single entities as {"<entity>": {...}}; tolerate a flat object too."""
        if isinstance(data, dict) and isinstance(data.get(entity), dict):
            return data[entity]
        return data

    @classmethod
    def entity_id_from(cls, data, entity: str) -> str | None:
        """Extract the identifier DCR assigned to a created entity."""
        data = cls.unwrap_entity(data, entity)
        if not isinstance(data, dict):
            return None
        for key in (f"{entity}_id", "id", f"{entity}Id"):
            value = data.get(key)
            if value:
                return str(value)
        return None

    # --------------------------------------------------------------- generic

    def get(self, path: str, token: str | None = None, params: dict | None = None):
        return self._request("GET", self._api_url(path), token=token, params=params)

    def post(self, path: str, token: str | None = None, json=None):
        return self._request("POST", self._api_url(path), token=token, json=json)

    def put(self, path: str, token: str | None = None, json=None):
        return self._request("PUT", self._api_url(path), token=token, json=json)

    def delete(self, path: str, token: str | None = None):
        return self._request("DELETE", self._api_url(path), token=token)

    # -------------------------------------------------------------- internal

    def _api_url(self, path: str) -> str:
        return self.api_root + "/" + path.lstrip("/")

    def _request(self, method, url, token=None, headers=None, params=None, json=None):
        if not self.base_url:
            raise DCRConfigurationError()

        request_headers = {"Accept": "application/json"}
        if headers:
            request_headers.update(headers)
        if token:
            request_headers["Authorization"] = f'DirectLogin token="{token}"'

        try:
            response = self.session.request(
                method, url, headers=request_headers, params=params, json=json, timeout=self.timeout
            )
        except requests.RequestException as exc:
            logger.warning("DCR request failed: %s %s (%s)", method, url, exc)
            raise DCRUnavailable() from exc

        payload = self._parse(response)
        if response.status_code in (401, 403):
            message = self._message(payload, DCRAuthError.default_message)
            raise DCRAuthError(message, response.status_code, payload)
        if response.status_code >= 400:
            logger.warning("DCR error %s on %s %s: %s", response.status_code, method, url, payload)
            raise DCRError(
                self._message(payload, f"DCR returned HTTP {response.status_code}"),
                response.status_code,
                payload,
            )
        return payload

    @staticmethod
    def _parse(response: requests.Response):
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    @staticmethod
    def _message(payload, default: str) -> str:
        if isinstance(payload, dict):
            return payload.get("message") or payload.get("error") or default
        return default


def get_client() -> DCRClient:
    """Factory used by views/auth; patched in tests."""
    return DCRClient()
