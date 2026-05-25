/** Shared thinking-effort options + dropdown markup */
export const EFFORTS = ['xhigh', 'high', 'medium', 'low', 'minimal', 'none'];

export const EFFORT_HINTS = {
  xhigh: 'Maximum reasoning depth',
  high: 'Deep analysis',
  medium: 'Balanced default',
  low: 'Light thinking',
  minimal: 'Brief passes only',
  none: 'Thinking off',
};

export function effortHint(value) {
  return EFFORT_HINTS[value] || '';
}

export function effortOptionsHtml({ optionClass = 'effort-option' } = {}) {
  return EFFORTS.map(
    (effort) => `
    <button type="button" class="${optionClass}" role="option" data-effort="${effort}" aria-selected="false" tabindex="-1">
      <i data-lucide="check" class="effort-option-check"></i>
      <span class="effort-option-body">
        <strong>${effort}</strong>
        <span>${EFFORT_HINTS[effort]}</span>
      </span>
    </button>`,
  ).join('');
}

export function bindEffortOptions(menu, onSelect) {
  if (!menu) return;
  menu.querySelectorAll('[data-effort]').forEach((btn) => {
    btn.addEventListener('click', () => onSelect(btn.dataset.effort));
  });
}

export function syncEffortOptions(menu, selected) {
  if (!menu) return;
  menu.querySelectorAll('[data-effort]').forEach((btn) => {
    const isSelected = btn.dataset.effort === selected;
    btn.classList.toggle('selected', isSelected);
    btn.setAttribute('aria-selected', isSelected ? 'true' : 'false');
  });
}
