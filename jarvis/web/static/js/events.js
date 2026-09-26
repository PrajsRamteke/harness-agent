/** SSE event router */
import { loadSnapshot, store } from './store.js';
import {
  appendMessage,
  appendDiff,
  renderSnapshot,
  streamDelta,
  streamEnd,
  streamStart,
  syncThoughtsVisibility,
  toolDone,
  toolStart,
  invalidateSnapshot,
} from './chat.js';
import { setBusy, setQueue, setStatusLabel } from './status.js';
import { renderShellApproval, renderAskUser, renderTextInput, handlePromptResolved } from './prompts.js';
import { refreshRecent } from './sidebar.js';
import { fetchState } from './api.js';

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
      setQueue(data.queue || []);
      if (prevSession && prevSession !== data.session_id) refreshRecent();
      break;
    }

    case 'state':
      // Changed elsewhere (terminal, another tab) — see jarvis/web/sync.py.
      loadSnapshot(data);
      setBusy(!!data.busy);
      setQueue(data.queue || []);
      syncThoughtsVisibility();
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
      appendMessage(data.role || 'assistant', data.text, data.title);
      break;

    case 'log':
      appendMessage('log', data.text);
      break;

    case 'diff':
      appendDiff(data);
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
      setQueue(data.items || []);
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
      setStatusLabel(toolLabel() || `${data.title || data.name || 'Tool'} ${data.args || ''}`.trim());
      break;
    case 'tool_done':
      runningTools = Math.max(0, runningTools - 1);
      toolDone(data);
      if (runningTools) setStatusLabel(toolLabel() || 'Running tools');
      else setStatusLabel('Thinking');
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

    default:
      break;
  }
}
