"""OAuth 2.1 authorization server for the hosted MCP endpoint.

The MCP SDK supplies the protocol endpoints (metadata, /register for dynamic
client registration, /authorize, /token, /revoke) and checks PKCE, redirect
URIs and expiry. This provider supplies storage and the one step the SDK leaves
to us: sending the user to our own sign-in and consent page (hosted.py).

ChatGPT, Claude and other MCP clients register themselves through /register,
so users only ever paste the MCP URL.
"""

from __future__ import annotations

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from .accounts import AccountStore

SCOPE = "memory"


class MemLayerOAuthProvider:
    def __init__(self, accounts: AccountStore, public_url: str):
        self.accounts = accounts
        self.public_url = public_url.rstrip("/")
        self.resource = f"{self.public_url}/mcp"

    # -------------------------------------------------------------- clients

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        info = self.accounts.get_client(client_id)
        return OAuthClientInformationFull.model_validate(info) if info else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.accounts.save_client(client_info.client_id, client_info.model_dump(mode="json", exclude_none=True))

    # ------------------------------------------------------------ authorize

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        resource = (params.resource or self.resource).rstrip("/")
        if resource != self.resource:
            raise AuthorizeError("invalid_request", f"This server only issues tokens for {self.resource}")
        rid = self.accounts.create_request(client.client_id, {
            "state": params.state,
            "scopes": params.scopes or [SCOPE],
            "code_challenge": params.code_challenge,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "resource": resource,
        })
        return f"{self.public_url}/consent?request={rid}"

    async def load_authorization_code(self, client: OAuthClientInformationFull,
                                      authorization_code: str) -> AuthorizationCode | None:
        d = self.accounts.load_code(authorization_code)
        if not d or d["client_id"] != client.client_id:
            return None
        return AuthorizationCode(
            code=authorization_code, scopes=d["scopes"], expires_at=d["expires_at"], client_id=d["client_id"],
            code_challenge=d["code_challenge"], redirect_uri=AnyUrl(d["redirect_uri"]),
            redirect_uri_provided_explicitly=d["redirect_uri_provided_explicitly"], resource=d["resource"],
            subject=d["user_id"])

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: AuthorizationCode) -> OAuthToken:
        grant_id = self.accounts.redeem_code(authorization_code.code)
        if not grant_id:
            raise TokenError("invalid_grant", "authorization code already used")
        return self._token(grant_id, authorization_code.scopes)

    # -------------------------------------------------------------- refresh

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        t = self.accounts.lookup_token(refresh_token, "refresh")
        if not t or t["client_id"] != client.client_id:
            return None
        return RefreshToken(token=refresh_token, client_id=t["client_id"], scopes=t["scopes"],
                            resource=t["resource"], subject=t["user_id"])

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
                                     scopes: list[str]) -> OAuthToken:
        grant_id = self.accounts.rotate_refresh(refresh_token.token)
        if not grant_id:
            raise TokenError("invalid_grant", "refresh token already used")
        return self._token(grant_id, scopes or refresh_token.scopes)

    def _token(self, grant_id: str, scopes: list[str]) -> OAuthToken:
        t = self.accounts.issue_tokens(grant_id)
        return OAuthToken(access_token=t["access_token"], refresh_token=t["refresh_token"],
                          expires_in=t["expires_in"], scope=" ".join(scopes))

    # --------------------------------------------------------------- access

    async def load_access_token(self, token: str) -> AccessToken | None:
        t = self.accounts.lookup_token(token, "access")
        if not t:
            return None
        self.accounts.touch_grant(t["grant_id"], t["last_used_at"])
        return AccessToken(token=token, client_id=t["client_id"], scopes=t["scopes"], expires_at=t["expires_at"],
                           resource=t["resource"], subject=t["user_id"], claims={"grant_id": t["grant_id"]})

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        t = self.accounts.lookup_token(token.token, "access") or self.accounts.lookup_token(token.token, "refresh")
        if t:
            self.accounts.revoke_grant(t["grant_id"])
