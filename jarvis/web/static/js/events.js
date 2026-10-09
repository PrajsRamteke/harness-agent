/** SSE event router */
import { loadSnapshot, store } from './store.js';
import {
  appendMessage,
  appendDiff,
  appendError,
  appendStats,
  renderSnapshot,
  streamDelta,
  streamEnd,
  streamStart,
  syncThoughtsVisibility,
  toolDone,
  toolStart,
  updateAgents,
  invalidateSnapshot,
} from './chat.js';
import { setBusy, setQueue, setStatusLabel } from './status.js';
import { renderShellApproval, renderAskUser, renderTextInput, handlePromptResolved, syncPrompts } from './prompts.js';
import { refreshRecent } from './sidebar.js';
import { fetchState } from './api.js';
import { refreshProviders } from './providers.js';
import { handleMcpEvent } from './mcp.js';
import { handleSkillsEvent } from './skills.js';
import { handleCommandsEvent } from './commands.js';
import { handlePinEvent } from './pin.js';
import { handleLocalEvent } from './local.js';
import { loadChanges, applyChange } from './changes.js';
import { loadActivity, setJobs, noteToolStart, noteToolDone } from './activity.js';
import { handleProjectsEvent } from './projects.js';

let runningTools = 0;

function toolLabel() {
  if (runningTools > 1) return `Running ${runningTools} tools`;
  return '';
}

export function handleEvent(evt) {
  const { type } = evt;
  const data = evt.data || {};

  switch (type) {
    case 'snapshot': {
      const prevSession = store.session.session_id;
      loadSnapshot(data);
      renderSnapshot(data);
      setBusy(!!data.busy);
      setQueue(data.queue || [], data.queue_items);
      loadChanges(data.changes, data.session_id);
      loadActivity(data.messages);
      setJobs(data.jobs);
      // Only the snapshot a connection opens with lists them (jarvis/web/handler.py).
      if (Array.isArray(data.prompts)) syncPrompts(data.prompts);
      if (prevSession && prevSession !== data.session_id) refreshRecent();
      break;
    }

    case 'state':
      // Changed elsewhere (terminal, another tab) — see jarvis/web/sync.py.
      loadSnapshot(data);
      setBusy(!!data.busy);
      setQueue(data.queue || [], data.queue_items);
      if ('jobs' in data) setJobs(data.jobs);
      syncThoughtsVisibility();
      break;

    case 'change':
      // A file was created, edited or deleted: the panel's list + that file's diff.
      applyChange(data);
      break;

    case 'resync':
      // We fell behind and missed events: reload the whole session.
      invalidateSnapshot();
      fetchState().then((snap) => handleEvent({ type: 'snapshot', data: snap })).catch(() => {});
      break;

    case 'settings':
      loadSnapshot(data);
      syncThoughtsVisibility();
      break;

    case 'message':
      appendMessage(data.role || 'assistant', data.text, data.title, data.attachments, { steered: !!data.steered });
      break;

    case 'log':
      appendMessage('log', data.text);
      break;

    case 'error':
      // A model request failed: a card with what happened and the fixes.
      appendError(data);
      break;

    case 'diff':
      appendDiff(data);
      break;

    case 'stats':
      // /stats: numbers as data, drawn as a card (chat.js).
      appendStats(data);
      break;

    case 'status':
    case 'activity':
      if (data.text || data.label) setStatusLabel(data.text || data.label);
      break;

    case 'busy':
      if (!data.busy) runningTools = 0;
      setBusy(data.busy);
      if (!data.busy) refreshRecent();
      break;

    case 'queue':
      setQueue(data.items || [], data.entries);
      break;

    case 'pin':
      // Pinned context changed (this tab, another one): the dialog + sidebar chip.
      handlePinEvent(data);
      break;

    case 'local':
      // A local model server was added / removed, or the context changed.
      handleLocalEvent(data);
      break;

    case 'stream_start':
      streamStart(data.kind, data.title);
      setStatusLabel(data.kind === 'thinking' ? 'Thinking' : 'Writing');
      break;
    case 'stream_delta':
      streamDelta(data.kind, data.chunk);
      break;
    case 'stream_end':
      streamEnd(data.kind, data.aborted);
      break;

    case 'tool_wave_reset':
      runningTools = 0;
      break;
    case 'tool_start':
      runningTools += 1;
      toolStart(data);
      noteToolStart(data);
      setStatusLabel(toolLabel() || `${data.title || data.name || 'Tool'} ${data.args || ''}`.trim());
      break;
    case 'tool_done':
      runningTools = Math.max(0, runningTools - 1);
      toolDone(data);
      noteToolDone(data);
      if (runningTools) setStatusLabel(toolLabel() || 'Running tools');
      else setStatusLabel('Thinking');
      break;

    case 'agents':
      updateAgents(data);
      break;

    case 'shell_approval':
      renderShellApproval(data);
      break;
    case 'ask_user':
      renderAskUser(data);
      break;
    case 'text_input':
      renderTextInput(data);
      break;
    case 'prompt_resolved':
      handlePromptResolved(data.id);
      break;

    case 'providers':
      // A key or sign-in changed (this tab, another one, or the terminal's
      // ChatGPT callback): reload the list for the sidebar and dialog.
      refreshProviders();
      break;

    case 'mcp':
      // A server connected, failed or needs its sign-in (this tab, another tab,
      // the terminal, or the agent adding one): refresh the dialog, banner and dot.
      handleMcpEvent(data);
      break;
    case 'skills':
      handleSkillsEvent();
      break;
    case 'projects':
      // Another Jarvis opened or closed, got busy, finished or asks for approval.
      handleProjectsEvent(data);
      break;

    case 'commands':
      // A command was made, edited or removed (this tab or another): reload the slash menu.
      handleCommandsEvent();
      break;

    default:
      break;
  }
}
