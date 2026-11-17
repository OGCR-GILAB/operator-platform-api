"""
Thin HTTP client for the DCR platform (Open Bank Project style API).

Authentication model:
  * the application is registered on DCR as a *consumer* (consumer key / secret)
  * users log in with DirectLogin -> DCR returns a token
  * every further call carries  Authorization: DirectLogin token="<token>"
"""

import logging
from urllib.parse import quote

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
        bank_id: str | None = None,
    ):
        cfg = settings.DCR
        self.base_url = (base_url if base_url is not None else cfg["BASE_URL"]).rstrip("/")
        self.api_version = api_version or cfg["API_VERSION"]
        self.consumer_key = consumer_key if consumer_key is not None else cfg["CONSUMER_KEY"]
        self.timeout = timeout or cfg["TIMEOUT"]
        self.bank_id = bank_id if bank_id is not None else (cfg.get("BANK_ID") or "")
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
        self,
        email: str,
        username: str,
        password: str,
        first_name: str,
        last_name: str,
        mobile_phone_number: str = "",
    ) -> dict:
        """POST /obp/<version>/users (no authentication). Returns the DCR user."""
        payload = {
            "email": email,
            "username": username,
            "password": password,
            "first_name": first_name,
            "last_name": last_name,
        }
        if mobile_phone_number:
            payload["mobile_phone_number"] = mobile_phone_number  # optional since v7.0.0
        return self._request("POST", self._api_url("/users"), json=payload)

    def resend_validation_email(self, username: str, email: str) -> dict:
        """POST /obp/<version>/users/validation-emails (no authentication, v7.0.0+)."""
        return self._request(
            "POST",
            self._api_url("/users/validation-emails"),
            json={"username": username, "email": email},
        )

    def password_config(self) -> dict:
        """GET /obp/<version>/public/password-config: the password policies DCR enforces."""
        return self._request("GET", self._api_url("/public/password-config"))

    def update_mobile_phone_number(self, token: str, number: str) -> dict:
        """PUT /obp/<version>/my/user/mobile-phone-number for the token's user (v7.0.0+)."""
        return self.put(
            "/my/user/mobile-phone-number", token=token, json={"mobile_phone_number": number}
        )

    def my_role(self, token: str, role_name: str, bank_id: str | None = None) -> dict:
        """
        GET /obp/<version>/banks/<bank>/my/roles/<role> (v7.0.0+): whether the caller holds
        the role at the bank, through the user or the consumer (`held_by`).
        """
        bank = bank_id or self.bank_id
        return self.get(f"/banks/{bank}/my/roles/{role_name}", token=token)

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

    def send_password_reset(self, username: str, email: str, user_id: str) -> dict:
        """
        POST management/user/reset-password-url as the service account; DCR e-mails the link.
        DCR (v6.0.0+) needs the user's id next to username and e-mail.
        """
        return self.post(
            "/management/user/reset-password-url",
            token=self.service_token(),
            json={"username": username, "email": email, "user_id": user_id},
        )

    def users_by_email(self, email: str) -> list[dict]:
        """DCR users with this e-mail, read as the service account (CanGetAnyUser)."""
        data = self.get(
            f"/users/email/{quote(email, safe='@')}/terminator", token=self.service_token()
        )
        users = data.get("users", []) if isinstance(data, dict) else data
        return [u for u in users or [] if isinstance(u, dict)]

    # ------------------------------------------------------- dynamic entities
    # OGCR domain objects (operator, activity, parcel, ...) are OBP dynamic entities
    # (no API version in the path). With DCR_BANK_ID set they are bank-level entities:
    #   /obp/dynamic-entity/banks/<bank>/<entity>, roles Can<Action>DynamicEntityRecord_<entity>
    # and vocabularies are also readable without authentication under .../public/<entity>.
    # An empty DCR_BANK_ID selects the older system-level model /obp/dynamic-entity/<entity>.

    DYNAMIC_ENTITY_PATH = "/obp/dynamic-entity"

    @property
    def entity_root(self) -> str:
        root = f"{self.base_url}{self.DYNAMIC_ENTITY_PATH}"
        return f"{root}/banks/{self.bank_id}" if self.bank_id else root

    def entity_url(self, entity: str, entity_id: str | None = None, public: bool = False) -> str:
        url = f"{self.entity_root}/public/{entity}" if public else f"{self.entity_root}/{entity}"
        return f"{url}/{entity_id}" if entity_id else url

    def list_entities(self, entity: str, token: str, params: dict | None = None) -> list:
        data = self._request("GET", self.entity_url(entity), token=token, params=params)
        return self.unwrap_list(data, entity)

    def list_public_entities(self, entity: str) -> list:
        """Records DCR publishes without authentication (bank-level vocabularies)."""
        if not self.bank_id:
            raise DCRConfigurationError("Public entity records need DCR_BANK_ID")
        data = self._request("GET", self.entity_url(entity, public=True))
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
        cached for six hours). None when the docs cannot be read, in which case callers send
        everything they have.
        """
        catalogue = self._entity_catalogue()
        names = (catalogue or {}).get(entity)
        return set(names) if names else None

    def _entity_catalogue(self) -> dict[str, list[str]] | None:
        from django.core.cache import cache

        key = f"dcr:entity_fields:{self.bank_id or 'system'}"
        fields = cache.get(key)
        if fields is None:
            params = {"content": "dynamic"}
            prefix = ""
            if self.bank_id:
                params["bank-id"] = self.bank_id
                prefix = f"/banks/{self.bank_id}"
            try:
                data = self._request(
                    "GET",
                    f"{self.base_url}/obp/{self.api_version}/resource-docs/{self.api_version}/obp",
                    params=params,
                    # the docs cover every bank's entities (several MB); allow for that
                    timeout=max(self.timeout, 90),
                )
            except DCRError as exc:
                logger.warning("could not read DCR resource docs: %s", exc)
                return None
            fields = {}
            for doc in data.get("resource_docs", []) if isinstance(data, dict) else []:
                url = doc.get("request_url", "")
                if doc.get("request_verb") != "POST" or not url.startswith(prefix + "/"):
                    continue
                name = url[len(prefix) + 1 :]
                if name and "/" not in name:
                    fields[name] = sorted((doc.get("example_request_body") or {}).keys())
            cache.set(key, fields, 6 * 3600)
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

    def _request(self, method, url, token=None, headers=None, params=None, json=None, timeout=None):
        if not self.base_url:
            raise DCRConfigurationError()

        request_headers = {"Accept": "application/json"}
        if headers:
            request_headers.update(headers)
        if token:
            request_headers["Authorization"] = f'DirectLogin token="{token}"'

        try:
            response = self.session.request(
                method,
                url,
                headers=request_headers,
                params=params,
                json=json,
                timeout=timeout or self.timeout,
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
