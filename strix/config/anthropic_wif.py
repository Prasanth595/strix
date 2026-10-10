"""Anthropic Workload Identity Federation: trade an IdP-issued JWT for a
short-lived Anthropic access token, and keep it fresh for the whole scan.

Configured with the same env vars the Anthropic SDKs read
(``ANTHROPIC_FEDERATION_RULE_ID``, ``ANTHROPIC_ORGANIZATION_ID``,
``ANTHROPIC_SERVICE_ACCOUNT_ID``, ``ANTHROPIC_WORKSPACE_ID``, and
``ANTHROPIC_IDENTITY_TOKEN_FILE`` or ``ANTHROPIC_IDENTITY_TOKEN``), so one
environment serves Strix and any SDK workload alongside it. Inside a GitHub
Actions job with ``id-token: write`` and no token configured, the JWT is fetched
from the runner's OIDC endpoint instead.

The minted ``sk-ant-oat01-`` token lives in memory only; it is never persisted.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, cast

import requests

from strix.config.loader import load_settings


logger = logging.getLogger(__name__)


DEFAULT_BASE_URL = "https://api.anthropic.com"
TOKEN_PATH = "/v1/oauth/token"  # noqa: S105  # nosec B105 - URL path, not a secret
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"
GITHUB_AUDIENCE = "https://api.anthropic.com"

_TIMEOUT_S = 30
# The Anthropic SDKs' refresh schedule: try early, and only fail once the cached
# token is too close to expiry to send.
_ADVISORY_REFRESH_S = 120
_MANDATORY_REFRESH_S = 30

_lock = threading.Lock()
_cached: tuple[str, float] | None = None  # (access token, expires-at epoch seconds)


class WifAuthError(RuntimeError):
    """The federated token exchange (or fetching the JWT for it) failed."""


def is_configured() -> bool:
    """Every field the exchange needs is set, and a JWT source exists."""
    wif = load_settings().anthropic_wif
    ids = (wif.federation_rule_id, wif.organization_id, wif.service_account_id)
    return all(ids) and _has_identity_source()


def is_anthropic_model(model_name: str) -> bool:
    """Whether LiteLLM sends ``model_name`` to Anthropic's own API.

    Claude on Bedrock or Vertex carries that provider's prefix and keeps that
    provider's auth.
    """
    name = model_name.strip().lower()
    for prefix in ("litellm/", "any-llm/"):
        name = name.removeprefix(prefix)
    return name.startswith("anthropic/") or ("/" not in name and name.startswith("claude"))


def applies(model_name: str | None, api_key: str | None = None) -> bool:
    """Whether ``model_name`` authenticates by Workload Identity Federation.

    An explicit API key wins, as it does in the Anthropic SDKs.
    """
    if api_key or not model_name:
        return False
    return is_anthropic_model(model_name) and is_configured()


def _has_identity_source() -> bool:
    return bool(
        load_settings().anthropic_wif.identity_token_file
        or os.environ.get("ANTHROPIC_IDENTITY_TOKEN")
        or _github_oidc_available()
    )


def _github_oidc_available() -> bool:
    return bool(
        os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
        and os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    )


def read_identity_token() -> str:
    """The IdP JWT for one exchange, read fresh every time.

    JWTs carrying a ``jti`` are single-use, and projected token files rotate on
    disk, so a cached JWT would be rejected on the next exchange.
    """
    token_file = load_settings().anthropic_wif.identity_token_file
    if token_file:
        try:
            token = Path(token_file).expanduser().read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise WifAuthError(f"cannot read ANTHROPIC_IDENTITY_TOKEN_FILE: {exc}") from exc
        if not token:
            raise WifAuthError(f"ANTHROPIC_IDENTITY_TOKEN_FILE {token_file} is empty")
        return token
    literal = (os.environ.get("ANTHROPIC_IDENTITY_TOKEN") or "").strip()
    if literal:
        return literal
    if _github_oidc_available():
        return _fetch_github_oidc_token()
    raise WifAuthError(
        "no identity token: set ANTHROPIC_IDENTITY_TOKEN_FILE or ANTHROPIC_IDENTITY_TOKEN"
    )


def _fetch_github_oidc_token() -> str:
    url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
    try:
        resp = requests.get(
            url,
            params={"audience": GITHUB_AUDIENCE},
            headers={"Authorization": f"Bearer {os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']}"},
            timeout=_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise WifAuthError(f"GitHub OIDC token request failed: {exc}") from exc
    if resp.status_code != 200:
        raise WifAuthError(f"GitHub OIDC token request failed: HTTP {resp.status_code}")
    value = _json(resp).get("value")
    if not isinstance(value, str) or not value:
        raise WifAuthError("GitHub OIDC token response has no value")
    return value


def exchange() -> tuple[str, float]:
    """Run one ``jwt-bearer`` exchange; return ``(access_token, expires_at)``."""
    settings = load_settings()
    wif = settings.anthropic_wif
    body: dict[str, str] = {
        "grant_type": GRANT_TYPE,
        "assertion": read_identity_token(),
        "federation_rule_id": wif.federation_rule_id or "",
        "organization_id": wif.organization_id or "",
        "service_account_id": wif.service_account_id or "",
    }
    if wif.workspace_id:
        body["workspace_id"] = wif.workspace_id
    url = token_url(settings.llm.api_base)
    started = time.time()
    try:
        resp = requests.post(url, json=body, timeout=_TIMEOUT_S)
    except requests.RequestException as exc:
        raise WifAuthError(f"token exchange request failed: {exc}") from exc
    payload = _json(resp)
    if resp.status_code != 200:
        raise WifAuthError(_describe_error(resp, payload))
    access = payload.get("access_token")
    expires_in = payload.get("expires_in")
    if not isinstance(access, str) or not access or not isinstance(expires_in, int | float):
        raise WifAuthError("token exchange response is missing access_token or expires_in")
    logger.info("Anthropic workload identity token minted (expires in %ss)", int(expires_in))
    return access, started + float(expires_in)


def token_url(api_base: str | None) -> str:
    """The exchange endpoint on the same host the Messages calls go to."""
    base = (api_base or DEFAULT_BASE_URL).rstrip("/")
    base = base.removesuffix("/v1")
    return f"{base}{TOKEN_PATH}"


def _json(resp: requests.Response) -> dict[str, Any]:
    try:
        data: object = resp.json()
    except ValueError:
        return {}
    return cast("dict[str, Any]", data) if isinstance(data, dict) else {}


def _describe_error(resp: requests.Response, payload: dict[str, Any]) -> str:
    error = payload.get("error")
    detail = ""
    if isinstance(error, dict):
        fields = cast("dict[str, Any]", error)
        detail = f"{fields.get('type', '')}: {fields.get('message', '')}".strip(": ")
    elif isinstance(error, str):
        detail = f"{error}: {payload.get('error_description', '')}".strip(": ")
    request_id = resp.headers.get("request-id")
    message = f"token exchange failed: HTTP {resp.status_code}"
    if detail:
        message += f" ({detail})"
    if request_id:
        message += f" [request-id {request_id}]"
    if resp.status_code == 401:
        message += "; see the authentication history under Settings > Workload identity"
    return message


def get_valid_token() -> str:
    """A usable access token, exchanging a new one when the cached one runs low.

    Inside the advisory window a failed exchange keeps serving the cached token;
    inside the mandatory window it raises.
    """
    global _cached  # noqa: PLW0603
    with _lock:
        now = time.time()
        if _cached is not None and now < _cached[1] - _ADVISORY_REFRESH_S:
            return _cached[0]
        try:
            _cached = exchange()
        except WifAuthError:
            if _cached is not None and now < _cached[1] - _MANDATORY_REFRESH_S:
                logger.warning(
                    "Anthropic workload identity refresh failed; using the cached token",
                    exc_info=True,
                )
                return _cached[0]
            raise
        return _cached[0]


def reset_cache() -> None:
    global _cached  # noqa: PLW0603
    with _lock:
        _cached = None
