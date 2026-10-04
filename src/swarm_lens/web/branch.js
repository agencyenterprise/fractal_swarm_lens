import { $, el, post, toast, time } from './ui.js';
import { field, openDialog } from './dialog.js?v=4';

// Every branch starts at the captured visual selection. Execution never moves it.
export function branchDialog({ source, title = 'Fork at cursor', fields = [], change,
  live, created }) {
  const content = el('div');
  const point = el('div', 'branch-point');
  point.append(el('span', 'eyebrow', `FORK POINT · EVENT ${source.cursor}`),
    el('strong', '', source.branchName),
    el('span', '', `${time(source.at)} UTC · ${source.label || 'Selected timeline position'}`));
  content.append(point, el('p', '', 'The new branch keeps this conversation’s history through the selected event.'));
  const name = field('Branch name', 'branch_name', source.defaultName);
  name.input.required = true;
  name.input.maxLength = 160;
  content.append(name.fragment);
  for (const item of fields) content.append(item.fragment);
  const status = el('div', 'branch-live-status');
  status.setAttribute('role', 'status');
  content.append(status);
  const requests = new Map();
  const bodyFor = form => ({ cursor: source.cursor, name: form.get('branch_name'),
    ...(change ? { intervention: change(form) } : {}) });
  let previewRevision = 0;
  const { alternateButton } = openDialog(title, content, {
    confirm: 'Create branch', pending: 'Creating branch…', kicker: 'BRANCH FROM TIMELINE',
    submit: async form => {
      const branch = await post(`/branches/${source.branchId}/fork`, bodyFor(form));
      await created(branch.id);
      toast('Branch created at the selected event.');
    },
    alternate: {
      label: 'Create branch and run live', disabled: true, pending: 'Creating and starting…',
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
  const refresh = async () => {
    const revision = ++previewRevision;
    alternateButton.disabled = true;
    status.dataset.state = 'pending';
    status.textContent = 'Checking live execution at this position…';
    try {
      const info = live.enabled
        ? await post(`/branches/${source.branchId}/execution/preview`, bodyFor(new FormData($('#dialog-form'))))
        : { can_execute: false, reason: 'No execution framework is connected to this workspace.' };
      if (revision !== previewRevision || !content.isConnected || !$('#dialog').open) return;
      if ($('#dialog-form').getAttribute('aria-busy') === 'true') return;
      alternateButton.disabled = !info.can_execute;
      status.dataset.state = info.can_execute ? 'ready' : 'unavailable';
      status.replaceChildren(el('strong', '', info.can_execute ? 'Ready to run with CrewAI' : 'Live execution unavailable here'),
        ...(info.can_execute && info.resume_mode === 'trace_continuation' ? [
          el('span', '', 'Creates a new CrewAI execution from the saved agents, conversation, memories and current goal.'),
          el('span', '', info.summary),
          el('span', '', `Next: ${info.next_actor} · round ${info.next_round}. Models: ${info.models.join(', ')}.`),
          ...(info.injection_agents.length ? [el('span', '', `Preserves the recorded input injection for ${info.injection_agents.join(', ')}.`)] : []),
          ...(info.tool_names.length ? [el('span', '', `Executable tools: ${info.tool_names.join(', ')}. Tools may run again.`)] : []),
          el('span', '', 'This reconstructs the saved state in CrewAI; it does not reproduce the original framework’s exact execution.'),
        ] : []),
        ...(info.can_execute && info.resume_mode === 'restart_task' ? [el('span', '',
          `Starts a new execution of task ${info.next_task + 1} using context saved at this point. The fork stays at event ${source.cursor}; tools in this task may run again.`)] : []),
        el('span', '', info.can_execute
          ? `Runs ${Math.min(100, info.remaining_tasks)} ${info.resume_mode === 'trace_continuation' ? 'agent turn' : 'remaining task'}${info.remaining_tasks === 1 ? '' : 's'}${change ? ' with your changes' : ''}, streaming new events into this branch.`
          : info.reason),
        ...(!info.can_execute ? [el('span', '', info.runtime_id
          ? 'You can still create this branch. Choose an earlier saved event or adjust the change to run with this runtime.'
          : 'You can still create this branch. Running it requires a compatible execution framework for this conversation.')] : []));
    } catch (error) {
      if (revision !== previewRevision || !content.isConnected) return;
      status.dataset.state = 'unavailable';
      status.textContent = error.message;
    }
  };
  for (const item of fields) {
    // Invalidate immediately so an edited form never shows stale availability.
    item.input.addEventListener('input', refresh);
  }
  refresh();
}
