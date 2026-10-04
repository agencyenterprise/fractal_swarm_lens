// Render the reviewed Markdown wiki into a static mirror for GitHub Pages.
import fs from 'node:fs';
import path from 'node:path';
import MarkdownIt from 'markdown-it';
const root = path.resolve(import.meta.dirname, '..');
const source = path.join(root, 'docs/wiki');
const dest = path.join(root, 'site/wiki');
fs.mkdirSync(dest, { recursive: true });
const md = new MarkdownIt({ html: false, linkify: true });
const names = ['Home','Getting-started','Explore-traces','Examples-and-datasets','Live-collection-and-branching','Build-a-plugin','Research-and-methods','Architecture-and-storage','Troubleshooting'];
const esc = s => s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
const base = md.renderer.rules.link_open || ((tokens, idx, opts, env, self) => self.renderToken(tokens,idx,opts));
md.renderer.rules.link_open = (tokens,idx,opts,env,self) => {
  const href=tokens[idx].attrGet('href');
  if (names.includes(href)) tokens[idx].attrSet('href', href+'.html');
  return base(tokens,idx,opts,env,self);
};
for (const name of names) {
 const title=name.replaceAll('-',' ');
 const nav=names.map(n=>`<a ${n===name?'aria-current="page"':''} href="${n}.html">${esc(n.replaceAll('-',' '))}</a>`).join('');
 const body=md.render(fs.readFileSync(path.join(source,name+'.md'),'utf8'));
 fs.writeFileSync(path.join(dest,name+'.html'),`<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(title)} — Swarm Lens Wiki</title><link rel="icon" href="../favicon.ico"><link rel="stylesheet" href="../style.css"><link rel="stylesheet" href="../wiki.css"></head><body><a class="skip" href="#article">Skip to content</a><header class="nav wrap"><a class="brand" href="../"><img src="../assets/logo.gif" width="38" height="38" alt="">swarm<span class="light">lens</span></a><nav aria-label="Main navigation"><a href="../#demo">Demo</a><a href="Home.html">Wiki</a><a class="nav-code" href="https://github.com/agencyenterprise/fractal_swarm_lens">GitHub ↗</a></nav></header><div class="wiki-layout wrap"><aside><div class="eyebrow">RESEARCH FIELD GUIDE</div><nav aria-label="Wiki pages">${nav}</nav></aside><main id="article">${body}<div class="wiki-bottom">Docs for main · <a href="https://github.com/agencyenterprise/fractal_swarm_lens/blob/docs/research-site-wiki/docs/wiki/${name}.md">View Markdown source ↗</a></div></main></div></body></html>`);
}
console.log(`Rendered ${names.length} wiki pages.`);
