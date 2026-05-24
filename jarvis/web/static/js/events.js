/** SSE event router */
import { loadSnapshot, loadSettingsEvent } from './store.js';
import { appendMessage } from './chat.js';
import {
  handleSnapshot,
  handleStreamEvent,
  setBusy,
  setQueue,
} from './stream.js';
import { refreshThinkingVisibility } from './chat.js';
import {
  renderShellApproval,
  renderAskUser,
  renderTextInput,
  closeOverlay,
} from './modals.js';
import { store } from './store.js';

export function handleEvent(evt) {
  const { type, data } = evt;

  switch (type) {
    case 'snapshot':
      loadSnapshot(data);
      handleSnapshot(data);
      break;

    case 'settings':
      loadSettingsEvent(data);
      refreshThinkingVisibility();
      break;

    case 'message':
      appendMessage(data.role || 'assistant', data.text, data.title);
      break;

    case 'log':
      appendMessage('log', data.text);
      break;

    case 'status':
      if (data.text) document.getElementById('status-label').textContent = data.text;
      break;

    case 'activity':
      if (data.label) document.getElementById('status-label').textContent = data.label;
      break;

    case 'busy':
      setBusy(data.busy);
      break;

    case 'queue':
      setQueue(data.items || []);
      break;

    case 'stream_start':
    case 'stream_delta':
    case 'stream_end':
      handleStreamEvent(type, data);
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
      if (store.activePrompt === data.id) closeOverlay();
      break;

    default:
      break;
  }
}
