// A small Markdown subset for agent messages. It builds DOM nodes directly,
// so recorded text is never parsed as HTML.

const INLINE = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*|\\\(.+?\\\))/g;

function inline(parent, text) {
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    parent.append(text.slice(last, match.index));
    const token = match[0];
    const [tag, body, className] = token.startsWith("`") ? ["code", token.slice(1, -1)]
      : token.startsWith("**") ? ["strong", token.slice(2, -2)]
      : token.startsWith("\\(") ? ["code", token.slice(2, -2), "md-math"]
      : ["em", token.slice(1, -1)];
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (tag === "code") node.textContent = body;
    else inline(node, body);
    parent.append(node);
    last = match.index + token.length;
  }
  parent.append(text.slice(last));
  return parent;
}

function block(tag, text, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return inline(node, text);
}

function fenced(lines, index, close) {
  const body = [];
  while (index < lines.length && !close(lines[index])) body.push(lines[index++]);
  return { body: body.join("\n"), next: index + 1 };
}

const LIST_ITEM = /^\s*(?:[-*+]|\d+[.)])\s+(.*)$/;

export function renderMarkdown(text) {
  const root = document.createDocumentFragment();
  const lines = String(text ?? "").replace(/\r\n?/g, "\n").split("\n");
  let paragraph = [];
  let list = null;
  const flush = () => {
    if (paragraph.length) root.append(block("p", paragraph.join(" ")));
    paragraph = [];
    list = null;
  };
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    const trimmed = line.trim();
    if (trimmed.startsWith("```")) {
      flush();
      const { body, next } = fenced(lines, index + 1, (candidate) => candidate.trim().startsWith("```"));
      const pre = document.createElement("pre");
      pre.className = "md-code";
      pre.textContent = body;
      root.append(pre);
      index = next - 1;
    } else if (trimmed.startsWith("\\[")) {
      flush();
      const single = trimmed.length > 2 && trimmed.endsWith("\\]");
      const { body, next } = single ? { body: trimmed.slice(2, -2), next: index + 1 }
        : fenced(lines, index + 1, (candidate) => candidate.trim().startsWith("\\]"));
      const math = document.createElement("div");
      math.className = "md-math-block";
      math.textContent = (single ? body : [trimmed.slice(2), body].join("\n")).trim();
      root.append(math);
      index = next - 1;
    } else if (/^#{1,6}\s/.test(trimmed)) {
      flush();
      root.append(block("h4", trimmed.replace(/^#+\s*/, ""), "md-heading"));
    } else if (LIST_ITEM.test(line)) {
      if (paragraph.length) flush();
      const number = line.match(/^\s*(\d+)/)?.[1];
      const ordered = number !== undefined;
      if (!list || list.ordered !== ordered) {
        list = { ordered, node: document.createElement(ordered ? "ol" : "ul") };
        // Models often split one numbered list with paragraphs; keep the numbering they wrote.
        if (ordered && number !== "1") list.node.start = Number(number);
        root.append(list.node);
      }
      list.node.append(block("li", line.match(LIST_ITEM)[1]));
    } else if (!trimmed) flush();
    else if (list && /^\s+/.test(line)) inline(list.node.lastChild, " " + trimmed);
    else {
      list = null;
      paragraph.push(trimmed);
    }
  }
  flush();
  return root;
}
