#!/usr/bin/env python3
"""Edits the recorded shots into the Swarm Lens demo and scores it.

Picture: time-remapped shots (speed ramps, jump cuts), camera punch-ins, captions, highlights and
transitions, rendered frame by frame. Sound: a procedural music bed at 120 BPM plus clicks, keys,
whooshes and hits placed from the recorded action cues. Loudness is normalized to -16 LUFS.

Usage: python3 build.py <work dir with shots/> <output.mp4>
Requires numpy, opencv-python, Pillow and ffmpeg.
"""
import argparse
import json
import subprocess
import tempfile
import wave
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1920, 1080, 30
RATE = 48000
BPM = 120
BEAT = 60 / BPM
FONT = "/System/Library/Fonts/SFNS.ttf"
INK, ACCENT, WARN = (27, 27, 26), (15, 123, 104), (194, 65, 12)
TRANSITION_SECONDS = {"cut": 0.0, "whip": 0.3, "zoom": 0.36, "wipe": 0.4, "fade": 0.3}


def ease(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * t * (t * (t * 6 - 15) + 10)


# ---------------------------------------------------------------- shots

class Shot:
    """A recorded shot: timestamped JPEG frames, action cues and named element boxes."""

    def __init__(self, directory: Path):
        self.dir = directory
        manifest = json.loads((directory / "shot.json").read_text())
        self.scale = manifest["scale"]
        self.viewport = manifest["viewport"]
        self.duration = manifest["duration"]
        self.files = [f["file"] for f in manifest["frames"]]
        self.times = np.array([f["t"] for f in manifest["frames"]])
        self.cues = manifest["cues"]
        self.marks = manifest["marks"]

    def frame(self, t):
        index = max(int(np.searchsorted(self.times, t, side="right")) - 1, 0)
        return load_jpeg(str(self.dir / self.files[index]))

    def mark_center(self, name):
        box = self.marks[name]
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


@lru_cache(maxsize=6)
def load_jpeg(path):
    image = cv2.imread(path, cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(path)
    return image


# ---------------------------------------------------------------- time remapping

def time_map(pieces, smooth=0.28):
    """Output frame index -> source time. Pieces are (in, out, seconds on screen). Speed changes inside
    a contiguous run of pieces are smoothed into ramps; a gap between pieces stays a jump cut."""
    runs, current = [], []
    for piece in pieces:
        if current and abs(piece[0] - current[-1][1]) > 1e-6:
            runs.append(current)
            current = []
        current.append(piece)
    runs.append(current)
    window = max(int(smooth * FPS) | 1, 1)
    out = []
    for run in runs:
        samples = []
        for start, end, seconds in run:
            n = max(int(round(seconds * FPS)), 1)
            samples.extend(start + (end - start) * (np.arange(n) / n))
        samples = np.array(samples)
        if len(run) > 1 and len(samples) > window:
            pad = window // 2
            head = samples[0] - (samples[1] - samples[0]) * np.arange(pad, 0, -1)
            tail = samples[-1] + (samples[-1] - samples[-2]) * np.arange(1, pad + 1)
            samples = np.convolve(np.concatenate([head, samples, tail]), np.ones(window) / window, mode="valid")
        out.append(samples)
    return np.concatenate(out)


# ---------------------------------------------------------------- camera

@dataclass
class Move:
    """Moves the camera to `zoom` around `focus` between t0 and t1 (seconds into the segment).
    `focus` is a mark name, an (x, y) point in viewport CSS px, or None for the viewport center."""
    t0: float
    t1: float
    zoom: float
    focus: object = None


def camera_at(moves, shot, t):
    center = (shot.viewport["width"] / 2, shot.viewport["height"] / 2)
    state = (1.0, center)
    for move in moves:
        focus = shot.mark_center(move.focus) if isinstance(move.focus, str) else move.focus or center
        if t <= move.t0:
            break
        k = float(ease((t - move.t0) / max(move.t1 - move.t0, 1e-6)))
        zoom = float(np.exp(np.log(state[0]) + (np.log(move.zoom) - np.log(state[0])) * k))
        state = (zoom, (state[1][0] + (focus[0] - state[1][0]) * k, state[1][1] + (focus[1] - state[1][1]) * k))
    return state


def render_view(image, shot, zoom, focus):
    """Frames the source image at `zoom` around `focus` (CSS px), returning a W x H frame and the
    map from CSS px to output px."""
    src_h, src_w = image.shape[:2]
    css_to_src = src_w / shot.viewport["width"]
    scale = W / src_w * zoom
    half_w, half_h = W / 2 / scale, H / 2 / scale
    cx = cx0 = float(np.clip(focus[0] * css_to_src, half_w, src_w - half_w))
    cy = cy0 = float(np.clip(focus[1] * css_to_src, half_h, src_h - half_h))
    if scale < 1:
        small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        actual = small.shape[1] / src_w
        image, cx, cy, residual = small, cx * actual, cy * actual, scale / actual
    else:
        residual = scale
    matrix = np.float32([[residual, 0, W / 2 - residual * cx], [0, residual, H / 2 - residual * cy]])
    interpolation = cv2.INTER_LINEAR if abs(residual - 1) < 0.02 else cv2.INTER_CUBIC
    frame = cv2.warpAffine(image, matrix, (W, H), flags=interpolation, borderMode=cv2.BORDER_REPLICATE)
    to_out = lambda x, y: (W / 2 + (x * css_to_src - cx0) * scale, H / 2 + (y * css_to_src - cy0) * scale)
    return frame, to_out


# ---------------------------------------------------------------- overlays

def font(size, weight="Semibold"):
    face = ImageFont.truetype(FONT, size)
    face.set_variation_by_name(weight)
    return face


@lru_cache(maxsize=64)
def caption_image(text, size=36):
    """A compact near-black pill (#1c1d1f, 92%) with white text on one line: 36 px captions, 22 px source notes."""
    face = font(size, "Semibold" if size >= 30 else "Medium")
    left, top, right, bottom = face.getbbox(text)
    pad_x, pad_y = (28, 16) if size >= 30 else (16, 9)
    width, height = right - left + pad_x * 2, size + pad_y * 2 + 4
    margin = 24
    canvas = Image.new("RGBA", (width + margin * 2, height + margin * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((margin, margin + 6, margin + width, margin + height + 6), 16,
                                             fill=(0, 0, 0, 70))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(12)))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((margin, margin, margin + width, margin + height), 16, fill=(28, 29, 31, 235))
    draw.text((margin + pad_x - left, margin + pad_y + 4 - top), text, font=face, fill=(255, 255, 255, 255))
    return np.array(canvas)


@lru_cache(maxsize=16)
def tag_image(text, tone):
    face = font(26, "Bold")
    left, top, right, bottom = face.getbbox(text)
    width, height = right - left + 28, 42
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), 10, fill=(WARN if tone == "warn" else ACCENT) + (255,))
    draw.text((14 - left, (height - (bottom - top)) / 2 - top), text, font=face, fill=(255, 255, 255, 255))
    return np.array(canvas)


