# SwarmLens: leaf and lens — concept 01

The leaf is the shared environment, the ants are collaborating agents, and the magnifying glass represents observation. The colors follow the application's mint, emerald and navy palette.

Open `preview.html` to compare the static mark, the animation and small-size samples. The preview supports pausing and honors reduced motion on initial load.

## Assets

- `swarm-lens-icon.png`: original 1254 × 1254 PNG.
- `swarm-lens-icon-256.png`: 256 × 256 PNG.
- `swarm-lens-icon-64.png`: 64 × 64 PNG.
- `swarm-lens-animated.gif`: 320 × 320 GIF, 16 frames at 10 fps, repeating every 1.6 seconds.
- `swarm-lens-animated-128.gif`: compact 128 × 128 export of the same animation.
- `swarm-lens-motion-poster.png`: first GIF frame for reduced motion and a paused preview.

Use the static icon for persistent navigation. The animation is suitable for a live-run activity indicator or larger product illustration. At 16 pixels, the leaf and lens carry the identity; the smaller ants lose detail. A dedicated simplified favicon can be developed once the direction is chosen.

Artwork and animation frames were generated with the built-in `image_gen` tool. The exact prompts are in `source/prompts.json`. FFmpeg split the generated 4 × 4 sprite sheet into frames and encoded the GIF; it also produced the smaller PNG exports. This concept has not replaced the application's existing branding.
