"""OpenAI Codex / ChatGPT subscription OAuth constants (matches Codex CLI)."""
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_ISSUER = "https://auth.openai.com"
CODEX_OAUTH_AUTHORIZE_URL = f"{CODEX_OAUTH_ISSUER}/oauth/authorize"
CODEX_OAUTH_TOKEN_URL = f"{CODEX_OAUTH_ISSUER}/oauth/token"
CODEX_OAUTH_CALLBACK_PORT = 1455
CODEX_OAUTH_CALLBACK_PATH = "/auth/callback"
CODEX_OAUTH_REDIRECT_URI = f"http://localhost:{CODEX_OAUTH_CALLBACK_PORT}{CODEX_OAUTH_CALLBACK_PATH}"
CODEX_OAUTH_SCOPES = (
    "openid profile email offline_access api.connectors.read api.connectors.invoke"
)
CODEX_OAUTH_ORIGINATOR = "codex_cli_rs"
CODEX_OAUTH_REQUESTED_API_KEY_TOKEN = "openai-api-key"
CODEX_OAUTH_USER_AGENT = "codex-cli/2.1.98 (external, cli)"
