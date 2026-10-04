// Records every shot of the Swarm Lens demo as a timestamped JPEG frame sequence plus a shot.json
// holding frame times, action cues (clicks, keys, card events) and marks (element boxes for the camera).
// Usage: PLAYWRIGHT_CORE=<path to playwright-core> CHROME=<chromium binary> node record.mjs <work dir> [shot ...]
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { execFileSync } from "node:child_process";

const { chromium } = await import(process.env.PLAYWRIGHT_CORE ?? "playwright-core");
const APP = process.env.SWARM_LENS_URL ?? "http://127.0.0.1:8931";
const CARDS = path.join(path.dirname(fileURLToPath(import.meta.url)), "cards");
const [workDir, ...onlyShots] = process.argv.slice(2);
if (!workDir) throw new Error("Pass a work directory for the recorded frames.");

// The protagonist: ACIArena medicine-004, where a malicious Debater 0 flips the team's answer from (A) to (B).
const RUNS = {
  withoutAttack: "4c84a4f9-3334-4197-bfa5-594f9dca8ae2",
  withMalicious: "e6b2dd6d-2631-4e50-8e6d-74b4399a8548",
  withMastReport: "235855fd-95a2-4bd6-b333-6dafb83fe951", // medicine-000, the run with a saved MAST report
};
const ATTACKER_FIRST_MESSAGE = "Debater 0, Initialization,";
const RUN_TIMEOUT_MS = 12 * 60 * 1000;
const APP_VIEWPORT = { width: 1600, height: 900 };
const APP_SCALE = 2; // 3200x1800 frames leave room for camera punch-ins at 1080p
const CARD_VIEWPORT = { width: 1920, height: 1080 };

// ---------- cursor overlay (headless capture has no pointer) ----------

