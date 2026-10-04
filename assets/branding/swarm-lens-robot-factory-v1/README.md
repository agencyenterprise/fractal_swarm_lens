# SwarmLens robot factory

Early-2000s Habbo-inspired isometric pixel art: three robots walk across a tiled factory floor, observed through a magnifying glass. The observed robot represents misalignment by turning red near the end of the animation.

- `swarm-lens-icon.png`: original 1254-pixel static artwork, with the observed robot red.
- `swarm-lens-icon-256.png` and `swarm-lens-icon-64.png`: smaller PNG exports.
- `swarm-lens.ico`: 16, 32, 48 and 64-pixel favicon.
- `swarm-lens-observation.gif`: 320-pixel animation.
- `swarm-lens-observation-128.gif`: compact version used by the application sidebar.
- `swarm-lens-motion-poster.png`: neutral first frame for the preview's pause control.
- `preview.html`: static and animated comparison, small-size examples and downloads.

The GIF contains 16 walking frames at 10 fps. All robots are neutral for the first 12 frames (1.2 seconds); only the robot inside the lens is red for the final four frames. The last frame is held for 400 ms, making a 1.9-second repeating loop. The application's reduced-motion fallback uses the static icon.

Both artwork and animation frames were made with the built-in image-generation tool. Exact prompts are saved in `source/prompts.json`; the frame sheet is `source/animation-sheet.png`. FFmpeg was used only for frame extraction, resizing and GIF encoding; Pillow encoded the ICO file. The previous leaf-and-ant design remains archived in the sibling branding directory.