def blend_rgba(frame, rgba, x, y, alpha=1.0):
    """Alpha-composites an RGBA image (RGB order) onto a BGR frame at integer (x, y)."""
    h, w = rgba.shape[:2]
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, W), min(y + h, H)
    if x1 <= x0 or y1 <= y0 or alpha <= 0:
        return
    patch = rgba[y0 - y:y1 - y, x0 - x:x1 - x]
    a = patch[..., 3:4].astype(np.float32) / 255 * alpha
    region = frame[y0:y1, x0:x1].astype(np.float32)
    frame[y0:y1, x0:x1] = (region * (1 - a) + patch[..., 2::-1].astype(np.float32) * a).astype(np.uint8)


@dataclass
class Caption:
    text: str
    t0: float
    t1: float
    place: str = "bottom"  # bottom | top | note (small source line, just above the bottom caption)


def draw_caption(frame, caption, t):
    fade = 0.15
    if not caption.t0 <= t <= caption.t1:
        return
    k = float(ease(min((t - caption.t0) / fade, (caption.t1 - t) / fade, 1.0)))
    image = caption_image(caption.text, 22 if caption.place == "note" else 36)
    h, w = image.shape[:2]
    y = 26 if caption.place == "top" else H - h - (122 if caption.place == "note" else 26)
    x = (W - w) // 2
    blend_rgba(frame, image, x, int(y + (1 - k) * 14), k)


