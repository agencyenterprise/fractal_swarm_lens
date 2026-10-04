// Shared by every card: the animated logo, and cues that tell the editor when to place sounds.
// The 320 px source of the app logo animation; shown only at integer or half scales, nearest-neighbour.
// Relative to the card HTML pages in this folder (loaded over file://), so it resolves to the repo's assets/.
const LOGO_GIF = "../../../assets/branding/swarm-lens-robot-factory-v1/swarm-lens-observation.gif";
const cue = (kind) => (window.__cues ??= []).push({ kind, wall: Date.now() / 1000 });
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
for (const img of document.querySelectorAll("img[data-logo]")) img.src = LOGO_GIF;
