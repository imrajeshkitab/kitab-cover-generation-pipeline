#!/usr/bin/env python3
"""Practical Gita cover pipeline: Gemini writes the image prompt, ElevenLabs renders the covers.

Mirrors the ElevenLabs "Book cover design flow". Fixed text (prompts/designer_prompt.txt + connector
lines) never changes; only the Book facts and Synopsis change per insight.

  workbook row (sheet K3 / G3 / F3)
        |  Gemini input = "write down word by word" + design prompt, "present all 4 covers in a 2x2 grid",
        |                 "Here is the synopsis: [...]", "and here are facts about the book: [...]", PLAIN_LINE
        v
  gemini-3.5-flash (thinking minimal)               ->  image_prompt.txt  (4 covers, plain text)
        v
  ElevenLabs /v1/flows/image: gpt-image-2.5-sunburst, 2:3, 1K, high  ->  covers.png  (2x2 grid)

Usage:
  # 1. image prompts (Gemini)
  python cover_pipeline.py --xlsx "Title Revamp - with insights.xlsx" --sheet K3 --rows 1-10 --skip-image --normalize-footer --footer-check
  # 2. covers from the saved prompts (ElevenLabs)
  python cover_pipeline.py --xlsx "Title Revamp - with insights.xlsx" --sheet K3 --rows 1-10 --image-only
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path

import openpyxl
import requests

ROOT = Path(__file__).parent
GEMINI_MODEL = "gemini-3.5-flash"
GEMINI_THINKING = "minimal"  # ElevenLabs flow setting; output length left on auto (no maxOutputTokens)
# Image step runs through the ElevenLabs API (same model as the ElevenLabs flow node)
IMAGE_MODEL = "gpt-image-2.5-sunburst"
IMAGE_ASPECT = "2:3"
IMAGE_RESOLUTION = "1K"   # 1K / 2K / 4K
IMAGE_QUALITY = "high"
MAX_GEMINI_CHARS = 10_000
LAST_FINISH = None

# Fixed text from the ElevenLabs flow. Do not edit.
DESIGNER_PROMPT = (ROOT / "prompts" / "designer_prompt.txt").read_text(encoding="utf-8").rstrip("\n")
WRITE_LEAD = "write down word by word"  # opening line of the flow input (single-message mode)
GRID_LINE = "present all 4 covers in a 2x2 grid"
# Added after the facts: makes Gemini's output match the ElevenLabs flow's style (plain text, footer under every cover)
PLAIN_LINE = ("Write the four covers as plain text for an image generator, with no markdown, no ASCII art and no intro, "
              "and describe the full footer again under every cover.")
SYNOPSIS_LEAD = "Here is the synopsis:"
FACTS_LEAD = "and here are facts about the book:"

PILLARS = {"K3": "Know yourself", "G3": "Grow yourself", "F3": "Free yourself"}
OUT_DIR = ROOT / "books" / "practical-gita"


def load_env():
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def build_facts(title: str, pillar: str, genre: str, audience: str) -> str:
    return f"Book title: {title}\n\nPillar: {pillar}\n\nGenre: {genre}\n\nTarget Audience: {audience}"


def build_gemini_input(synopsis: str, facts: str) -> str:
    return (f"{DESIGNER_PROMPT}\n\n{GRID_LINE}\n\n\n\n"
            f"{SYNOPSIS_LEAD} [{synopsis}]\n\n\n\n"
            f"{FACTS_LEAD} [{facts}]")


def footer_score(text: str, pillar: str) -> int:
    """Footer strings present with exact casing: FOOTER_MIN if both are spelled out in plain text (outside ``` blocks),
    else how many times both appear anywhere (e.g. repeated per cover inside the ASCII grid)."""
    prose = re.sub(r"```.*?```", "", text, flags=re.S)
    if min(prose.count("Practical Gita"), prose.count(pillar)) >= 1:
        return FOOTER_MIN
    return min(text.count("Practical Gita"), text.count(pillar))


FOOTER_MIN = 4  # once per cover
MAX_RETRIES = 2
TARGET_LEN = 5500   # target output length in characters (with spaces)
LEN_TOL = 0.20      # accept within +/-20% of the target
LENGTH_LINE = "Keep your whole answer to about {n:,} characters including spaces."


def build_user_part(synopsis: str, facts: str) -> str:
    """Everything after the design prompt (used when the design prompt goes in the system instruction)."""
    return (f"{GRID_LINE}\n\n\n\n{SYNOPSIS_LEAD} [{synopsis}]\n\n\n\n{FACTS_LEAD} [{facts}]")


def normalize_footer(text: str, pillar: str) -> str:
    """Case-only fix: force the two footer strings to the exact casing used in the facts."""
    text = re.sub(r"(?i)practical gita", "Practical Gita", text)
    return re.sub(r"(?i)" + re.escape(pillar), pillar, text)


def gemini(prompt: str, system: str | None = None) -> str:
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"thinkingConfig": {"thinkingLevel": GEMINI_THINKING}}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    for attempt in range(1, 4):  # retry timeouts / 429 / 5xx
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
                headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                json=body,
                timeout=300,
            )
        except requests.exceptions.RequestException as e:
            print(f"Gemini request error ({type(e).__name__}), attempt {attempt}/3", file=sys.stderr)
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            print(f"Gemini {r.status_code}, attempt {attempt}/3", file=sys.stderr)
            time.sleep(5 * attempt)
            continue
        break
    else:
        sys.exit("Gemini: 3 attempts failed")
    if not r.ok:
        sys.exit(f"Gemini {r.status_code}: {r.text[:500]}")
    global LAST_FINISH
    cand0 = r.json()["candidates"][0]
    LAST_FINISH = cand0.get("finishReason")
    parts = cand0.get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts).strip()


def generate_image(prompt: str, resolution: str = IMAGE_RESOLUTION) -> bytes:
    """Generate one image via ElevenLabs (POST /v1/flows/image, then poll until done)."""
    h = {"xi-api-key": os.environ["ELEVEN_LABS_API_KEY_ADMIN"]}
    body = {"model_id": IMAGE_MODEL, "prompt": prompt, "aspect_ratio": IMAGE_ASPECT,
            "resolution": resolution, "quality": IMAGE_QUALITY}
    r = requests.post("https://api.elevenlabs.io/v1/flows/image", headers=h, json=body, timeout=60)
    if not r.ok:
        sys.exit(f"ElevenLabs image {r.status_code}: {r.text[:500]}")
    gid, t0 = r.json()["id"], time.time()
    while True:
        g = requests.get(f"https://api.elevenlabs.io/v1/flows/image/{gid}", headers=h, timeout=60).json()
        if g.get("status") == "completed":
            return requests.get(g["content_url"], timeout=120).content
        if g.get("status") == "failed":
            sys.exit(f"ElevenLabs image {gid} failed: {g.get('failure_reason')} {g.get('error_message')}")
        if time.time() - t0 > 900:
            sys.exit(f"ElevenLabs image {gid} timed out")
        time.sleep(5)


def make_image(out: Path, image_prompt: str, resolution: str = IMAGE_RESOLUTION):
    t = time.time()
    (out / "covers.png").write_bytes(generate_image(image_prompt, resolution))
    print(f"{out.name}: covers.png ({IMAGE_MODEL}, {IMAGE_ASPECT}, {resolution}) in {time.time() - t:.0f}s")


def run_one(out: Path, facts: str, synopsis: str, skip_image: bool, system_mode: bool = False, norm: bool = False,
            footer_check: bool = False, write_down: bool = True, target_len: int | None = None,
            plain_line: bool = True):
    out.mkdir(parents=True, exist_ok=True)
    (out / "facts.txt").write_text(facts, encoding="utf-8")
    (out / "synopsis.txt").write_text(synopsis, encoding="utf-8")
    gemini_input = build_gemini_input(synopsis, facts)
    if write_down and not system_mode:
        gemini_input = f"{WRITE_LEAD} {gemini_input}"
    if plain_line:
        gemini_input = f"{gemini_input}\n\n{PLAIN_LINE}"
    length_line = LENGTH_LINE.format(n=target_len) if target_len else ""
    if length_line:
        gemini_input = f"{gemini_input}\n\n{length_line}"
    if len(gemini_input) >= MAX_GEMINI_CHARS:
        sys.exit(f"{out.name}: Gemini input is {len(gemini_input)} chars, limit {MAX_GEMINI_CHARS}")
    (out / "gemini_input.txt").write_text(gemini_input, encoding="utf-8")

    pillar = re.search(r"^Pillar:\s*(.+)$", facts, re.M).group(1).strip()
    max_attempts = 1 + MAX_RETRIES  # first try plus at most 2 retries
    best, best_score, best_rank = None, -1, None
    for attempt in range(1, max_attempts + 1):
        if system_mode:
            cand = gemini(build_user_part(synopsis, facts) + (f"\n\n{PLAIN_LINE}" if plain_line else "")
                          + (f"\n\n{length_line}" if length_line else ""), system=DESIGNER_PROMPT)
        else:
            cand = gemini(gemini_input)
        if norm or footer_check:
            cand = normalize_footer(cand, pillar)
        if "<svg" in cand:  # guard: Gemini occasionally answers with SVG code instead of a text prompt
            print(f"{out.name}: attempt {attempt} returned SVG code, retrying", file=sys.stderr)
            continue
        score = footer_score(cand, pillar)
        footer_ok = score >= FOOTER_MIN
        len_ok = (not target_len) or abs(len(cand) - target_len) <= LEN_TOL * target_len
        rank = (footer_ok or not footer_check, len_ok, -abs(len(cand) - (target_len or len(cand))))
        if best is None or rank > best_rank:
            best, best_score, best_rank = cand, score, rank
        if (footer_ok or not footer_check) and len_ok:
            break
        why = []
        if footer_check and not footer_ok: why.append(f"footer {score}/{FOOTER_MIN}")
        if not len_ok: why.append(f"length {len(cand):,} (target {target_len:,})")
        print(f"{out.name}: attempt {attempt} not accepted ({', '.join(why)}), retrying", file=sys.stderr)
    if best is None:
        sys.exit(f"{out.name}: Gemini kept returning SVG code")
    image_prompt = best
    if footer_check and best_score < FOOTER_MIN:
        print(f"{out.name}: WARNING footer check not met after {max_attempts} attempts (best {best_score}/{FOOTER_MIN})",
              file=sys.stderr)
    if target_len and abs(len(best) - target_len) > LEN_TOL * target_len:
        print(f"{out.name}: WARNING length {len(best):,} outside +/-{LEN_TOL:.0%} of {target_len:,}", file=sys.stderr)
    (out / "image_prompt.txt").write_text(image_prompt, encoding="utf-8")
    print(f"{out.name}: input {len(gemini_input)} chars -> image prompt {len(image_prompt)} chars | "
          f"{attempt} Gemini call(s), {attempt - 1} retr{'y' if attempt == 2 else 'ies'}"
          + (f" | footer {best_score}/{FOOTER_MIN}" if footer_check else "") + f" | finish {LAST_FINISH}")
    if skip_image:
        return
    make_image(out, image_prompt)


def parse_rows(spec: str, available: list[int]) -> list[int]:
    if spec == "all":
        return available
    rows = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        rows += range(int(a), int(b or a) + 1)
    return [r for r in rows if r in available]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", help="workbook with K3/G3/F3 sheets")
    ap.add_argument("--sheet", choices=list(PILLARS))
    ap.add_argument("--rows", default="all", help='insight numbers, e.g. "1-3,7" or "all"')
    ap.add_argument("--dir", help="folder with facts.txt and synopsis.txt")
    ap.add_argument("--skip-image", action="store_true")
    ap.add_argument("--image-only", action="store_true",
                    help="skip Gemini; generate covers.png from each folder's existing image_prompt.txt")
    ap.add_argument("--resolution", default=IMAGE_RESOLUTION, choices=["1K", "2K", "4K"])
    ap.add_argument("--design-as-system", action="store_true",
                    help="send the design prompt as Gemini's system instruction instead of inside the user message")
    ap.add_argument("--write-down", action=argparse.BooleanOptionalAction, default=True,
                    help='single-message mode: start the input with "write down word by word" like the ElevenLabs node '
                         '(default on; ignored with --design-as-system)')
    ap.add_argument("--target-length", type=int, default=None,
                    help="ask for ~N characters (with spaces) and retry if the output is outside +/-20%% of N")
    ap.add_argument("--plain-line", action=argparse.BooleanOptionalAction, default=True,
                    help="add the plain-text/footer-per-cover line after the facts (default on)")
    ap.add_argument("--footer-check", action="store_true",
                    help="retry until 'Practical Gita' and the pillar name are each written out in plain text once per cover")
    ap.add_argument("--normalize-footer", action="store_true",
                    help="force exact casing of 'Practical Gita' and the pillar name in the output")
    args = ap.parse_args()
    load_env()

    if args.dir:
        d = Path(args.dir)
        run_one(d, (d / "facts.txt").read_text(encoding="utf-8").strip(),
                (d / "synopsis.txt").read_text(encoding="utf-8").strip(), args.skip_image,
                args.design_as_system, args.normalize_footer, args.footer_check, args.write_down, args.target_length, args.plain_line)
        return

    if not (args.xlsx and args.sheet):
        sys.exit("pass --dir, or --xlsx with --sheet")
    if args.image_only:
        ws = openpyxl.load_workbook(args.xlsx)[args.sheet]
        avail = sorted(int(r[0]) for r in ws.iter_rows(min_row=2, values_only=True) if r[0])
        for n in parse_rows(args.rows, avail):
            out = OUT_DIR / f"{args.sheet}-{n:02d}"
            make_image(out, (out / "image_prompt.txt").read_text(encoding="utf-8"), args.resolution)
        return
    ws = openpyxl.load_workbook(args.xlsx)[args.sheet]
    hdr = {c.value: i for i, c in enumerate(ws[1])}
    data = {int(r[0]): r for r in ws.iter_rows(min_row=2, values_only=True) if r[0]}
    for n in parse_rows(args.rows, sorted(data)):
        r = data[n]
        facts = build_facts(r[hdr["Reworked title"]], PILLARS[args.sheet],
                            r[hdr["Genre"]], r[hdr["Target Audience"]])
        run_one(OUT_DIR / f"{args.sheet}-{n:02d}", facts, r[hdr["Synopsis"]].strip(), args.skip_image,
                args.design_as_system, args.normalize_footer, args.footer_check, args.write_down, args.target_length, args.plain_line)


if __name__ == "__main__":
    main()