@dataclass
class Highlight:
    """Rings a marked element (CSS px box) and tags it."""
    mark: str
    t0: float
    t1: float
    tag: str = ""
    tone: str = "warn"
    pad: float = 16


def draw_highlight(frame, highlight, shot, to_out, t):
    if not highlight.t0 <= t <= highlight.t1:
        return
    k = float(ease(min((t - highlight.t0) / 0.25, (highlight.t1 - t) / 0.2, 1.0)))
    box = shot.marks[highlight.mark]
    x0, y0 = to_out(box["x"], box["y"])
    x1, y1 = to_out(box["x"] + box["width"], box["y"] + box["height"])
    grow = (1 - k) * 30 + highlight.pad
    rect = (int(x0 - grow), int(y0 - grow), int(x1 + grow), int(y1 + grow))
    overlay = frame.copy()
    color = (WARN if highlight.tone == "warn" else ACCENT)[::-1]
    cv2.rectangle(overlay, rect[:2], rect[2:], color, -1, cv2.LINE_AA)
    cv2.addWeighted(overlay, 0.10 * k, frame, 1 - 0.10 * k, 0, dst=frame)
    ring = frame.copy()
    rounded_rectangle(ring, rect, 14, color, 5)
    cv2.addWeighted(ring, k, frame, 1 - k, 0, dst=frame)
    if highlight.tag:
        tag = tag_image(highlight.tag, highlight.tone)
        above = rect[1] - tag.shape[0] - 10
        blend_rgba(frame, tag, rect[0], above if above >= 0 else rect[3] + 10, k)


