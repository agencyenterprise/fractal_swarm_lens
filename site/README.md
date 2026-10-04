# Public website and wiki

The site is standalone HTML/CSS/JS. It reuses the explorer's colors, typographic approach, and logo. The demo (2:44, H.264/AAC, about 27 MB) is stored in `assets/demo.mp4`. It is byte-identical to `demo-video/swarm-lens-demo.mp4`, which `demo-video/` builds; keep the two identical so Git stores the video once, and replace both together. `demo-poster.jpg` is a frame from it. No analytics, external fonts, CDN scripts, or framework build is required.

Preview: `python3 -m http.server 8771 --bind 127.0.0.1 --directory site`.

Deployment: the `gh-pages` branch contains only the contents of `site/`. GitHub Pages serves that branch at `/`. Update from a reviewed source revision with `git subtree split --prefix site HEAD`, then push that commit to `gh-pages` without force. If it does not fast-forward, reconcile the publishing history first. Never publish the repository root or local workspace data.

Wiki source is maintained in `docs/wiki/`, with native GitHub wiki links and a custom sidebar. After creating the first wiki page in GitHub, clone `https://github.com/agencyenterprise/fractal_swarm_lens.wiki.git`, copy these Markdown files into it, review the diff, commit, and push. Do not delete other wiki pages. The GitHub wiki is a separate repository and does not update automatically when this repository is merged.

The documentation structure takes inspiration from Dear ImGui's task-oriented wiki index (https://github.com/ocornut/imgui/wiki) and Neovim's motivation/architecture guidance (https://github.com/neovim/neovim/wiki/Introduction). No popularity ranking is claimed and no prose was copied.

Research claims are deliberately limited to implemented functionality. Do not add benchmark performance, publication, or endorsement claims without supporting evidence. The quickstart uses `main`; update both website and wiki when the documented interface changes.

A browsable wiki mirror is included at `wiki/Home.html`, so the documentation is usable before the native GitHub wiki is initialized. After editing `docs/wiki/`, run `npm ci && node tools/build-site-wiki.mjs` and commit the generated HTML with the Markdown. The mirror and native wiki share their source; neither performs runtime Markdown rendering.
