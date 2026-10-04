import MarkdownIt from 'markdown-it';
import katex from 'katex';
import texmath from 'markdown-it-texmath';

// Recorded messages are untrusted. Render Markdown, never their HTML or remote images.
const markdown = new MarkdownIt({ html: false, linkify: false, typographer: false });
markdown.validateLink = url => /^(https?:\/\/|mailto:)/i.test(url);
markdown.renderer.rules.image = (tokens, index) =>
  markdown.utils.escapeHtml(`[Image: ${tokens[index].content || 'image'}]`);
markdown.renderer.rules.link_open = (tokens, index, options, env, renderer) => {
  tokens[index].attrSet('target', '_blank');
  tokens[index].attrSet('rel', 'noopener noreferrer');
  return renderer.renderToken(tokens, index, options);
};
markdown.use(texmath, {
  engine: { renderToString: (source, options) => katex.renderToString(source, { ...options, macros: {} }) },
  delimiters: ['brackets', 'dollars'],
  katexOptions: { output: 'mathml', trust: false, throwOnError: false, strict: 'ignore', maxExpand: 1000, maxSize: 20 },
});

// LLM messages often put display math inside a list paragraph without blank lines.
// Keep Markdown's list structure while recognizing those bracketed expressions.
markdown.inline.ruler.before('escape', 'mast_display_math', (state, silent) => {
  if (!state.src.startsWith('\\[', state.pos)) return false;
  const end = state.src.indexOf('\\]', state.pos + 2);
  if (end < 0) return false;
  if (!silent) {
    const token = state.push('mast_display_math', '', 0);
    token.content = state.src.slice(state.pos + 2, end);
  }
  state.pos = end + 2;
  return true;
});
markdown.renderer.rules.mast_display_math = (tokens, index) => katex.renderToString(tokens[index].content, {
  output: 'mathml', displayMode: true, trust: false, throwOnError: false,
  strict: 'ignore', maxExpand: 1000, maxSize: 20, macros: {},
});

export function renderMastMessage(text) {
  return markdown.render(text);
}
