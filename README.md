# Kitab cover generation pipeline

Generates book-cover artwork for the **Practical Gita** insights (Know / Grow / Free yourself) in two steps:

1. **Gemini 3.5 Flash** turns each insight into an image prompt describing four very different front covers.
2. **ElevenLabs** (`gpt-image-2.5-sunburst`) renders those four covers as one 2x2 grid image.

It reproduces the ElevenLabs Flows "Book cover design flow" in code, because ElevenLabs templates that use the Gemini LLM node can't be run through the ElevenLabs API.

```
workbook row (sheet K3 / G3 / F3)
   │  Book facts  = Book title (Reworked title), Pillar, Genre, Target Audience
   │  Synopsis    = the insight body, as is
   ▼
Gemini input  (fixed template + facts + synopsis)        → gemini_input.txt
   ▼  gemini-3.5-flash · thinking minimal
image prompt  (4 covers, plain text, footer under each)  → image_prompt.txt
   ▼  ElevenLabs /v1/flows/image · gpt-image-2.5-sunburst · 2:3 · 1K · high
2x2 cover grid                                            → covers.png
   ▼  upload_covers.py
Supabase storage  RMS-content/gita/draft-covers/K1.png …
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env    # then fill in the keys
```

| Variable | Used for |
|---|---|
| `GEMINI_API_KEY` | Gemini prompt generation |
| `ELEVEN_LABS_API_KEY_ADMIN` | ElevenLabs image generation |
| `APPDEV_SUPABASE_URL`, `APPDEV_SUPABASE_SERVICE_ROLE_KEY` | Uploading covers (App dev project) |

## Input workbook

An `.xlsx` with one sheet per pillar. Sheet name sets the pillar line:

| Sheet | Pillar |
|---|---|
| `K3` | Know yourself |
| `G3` | Grow yourself |
| `F3` | Free yourself |

Required columns (row 1 headers): `#`, `Reworked title`, `Synopsis`, `Genre`, `Target Audience`.

## Usage

```bash
# 1. Image prompts with Gemini (no image cost)
python cover_pipeline.py --xlsx "Title Revamp - with insights.xlsx" --sheet K3 --rows 1-10 \
    --skip-image --normalize-footer --footer-check

# 2. Covers with ElevenLabs, from the saved prompts (Gemini not called again)
python cover_pipeline.py --xlsx "Title Revamp - with insights.xlsx" --sheet K3 --rows 1-10 --image-only

# 3. Upload to Supabase (skips files that already exist)
python upload_covers.py                         # → RMS-content/gita/draft-covers/
python upload_covers.py --prefix gita/covers    # final folder
```

`--rows` accepts `all`, a range `1-10`, or a list `1,4,7`. Run several sheets/rows in parallel as separate processes to go faster; calls are normal synchronous API calls, not batch.

### Output layout

```
books/practical-gita/K3-01/
  facts.txt          Book facts block
  synopsis.txt       insight body
  gemini_input.txt   exact text sent to Gemini
  image_prompt.txt   Gemini output (the image prompt)
  covers.png         2x2 cover grid (1024×1536 at 1K)
```

Uploaded names: `K3-01` → `K1.png`, `G3-12` → `G12.png`, `F3-05` → `F5.png`.

## The Gemini input

Built in `build_gemini_input()`; the fixed parts never change:

```
write down word by word <prompts/designer_prompt.txt>

present all 4 covers in a 2x2 grid



Here is the synopsis: [<insight body>]



and here are facts about the book: [Book title: …

Pillar: Know yourself

Genre: …

Target Audience: …]

Write the four covers as plain text for an image generator, with no markdown, no ASCII art and no intro, and describe the full footer again under every cover.
```

The last line (`PLAIN_LINE`) is our only addition to the ElevenLabs flow. Without it Gemini returns 7–15k characters of markdown and ASCII mock-ups with the footer described inconsistently. With it, output matches the ElevenLabs flow: about 4–5.5k characters, plain text, and the identical footer sentence under every cover.

## Settings

| Step | Setting |
|---|---|
| Gemini | `gemini-3.5-flash`, `thinkingLevel: minimal`, no output-token cap (a cap truncates covers mid-sentence) |
| Image | `gpt-image-2.5-sunburst`, aspect `2:3`, resolution `1K` (`--resolution 2K` / `4K` available), quality `high` |

## Quality checks

| Check | What it does |
|---|---|
| SVG guard | Rejects outputs where Gemini answers with SVG code |
| `--footer-check` | Requires "Practical Gita" and the pillar name, exact casing, in the output; retries otherwise |
| `--normalize-footer` | Forces exact casing of "Practical Gita" and the pillar name |
| Retries | At most 2 retries (3 calls) per insight; best attempt kept, warning printed |
| Input limit | Gemini input must stay under 10,000 characters |

Optional experiments kept as flags: `--design-as-system`, `--no-write-down`, `--no-plain-line`, `--target-length N`.

## Cost (measured)

| Item | Cost |
|---|---|
| ElevenLabs cover grid, 1K | ~268 credits each (~29k for 107 insights) |
| ElevenLabs cover grid, 2K | ~352 credits each |
| Gemini prompt | 1 call per insight on the current settings (107/107 passed first try) |

## Known limitations

- The image model occasionally renders the ribbon text as small caps ("PRACTICAL GITA") or adds stray text on one of the four covers; review covers before publishing.
- `upload_covers.py` never overwrites unless `--overwrite` is passed.
- The ElevenLabs Flows template itself can't be run via API (`model_access_denied` for `gemini-3.5-flash-llm`), which is why this pipeline exists.
