"""Render docs/demo.gif (animated terminal demo) and docs/social-preview.png.

Needs Pillow (dev only, not a runtime dependency):  python scripts/make_media.py
The terminal text is real output from this repo's CLI and hook, captured at render time.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from guardrails import cli  # noqa: E402

DOCS = ROOT / "docs"
BG, FG, DIM = (13, 17, 23), (230, 237, 243), (125, 133, 144)
GREEN, RED, YELLOW, CYAN, MAGENTA = (63, 185, 80), (248, 81, 73), (210, 153, 34), (86, 182, 194), (188, 140, 255)
W, H, PAD, LINE = 1000, 560, 28, 24
BADGES = {"DENY": RED, "ASK": YELLOW, "ALLOW": GREEN, "PASS": DIM}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = (["consolab.ttf", "DejaVuSansMono-Bold.ttf"] if bold else ["consola.ttf", "DejaVuSansMono.ttf"])
    for name in names:
        for base in (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")):
            if (base / name).is_file():
                return ImageFont.truetype(str(base / name), size)
    return ImageFont.load_default()


MONO, MONO_B = font(17), font(17, True)


def capture(*argv: str) -> list[str]:
    buf = io.StringIO()
    cli._COLOR = False
    with redirect_stdout(buf):
        cli.main(list(argv))
    return buf.getvalue().rstrip("\n").splitlines()


def color_for(line: str) -> tuple:
    s = line.strip()
    if s.startswith("$"):
        return FG
    if s.startswith("✓") or "on a leash" in s or s.startswith("ALLOW"):
        return GREEN
    if s.startswith("DENY") or s.startswith("✗") or "Blocked" in s:
        return RED
    if s.startswith("ASK") or s.startswith("•"):
        return YELLOW
    if s.startswith("rule:") or s.startswith("Then") or s.startswith("Try"):
        return DIM
    if s.startswith("⏺"):
        return MAGENTA
    return FG


def frame(lines: list[str], cursor: bool) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, W - 1, 36), radius=0, fill=(22, 27, 34))
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse((16 + i * 22, 12, 28 + i * 22, 24), fill=c)
    d.text((W // 2 - 110, 9), "claude-code-guardrails", font=MONO, fill=DIM)
    visible = lines[-((H - 60) // LINE):]
    y = 50
    for ln in visible:
        word = ln.split(" ", 1)[0]
        if ln.startswith("$ "):
            d.text((PAD, y), "$", font=MONO_B, fill=CYAN)
            d.text((PAD + 20, y), ln[2:], font=MONO_B, fill=FG)
        elif word in BADGES:
            col = BADGES[word]
            label = f" {word} "
            bw = int(d.textlength(label, font=MONO_B))
            d.rounded_rectangle((PAD, y - 2, PAD + bw, y + LINE - 4), radius=4, fill=col)
            d.text((PAD, y), label, font=MONO_B, fill=BG)
            d.text((PAD + bw + 10, y), ln[len(word) + 1:].strip(), font=MONO, fill=FG)
        elif ln.startswith(("✓", "✗", "•")):
            col = {"✓": GREEN, "✗": RED, "•": YELLOW}[ln[0]]
            d.text((PAD, y), {"✓": "√", "✗": "x", "•": "·"}[ln[0]], font=MONO_B, fill=col)
            d.text((PAD + 20, y), ln[1:].strip(), font=MONO, fill=FG if ln[0] == "✓" else col)
        else:
            d.text((PAD, y), ln.replace("\t", "  "), font=MONO, fill=color_for(ln))
        y += LINE
    if cursor and visible:
        last = visible[-1]
        x = PAD + int(d.textlength(last, font=MONO_B if last.startswith("$ ") else MONO)) + 2
        d.rectangle((x, y - LINE + 3, x + 9, y - 4), fill=FG)
    return img


def build_script(tmp: Path) -> list[tuple[str, list[str]]]:
    os.environ["CLAUDE_PROJECT_DIR"] = str(tmp)
    init = capture("init", "--preset", "production", "--dir", str(tmp))
    explain_env = capture("explain", "Read", json.dumps({"file_path": "~/.ssh/id_ed25519"}), "--dir", str(tmp))
    explain_tf = capture("explain", "Bash", json.dumps({"command": "terraform destroy"}), "--dir", str(tmp))
    explain_get = capture("explain", "mcp__crm__execute_api", json.dumps({"endpoint": "GET /v1/deals"}), "--dir", str(tmp))
    explain_post = capture("explain", "mcp__crm__execute_api", json.dumps({"endpoint": "POST /v1/refunds"}), "--dir", str(tmp))
    doctor = capture("doctor", "--dir", str(tmp))
    clean = lambda out: [ln for ln in out if ln.strip() and not ln.startswith(("Try", "Then"))]  # noqa: E731
    strip_json = lambda out: [ln.strip() for ln in out[1:]]  # noqa: E731  (drop the echoed call line)
    return [
        ("claude-guardrails init --preset production", clean(init)),
        ("claude-guardrails explain Read '{\"file_path\": \"~/.ssh/id_ed25519\"}'", strip_json(explain_env)),
        ("claude-guardrails explain Bash '{\"command\": \"terraform destroy\"}'", strip_json(explain_tf)),
        ("claude-guardrails explain mcp__crm__execute_api '{\"endpoint\": \"GET /v1/deals\"}'", strip_json(explain_get)),
        ("claude-guardrails explain mcp__crm__execute_api '{\"endpoint\": \"POST /v1/refunds\"}'", strip_json(explain_post)),
        ("claude-guardrails doctor", [ln for ln in doctor if ln.strip()]),
    ]


def make_gif(script: list[tuple[str, list[str]]]) -> None:
    frames, durations, screen = [], [], []
    for cmd, output in script:
        typed = ""
        for i in range(0, len(cmd), 3):
            typed = cmd[: i + 3]
            frames.append(frame(screen + ["$ " + typed], True))
            durations.append(45)
        screen.append("$ " + cmd)
        frames.append(frame(screen, False))
        durations.append(350)
        for ln in output:
            screen.append(ln)
            frames.append(frame(screen, False))
            durations.append(90)
        durations[-1] = 1500
        screen.append("")
    durations[-1] = 4000
    DOCS.mkdir(exist_ok=True)
    pal = [f.convert("P", palette=Image.ADAPTIVE, colors=32) for f in frames]
    pal[0].save(DOCS / "demo.gif", save_all=True, append_images=pal[1:], duration=durations, loop=0, optimize=True)


def make_social() -> None:
    img = Image.new("RGB", (1280, 640), BG)
    d = ImageDraw.Draw(img)
    for x in range(0, 1280, 40):
        d.line((x, 0, x, 640), fill=(18, 23, 30))
    for y in range(0, 640, 40):
        d.line((0, y, 1280, y), fill=(18, 23, 30))
    d.text((80, 110), "claude-code-guardrails", font=font(64, True), fill=FG)
    d.text((80, 200), "Put your AI agent on a leash.", font=font(36), fill=CYAN)
    rows = [
        ("DENY ", RED, "Read ~/.ssh/id_ed25519", "protected path"),
        ("DENY ", RED, "Bash terraform destroy", "infra goes through your pipeline"),
        ("ASK  ", YELLOW, "POST /v1/refunds", "live write needs a human"),
        ("ALLOW", GREEN, "GET /v1/deals", "reads flow freely"),
    ]
    y = 300
    for badge, col, call, why in rows:
        d.rounded_rectangle((80, y, 190, y + 40), radius=6, fill=col)
        d.text((96, y + 7), badge, font=font(24, True), fill=BG)
        d.text((215, y + 6), call, font=font(26, True), fill=FG)
        d.text((640, y + 8), why, font=font(22), fill=DIM)
        y += 58
    d.text((80, 560), "One Python file  ·  zero dependencies  ·  pip install claude-code-guardrails", font=font(22), fill=DIM)
    img.save(DOCS / "social-preview.png", optimize=True)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        script = build_script(Path(tmp))
    make_gif(script)
    make_social()
    print("wrote", DOCS / "demo.gif", f"{(DOCS / 'demo.gif').stat().st_size // 1024} KB")
    print("wrote", DOCS / "social-preview.png")


if __name__ == "__main__":
    main()
