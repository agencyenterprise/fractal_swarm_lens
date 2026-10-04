import { $, el, post, toast, time } from './ui.js';
import { field, openDialog } from './dialog.js';

const plural = (count, word) => `${count} ${word}${count === 1 ? '' : 's'}`;

function forkPoint(source) {
  const point = el('div', 'fork-point');
  const parts = [source.at && time(source.at), source.label, `from ${source.branchName}`].filter(Boolean);
  point.append(el('strong', '', `Event ${source.cursor}`), el('span', 'muted', ` · ${parts.join(' · ')}`));
  return point;
}

function readyHeadline(info) {
  if (info.resume_mode === 'trace_continuation') {
    return `Ready to run with CrewAI · next: ${info.next_actor}, round ${info.next_round}`;
  }
  if (info.resume_mode === 'restart_task') return `Ready to run with CrewAI · restarts task ${info.next_task + 1}`;
  return 'Ready to run with CrewAI';
}

function readyDetails(info, source, withChanges) {
  const steps = Math.min(100, info.remaining_tasks);
  const unit = info.resume_mode === 'trace_continuation' ? 'agent turn' : 'remaining task';
  const lines = [`Runs ${plural(steps, unit)}${withChanges ? ' with your changes' : ''}, streaming new events into this branch.`];
  if (info.resume_mode === 'trace_continuation') {
    lines.push('Creates a new CrewAI execution from the saved agents, conversation, memories and current goal.',
      info.summary, `Models: ${info.models.join(', ')}.`);
    if (info.injection_agents.length) lines.push(`Preserves the recorded input injection for ${info.injection_agents.join(', ')}.`);
    if (info.tool_names.length) lines.push(`Executable tools: ${info.tool_names.join(', ')}. Tools may run again.`);
    lines.push('This reconstructs the saved state in CrewAI; it does not reproduce the original framework’s exact execution.');
  }
  if (info.resume_mode === 'restart_task') {
    lines.push(`Starts a new execution of task ${info.next_task + 1} using context saved at this point. The fork stays at event ${source.cursor}; tools in this task may run again.`);
  }
  const node = el('details', 'fork-live-details');
  node.append(el('summary', '', 'Details'), ...lines.map(line => el('p', '', line)));
  return node;
}

// Every branch starts at the captured visual selection. Execution never moves it.
export function branchDialog({ source, title = 'Fork here', fields = [], change, live, created }) {
  const content = el('div', 'fork-dialog');
  const name = field('Branch name', 'branch_name', source.defaultName);
  name.input.required = true;
  name.input.maxLength = 160;
  content.append(forkPoint(source), name.fragment);
  for (const item of fields) content.append(item.fragment);
  const status = el('div', 'fork-live');
  status.setAttribute('role', 'status');
  content.append(status);
  const requests = new Map();
  const bodyFor = form => ({ cursor: source.cursor, name: form.get('branch_name'),
    ...(change ? { intervention: change(form) } : {}) });
  let previewRevision = 0;
  const { alternateButton } = openDialog(title, content, {
    confirm: 'Create branch', pending: 'Creating branch…', kicker: '',
    submit: async form => {
      const branch = await post(`/branches/${source.branchId}/fork`, bodyFor(form));
      await created(branch.id);
      toast('Branch created');
    },
    alternate: {
      label: 'Create and run', disabled: true, pending: 'Starting…',
      submit: async form => {
        const body = bodyFor(form);
        // Recheck the final edited form; a stale preview must not authorize execution.
        const info = await post(`/branches/${source.branchId}/execution/preview`, body);
        if (!info.can_execute) throw new Error(info.reason);
        body.steps = Math.min(100, info.remaining_tasks);
        const signature = JSON.stringify(body);
        if (!requests.has(signature)) requests.set(signature, crypto.randomUUID());
        await live.startFork(source.branchId, body, requests.get(signature));
      },
    },
  });
  const showRunChoice = canRun => {
    alternateButton.hidden = !canRun;
    alternateButton.disabled = !canRun;
    alternateButton.classList.toggle('primary', canRun);
    $('#confirm-dialog').classList.toggle('primary', !canRun);
  };
  const showStatus = (state, text, extra) => {
    status.dataset.state = state;
    status.replaceChildren(el('span', 'fork-live-line', text));
    if (extra) status.append(extra);
  };
  showRunChoice(false);
  const refresh = async () => {
    const revision = ++previewRevision;
    alternateButton.disabled = true;
    showStatus('pending', 'Checking live execution…');
    try {
      const info = live.enabled
        ? await post(`/branches/${source.branchId}/execution/preview`, bodyFor(new FormData($('#dialog-form'))))
        : { can_execute: false, reason: 'No execution framework is connected.' };
      if (revision !== previewRevision || !content.isConnected || !$('#dialog').open) return;
      if ($('#dialog-form').getAttribute('aria-busy') === 'true') return;
      showRunChoice(info.can_execute);
      if (info.can_execute) showStatus('ready', readyHeadline(info), readyDetails(info, source, Boolean(change)));
      else showStatus('unavailable', `Live run unavailable · ${info.reason}`);
    } catch (error) {
      if (revision !== previewRevision || !content.isConnected) return;
      showRunChoice(false);
      showStatus('unavailable', `Live run unavailable · ${error.message}`);
    }
  };
  for (const item of fields) {
    // Invalidate immediately so an edited form never shows stale availability.
    item.input.addEventListener('input', refresh);
  }
  refresh();
}
