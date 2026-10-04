# Swarm Lens demo video

`swarm-lens-demo.mp4` is the hackathon submission video (v7, 2:06, 1920x1080, 30 fps, H.264, AAC 48 kHz, -16 LUFS). It is built entirely from code: real screen recordings of the app driven by Playwright, HTML title cards, procedurally generated sound, and an ffmpeg edit.

- `storyboard.md`: the story, the shot list with timings, which shots are real recordings, and the sources read before writing the copy.
- `STYLE.md`: the rules the video follows, collected from every round of review. Read it before changing anything.
- `scripts/`: the recipe.

## Recipe

This documents how the video was made; it is not reproducible from a fresh clone. The recording needs data that is not in the repo: the ACIArena medicine-004 run (only medicine-000 is bundled in `examples/aciarena/samples/`), the specific branch UUIDs hardcoded in `RUNS` at the top of `scripts/record.mjs`, and a saved MAST report on the run named there.

Requirements:
- Node 20+ with `playwright-core`, and a Chromium binary (Playwright's "Google Chrome for Testing" build works).
- Python 3.11+ with `numpy`, `opencv-python` and `Pillow`, plus `ffmpeg` on the `PATH`.
- The `gh` CLI, authenticated: `record.mjs` reads code excerpts for one card from GitHub with `gh api`.
- A running Swarm Lens server with those runs imported.

1. Start the app on a disposable copy of the data, for example `swarm-lens --data /tmp/lens-demo --port 8931`, and import the runs (for the bundled samples: `python -m examples.aciarena.samples import --input examples/aciarena/samples/<set> --data /tmp/lens-demo`). The recording creates branches and comments, so never record against real data.
2. Record the shots and render the HTML cards in `scripts/cards/`:
   `PLAYWRIGHT_CORE=<path to playwright-core> CHROME=<chromium binary> SWARM_LENS_URL=http://127.0.0.1:8931 node scripts/record.mjs <work dir> [shot ...]`
   - `PLAYWRIGHT_CORE` defaults to the `playwright-core` package, `SWARM_LENS_URL` to `http://127.0.0.1:8931`, and `SWARM_LENS_REPO` (where code excerpts are read) to this checkout. Pass shot names to re-record only those.
   - It drives the app with eased cursor moves and logs every click and key press with its timestamp into `<work dir>/shots/`. Those timestamps drive the click sounds.
   - The fork shot clicks "Create and run", which continues the debate with the CrewAI runtime and calls OpenAI (gpt-4o-mini, a few cents, about 4 minutes). The server needs the `crewai` extra and `OPENAI_API_KEY`.
   - The MAST shot needs a saved MAST report on the recorded run. Opening the analyze dialog is free; the video never submits it.
3. Build: `python scripts/build.py <work dir> <output.mp4>`. It reads the edit decision list in `scripts/edit.py` (cuts, speed ramps, zooms, captions; edit that file to change the cut, it is not run on its own), renders the picture, synthesizes the music bed and sound effects, mixes them, applies one measured gain change, and encodes the MP4. It stops with an error if the true peak would exceed -1.5 dBTP. Captions use the font at `DEMO_FONT`, which defaults to macOS's system font (`/System/Library/Fonts/SFNS.ttf`).
4. Check frames (`ffmpeg -ss <t> -i out.mp4 -frames:v 1 frame.png`) and audio (`ffmpeg -i out.mp4 -af ebur128=peak=true -f null -`) before sharing.

Raw recordings (about 1 GB) are not committed; step 2 regenerates them.
