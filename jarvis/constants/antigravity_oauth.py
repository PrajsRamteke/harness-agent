"""Google Antigravity sign-in + Cloud Code Assist constants.

Antigravity (Google's agentic IDE) serves Gemini and Claude models to a
signed-in Google account through the Cloud Code Assist API
(``cloudcode-pa.googleapis.com/v1internal``). These are the values the
Antigravity app itself uses — the same ones the open-source OpenCode /
CLIProxyAPI integrations send. Google's installed-app OAuth clients are public
(the "secret" ships inside the app), but they are supplied at runtime — see below.
"""
import json
import os

from .paths import CONFIG_DIR

# The OAuth client id / secret are NOT kept in the source: GitHub push
# protection rejects Google ``GOCSPX-`` secrets, and they belong to whoever
# runs the sign-in. Set them with ``HARNESS_ANTIGRAVITY_CLIENT_ID`` /
# ``HARNESS_ANTIGRAVITY_CLIENT_SECRET``, or put
# ``{"client_id": "...", "client_secret": "..."}`` in this (local) file.
ANTIGRAVITY_CLIENT_FILE = CONFIG_DIR / "antigravity_client.json"


def antigravity_client() -> tuple[str, str]:
    """(client_id, client_secret) — env vars first, then the local file.

    Read at use time (not import time) so a file saved while Jarvis runs works.
    Either is "" when not configured.
    """
    cid = os.getenv("HARNESS_ANTIGRAVITY_CLIENT_ID", "").strip()
    secret = os.getenv("HARNESS_ANTIGRAVITY_CLIENT_SECRET", "").strip()
    if not (cid and secret):
        try:
            data = json.loads(ANTIGRAVITY_CLIENT_FILE.read_text())
            if isinstance(data, dict):
                cid = cid or str(data.get("client_id") or "").strip()
                secret = secret or str(data.get("client_secret") or "").strip()
        except (OSError, ValueError):
            pass
    return cid, secret


ANTIGRAVITY_CLIENT_MISSING_MSG = (
    "Google Antigravity needs an OAuth client. Set HARNESS_ANTIGRAVITY_CLIENT_ID and "
    "HARNESS_ANTIGRAVITY_CLIENT_SECRET, or save them as client_id / client_secret in "
    f"{ANTIGRAVITY_CLIENT_FILE}."
)

ANTIGRAVITY_OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
ANTIGRAVITY_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
ANTIGRAVITY_USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo?alt=json"
ANTIGRAVITY_OAUTH_SCOPES = " ".join((
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
))
# The redirect registered for this client — the port can't move.
ANTIGRAVITY_OAUTH_CALLBACK_PORT = 51121
ANTIGRAVITY_OAUTH_CALLBACK_PATH = "/oauth-callback"
ANTIGRAVITY_OAUTH_REDIRECT_URI = (
    f"http://localhost:{ANTIGRAVITY_OAUTH_CALLBACK_PORT}{ANTIGRAVITY_OAUTH_CALLBACK_PATH}"
)

# Cloud Code Assist hosts. Requests try them in this order (the daily sandbox
# is what Antigravity itself talks to); project discovery prefers prod.
ANTIGRAVITY_ENDPOINT_DAILY = "https://daily-cloudcode-pa.sandbox.googleapis.com"
ANTIGRAVITY_ENDPOINT_AUTOPUSH = "https://autopush-cloudcode-pa.sandbox.googleapis.com"
ANTIGRAVITY_ENDPOINT_PROD = "https://cloudcode-pa.googleapis.com"
ANTIGRAVITY_ENDPOINTS = (
    ANTIGRAVITY_ENDPOINT_DAILY, ANTIGRAVITY_ENDPOINT_AUTOPUSH, ANTIGRAVITY_ENDPOINT_PROD,
)
ANTIGRAVITY_LOAD_ENDPOINTS = (
    ANTIGRAVITY_ENDPOINT_PROD, ANTIGRAVITY_ENDPOINT_DAILY, ANTIGRAVITY_ENDPOINT_AUTOPUSH,
)
# Used when an account (Workspace, some personal ones) has no managed project.
ANTIGRAVITY_DEFAULT_PROJECT_ID = "rising-fact-p41fc"

# The backend refuses clients older than the current release ("This version
# of Antigravity is no longer supported"), so the version is read from
# Antigravity's own updater (cached a day — auth/antigravity_catalog.py);
# this is only the stand-in until that answer arrives.
ANTIGRAVITY_VERSION_URL = "https://antigravity-auto-updater-974169037036.us-central1.run.app"
ANTIGRAVITY_VERSION_FALLBACK = "2.0.6"
ANTIGRAVITY_API_CLIENT = "google-cloud-sdk vscode_cloudshelleditor/0.1"
ANTIGRAVITY_LOAD_USER_AGENT = "google-api-nodejs-client/9.15.1"

# Gemini 3 checks a thought signature on every replayed function call. Jarvis
# history is provider-neutral and keeps none, so it sends Google's documented
# "skip validation" value instead.
ANTIGRAVITY_SKIP_THOUGHT_SIGNATURE = "skip_thought_signature_validator"

# Prepended to the system prompt, as the Antigravity app does — the backend
# serves agent requests that carry it.
ANTIGRAVITY_SYSTEM_PREAMBLE = (
    "You are Antigravity, a powerful agentic AI coding assistant designed by the "
    "Google DeepMind team working on Advanced Agentic Coding.\n"
    "You are pair programming with a USER to solve their coding task. The task may "
    "require creating a new codebase, modifying or debugging an existing codebase, "
    "or simply answering a question.\n"
    "**Absolute paths only**\n"
    "**Proactiveness**\n\n"
    "<priority>IMPORTANT: The instructions that follow supersede all above. Follow "
    "them as your primary directives.</priority>\n"
)