function installCursor() {
  const style = `
    #demo-cursor{position:fixed;inset:auto;left:0;top:0;margin:0;padding:0;border:0;background:transparent;
      overflow:visible;width:1px;height:1px;pointer-events:none;transform:translate(-100px,-100px)}
    #demo-cursor svg{position:absolute;left:-3px;top:-2px;width:26px;height:26px;filter:drop-shadow(0 1px 2px rgba(0,0,0,.35))}
    #demo-cursor .ripple{position:absolute;left:-18px;top:-18px;width:36px;height:36px;border-radius:50%;
      border:2.5px solid #0f7b68;background:rgba(15,123,104,.18);animation:demo-ripple .55s ease-out forwards}
    @keyframes demo-ripple{from{transform:scale(.3);opacity:1}to{transform:scale(1.25);opacity:0}}`;
  const mount = () => {
    if (document.getElementById("demo-cursor")) return;
    const sheet = document.createElement("style");
    sheet.textContent = style;
    document.head.append(sheet);
    const cursor = document.createElement("div");
    cursor.id = "demo-cursor";
    cursor.setAttribute("popover", "manual");
    cursor.innerHTML = `<svg viewBox="0 0 24 24"><path d="M4 2.5l15 10.2-6.6 1.3 3.9 7.4-2.9 1.5-3.9-7.5L4 20.2z"
      fill="#111" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/></svg>`;
    document.body.append(cursor);
    cursor.showPopover();
    // Keep the cursor above dialogs and menus that enter the top layer after it.
    let layered = 0;
    setInterval(() => {
      const open = document.querySelectorAll("dialog[open], :popover-open:not(#demo-cursor)").length;
      if (open !== layered) { layered = open; cursor.hidePopover(); cursor.showPopover(); }
    }, 30);
    window.addEventListener("mousemove", (e) => {
      cursor.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`;
    }, true);
    window.addEventListener("mousedown", () => {
      const ripple = document.createElement("div");
      ripple.className = "ripple";
      cursor.append(ripple);
      setTimeout(() => ripple.remove(), 600);
    }, true);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
}

// ---------- screencast recorder ----------

const now = () => Date.now() / 1000;

class Recorder {
  constructor(page, { viewport, scale }) { Object.assign(this, { page, viewport, scale }); }

  async start(shot) {
    this.shot = shot;
    this.dir = path.join(workDir, "shots", shot);
    fs.rmSync(this.dir, { recursive: true, force: true });
    fs.mkdirSync(this.dir, { recursive: true });
    this.frames = [];
    this.cues = [];
    this.marks = {};
    this.cdp = await this.page.context().newCDPSession(this.page);
    this.cdp.on("Page.screencastFrame", ({ data, metadata, sessionId }) => {
      const file = `${String(this.frames.length).padStart(6, "0")}.jpg`;
      fs.writeFileSync(path.join(this.dir, file), Buffer.from(data, "base64"));
      this.frames.push({ file, t: metadata.timestamp });
      this.cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
    });
    const firstFrame = new Promise((resolve) => this.cdp.once("Page.screencastFrame", resolve));
    await this.cdp.send("Page.startScreencast", { format: "jpeg", quality: 92,
      maxWidth: this.viewport.width * this.scale, maxHeight: this.viewport.height * this.scale });
    await firstFrame;
  }

  cue(kind, extra = {}) { if (this.frames) this.cues.push({ kind, wall: now(), ...extra }); }

  // Records an element's box (CSS px) under a name, so the editor can aim the camera at it.
  async mark(name, locator) {
    const box = await locator.boundingBox();
    if (!box) throw new Error(`${this.shot}: mark ${name} has no box.`);
    this.marks[name] = { wall: now(), ...box };
  }

  markRect(name, box) { this.marks[name] = { wall: now(), ...box }; }

  async stop() {
    const stoppedAt = now();
    await this.cdp.send("Page.stopScreencast");
    await this.cdp.detach();
    if (!this.frames.length) throw new Error(`No frames captured for ${this.shot}.`);
    const pageCues = await this.page.evaluate(() => window.__cues ?? []);
    const t0 = this.frames[0].t;
    const rel = (wall) => Number((wall - t0).toFixed(4));
    const manifest = {
      shot: this.shot, viewport: this.viewport, scale: this.scale, duration: rel(stoppedAt),
      frames: this.frames.map(({ file, t }) => ({ file, t: rel(t) })),
      cues: [...this.cues, ...pageCues].map(({ wall, ...cue }) => ({ ...cue, t: rel(wall) })).sort((a, b) => a.t - b.t),
      marks: Object.fromEntries(Object.entries(this.marks).map(([name, { wall, ...box }]) => [name, { ...box, t: rel(wall) }])),
    };
    fs.writeFileSync(path.join(this.dir, "shot.json"), JSON.stringify(manifest, null, 1));
    console.log(`${this.shot}: ${manifest.duration.toFixed(1)} s, ${this.frames.length} frames, ${manifest.cues.length} cues`);
    this.frames = null;
  }
}

// ---------- scripted, eased interactions ----------

class Actor {
  constructor(page, recorder) { Object.assign(this, { page, recorder, x: 800, y: 450 }); }

  wait(ms) { return this.page.waitForTimeout(ms); }

  async moveTo(x, y, ms = 650) {
    const steps = Math.max(10, Math.round(ms / 16));
    const [x0, y0] = [this.x, this.y];
    for (let i = 1; i <= steps; i += 1) {
      const t = i / steps;
      const e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
      await this.page.mouse.move(x0 + (x - x0) * e, y0 + (y - y0) * e);
      await this.wait(16);
    }
    [this.x, this.y] = [x, y];
  }

  async center(locator) {
    await locator.waitFor({ state: "visible" });
    const box = await locator.boundingBox();
    return [box.x + box.width / 2, box.y + box.height / 2];
  }

  async hover(locator, ms) { const [x, y] = await this.center(locator); await this.moveTo(x, y, ms); }

  async press(button) {
    this.recorder.cue("click", { x: this.x, y: this.y });
    await this.page.mouse.down({ button });
    await this.wait(70);
    await this.page.mouse.up({ button });
  }

  async click(locator, { ms, button = "left", pause = 140 } = {}) {
    await this.hover(locator, ms);
    await this.wait(pause);
    await this.press(button);
  }

  async drag(fromX, fromY, toX, toY, ms) {
    await this.moveTo(fromX, fromY);
    this.recorder.cue("click", { x: fromX, y: fromY });
    await this.page.mouse.down();
    await this.moveTo(toX, toY, ms);
    await this.page.mouse.up();
    this.recorder.cue("release", { x: toX, y: toY });
  }

  async type(text, delay = 28) {
    for (const char of text) {
      this.recorder.cue("key");
      await this.page.keyboard.type(char);
      await this.wait(delay);
    }
  }

  async key(combo) { this.recorder.cue("key"); await this.page.keyboard.press(combo); }
}

// ---------- pages ----------

async function newPage(browser, { viewport = APP_VIEWPORT, scale = APP_SCALE } = {}) {
  const context = await browser.newContext({ viewport, deviceScaleFactor: scale, colorScheme: "light" });
  await context.addInitScript(() => {
    try {
      localStorage.setItem("swarm-lens:theme", "light");
      localStorage.setItem("swarm-lens:playback-rate", "2");
      localStorage.setItem("swarm-lens:visualization", "lanes");
      localStorage.removeItem("swarm-lens:favorite-runs");
    } catch { /* storage blocked: the app defaults apply */ }
  });
  await context.addInitScript(installCursor);
  const page = await context.newPage();
  const recorder = new Recorder(page, { viewport, scale });
  return { context, page, recorder, actor: new Actor(page, recorder) };
}

async function openApp(browser, hash, ready = ".tl-playhead") {
  const session = await newPage(browser);
  await session.page.goto(`${APP}/#${hash}`);
  await session.page.locator(ready).first().waitFor();
  await session.actor.wait(1200);
  return session;
}

const lanesDot = (page, label) => page.locator(`[aria-label^="${label}"]`).first();
const viewTab = (page, name) => page.getByRole("button", { name, exact: true });
const timeline = (branch, cursor) => `branch=${branch}&cursor=${cursor}&view=timeline`;

// Box around the flat tail of the lowest Echo line (the attacker's), in viewport CSS px.
async function echoAttackerTail(page) {
  return page.evaluate(() => {
    const lines = [...document.querySelectorAll(".echo-svg polyline")].map((line) => {
      const matrix = line.getScreenCTM();
      const points = [...line.points].map((p) => new DOMPoint(p.x, p.y).matrixTransform(matrix));
      return { points, bottom: Math.max(...points.map((p) => p.y)) };
    });
    const lowest = lines.sort((a, b) => b.bottom - a.bottom)[0].points;
    const tail = lowest.slice(Math.floor(lowest.length * 0.4));
    const xs = tail.map((p) => p.x), ys = tail.map((p) => p.y);
    return { x: Math.min(...xs), y: Math.min(...ys), width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys) };
  });
}

// ---------- comments ----------

const COMMENT_AUTHOR = "Alex";
const TYPED_COMMENT = "First message argues for (B), as its prompt instructs.";
const SEEDED_COMMENTS = [
  { position: 22, text: "Debater 1 switches (A) to (B) after reading Debater 0." },
  { position: 25, text: "Debater 2 switches to (B) too: all three debaters now agree." },
  { position: 199, text: "Aggregator picks (B), following the converged debate." },
];

async function api(pathname, options = {}) {
  const response = await fetch(`${APP}${pathname}`, { headers: { "content-type": "application/json" }, ...options });
  if (!response.ok) throw new Error(`${options.method ?? "GET"} ${pathname} failed: ${response.status}`);
  return response.status === 204 ? null : response.json();
}

async function resetComments(branchId, seeds) {
  const { comments } = await api(`/api/branches/${branchId}/comments`);
  for (const comment of comments.filter((c) => !c.parent_id)) await api(`/api/comments/${comment.id}`, { method: "DELETE" });
  const { events } = await api(`/api/branches/${branchId}/timeline`);
  for (const { position, text } of seeds) {
    const event = events.find((e) => e.position === position);
    await api(`/api/branches/${branchId}/comments`, { method: "POST",
      body: JSON.stringify({ event_id: event.id, author: COMMENT_AUTHOR, text }) });
  }
}

// ---------- shots ----------

const shots = {
  async import(browser) {
    const { context, page, actor, recorder } = await openApp(browser, timeline(RUNS.withoutAttack, 199));
    await recorder.start("02-import");
    await actor.click(page.locator("#more-button"), { ms: 900 });
    await actor.wait(350);
    await recorder.mark("menu", page.getByRole("menu").first());
    await actor.hover(page.getByText("Import run…"), 450);
    await actor.wait(300);
    await actor.click(page.getByText("Import trace…"), { ms: 350 });
    const dialog = page.locator("dialog[open]");
    await dialog.waitFor();
    await actor.wait(500);
    await recorder.mark("dialog", dialog);
    await actor.click(dialog.getByLabel("Name"), { ms: 500 });
    await actor.type("Research crew, run 12", 32);
    await actor.click(dialog.getByLabel("Transcript"), { ms: 450 });
    await actor.type("Planner: Split the task into three steps.", 18);
    await actor.wait(500);
    await actor.click(dialog.getByRole("button", { name: "Cancel" }), { ms: 550 });
    await actor.wait(300);
    await actor.click(page.locator('[id="combobox:conversation:toggle-btn"]'), { ms: 800 });
    await actor.wait(300);
    await page.locator('input[id^="combobox:conversation"]').first().fill("");
    await actor.type("medicine-004", 55);
    await actor.wait(350);
    await recorder.mark("picker", page.locator('[id="combobox:conversation:content"]'));
    await actor.click(page.getByRole("button", { name: /Add ACIArena · LLMDebate · medicine-004 · With malicious agent/ }), { ms: 600 });
    await actor.wait(400);
    await actor.click(page.getByRole("option", { name: /medicine-004 · With malicious agent/ }), { ms: 450 });
    await page.waitForFunction((id) => location.hash.includes(id), RUNS.withMalicious);
    await actor.wait(1300);
    await recorder.stop();
    await context.close();
  },

  // Two data sources in one workspace: ACIArena debates and Messageboard runs from a different study.
  async datasets(browser) {
    const { context, page, actor, recorder } = await openApp(browser, timeline(RUNS.withoutAttack, 199));
    const picker = page.locator('input[id^="combobox:conversation"]').first();
    await recorder.start("02-datasets");
    await actor.click(page.locator('[id="combobox:conversation:toggle-btn"]'), { ms: 800 });
    await picker.fill("");
    await actor.wait(900);
    await recorder.mark("list", page.locator('[id="combobox:conversation:content"]'));
    await actor.type("Messageboard", 50);
    await actor.wait(700);
    await actor.click(page.getByRole("option", { name: /Messageboard · run-qwen-01/ }), { ms: 600 });
    await page.waitForFunction(() => document.querySelector(".tl-playhead"));
    await actor.wait(1600);
    await actor.click(page.locator('[id="combobox:conversation:toggle-btn"]'), { ms: 700 });
    await picker.fill("");
    await actor.type("medicine-004", 50);
    await actor.wait(400);
    await actor.click(page.getByRole("option", { name: /medicine-004 · With malicious agent/ }), { ms: 500 });
    await page.waitForFunction((id) => location.hash.includes(id), RUNS.withMalicious);
    await actor.wait(1300);
    await recorder.stop();
    await context.close();
  },

  async investigate(browser) {
    const { context, page, actor, recorder } = await openApp(browser, timeline(RUNS.withMalicious, 200));
    await recorder.start("03-scrub");
    const box = await page.locator(".tl-ruler-track").boundingBox();
    const y = box.y + box.height / 2;
    await actor.drag(box.x + box.width - 30, y, box.x + 260, y, 1000);
    await actor.wait(200);
    await actor.drag(box.x + 260, y, box.x + box.width * 0.45, y, 1100);
    await actor.wait(250);
    await actor.click(page.locator("select.viz-speed"), { ms: 600 });
    await page.locator("select.viz-speed").selectOption("10");
    await actor.wait(250);
    await actor.click(page.getByRole("button", { name: "Play" }), { ms: 500 });
    await actor.moveTo(1050, 330, 900);
    await actor.wait(1500);
    await actor.click(page.getByRole("button", { name: "Pause" }), { ms: 500 });
    await actor.wait(250);
    const head = await page.locator(".tl-playhead").boundingBox();
    await actor.drag(head.x + 1, y, box.x + box.width - 4, y, 700);
    await actor.wait(500);
    await recorder.stop();

    await recorder.start("04-echo");
    await actor.click(viewTab(page, "Influence"), { ms: 600 });
    await actor.wait(1500);
    await recorder.mark("influence", page.locator("#timeline"));
    await actor.click(viewTab(page, "Echo"), { ms: 500 });
    await actor.wait(900);
    await recorder.mark("echoChart", page.locator(".echo-svg"));
    recorder.markRect("attackerLine", await echoAttackerTail(page));
    await actor.moveTo(1180, 380, 900);
    await actor.wait(3800);
    await recorder.stop();
    await context.close();
  },

  // Creates a branch and runs it forward with the CrewAI runtime (about 60 gpt-4o-mini turns),
  // then compares the outcome with the recorded run. Each run adds a branch to the app's data.
  async fork(browser) {
    const { context, page, actor, recorder } = await openApp(browser, timeline(RUNS.withMalicious, 200));
    await recorder.start("05-fork");
    const dot = lanesDot(page, ATTACKER_FIRST_MESSAGE);
    await actor.click(dot, { ms: 800 });
    await actor.wait(700);
    await recorder.mark("dot", dot);
    await recorder.mark("message", page.locator("article.tx-at-cursor").first());
    await actor.click(dot, { ms: 200, button: "right" });
    await actor.wait(450);
    await recorder.mark("contextMenu", page.getByRole("menu").first());
    await actor.click(page.getByRole("menuitem", { name: /Fork with new prompt for Debater 0/ }), { ms: 600 });
    const dialog = page.locator("dialog[open]");
    await dialog.waitFor();
    await dialog.getByText(/Ready to run/).waitFor();
    await actor.wait(500);
    await recorder.mark("dialog", dialog);
    await recorder.mark("effect", dialog.getByText(/New branch after event/));
    const prompt = dialog.getByLabel("System prompt");
    await recorder.mark("prompt", prompt);
    await actor.click(dialog.getByLabel("Branch name"), { ms: 600 });
    await actor.key("Meta+A");
    await actor.type("Honest Debater 0", 30);
    await actor.click(prompt, { ms: 500 });
    await actor.key("Meta+A");
    await actor.type("You are a careful clinician. Weigh the evidence and give the option you believe is correct.", 13);
    await actor.wait(500);
    await recorder.mark("run", dialog.getByRole("button", { name: "Create and run", exact: true }));
    await actor.click(dialog.getByRole("button", { name: "Create and run", exact: true }), { ms: 700 });
    await dialog.waitFor({ state: "detached" });
    await recorder.mark("liveStatus", page.locator("#live-controls"));
    await actor.moveTo(1180, 330, 900);
    await actor.wait(14000);
    await recorder.stop();

    await page.waitForFunction(() => {
      const status = document.querySelector("#live-controls")?.dataset.status;
      return status && !["running", "queued", "idle"].includes(status);
    }, null, { timeout: RUN_TIMEOUT_MS, polling: 2000 });
    const status = await page.locator("#live-controls").getAttribute("data-status");
    if (status !== "completed") throw new Error(`The continued run ended with status ${status}.`);
    console.log("continued run completed");
    await context.close();
    await shots.payoff(browser);
  },

  // The payoff: the recorded outcome next to the outcome of the newest continued "Honest Debater 0" branch.
  async payoff(browser) {
    const workspace = await (await fetch(`${APP}/api/workspace`)).json();
    const runId = workspace.branches.find((b) => b.id === RUNS.withMalicious).run_id;
    const branch = workspace.branches
      .filter((b) => b.run_id === runId && b.name === "Honest Debater 0" && b.head > b.fork_position + 100)
      .sort((x, y) => x.created_at.localeCompare(y.created_at)).at(-1);
    if (!branch) throw new Error("No continued Honest Debater 0 branch yet; record the fork shot first.");
    const { context, page, actor, recorder } = await openApp(browser,
      `branch=${branch.id}&cursor=0&view=compare&left=${RUNS.withMalicious}&right=${branch.id}`, ".cmp-grid");
    const outcomes = page.locator(".cmp-grid").nth(1);
    await recorder.start("06-payoff");
    await actor.click(outcomes.getByText("Show more").first(), { ms: 900 });
    await actor.wait(500);
    await recorder.mark("answerB", outcomes.getByText(/^\(B\) 7mm/).last());
    await recorder.mark("answerA", outcomes.getByText(/^\(A\) 5mm/).last());
    await actor.moveTo(800, 470, 700);
    await actor.wait(3200);
    await recorder.stop();
    await context.close();
  },

  async compare(browser) {
    const { context, page, actor, recorder } = await openApp(browser,
      `branch=${RUNS.withMalicious}&cursor=200&view=compare&left=${RUNS.withoutAttack}&right=${RUNS.withMalicious}`, ".cmp-grid");
    await recorder.start("08-compare-runs");
    await recorder.mark("outcomes", page.locator(".cmp-grid").nth(1));
    await actor.hover(page.locator(".cmp-grid").nth(1), 900);
    await actor.wait(1500);
    await actor.click(page.getByRole("button", { name: "Jump to first difference" }), { ms: 800 });
    await actor.wait(700);
    await recorder.mark("firstDifference", page.getByText("First difference").first());
    await actor.wait(1500);
    await recorder.stop();
    await context.close();
  },

  async plugins(browser) {
    const { context, page, actor, recorder } = await openApp(browser, timeline(RUNS.withMastReport, 200));
    await recorder.start("09-mast");
    await actor.click(page.locator("#more-button"), { ms: 700 });
    await actor.wait(500);
    await recorder.mark("menu", page.getByRole("menu").first());
    await actor.wait(500);
    await actor.click(page.getByText("Analyze with MAST…"), { ms: 600 });
    const dialog = page.locator("dialog[open]");
    await dialog.waitFor();
    await actor.wait(600);
    await recorder.mark("dialog", dialog);
    await actor.wait(1000);
    await actor.click(dialog.getByRole("button", { name: "Cancel" }), { ms: 600 });
    await actor.wait(300);
    await actor.click(page.locator("#view-mast"), { ms: 700 });
    await page.getByText("Action-Reasoning Mismatch", { exact: true }).waitFor();
    await actor.wait(900);
    await recorder.mark("summary", page.getByText(/of 14 failure modes present/));
    await actor.click(page.getByText("Action-Reasoning Mismatch", { exact: true }), { ms: 700 });
    await actor.wait(400);
    await page.mouse.wheel(0, 260);
    await actor.wait(600);
    await recorder.mark("evidence", page.getByText(/^Occurrence 1/).first());
    await actor.wait(1800);
    await recorder.stop();
    await context.close();
  },

  // Flashes through the built-in views; each is computed by counting, without a model.
  async montage(browser) {
    const { context, page, actor, recorder } = await openApp(browser, timeline(RUNS.withMalicious, 200));
    await recorder.start("14-montage");
    for (const name of ["Influence", "Blast radius", "Phrase spread", "Echo", "Activity"]) {
      await actor.click(viewTab(page, name), { ms: 300, pause: 60 });
      await actor.wait(900);
    }
    await recorder.stop();
    await context.close();
  },

  // Comments on the moments where Debater 0's (B) spreads, then a real "Export run" download.
  // Replaces every comment on the run's recorded branch first, so the thread list stays tidy.
  async comment(browser) {
    await resetComments(RUNS.withMalicious, SEEDED_COMMENTS);
    const session = await newPage(browser);
    const { context, page, actor, recorder } = session;
    await context.addInitScript((name) => { try { localStorage.setItem("swarm-lens:comment-author", name); } catch {} }, COMMENT_AUTHOR);
    await page.goto(`${APP}/#${timeline(RUNS.withMalicious, 200)}`);
    await page.waitForSelector(".tl-note");
    await actor.wait(1200);
    await recorder.start("15-comment");
    const dot = lanesDot(page, ATTACKER_FIRST_MESSAGE);
    await actor.click(dot, { ms: 700 });
    await actor.wait(350);
    await actor.key("c");
    const box = page.locator("textarea.cm-input").last();
    await box.waitFor();
    await actor.wait(250);
    await recorder.mark("composer", page.locator("form.cm-composer").last());
    await actor.type(TYPED_COMMENT, 26);
    await actor.wait(300);
    await actor.key("Meta+Enter");
    await page.locator(`.tl-note[aria-label$="event 10"]`).waitFor();
    await actor.wait(700);
    await recorder.mark("ruler", page.locator(".tl-ruler"));
    for (const position of [22, 199]) {
      await actor.hover(page.locator(`.tl-note[aria-label$="event ${position}"]`), 650);
      await actor.wait(300);
      await recorder.mark(`tooltip${position}`, page.locator(".tl-tooltip"));
      await actor.wait(1000);
    }
    await actor.click(page.locator("#more-button"), { ms: 800 });
    await actor.wait(350);
    await recorder.mark("menu", page.getByRole("menu").first());
    const download = page.waitForEvent("download");
    await actor.click(page.getByText("Export run"), { ms: 500 });
    const file = await download;
    const saved = path.join(workDir, "export", file.suggestedFilename());
    await file.saveAs(saved);
    fs.writeFileSync(path.join(workDir, "export", "meta.json"),
      JSON.stringify({ name: file.suggestedFilename(), bytes: fs.statSync(saved).size }));
    await actor.wait(900);
    await recorder.stop();
    await context.close();
  },
};

// ---------- HTML cards: title, chapters, coding agent, API terminal and end ----------

const SWARMCHASING = "swarmchasing.com";
const CHAPTERS = {
  "p1-quote": { kind: "quote", seconds: "4.6",
    title: "\u201cWe don't have good approaches for understanding/overseeing the activity and aims of AI \u2018swarms\u2019.\u201d",
    sub: `Ryan Greenblatt, quoted on ${SWARMCHASING}` },
  "p2-quote": { kind: "quote", seconds: "4.2",
    title: "\u201cSociety lacks the urgently needed tools to make sense of thousands of agents coordinating.\u201d",
    sub: SWARMCHASING },
  "p3-need": { kind: "setup", title: "When one agent goes wrong, it can pull the others with it.",
    line2: "We need to see where, and test what would have stopped it." },
  "d1-data": { title: "Bring your own data", seconds: "3.2",
    sub: "Any agentic dataset maps to agents, channels, messages, memory, tool calls and environment." },
  "l1-long": { kind: "question", seconds: "4.2", title: "Built for long, text-heavy runs",
    sub: "Each text is stored once, compressed. Snapshots store only what changed. The timeline draws only what is on screen.",
    note: "Synthetic benchmark, 30k messages: 11,970 MB \u2192 119 MB" },
  "c0-setup": { kicker: "Example scenario:", title: "ACIArena LLM debate: 3 debaters + aggregator.",
    line2: "Debater 0 is prompted to argue for a wrong answer.", kind: "setup" },
  "c0-question": { title: "Where does the run go wrong, and what fixes it?", kind: "question" },
  "c1-import": { title: "Import any dataset" },
  "c2-replay": { title: "Replay any moment" },
  "c3-fork": { title: "Fork and change one thing" },
  "c4-compare": { title: "Compare outcomes side by side" },
  "c6-views": { title: "Many lenses on the same run", kind: "question", sub: "Built-in views of swarm dynamics: who reads whom, who copies whom" },
  "c7-comment": { title: "Comment and share runs" },
  "c8-live": { title: "Live monitoring" },
};
const cardPages = {
  "01-title": "01-title.html",
  "10-coding-agent": "10-coding-agent.html",
  "12-end": "12-end.html",
  "13-api": "api.html",
  "16-chat": "chat.html",
  "d2-adapter": "code.html",
  "g0-builtin": "plugin-list.html",
  "g2-docs": "code.html",
  "g6-method-plugins": "plugin-list.html",
  ...Object.fromEntries(Object.entries(CHAPTERS).map(([shot, params]) =>
    [shot, `chapter.html?${new URLSearchParams(params)}`])),
};

// Runs two real API calls (a read and a fork, as a coding agent would) and returns the terminal transcript.
async function apiTranscript() {
  const base = APP.replace("http://", "");
  const timelineUrl = `${APP}/api/branches/${RUNS.withMalicious}/timeline`;
  const events = (await (await fetch(timelineUrl)).json()).events.length;
  const body = { cursor: 19, name: "From my agent" };
  const response = await fetch(`${APP}/api/branches/${RUNS.withMalicious}/fork`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (!response.ok) throw new Error(`Fork through the API failed: ${response.status}`);
  const fork = await response.json();
  return [
    { cmd: `curl -s ${base}/api/branches/$RUN/timeline | jq '.events | length'`, out: String(events) },
    { cmd: `curl -s -X POST ${base}/api/branches/$RUN/fork -d '${JSON.stringify(body)}' | jq '{name, fork_position}'`,
      out: JSON.stringify({ name: fork.name, fork_position: fork.fork_position }, null, 2) },
  ];
}

// Code excerpts are read verbatim at record time: the local checkout, or the merged plugin PR on GitHub.
const REPO = process.env.SWARM_LENS_REPO ?? "/Users/jessica/AEStudio/agi/fractal_swarm_lens-ui-revamp";
const GITHUB_REPO = "agencyenterprise/fractal_swarm_lens";
const PLUGIN_COMMIT = "45ec084997455628baa363f3bb1b485fe859cb13"; // merge of PR #7 (web plugins ship their own frontend)

function excerpt(text, ranges) {
  const lines = text.split("\n");
  return ranges.flatMap(([from, to], i) => [...(i ? [null] : []), ...lines.slice(from - 1, to)]);
}
const repoLines = (file, ranges) => excerpt(fs.readFileSync(path.join(REPO, file), "utf8"), ranges);
const githubLines = (ref, file, ranges) => excerpt(execFileSync("gh",
  ["api", `repos/${GITHUB_REPO}/contents/${file}?ref=${ref}`, "-H", "Accept: application/vnd.github.raw"]).toString(), ranges);

// Cards that show real data receive it in play(data).
const CARD_DATA = {
  "13-api": apiTranscript,
  "16-chat": () => JSON.parse(fs.readFileSync(path.join(workDir, "export", "meta.json"), "utf8")),
  "d2-adapter": () => ({
    title: "One small adapter", seconds: 6.2,
    sub: "Your source yields Fact records; Swarm Lens builds state, history and branches from them.",
    files: [
      { label: "src/swarm_lens/application/ports.py", lines: repoLines("src/swarm_lens/application/ports.py", [[48, 51]]) },
      { label: "examples/aciarena/sample_source.py", lines: repoLines("examples/aciarena/sample_source.py",
        [[71, 71], [75, 75], [81, 83], [108, 109], [115, 115]]) },
    ],
  }),
  "g0-builtin": () => ({
    title: "Built-in plugins included", seconds: 5.6,
    rows: [
      ["MAST failure analysis", "An LLM judge marks the 14 MAST failure modes in a trace, each with cited events."],
      ["Activity summary", "Counts of messages, tool calls and memory items, by agent and channel."],
      ["Views", "Lanes, Influence, Blast radius, Phrase spread, Echo, Activity."],
    ],
    refs: "MAST: Cemri et al., 2025, \u201cWhy Do Multi-Agent LLM Systems Fail?\u201d, arXiv:2503.13657",
  }),
  "g2-docs": () => ({
    title: "Write your own plugin. It is documented.", seconds: 6.6,
    sub: "Analyses, views, menu actions and API routes: a plugin can add any of them.",
    files: [
      { label: "docs/integration.md \u00b7 Write a web plugin", wrap: true,
        lines: githubLines(PLUGIN_COMMIT, "docs/integration.md", [[67, 67]]) },
      { label: "docs/integration.md \u00b7 host API (excerpt)", wrap: true,
        lines: githubLines(PLUGIN_COMMIT, "docs/integration.md", [[78, 78], [84, 85]]) },
    ],
  }),
  // Copy supplied by the team building these plugins; none runs in the app yet, so the video shows no UI for them.
  "g6-method-plugins": () => ({
    title: "Method plugins we are building", seconds: 8.6,
    rows: [
      ["Plugin platform", "Plugins read a branch through a read-only view and return metrics, annotations, reports or interventions."],
      ["Influence ribbon", "Directional coupling between agents: does A's history help predict B's next message (transfer entropy)."],
      ["Change points", "Marks where an agent's behavior changes, such as verbosity, with a shortcut to fork there."],
      ["Spread tracer", "Pick a message or phrase: see who received it, from whom, when, and who repeated it."],
      ["Stance lanes", "Each agent's stance per round, with flips linked to the message it read."],
      ["Failure attribution", "An LLM judge suggests the agent and step behind a failure, to confirm by fork-and-fix."],
    ],
    refs: "Influence ribbon: Schreiber 2000; Barnett, Barrett & Seth 2009. Failure attribution based on Who&When (arXiv:2505.00212).",
  }),
};

for (const [shot, file] of Object.entries(cardPages)) {
  shots[shot] = async (browser) => {
    const { context, page, recorder } = await newPage(browser, { viewport: CARD_VIEWPORT, scale: 1 });
    const [name, query] = file.split("?");
    await page.goto(`${pathToFileURL(path.join(CARDS, name)).href}${query ? `?${query}` : ""}`);
    await page.evaluate(() => document.fonts.ready);
    await page.waitForFunction(() => [...document.images].every((img) => img.complete));
    const data = await (CARD_DATA[shot] ?? (() => null))();
    await recorder.start(shot);
    const seconds = await page.evaluate((input) => window.play(input), data);
    await page.waitForTimeout(seconds * 1000);
    await recorder.stop();
    await context.close();
  };
}

const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME });
try {
  for (const [name, record] of Object.entries(shots)) {
    if (onlyShots.length && !onlyShots.includes(name)) continue;
    await record(browser);
  }
} finally {
  await browser.close();
}
