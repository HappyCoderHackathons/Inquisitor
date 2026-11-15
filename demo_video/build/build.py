# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "pillow", "soundfile"]
# ///
"""Build the demo video: picture from takes and cards, cues from VO durations, then the mix.

uv run build/build.py            # picture + cues + mix -> out/inquisitor-demo.mp4
uv run build/build.py --mix-only # re-mix after swapping VO WAVs with the same durations
"""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TAKES, VO, ASSETS, OUT = ROOT / "takes", ROOT / "vo", ROOT / "assets", ROOT / "out"
TMP = HERE / "tmp"
W, H, FPS, RATE = 1920, 1080, 30, 48000
LIMIT = 180.0
BG, LIME, PURPLE, LAVENDER, TEXT, MUTED = "#09070D", "#A3E635", "#7C3AED", "#C4B5FD", "#F4F2F7", "#A59FB3"
FONTS = Path("C:/Windows/Fonts")
MUSIC = ASSETS / "music" / "plains_of_luminescence.ogg"
MUSIC_CREDIT = 'Music: "Plains of Luminescence" by vitalezzz (CC0), OpenGameArt'

TEAM = [
    ("austin", "Austin Sternberg", "ML service and integration"),
    ("ian", "Ian Rohrbacher", "Windows agent (Rust)"),
    ("conner", "Conner Brunner", "Website, games, dashboard"),
    ("colin", "Colin Grant", "Backend data model and API"),
]

# Each scene: footage segments (file, in, out) or a card, its VO cue ids, and an optional caption chip.
SYNTH = "Synthetic windows sent through the agent API"
SCENES = [
    {"name": "title", "card": "title", "vo": ["c01_title"], "min": 3.5},
    {"name": "team", "card": "team", "vo": ["c02_austin", "c03_ian", "c04_conner", "c05_colin"]},
    {"name": "landing", "clips": [("01_landing.webm", 0.3, 14.2)], "vo": ["c06_problem"]},
    {"name": "privacy", "clips": [("02_privacy.webm", 0.3, 12.6)], "vo": ["c07_privacy"],
     "caption": "Only aggregate timing leaves the PC"},
    {"name": "agent_setup", "agent": True, "vo": ["c08_agent"], "caption": "Windows agent 0.7.2 setup",
     "clips": [("agent_01_terms.mp4", 0.3, 6.8), ("agent_02_location.mp4", 0.5, 3.5), ("agent_03_ready.mp4", 0.5, 4.6)],
     "min": 13.6},
    {"name": "signup", "clips": [("03_signup.webm", 2.5, 7.0), ("04_pair.webm", 3.0, 6.5)], "vo": ["c09_signup"], "min": 8,
     "caption": "Demo account on a local test server"},
    {"name": "agent_pair", "vo": ["c10_pair"], "caption": "Agent: Connect to your account",
     "clips": [("agent_04_pair.mp4", 0.2, 4.6, True), ("04_pair.webm", 9.5, 18.0)], "min": 9},
    {"name": "game", "clips": [("05_game.webm", 3.2, 20.4)], "vo": ["c11_games"], "caption": "Word Sprint calibration round"},
    {"name": "train", "clips": [("06_train.webm", 0.6, 15.4)], "vo": ["c12_rounds", "c13_train"],
     "caption": "Remaining rounds sped up for the recording"},
    {"name": "dashboard", "clips": [("07_dashboard.webm", 0.3, 21.8)], "vo": ["c14_monitor"], "caption": SYNTH},
    {"name": "demo_switch", "clips": [("08_demo_switch.webm", 5.0, 23.6)], "vo": ["c15_switch"], "caption": SYNTH},
    {"name": "alert", "clips": [("09_alert.webm", 0.3, 9.5)], "vo": ["c16_alert"]},
    {"name": "gemini", "clips": [("11_gemini.webm", 1.8, 10.4)], "vo": ["c16b_gemini"],
     "caption": "Suggestions from Gemini on aggregate scores only"},
    {"name": "devices", "clips": [("10_devices.webm", 0.3, 6.0)], "vo": ["c17_devices"]},
    {"name": "end", "card": "end", "vo": ["c18_end"], "min": 6.0},
]
LEAD, GAP, TAIL, BREATHE = 0.4, 0.4, 0.7, 1.2


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / ("segoeuib.ttf" if bold else "segoeui.ttf")), size)


def run(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout)


def background() -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    glow = Image.new("RGB", (W, H), BG)
    ImageDraw.Draw(glow).ellipse((1100, -500, 2300, 500), fill="#1E1530")
    return Image.blend(img, glow.filter(ImageFilter.GaussianBlur(160)), 1.0)


