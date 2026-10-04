# Swarm Lens demo video

`swarm-lens-demo.mp4` is the hackathon submission video (v7, 2:06, 1920x1080, 30 fps, H.264, AAC 48 kHz, -16 LUFS). It is built entirely from code: real screen recordings of the app driven by Playwright, HTML title cards, procedurally generated sound, and an ffmpeg edit.

- `storyboard.md`: the story, the shot list with timings, which shots are real recordings, and the sources read before writing the copy.
- `STYLE.md`: the rules the video follows, collected from every round of review. Read it before changing anything.
- `scripts/`: the recipe.

## Recipe

Requirements: Node 20+, Python 3.11+ with numpy, ffmpeg, a Chromium (Playwright's "Google Chrome for Testing" build works), and a running Swarm Lens server with the ACIArena sample runs imported.

1. Start the app on a disposable copy of the data, for example `swarm-lens --data /tmp/lens-demo --port 8931`. Import the ACIArena samples (`python -m examples.aciarena.samples import --input examples/aciarena/samples/<set> --data /tmp/lens-demo`). The recording creates branches and comments, so never record against real data.
2. Record the shots: `node scripts/record.mjs` (set the Chromium path and server URL at the top of the file). It drives the app with eased cursor moves and logs every click and key press with its timestamp. Those timestamps drive the click sounds.
   - The fork shot clicks "Create and run", which continues the debate with the CrewAI runtime and calls OpenAI (gpt-4o-mini, a few cents, about 4 minutes). The server needs the `crewai` extra and `OPENAI_API_KEY`.
   - The MAST shot needs a saved MAST report on the recorded run. Opening the analyze dialog is free; the video never submits it.
3. Edit: `python scripts/edit.py` cuts, speed-ramps, zooms and composes the shots with the HTML cards in `scripts/cards/` (rendered with Playwright).
4. Build: `python scripts/build.py` synthesizes the music bed and sound effects, mixes them, applies one measured gain change, and encodes the final MP4. It stops with an error if the true peak would exceed -1.5 dBTP.
5. Check frames (`ffmpeg -ss <t> -i out.mp4 -frames:v 1 frame.png`) and audio (`ffmpeg -i out.mp4 -af ebur128=peak=true -f null -`) before sharing.

Raw recordings (about 1 GB) are not committed; step 2 regenerates them.
