// Shared by every card: the animated logo, and cues that tell the editor when to place sounds.
// The 320 px source of the app logo animation; shown only at integer or half scales, nearest-neighbour.
const LOGO_GIF = "/Users/jessica/AEStudio/agi/fractal_swarm_lens-ui-revamp/assets/branding/swarm-lens-robot-factory-v1/swarm-lens-observation.gif";
const cue = (kind) => (window.__cues ??= []).push({ kind, wall: Date.now() / 1000 });
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
for (const img of document.querySelectorAll("img[data-logo]")) img.src = LOGO_GIF;