def avatar(slug: str, name: str, size: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    photo = next((p for p in (ASSETS / "team").glob(f"{slug}.*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}), None)
    if photo:
        img = Image.open(photo).convert("RGB")
        side = min(img.size)
        img = img.crop(((img.width - side) // 2, (img.height - side) // 2, (img.width + side) // 2, (img.height + side) // 2))
        img = img.resize((size, size), Image.LANCZOS)
    else:
        img = Image.new("RGB", (size, size), "#211B2A")
        draw = ImageDraw.Draw(img)
        initials = "".join(part[0] for part in name.split())
        draw.text((size / 2, size / 2), initials, font=font(size // 3, True), fill=LAVENDER, anchor="mm")
    ring = Image.new("RGBA", (size + 12, size + 12), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse((0, 0, size + 11, size + 11), outline=LIME, width=4)
    ring.paste(img, (6, 6), mask)
    return ring


def card(kind: str) -> Path:
    img = background()
    draw = ImageDraw.Draw(img)
    logo = Image.open(ASSETS / "logo.png").convert("RGBA")
    if kind == "title":
        mark = logo.resize((220, 220), Image.LANCZOS)
        img.paste(mark, (W // 2 - 110, 250), mark)
        draw.text((W / 2, 560), "Inquisitor", font=font(120, True), fill=TEXT, anchor="mm")
        draw.text((W / 2, 680), "Timing summaries, not typed content.", font=font(46), fill=LIME, anchor="mm")
        draw.text((W / 2, 760), "Continuous, consent-based behavioral signals for Windows", font=font(32), fill=MUTED, anchor="mm")
    elif kind == "team":
        draw.text((W / 2, 170), "The team", font=font(72, True), fill=TEXT, anchor="mm")
        for i, (slug, name, role) in enumerate(TEAM):
            x = 240 + i * 480
            face = avatar(slug, name, 300)
            img.paste(face, (x - face.width // 2, 300), face)
            draw.text((x, 690), name, font=font(40, True), fill=TEXT, anchor="mm")
            draw.text((x, 745), role, font=font(28), fill=LAVENDER, anchor="mm")
    else:
        mark = logo.resize((150, 150), Image.LANCZOS)
        img.paste(mark, (W // 2 - 75, 150), mark)
        draw.text((W / 2, 380), "Inquisitor", font=font(96, True), fill=TEXT, anchor="mm")
        draw.text((W / 2, 470), "github.com/hack-the-nest-but-happier/Inquisitor", font=font(36), fill=LIME, anchor="mm")
        names = "   ·   ".join(name for _, name, _ in TEAM)
        draw.text((W / 2, 590), names, font=font(34), fill=TEXT, anchor="mm")
        draw.text((W / 2, 700), "Research prototype. Scores are anomaly signals, not proof of identity.", font=font(30), fill=MUTED, anchor="mm")
        draw.text((W / 2, 750), "Monitoring footage uses synthetic windows on a local test server.", font=font(30), fill=MUTED, anchor="mm")
        draw.text((W / 2, 900), MUSIC_CREDIT, font=font(26), fill=MUTED, anchor="mm")
    path = TMP / f"card_{kind}.png"
    img.save(path)
    return path


def caption(text: str, name: str) -> Path:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    f = font(28, True)
    width = draw.textlength(text, font=f)
    box = (40, H - 84, 40 + width + 60, H - 30)
    mid = (box[1] + box[3]) / 2
    draw.rounded_rectangle(box, radius=14, fill=(16, 13, 20, 240), outline=PURPLE, width=3)
    draw.ellipse((box[0] + 20, mid - 6, box[0] + 32, mid + 6), fill=LIME)
    draw.text((box[0] + 44, mid), text, font=f, fill=TEXT, anchor="lm")
    path = TMP / f"caption_{name}.png"
    img.save(path)
    return path


def layout(durations: dict[str, float]) -> tuple[list[dict], list[dict]]:
    cues, t = [], 0.0
    for scene in SCENES:
        scene["start"] = t
        cursor = t + LEAD
        for cue in scene["vo"]:
            cues.append({"id": cue, "start": round(cursor, 3), "duration": durations[cue]})
            cursor += durations[cue] + GAP
        needed = cursor - GAP + TAIL - t
        footage = sum(c[2] - c[1] for c in scene.get("clips", []))
        scene["duration"] = round(max(needed, scene.get("min", 0), min(footage, needed + BREATHE)), 3)
        t += scene["duration"]
    return SCENES, cues


def render_scene(i: int, scene: dict) -> Path:
    out = TMP / f"scene_{i:02d}.mp4"
    d = scene["duration"]
    norm = f"fps={FPS},format=yuv420p,setsar=1"
    if "card" in scene:
        run(["-loop", "1", "-t", d, "-i", card(scene["card"]), "-vf", norm, "-c:v", "libx264", "-crf", "16", out])
        return out
    inputs, filters, labels = [], [], []
    for j, clip in enumerate(scene["clips"]):
        name, start, end = clip[:3]
        inputs += ["-ss", start, "-to", end, "-i", TAKES / name]
        if scene.get("agent") or (len(clip) > 3 and clip[3]):
            filters.append(
                f"[{j}:v]fps={FPS},scale='min(1500/iw,min(900/ih,1.5))*iw':-2:flags=lanczos,"
                f"pad=iw+8:ih+8:4:4:color=#393243[w{j}];color=c={BG}:s={W}x{H}:r={FPS}[bg{j}];"
                f"[bg{j}][w{j}]overlay=(W-w)/2:(H-h)/2-20:shortest=1,setsar=1[v{j}]"
            )
        else:
            filters.append(f"[{j}:v]fps={FPS},scale={W}:{H},setsar=1[v{j}]")
        labels.append(f"[v{j}]")
    filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0,tpad=stop_mode=clone:stop_duration={d},"
                   f"trim=duration={d},setpts=PTS-STARTPTS[base]")
    last = "[base]"
    if text := scene.get("caption"):
        inputs += ["-loop", "1", "-i", caption(text, scene["name"])]
        n = len(scene["clips"])
        filters.append(f"[{n}:v]format=rgba,fade=in:st=0.6:d=0.4:alpha=1,fade=out:st={d - 0.8}:d=0.4:alpha=1[cap];"
                       f"{last}[cap]overlay=0:0:shortest=1[capd]")
        last = "[capd]"
    filters.append(f"{last}format=yuv420p[out]")
    run([*inputs, "-filter_complex", ";".join(filters), "-map", "[out]", "-t", d, "-r", FPS,
         "-c:v", "libx264", "-crf", "16", "-preset", "medium", out])
    return out


def picture(scenes: list[dict]) -> Path:
    parts = [render_scene(i, s) for i, s in enumerate(scenes)]
    listing = TMP / "concat.txt"
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
    total = sum(s["duration"] for s in scenes)
    out = OUT / "picture.mp4"
    run(["-f", "concat", "-safe", "0", "-i", listing,
         "-vf", f"fade=in:st=0:d=0.5,fade=out:st={total - 0.8}:d=0.8", "-c:v", "libx264", "-crf", "16", out])
    return out


def resample(x: np.ndarray, rate: int) -> np.ndarray:
    if rate == RATE:
        return x
    t = np.arange(int(len(x) * RATE / rate)) * rate / RATE
    return np.stack([np.interp(t, np.arange(len(x)), x[:, c]) for c in range(x.shape[1])], axis=1).astype("float32")


def mix(cues: list[dict], total: float) -> Path:
    n = int(total * RATE)
    music, rate = sf.read(MUSIC, dtype="float32", always_2d=True)
    music = resample(music, rate)
    music = np.tile(music, (int(np.ceil(n / len(music))), 1))[:n] if len(music) < n else music[:n].copy()
    voice = np.zeros((n, 2), dtype="float32")
    gain = np.full(n, 10 ** (-14 / 20), dtype="float32")
    ramp = int(0.3 * RATE)

    def rms(x: np.ndarray) -> float:
        return float(np.sqrt(np.mean(np.square(x)) + 1e-12))

    for cue in cues:
        line, line_rate = sf.read(VO / f"{cue['id']}.wav", dtype="float32", always_2d=True)
        line = resample(line, line_rate)
        line = line[:, :1].repeat(2, axis=1) * (10 ** (-16 / 20) / max(rms(line), 1e-6))
        a = int(cue["start"] * RATE)
        b = min(a + len(line), n)
        voice[a:b] += line[: b - a]
        # Duck the bed to about 18 dB under this line, measured on the music it sits on.
        target = 10 ** (-18 / 20) * rms(line) / max(rms(music[a:b]), 1e-6)
        lo, hi = max(a - ramp, 0), min(b + ramp, n)
        env = np.full(hi - lo, min(target, gain[lo:hi].min()), dtype="float32")
        env[: a - lo] = np.linspace(gain[lo], env[0], a - lo)
        env[b - lo:] = np.linspace(env[0], 10 ** (-14 / 20), hi - b)
        gain[lo:hi] = np.minimum(gain[lo:hi], env)
    fade = int(2.5 * RATE)
    gain[-fade:] *= np.linspace(1, 0, fade)
    gain[: int(1.5 * RATE)] *= np.linspace(0, 1, int(1.5 * RATE))
    mixed = music * gain[:, None] + voice
    path = TMP / "mix.wav"
    sf.write(path, np.clip(mixed, -1, 1), RATE)
    return path


def main() -> None:
    TMP.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(exist_ok=True)
    durations = {p.stem: round(sf.info(p).duration, 3) for p in VO.glob("c*.wav")}
    scenes, cues = layout(durations)
    total = round(sum(s["duration"] for s in scenes), 3)
    print(f"total {total:.1f}s")
    if total > LIMIT:
        raise SystemExit(f"over the {LIMIT:.0f}s limit by {total - LIMIT:.1f}s; tighten BREATHE or the VO")
    (ROOT / "cues.json").write_text(json.dumps({"total": total, "scenes": [
        {"name": s["name"], "start": round(s["start"], 3), "duration": s["duration"]} for s in scenes], "cues": cues},
        indent=1))
    pic = OUT / "picture.mp4"
    if "--mix-only" not in sys.argv or not pic.exists():
        pic = picture(scenes)
    audio = mix(cues, total)
    run(["-i", pic, "-i", audio, "-map", "0:v", "-map", "1:a", "-c:v", "copy",
         "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k", "-ar", RATE, "-shortest",
         OUT / "inquisitor-demo.mp4"])
    print("wrote", OUT / "inquisitor-demo.mp4")


if __name__ == "__main__":
    main()
