"""Tiny MCP server for tests: streamable HTTP / SSE, optionally with in-memory OAuth.

Usage: python mcp_demo_server.py PORT [oauth|app|open|sse] [access-token-ttl-seconds] [app-redirect-uri]

``app`` is OAuth *without* dynamic client registration (like Slack): only the
pre-registered client ``demo-app`` / ``demo-secret`` may sign in, and only back
to ``app-redirect-uri``.
"""
import secrets, sys, time
from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizationParams,
                                      RefreshToken, construct_redirect_uri)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.fastmcp import FastMCP
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8931
MODE = sys.argv[2] if len(sys.argv) > 2 else "oauth"      # oauth | app | open | sse | bearer
TTL = int(sys.argv[3]) if len(sys.argv) > 3 else 3600
APP_REDIRECT = sys.argv[4] if len(sys.argv) > 4 else "http://localhost:3118/callback"


class Provider:
    def __init__(self):
        self.clients, self.codes, self.access, self.refresh = {}, {}, {}, {}

    async def get_client(self, client_id):
        return self.clients.get(client_id)

    async def register_client(self, client_info):
        self.clients[client_info.client_id] = client_info

    async def authorize(self, client, params: AuthorizationParams):
        code = secrets.token_hex(8)
        self.codes[code] = AuthorizationCode(
            code=code, scopes=params.scopes or [], expires_at=time.time() + 300,
            client_id=client.client_id, code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource)
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(self, client, code):
        return self.codes.get(code)

    def _issue(self, client_id, scopes):
        at, rt = "at-" + secrets.token_hex(8), "rt-" + secrets.token_hex(8)
        self.access[at] = AccessToken(token=at, client_id=client_id, scopes=scopes, expires_at=int(time.time()) + TTL)
        self.refresh[rt] = RefreshToken(token=rt, client_id=client_id, scopes=scopes)
        return OAuthToken(access_token=at, token_type="Bearer", expires_in=TTL, refresh_token=rt, scope=" ".join(scopes))

    async def exchange_authorization_code(self, client, code: AuthorizationCode):
        del self.codes[code.code]
        return self._issue(client.client_id, code.scopes)

    async def load_refresh_token(self, client, refresh_token):
        return self.refresh.get(refresh_token)

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        print("REFRESHED", flush=True)
        return self._issue(client.client_id, scopes or refresh_token.scopes)

    async def load_access_token(self, token):
        at = self.access.get(token)
        if at and at.expires_at and at.expires_at < time.time():
            return None
        return at

    async def revoke_token(self, token):
        self.access.pop(getattr(token, "token", ""), None)


base = f"http://127.0.0.1:{PORT}"
kwargs = dict(host="127.0.0.1", port=PORT)
if MODE in ("oauth", "app"):
    provider = Provider()
    if MODE == "app":
        provider.clients["demo-app"] = OAuthClientInformationFull(
            client_id="demo-app", client_secret="demo-secret", redirect_uris=[APP_REDIRECT],
            grant_types=["authorization_code", "refresh_token"], response_types=["code"],
            token_endpoint_auth_method="client_secret_post", scope="user")
    kwargs.update(
        auth_server_provider=provider,
        auth=AuthSettings(issuer_url=base, resource_server_url=f"{base}/mcp",
                          client_registration_options=ClientRegistrationOptions(
                              enabled=MODE == "oauth", valid_scopes=["user"], default_scopes=["user"]),
                          required_scopes=["user"]))
app = FastMCP("demo", **kwargs)


@app.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


if __name__ == "__main__":
    app.run(transport="sse" if MODE == "sse" else "streamable-http")
