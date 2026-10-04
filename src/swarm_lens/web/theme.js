// Loaded as a classic script in <head>: it sets the theme before first paint
// and keeps the "system" choice in sync with the operating system.
(() => {
  const KEY = "swarm-lens:theme";
  const media = matchMedia("(prefers-color-scheme: dark)");
  let choice = "system";
  try { choice = localStorage.getItem(KEY) || "system"; } catch { /* Storage unavailable; the choice lasts for this visit. */ }
  const apply = () => {
    document.documentElement.dataset.theme = choice === "system" ? (media.matches ? "dark" : "light") : choice;
  };
  media.addEventListener("change", apply);
  apply();
  window.swarmLensTheme = {
    get: () => choice,
    set(next) {
      choice = next;
      try { localStorage.setItem(KEY, next); } catch { /* Storage unavailable; the choice lasts for this visit. */ }
      apply();
    },
  };
})();