def rounded_rectangle(image, rect, radius, color, thickness):
    x0, y0, x1, y1 = rect
    radius = int(min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
    for (cx, cy, start) in ((x0 + radius, y0 + radius, 180), (x1 - radius, y0 + radius, 270),
                            (x1 - radius, y1 - radius, 0), (x0 + radius, y1 - radius, 90)):
        cv2.ellipse(image, (cx, cy), (radius, radius), start, 0, 90, color, thickness, cv2.LINE_AA)
    cv2.line(image, (x0 + radius, y0), (x1 - radius, y0), color, thickness, cv2.LINE_AA)
    cv2.line(image, (x0 + radius, y1), (x1 - radius, y1), color, thickness, cv2.LINE_AA)
    cv2.line(image, (x0, y0 + radius), (x0, y1 - radius), color, thickness, cv2.LINE_AA)
    cv2.line(image, (x1, y0 + radius), (x1, y1 - radius), color, thickness, cv2.LINE_AA)


# ---------------------------------------------------------------- segments

@dataclass
class Segment:
    shot: str
    pieces: list
    enter: str = "cut"
    camera: list = field(default_factory=list)
    captions: list = field(default_factory=list)
    highlights: list = field(default_factory=list)
    sounds: list = field(default_factory=list)  # extra (kind, t) sounds placed by the edit

    def prepare(self, shots_dir):
        self.source = Shot(shots_dir / self.shot)
        self.map = time_map(self.pieces)
        self.length = len(self.map) / FPS
        for start, end, seconds in self.pieces:
            # A card may be held past its recorded end (it is static by then); a recorded shot may not.
            if end > self.source.duration + 0.05 and not (self.shot.startswith("c") and seconds == end - start):
                raise ValueError(f"{self.shot}: piece ends at {end}s, the shot has {self.source.duration:.2f}s")

    def render(self, t):
        index = int(np.clip(round(t * FPS), 0, len(self.map) - 1))
        local = np.clip(t, 0, self.length)
        zoom, focus = camera_at(self.camera, self.source, local)
        frame, to_out = render_view(self.source.frame(self.map[index]), self.source, zoom, focus)
        for highlight in self.highlights:
            draw_highlight(frame, highlight, self.source, to_out, local)
        for caption in self.captions:
            draw_caption(frame, caption, local)
        return frame

    def cue_times(self):
        """Source cues mapped onto this segment's timeline; cues inside a jump cut are dropped."""
        placed = []
        for cue in self.source.cues:
            hits = np.nonzero(np.abs(self.map - cue["t"]) <= 0.6 / FPS * max(1.0, self.local_speed(cue["t"])))[0]
            if len(hits):
                placed.append((cue["kind"], hits[0] / FPS))
        return placed + list(self.sounds)

    def local_speed(self, source_t):
        for start, end, seconds in self.pieces:
            if start <= source_t <= end:
                return (end - start) / seconds
        return 1.0


# ---------------------------------------------------------------- transitions

def whip(a, b, p):
    e = float(ease(p))
    shift = int(e * W)
    frame = np.empty_like(a)
    frame[:, :W - shift] = a[:, shift:]
    frame[:, W - shift:] = b[:, :shift]
    blur = int(90 * 4 * p * (1 - p)) | 1
    return cv2.blur(frame, (blur, 1)) if blur > 1 else frame


def scale_frame(frame, s, background=(246, 246, 244)):
    matrix = cv2.getRotationMatrix2D((W / 2, H / 2), 0, s)
    return cv2.warpAffine(frame, matrix, (W, H), flags=cv2.INTER_LINEAR, borderValue=background[::-1])


def zoom(a, b, p):
    """Punches through the outgoing frame, dips to the page color, and settles the incoming one."""
    background = np.full_like(a, (244, 246, 246))
    if p < 0.5:
        e = float(ease(p * 2))
        return cv2.addWeighted(scale_frame(a, 1 + 0.3 * e), 1 - e, background, e, 0)
    e = float(ease((p - 0.5) * 2))
    return cv2.addWeighted(background, 1 - e, scale_frame(b, 0.9 + 0.1 * e), e, 0)


def wipe(a, b, p):
    e = float(ease(p))
    xs = np.arange(W)[None, :] + 0.35 * np.arange(H)[:, None]
    edge = e * (W + 0.35 * H + 240) - 120
    mask = np.clip((edge - xs) / 120 + 0.5, 0, 1)[..., None].astype(np.float32)
    return (a * (1 - mask) + b * mask).astype(np.uint8)


def fade(a, b, p):
    e = float(ease(p))
    return cv2.addWeighted(a, 1 - e, b, e, 0)


TRANSITIONS = {"whip": whip, "zoom": zoom, "wipe": wipe, "fade": fade}


def layout(segments):
    """Segment k starts at the sum of the previous lengths; a transition is centered on that cut."""
    start = 0.0
    for segment in segments:
        segment.start = start
        start += segment.length
    return start


def composite(segments, t):
    """The output frame at time t, including any transition into or out of the current segment."""
    k = max(i for i, s in enumerate(segments) if s.start <= t + 1e-9)
    cur = segments[k]
    frame = cur.render(t - cur.start)
    half_in = TRANSITION_SECONDS[cur.enter] / 2
    if k > 0 and t - cur.start < half_in:
        prev = segments[k - 1]
        return TRANSITIONS[cur.enter](prev.render(t - prev.start), frame, (t - cur.start + half_in) / (2 * half_in))
    nxt = segments[k + 1] if k + 1 < len(segments) else None
    if nxt and nxt.enter != "cut" and nxt.start - t <= TRANSITION_SECONDS[nxt.enter] / 2:
        half = TRANSITION_SECONDS[nxt.enter] / 2
        return TRANSITIONS[nxt.enter](frame, nxt.render(t - nxt.start), (t - (nxt.start - half)) / (2 * half))
    return frame


def render_video(segments, total, path):
    encoder = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS),
         "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
         "-color_range", "tv", "-profile:v", "high", str(path)], stdin=subprocess.PIPE)
    frames = int(round(total * FPS))
    for n in range(frames):
        frame = composite(segments, n / FPS)
        encoder.stdin.write(frame.tobytes())
        if n % 150 == 0:
            print(f"  frame {n}/{frames}", flush=True)
    encoder.stdin.close()
    if encoder.wait():
        raise RuntimeError("ffmpeg failed while encoding the picture")


# ---------------------------------------------------------------- sound

rng = np.random.default_rng(7)


def env_exp(n, decay):
    return np.exp(-np.arange(n) / (decay * RATE))


def noise(n):
    return rng.standard_normal(n)


