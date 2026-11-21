"""Google OAuth 2.0 for YouTube channels (spec 5.9: an admin connects the channel).

Two ways to connect:
- browser redirect (`web`): /api/channels/oauth/callback receives the code. Google allows only
  https or localhost redirect URIs, so this needs the app behind HTTPS (or opened on localhost).
- device code (`device`): the server shows a short code, the admin enters it at google.com/device
  on any computer and the server polls for the result. Works for an internal http:// server.
  Needs an OAuth client of type "TVs and Limited Input devices".

The client secret goes to the token endpoint as HTTP Basic auth (client_secret_basic), so a proxy
can add it instead of the app holding it (GOOGLE_CLIENT_SECRET=proxy-injected).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from .base import PublishError

SCOPE = "https://www.googleapis.com/auth/youtube"  # upload, thumbnails, status, delete
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


@dataclass
class Tokens:
    access_token: str
    expires_in: int
    refresh_token: str | None = None
    scope: str | None = None


@dataclass
class DeviceCode:
    device_code: str
    user_code: str
    verification_url: str
    expires_in: int
    interval: int


class DevicePending(Exception):
    """The admin has not finished on google.com/device yet. `slow_down`: poll less often."""

    def __init__(self, slow_down: bool = False) -> None:
        super().__init__("pending")
        self.slow_down = slow_down


OAUTH_ERRORS = {
    "access_denied": "Google 계정에서 권한 요청을 거부했습니다",
    "expired_token": "코드가 만료됐습니다. 다시 연결해 주세요",
    "invalid_grant": "인증이 만료되었거나 취소됐습니다. 채널을 다시 연결해 주세요",
    "invalid_client": "OAuth 클라이언트 ID 또는 시크릿이 맞지 않습니다",
    "unauthorized_client": "이 OAuth 클라이언트로는 이 방식의 연결을 쓸 수 없습니다 (클라이언트 유형 확인)",
    "invalid_scope": "요청한 권한(YouTube)을 이 클라이언트에서 쓸 수 없습니다",
}


class GoogleOAuth:
    def __init__(self, client_id: str, client_secret: str, *, token_base: str = "https://oauth2.googleapis.com",
                 auth_url: str = "https://accounts.google.com/o/oauth2/v2/auth",
                 transport: httpx.BaseTransport | None = None, timeout: float = 20.0) -> None:  # fmt: skip
        if not client_id:
            raise PublishError("Google OAuth 클라이언트 ID(GOOGLE_CLIENT_ID)가 설정되지 않았습니다", "invalid")
        self.client_id = client_id
        self._auth_url = auth_url
        self._token_base = token_base.rstrip("/")
        self._client = httpx.Client(timeout=timeout, transport=transport, auth=(client_id, client_secret or ""))
        self._public = httpx.Client(timeout=timeout, transport=transport)

    def authorization_url(self, redirect_uri: str, state: str) -> str:
        return self._auth_url + "?" + urlencode({
            "client_id": self.client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPE,
            "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state,
        })  # fmt: skip

    def _post(self, client: httpx.Client, path: str, data: dict) -> dict:
        try:
            r = client.post(self._token_base + path, data=data)
        except httpx.TransportError as e:
            raise PublishError(f"Google 인증 서버에 연결하지 못했습니다: {e}", "network") from e
        body: dict = {}
        try:
            body = r.json()
        except ValueError:
            pass
        if r.status_code >= 500:
            raise PublishError(f"Google 인증 서버 오류 ({r.status_code})", "server")
        if r.status_code >= 400 or "error" in body:
            code = str(body.get("error", r.status_code))
            if code in ("authorization_pending", "slow_down"):
                raise DevicePending(code == "slow_down")
            kind = "auth" if code in ("invalid_grant", "access_denied", "expired_token") else "invalid"
            message = OAUTH_ERRORS.get(code) or body.get("error_description") or code
            raise PublishError(message, kind, reason=code)
        return body

    @staticmethod
    def _tokens(body: dict) -> Tokens:
        return Tokens(body["access_token"], int(body.get("expires_in", 3600)), body.get("refresh_token"), body.get("scope"))

    def exchange_code(self, code: str, redirect_uri: str) -> Tokens:
        body = self._post(self._client, "/token", {"grant_type": "authorization_code", "code": code,
                                                   "redirect_uri": redirect_uri})  # fmt: skip
        return self._tokens(body)

    def device_code(self) -> DeviceCode:
        body = self._post(self._public, "/device/code", {"client_id": self.client_id, "scope": SCOPE})
        return DeviceCode(body["device_code"], body["user_code"], body.get("verification_url") or body.get("verification_uri"),
                          int(body.get("expires_in", 1800)), int(body.get("interval", 5)))  # fmt: skip

    def poll_device(self, device_code: str) -> Tokens:
        """Raises DevicePending until the admin has approved."""
        return self._tokens(self._post(self._client, "/token", {"grant_type": DEVICE_GRANT, "device_code": device_code}))

    def refresh(self, refresh_token: str) -> Tokens:
        return self._tokens(self._post(self._client, "/token", {"grant_type": "refresh_token", "refresh_token": refresh_token}))

    def revoke(self, token: str) -> None:
        try:
            self._public.post(self._token_base + "/revoke", data={"token": token})
        except httpx.TransportError:
            pass  # best effort: the tokens are deleted here anyway
