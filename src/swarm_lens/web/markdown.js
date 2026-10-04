// Agent messages render through markdown-it + KaTeX (frontend/message-format.js). Recorded HTML stays
// text there. The bundle is large, so it loads on first use; until then a message shows as plain text.

let formatter;
const latest = new WeakMap();

function loadFormatter() {
  formatter ||= import("./message-format.js").catch((error) => {
    formatter = null; // A failed download is retried by the next render.
    throw error;
  });
  return formatter;
}

export async function renderMarkdownInto(node, text) {
  text = String(text ?? "");
  latest.set(node, text);
  node.classList.add("md");
  node.classList.remove("is-formatted");
  node.textContent = text;
  const { renderMessageHTML } = await loadFormatter();
  // A newer render or cancelRender() owns the node now.
  if (latest.get(node) !== text) return;
  node.innerHTML = renderMessageHTML(text);
  node.classList.add("is-formatted");
}

export function cancelRender(node) {
  latest.delete(node);
}