def bandpass(signal, low, high):
    spectrum = np.fft.rfft(signal)
    freqs = np.fft.rfftfreq(len(signal), 1 / RATE)
    spectrum[(freqs < low) | (freqs > high)] = 0
    return np.fft.irfft(spectrum, len(signal))


def sine(freq, n, phase=0.0):
    return np.sin(2 * np.pi * np.cumsum(np.broadcast_to(freq, (n,)) / RATE) + phase)


def click_sound(soft=False):
    n = int(0.03 * RATE)
    body = bandpass(noise(n), 1800, 7000) * env_exp(n, 0.0025)
    tone = sine(1500 + rng.uniform(-80, 80), n) * env_exp(n, 0.004) * 0.5
    return (body * 0.6 + tone) * (0.15 if soft else 0.22)


def key_sound():
    n = int(0.02 * RATE)
    return bandpass(noise(n), 2500, 9000) * env_exp(n, 0.0018) * rng.uniform(0.12, 0.2)


def swept_bandpass(signal, centres, q=0.9):
    """A state-variable band-pass whose centre frequency follows `centres` sample by sample. The filter
    keeps its state across the sweep, so the output is continuous (no block boundaries)."""
    low = band = 0.0
    out = np.empty_like(signal)
    coefficients = 2 * np.sin(np.pi * np.minimum(centres, RATE / 6) / RATE)
    damping = 1 / q
    for i, (x, f) in enumerate(zip(signal, coefficients)):
        low += f * band
        high = x - low - damping * band
        band += f * high
        out[i] = band
    return out


def whoosh_sound(length=0.45, up=True):
    n = int(length * RATE)
    t = np.linspace(0, 1, n)
    sweep = t if up else 1 - t
    shape = np.sin(np.pi * t) ** 2
    return swept_bandpass(noise(n), 400 + 3600 * sweep) * shape * 0.35


def hit_sound(big=True):
    n = int((2.4 if big else 0.8) * RATE)  # long enough for the boom to decay below -50 dB
    sweep = 110 * np.exp(-np.arange(n) / (0.09 * RATE)) + 42
    boom = sine(sweep, n) * env_exp(n, 0.45 if big else 0.14)
    crack = bandpass(noise(n), 800, 9000) * env_exp(n, 0.06 if big else 0.02) * 0.45
    return (boom + crack) * (0.32 if big else 0.09)


def riser_sound(length):
    n = int(length * RATE)
    t = np.linspace(0, 1, n)
    tone = sine(180 + 700 * t ** 2, n) * 0.18 + sine(270 + 1050 * t ** 2, n) * 0.08
    air = swept_bandpass(noise(n), 500 + 6000 * t ** 2) * 0.35
    return (tone + air) * t ** 2.2 * (1 - t ** 16) * 0.7


def pop_sound():
    n = int(0.12 * RATE)
    return sine(np.linspace(500, 1200, n), n) * env_exp(n, 0.03) * 0.3


def alert_sound():
    n = int(0.22 * RATE)
    one = sine(988, n) * env_exp(n, 0.07)
    two = sine(1319, n) * env_exp(n, 0.09)
    out = np.zeros(int(0.42 * RATE))
    out[:n] += one * 0.28
    out[int(0.15 * RATE):int(0.15 * RATE) + n] += two * 0.28
    return out


SOUNDS = {
    "click": lambda: click_sound(),
    "release": lambda: click_sound(soft=True),
    "key": key_sound,
    "enter": lambda: click_sound() * 0.8,
    "tick": lambda: click_sound(soft=True) * 0.7,
    "slam": lambda: hit_sound(big=False),
    "hit": lambda: hit_sound(big=True),
    "pop": pop_sound,
    "alert": alert_sound,
    "swoosh": lambda: whoosh_sound(0.5),
}


def place(track, sound, t, gain=1.0, pan=0.0):
    start = int(t * RATE)
    if start >= track.shape[0] or start + len(sound) <= 0:
        return
    end = min(start + len(sound), track.shape[0])
    sound = sound[max(-start, 0):end - start]
    start = max(start, 0)
    track[start:end, 0] += sound * gain * (1 - max(pan, 0))
    track[start:end, 1] += sound * gain * (1 + min(pan, 0))


