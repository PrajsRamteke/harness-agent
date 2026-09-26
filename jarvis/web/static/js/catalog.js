/** Command catalog shared by the ⌘K palette and the composer's slash menu.
 *
 * Item kinds:
 *   picker — opens a web picker (sessions, models, …)
 *   action — runs a local handler (new chat, toggles, theme)
 *   cmd    — sent to Jarvis as a slash command; `laptop` ones open a dialog
 *            in the terminal, so the web shows a hint instead of nothing
 *   fill   — only puts the command in the composer (needs an argument)
 */
export const CATALOG = [
  { group: 'Go to', picker: 'session', cmd: '/session', label: 'Sessions', desc: 'Resume or delete a saved session', icon: 'history', keys: 'resume history' },
  { group: 'Go to', picker: 'model', cmd: '/model', label: 'Model', desc: 'Switch the model or provider', icon: 'cpu', keys: 'provider llm' },
  { group: 'Go to', picker: 'agent', cmd: '/agent', label: 'Agent', desc: 'Activate an agent profile', icon: 'sparkles', keys: 'profile persona' },
  { group: 'Go to', picker: 'skill', cmd: '/skill', label: 'Skills', desc: 'Browse installed skill packs', icon: 'book-open' },
  { group: 'Go to', picker: 'mcp', cmd: '/mcp', label: 'MCP servers', desc: 'Connect or disconnect tool servers', icon: 'plug', keys: 'tools servers' },

  { group: 'Conversation', action: 'session_new', cmd: '/new', label: 'New chat', desc: 'Start a fresh conversation', icon: 'plus', keys: 'clear fresh' },
  { group: 'Conversation', cmd: '/retry', label: 'Retry', desc: 'Send the last message again', icon: 'refresh-cw' },
  { group: 'Conversation', cmd: '/reset', label: 'Reset', desc: 'Clear the conversation history', icon: 'rotate-ccw' },
  { group: 'Conversation', cmd: '/history', label: 'History', desc: 'Summarise the messages so far', icon: 'list' },
  { group: 'Conversation', cmd: '/stats', label: 'Stats', desc: 'Session time, messages and tools', icon: 'chart-column' },
  { group: 'Conversation', fill: true, cmd: '/export ', label: 'Export', desc: 'Save the conversation as markdown', icon: 'download' },
  { group: 'Conversation', cmd: '/copy', label: 'Copy last reply', desc: 'Copy the latest answer on your computer', icon: 'copy' },

  { group: 'Settings', action: 'toggle-think', label: 'Extended thinking', desc: 'Think before answering', icon: 'brain', toggle: 'think_mode' },
  { group: 'Settings', action: 'toggle-trace', label: 'Tool trace', desc: 'Show thinking and tool details', icon: 'list-tree', toggle: 'show_internal' },
  { group: 'Settings', action: 'toggle-thoughts', label: 'Show thoughts here', desc: 'Thinking in this browser only', icon: 'eye', toggle: 'showThoughts' },
  { group: 'Settings', action: 'toggle-auto', label: 'Auto-approve commands', desc: 'Run shell commands without asking', icon: 'shield', toggle: 'auto_approve', warn: true },
  { group: 'Settings', action: 'theme', label: 'Light or dark', desc: 'Flip the colour mode', icon: 'sun-moon', keys: 'dark light mode theme soft dim' },
  { group: 'Settings', action: 'appearance', label: 'Appearance', desc: 'Light, soft dark or dark, accent colour, compact view', icon: 'palette', keys: 'theme accent color colour compact notification soft dim dark light' },

  { group: 'Memory', cmd: '/memory', label: 'Memory', desc: 'Personal facts Jarvis remembers', icon: 'database', laptop: true },
  { group: 'Memory', cmd: '/lesson', label: 'Lessons', desc: 'Lessons the agent has saved', icon: 'graduation-cap', laptop: true },
  { group: 'Memory', cmd: '/pin', label: 'Pinned context', desc: 'Context added to every prompt', icon: 'pin', laptop: true },
  { group: 'Memory', cmd: '/scan', label: 'Scan project', desc: 'Deep scan of identity and docs', icon: 'scan-search' },

  { group: 'On your computer', cmd: '/provider', label: 'Providers and login', desc: 'OAuth, API keys, provider switch', icon: 'key-round', laptop: true },
  { group: 'On your computer', cmd: '/settings', label: 'All settings', desc: 'Every preference, in the terminal', icon: 'sliders-horizontal', laptop: true },
  { group: 'On your computer', cmd: '/theme', label: 'Terminal theme', desc: 'Colours of the terminal app', icon: 'palette', laptop: true },
  { group: 'On your computer', cmd: '/agent init', label: 'Scaffold .harness/', desc: 'Create the project agent folders', icon: 'folder-plus', laptop: true },

  { group: 'Help', action: 'shortcuts', label: 'Keyboard shortcuts', desc: 'Every key that does something here', icon: 'keyboard', keys: 'keys hotkeys help' },
  { group: 'Help', cmd: '/help', label: 'Help', desc: 'Every command, explained', icon: 'circle-help' },
  { group: 'Help', cmd: '/version', label: 'Version', desc: 'Installed Jarvis version', icon: 'info' },
  { group: 'Help', cmd: '/upgrade', label: 'Upgrade', desc: 'Update Jarvis to the latest release', icon: 'circle-arrow-up' },
];

/** Bare commands the web handles itself instead of opening a terminal dialog. */
export const LOCAL_PICKERS = {
  '/model': 'model',
  '/models': 'model',
  '/session': 'session',
  '/sessions': 'session',
  '/resume': 'session',
  '/agent': 'agent',
  '/agents': 'agent',
  '/skill': 'skill',
  '/skills': 'skill',
  '/mcp': 'mcp',
};

export const LAPTOP_COMMANDS = new Set(
  CATALOG.filter((c) => c.laptop).map((c) => c.cmd.trim()),
);

/** Case-insensitive match over label, command, description and keywords. */
export function matchItem(item, q) {
  if (!q) return true;
  const hay = `${item.label} ${item.cmd || ''} ${item.desc || ''} ${item.keys || ''} ${item.group}`.toLowerCase();
  return q.split(/\s+/).every((part) => hay.includes(part));
}

/** Rank: command prefix first, then label prefix, then the rest. */
export function rankItems(items, q) {
  if (!q) return items;
  const score = (it) => {
    const cmd = (it.cmd || '').toLowerCase();
    const label = it.label.toLowerCase();
    if (cmd.startsWith(q) || cmd.startsWith(`/${q}`)) return 0;
    if (label.startsWith(q)) return 1;
    return 2;
  };
  return [...items].sort((a, b) => score(a) - score(b));
}
