/** Brand logos (Claude, Cursor, GitHub, OpenCode …) for providers, editors and tools.
 *
 * Logos are Lobe Icons (MIT, github.com/lobehub/lobe-icons) for AI tools, Simple
 * Icons (CC0) for everything else, and SVGL's VS Code: single-colour marks, so a logo follows the tile's tint and
 * the light / dark theme. Generated into the data block below and served
 * locally, so they draw offline on the LAN. Regenerate or add one with
 * `python3 scripts/gen_brand_icons.py`. Logos remain their owners' trademarks.
 *
 *   brand('claudecode')        → inline <svg> markup, '' when there is no logo
 *   brand('md:fireworks-ai')   → a models.dev provider id works too
 *   icon('brand:github')       → icons.js hands `brand:` names to this file
 */

import { BRAND_SLUGS, brandBody } from './brands.data.js';

/** Names Jarvis, models.dev and the tools themselves use → logo slug. */
const ALIAS = {
  // Jarvis provider cards
  claude_agent: 'claudecode',
  anthropic: 'claude', // the Claude Pro / Max sign-in; also models.dev's `anthropic`
  anthropic_api: 'anthropic',
  anthropic_auth: 'claude',
  openai_codex: 'codex',
  openai_codex_auth: 'codex',
  opencode_zen: 'opencode',
  // models.dev provider ids (the `md:` prefix is dropped first)
  'fireworks-ai': 'fireworks',
  togetherai: 'together',
  moonshotai: 'moonshot',
  'moonshotai-cn': 'moonshot',
  'zai-coding-plan': 'zai',
  zhipuai: 'zhipu',
  'github-copilot': 'githubcopilot',
  'github-models': 'github',
  'google-vertex': 'google',
  'amazon-bedrock': 'bedrock',
  'azure-openai': 'azure',
  'vercel-ai-gateway': 'vercel',
  'cloudflare-workers-ai': 'cloudflare',
  'cloudflare-ai-gateway': 'cloudflare',
  'kimi-for-coding': 'kimi',
  'xiaomi-mimo': 'xiaomimimo',
  'siliconflow': 'siliconcloud',
  'alibaba-cn': 'alibaba',
  'lmstudio-local': 'lmstudio',
  // MCP marketplace ids
  'brave-search': 'brave',
  'chrome-devtools': 'googlechrome',
  'figma-token': 'figma',
  'cloudflare-observability': 'cloudflare',
  'cloudflare-docs': 'cloudflare',
  // Editors (git card "Open in")
  zed: 'zedindustries',
  idea: 'jetbrains',
  pycharm: 'jetbrains',
  // Names of tools in skill / editor labels
  'claude code': 'claudecode',
  'gemini cli': 'geminicli',
  copilot: 'githubcopilot',
  'vs code': 'vscode',
  'visual studio code': 'vscode',
  'github copilot': 'githubcopilot',
  'chatgpt': 'openai',
};

/** Logo slug for a provider id, models.dev id or tool name; '' when there is none. */
export function brandSlug(name) {
  let k = String(name || '').trim().toLowerCase();
  if (!k) return '';
  if (k.startsWith('md:')) k = k.slice(3);
  if (Object.prototype.hasOwnProperty.call(ALIAS, k)) k = ALIAS[k];
  return BRAND_SLUGS.has(k) ? k : '';
}

export const hasBrand = (name) => !!brandSlug(name);

const cache = new Map();

/** Inline SVG of the logo (class `br`), or '' when there is none. */
export function brand(name, cls = '') {
  const slug = brandSlug(name);
  if (!slug) return '';
  const key = `${slug}|${cls}`;
  if (cache.has(key)) return cache.get(key);
  const svg = `<svg class="br${cls ? ` ${cls}` : ''}" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" fill-rule="evenodd" aria-hidden="true" focusable="false">${brandBody(slug)}</svg>`;
  cache.set(key, svg);
  return svg;
}