NOTE = {"A2": 110.0, "C3": 130.81, "D3": 146.83, "E3": 164.81, "F2": 87.31, "G2": 98.0, "C2": 65.41,
        "A3": 220.0, "C4": 261.63, "E4": 329.63, "F3": 174.61, "G3": 196.0, "B3": 246.94, "D4": 293.66}
CHORDS = [("A2", ["A3", "C4", "E4"]), ("F2", ["F3", "A3", "C4"]), ("C2", ["C3", "E3", "G3"]), ("G2", ["G3", "B3", "D4"])]


def music_bed(total, sections):
    """Am-F-C-G at 120 BPM. `sections` maps names to (start, end) seconds: 'drums', 'lift', 'end'."""
    n = int(total * RATE)
    track = np.zeros((n, 2))
    bar = 4 * BEAT
    drums_on = lambda t: sections["drums"][0] <= t < sections["drums"][1]
    lift_on = lambda t: sections["lift"][0] <= t < sections["lift"][1]
    end_at = sections["end"]
    # Pad: soft detuned saw chords, two bars each.
    for i, start in enumerate(np.arange(0, end_at + 2 * bar, 2 * bar)):
        root, notes = CHORDS[i % 4]
        length = int(2 * bar * RATE) + int(0.6 * RATE)
        tt = np.arange(length) / RATE
        pad = np.zeros(length)
        for note in notes:
            for detune in (-0.12, 0.12):
                f = NOTE[note] * 2 ** (detune / 12)
                pad += sum(np.sin(2 * np.pi * f * h * tt) / h ** 1.6 for h in range(1, 7))
        attack = np.clip(tt / 0.5, 0, 1)
        release = np.clip((2 * bar + 0.6 - tt) / 0.6, 0, 1)
        pad *= attack * release * 0.05
        if start >= end_at:
            pad *= np.exp(-tt / 1.2)
        place(track, pad, start, pan=0.0)
        bass_len = int(BEAT * 0.9 * RATE)  # decays to about 2% by the end of the buffer
        for beat in range(8):
            t = start + beat * BEAT
            if t >= end_at or not (drums_on(t) or lift_on(t)):
                continue
            bt = np.arange(bass_len) / RATE
            bass = np.sin(2 * np.pi * NOTE[root] * bt) * np.exp(-bt / 0.11) * 0.26
            place(track, np.tanh(bass * 2) * 0.5, t + (BEAT / 2 if beat % 2 else 0))
    # Arp: eighth notes over the chord, quiet, ping-pong.
    step = BEAT / 2
    for k, t in enumerate(np.arange(0, end_at, step)):
        chord = CHORDS[int(t // (2 * bar)) % 4][1]
        f = NOTE[chord[k % 3]] * 2
        ln = int(0.45 * RATE)
        at = np.arange(ln) / RATE
        pluck = (np.sin(2 * np.pi * f * at) + 0.3 * np.sin(4 * np.pi * f * at)) * np.exp(-at / 0.09)
        place(track, pluck * (0.05 if drums_on(t) or lift_on(t) else 0.03), t, pan=0.5 if k % 2 else -0.5)
    # Drums: kick on beats, hats on off-beats, a clap on 2 and 4 in the lift.
    for k, t in enumerate(np.arange(0, end_at, BEAT)):
        if not (drums_on(t) or lift_on(t)):
            continue
        kick_len = int(0.6 * RATE)
        kt = np.arange(kick_len) / RATE
        kick = np.sin(2 * np.pi * np.cumsum(50 + 90 * np.exp(-kt / 0.03)) / RATE) * np.exp(-kt / 0.12)
        place(track, kick * 0.2, t)
        hat_len = int(0.04 * RATE)
        place(track, bandpass(noise(hat_len), 7000, 15000) * env_exp(hat_len, 0.01) * 0.12, t + BEAT / 2, pan=0.3)
        if lift_on(t) and k % 2:
            clap_len = int(0.15 * RATE)
            place(track, bandpass(noise(clap_len), 900, 5000) * env_exp(clap_len, 0.04) * 0.2, t)
    fade_in = np.clip(np.arange(n) / (1.5 * RATE), 0, 1)[:, None]
    return track * fade_in


def build_audio(segments, total, music, path):
    track = np.zeros((int((total + 2.5) * RATE), 2))
    hits = []
    last_key = -1.0
    for segment in segments:
        for kind, t in segment.cue_times():
            at = segment.start + t
            if kind == "key":
                if at - last_key < 0.05:
                    continue
                last_key = at
            if kind == "hit":
                hits.append(at)
            if kind in SOUNDS:
                place(track, SOUNDS[kind](), at, pan=rng.uniform(-0.15, 0.15))
        if segment.enter in ("whip", "zoom", "wipe"):
            # J-cut: the whoosh starts before the picture moves.
            length = 0.5
            place(track, whoosh_sound(length, up=segment.enter != "zoom"), segment.start - length * 0.62, gain=0.45)
    if hits:
        riser = riser_sound(1.6)
        place(track, riser, hits[0] - 1.6, gain=0.9)
    starts = {segment.shot: segment.start for segment in segments}
    sections = {name: (starts[music[name][0]], starts[music[name][1]]) for name in ("drums", "lift")}
    sections["end"] = starts[music["end"]]
    bed = music_bed(total + 2.5, sections)
    track += bed[:len(track)] * 1.7
    track = track[:int(total * RATE)]
    tail = np.clip((total - np.arange(len(track)) / RATE) / 0.4, 0, 1)[:, None]
    track *= tail
    track /= max(np.abs(track).max(), 1e-9) / 0.7
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes((np.clip(track, -1, 1) * 32767).astype("<i2").tobytes())


TARGET_LUFS, MAX_TRUE_PEAK = -16.0, -1.5


def measure_loudness(path):
    report = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
                            capture_output=True, text=True, check=True).stderr
    summary = report[report.rindex("Summary:"):]
    integrated = float(summary.split("I:")[1].split("LUFS")[0])
    true_peak = float(summary.split("Peak:")[1].split("dBFS")[0])
    return integrated, true_peak


def normalize(source, target):
    """Applies one gain so the mix reaches TARGET_LUFS. The mix itself must leave enough headroom for that;
    no limiter or dynamic normalizer touches the signal."""
    integrated, true_peak = measure_loudness(source)
    gain = TARGET_LUFS - integrated
    if true_peak + gain > MAX_TRUE_PEAK:
        raise RuntimeError(f"Mix too peaky: {integrated:.1f} LUFS, {true_peak:.1f} dBTP; reaching {TARGET_LUFS} LUFS "
                           f"would put the true peak at {true_peak + gain:.1f} dBTP. Lower the loudest effects.")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(source), "-af", f"volume={gain:.3f}dB",
                    "-c:a", "pcm_s24le", str(target)], check=True)
    print(f"  loudness {integrated:.1f} LUFS, true peak {true_peak:.1f} dBTP, gain {gain:+.2f} dB")


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("work", type=Path)
    parser.add_argument("output", type=Path, help="the .mp4 to write, or a directory for --stills")
    parser.add_argument("--stills", help="comma-separated times: write those frames as PNGs instead of a video")
    args = parser.parse_args()
    from edit import EDIT, MUSIC
    segments = [Segment(**spec) for spec in EDIT]
    for segment in segments:
        segment.prepare(args.work / "shots")
    total = layout(segments)
    print(f"{len(segments)} segments, {total:.2f} s")
    for segment in segments:
        print(f"  {segment.start:6.2f}  {segment.length:5.2f}  {segment.shot}")
    if args.stills:
        args.output.mkdir(parents=True, exist_ok=True)
        for t in (float(x) for x in args.stills.split(",")):
            cv2.imwrite(str(args.output / f"t{t:06.2f}.png"), composite(segments, t))
        return
    with tempfile.TemporaryDirectory(dir=args.work) as tmp:
        tmp = Path(tmp)
        build_audio(segments, total, MUSIC, tmp / "mix.wav")
        normalize(tmp / "mix.wav", tmp / "mix-16lufs.wav")
        render_video(segments, total, tmp / "picture.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp / "picture.mp4"), "-i", str(tmp / "mix-16lufs.wav"),
                        "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-ar", str(RATE),
                        "-ac", "2", "-shortest", "-movflags", "+faststart", str(args.output)], check=True)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
