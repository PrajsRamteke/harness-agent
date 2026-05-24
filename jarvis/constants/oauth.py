"""Anthropic OAuth (Claude Pro/Max subscription) constants."""
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
OAUTH_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
# Claude Code now uses platform.claude.com (console.anthropic.com redirects are stale).
OAUTH_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
OAUTH_REDIRECT_URI = "https://platform.claude.com/oauth/code/callback"
OAUTH_SCOPES = (
    "org:create_api_key user:profile user:inference "
    "user:sessions:claude_code user:mcp_servers user:file_upload"
)
# Wire betas required for OAuth subscription traffic (matches current Claude Code).
OAUTH_BETA_HEADER = (
    "oauth-2025-04-20,claude-code-20250219,interleaved-thinking-2025-05-14,"
    "effort-2025-11-24"
)
OAUTH_USER_AGENT = "claude-cli/2.1.98 (external, cli)"
OAUTH_TOKEN_USER_AGENT = OAUTH_USER_AGENT
# Anthropic OAuth wire protocol requires this exact string as the first system
# block. It is NOT the agent's user-facing identity — see repl/system.py and
# constants/system_prompt.py for the Jarvis/Harness Agent identity override.
OAUTH_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."
