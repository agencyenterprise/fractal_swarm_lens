import { $, api, post, failure, toast } from './ui.js';

const active = status => status === 'queued' || status === 'running';
const watchKey = 'swarm-lens-pending-executions';

export class LiveView {
  constructor({ selection, update, forked, seek }) {
    this.selection = selection;
    this.update = update;
    this.forked = forked;
    this.seek = seek;
    this.generation = 0;
    this.following = false;
    this.watched = new Map();
    this.finishedJobs = new Map();
    this.captureStates = new Map();
    try {
      for (const job of JSON.parse(sessionStorage.getItem(watchKey) || '[]')) {
        if (job.id && job.branch_id && active(job.status)) this.watched.set(job.id, job);
      }
    } catch (_) { /* Watching still works when browser storage is unavailable. */ }
    $('#follow-live').onchange = () => {
      this.following = $('#follow-live').checked;
      if (this.following) this.seek(this.selection().head);
    };
    $('#dismiss-execution-notice').onclick = () => { $('#execution-notice').hidden = true; };
    $('#view-execution-branch').onclick = () => this.forked(this.noticeBranch).catch(failure);
    this.scheduleWatch();
  }

  persistWatches() {
    try { sessionStorage.setItem(watchKey, JSON.stringify([...this.watched.values()])); } catch (_) {}
  }

  watch(job) {
    if (!active(job.status) || this.finishedJobs.has(job.id)) return;
    this.watched.set(job.id, job);
    this.persistWatches();
    this.scheduleWatch();
  }

  scheduleWatch() {
    if (this.watchTimer || this.polling || !this.watched.size) return;
    this.watchTimer = setTimeout(() => this.pollWatched(), 1000);
  }

  async pollWatched() {
    this.watchTimer = null;
    this.polling = true;
    try {
      await Promise.all([...this.watched.keys()].map(async id => {
        try {
          const job = await api(`/executions/${id}`);
          if (!this.watched.has(id)) return; // A WebSocket may have delivered completion first.
          if (active(job.status)) this.watched.set(id, job);
          else this.finished(job);
          if (this.selection()?.branchId === job.branch_id) this.renderStatus(job);
        } catch (_) { /* Retry after reconnect; keep the pending notification. */ }
      }));
      this.persistWatches();
    } finally {
      this.polling = false;
      this.scheduleWatch();
    }
  }

  finished(job) {
    if (!this.watched.has(job.id)) return;
    this.watched.delete(job.id);
    this.finishedJobs.set(job.id, job);
    this.persistWatches();
    const count = Number.isInteger(job.output_cursor) ? job.output_cursor - job.input_cursor : null;
    const name = job.branch_name || 'CrewAI branch';
    const message = job.status === 'completed'
      ? `${name} completed${count === null ? '.' : ` · ${count} new events saved.`}`
      : `${name} ${job.status}.${job.error ? '' : ' Recorded events are preserved.'}`;
    this.notify(message, job.branch_id, job.status === 'completed');
  }

  notify(message, branchId, success) {
    this.noticeBranch = branchId;
    $('#execution-notice-text').textContent = message;
    $('#execution-notice').dataset.outcome = success ? 'success' : 'error';
    $('#execution-notice').hidden = false;
    toast(message, 9000);
  }

  renderStatus(job = null, capture = null) {
    job = this.finishedJobs.get(job?.id) || job;
    clearInterval(this.elapsedTimer);
    const busy = active(job?.status) || (!job && capture?.status === 'capturing');
    $('#live-spinner').hidden = !busy;
    // The pill appears only while something live is happening or just happened.
    $('#live-controls').hidden = !this.enabled || (!job && !capture);
    $('#live-controls').dataset.status = busy ? 'running' : job?.status || capture?.status || 'idle';
    const labels = { queued: 'Queued', running: 'Running', completed: 'Run completed',
      failed: 'Run failed', interrupted: 'Run interrupted' };
    $('#live-status').textContent = job ? labels[job.status] || job.status
      : capture ? (capture.status === 'capturing' ? 'Live' : `Capture ${capture.status}`) : '';
    $('#execution-error').textContent = job?.error || '';
    $('#execution-error').hidden = !job?.error;
    const started = job?.started_at || job?.created_at;
    const updateTime = () => {
      const seconds = Math.max(0, Math.floor(((job?.finished_at ? Date.parse(job.finished_at) : Date.now()) - Date.parse(started)) / 1000));
      $('#live-elapsed').textContent = started ? `${seconds}s` : '';
    };
    updateTime();
    if (busy && started) this.elapsedTimer = setInterval(updateTime, 1000);
  }

  enable(enabled) {
    this.enabled = enabled;
  }

  pause() {
    this.following = false;
    $('#follow-live').checked = false;
  }

  connect(branch, follow = false) {
    this.generation++;
    clearTimeout(this.timer);
    this.socket?.close();
    this.following = follow;
    $('#follow-live').checked = follow;
    if (!this.enabled) return;
    const generation = this.generation;
    this.after = branch.head;
    this.renderStatus([...this.watched.values()].find(job => job.branch_id === branch.id));
    const open = () => {
      if (generation !== this.generation) return;
      const url = new URL(`/api/live/branches/${branch.id}/stream?after=${this.after}`, location.href);
      url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const socket = this.socket = new WebSocket(url);
      socket.onopen = () => { if (generation === this.generation) $('#live-connection').textContent = ''; };
      let chain = Promise.resolve();
      socket.onmessage = (event) => {
        chain = chain.then(async () => {
          if (generation !== this.generation) return;
          const data = JSON.parse(event.data);
          if (data.type === 'error') throw new Error(data.detail);
          await this.update(data, this.following);
          if (generation !== this.generation) return;
          this.after = data.cursor;
          for (const job of data.jobs || []) {
            if (active(job.status)) this.watch(job);
            else this.finished(job);
          }
          const job = data.jobs?.[0];
          this.renderStatus(job, data.capture);
          if (!job && data.capture) {
            const status = data.capture.status;
            if (this.captureStates.get(data.capture.id) === 'capturing' && status !== 'capturing') {
              this.notify(`Live capture ${status}. Events are saved in this conversation.`, branch.id, status === 'completed');
            }
            this.captureStates.set(data.capture.id, status);
          }
        }).catch((error) => { if (generation === this.generation) { failure(error); socket.close(); } });
      };
      socket.onclose = () => {
        if (generation !== this.generation) return;
        $('#live-connection').textContent = 'Reconnecting…';
        this.timer = setTimeout(open, 1500);
      };
    };
    open();
  }

  async startFork(source, body, requestId) {
    const job = await post(`/branches/${source}/fork-execute`, { ...body, request_id: requestId });
    this.watch({ ...job, status: 'queued' });
    await this.forked(job.branch_id);
    this.following = true;
    $('#follow-live').checked = true;
    if (!active(job.status)) this.finished(job);
  }
}
