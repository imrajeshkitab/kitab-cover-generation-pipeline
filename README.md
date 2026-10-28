# Kitab cover generation pipeline

Generates cover artwork for Kitab content (book summaries, Practical Gita insights, journeys, bites).
Content is read from **App Prod** (Supabase, read-only); every artifact is saved **locally** with a
self-describing name. Nothing is published or written back to App Prod by this pipeline.

```
1. SOURCE     App Prod (read-only)  summaries + chapters · journey_steps · journeys · bytes
                 │  normalised to one Item: type, source_id, lang, title, body, fields
2. NODES      content → facts → synopsis → prompt → cover → split → export
                 │      (each node reads its section of a versioned recipe)
3. OUTPUT     outputs/<type>/<source_id>__<slug>/<lang>/<gen_id>/<type>__<slug>__<source_id>__<lang>__<gen_id>__<stage>.<ext>
              + outputs/index.sqlite (one row per generation) + outputs/batches.jsonl (one line per run, with credits)
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env      # GEMINI_API_KEY, ELEVEN_LABS_API_KEY_ADMIN
supabase login            # App Prod is read through the Supabase Management API with this login
```

App Prod's database host is IPv6-only, so direct Postgres connections time out on most networks; the pipeline
uses the Management API over HTTPS with `read_only: true` on every query.

## Commands

```bash
python -m ccl items summary --search chatter                 # browse content (source_id, title, author, ...)
python -m ccl run summary --ids 48b88d565dd40b745de573db      # full pipeline for one or more items
python -m ccl run summary --ids SUM-440,SUM-759 --until prompt   # stop after the image prompt (Gemini only, no image cost)
python -m ccl run summary --ids SUM-440 --start-at cover --from latest   # reuse earlier stages, redo cover onwards
python -m ccl run summary --ids SUM-440 --until prompt --set prompt.design=design/generic/v2   # try another prompt version
python -m ccl run gita_insight --ids K1,G12,F5               # aliases or step ids; --ids all for all 107
python -m ccl status --type summary                          # generations from the index
```

`--workers N` runs items in parallel (default 4). All calls are normal API calls, not batch.

## Nodes

| Stage | Does | Output file(s) |
|---|---|---|
| `content` | Snapshot of the exact text and fields fetched from App Prod | `__content.json` |
| `facts` | Facts block from a versioned template (fields from App Prod / overrides) | `__facts.txt` |
| `synopsis` | `passthrough`, or condense to `min_chars`–`max_chars` with a versioned prompt (`auto` = only if longer than `max_chars`) | `__synopsis.txt` |
| `prompt` | Assemble design prompt + synopsis + facts, Gemini writes the 4-cover image prompt (SVG guard, optional footer check) | `__gemini-input.txt`, `__prompt.txt` |
| `cover` | ElevenLabs renders the 2x2 grid | `__grid.png` |
| `split` | `mode = "crop"`: cuts the grid into 4 covers locally (inner gutters trimmed, no model call). `mode = "model"`: ElevenLabs redraws each cell from the grid (~495 credits per cell) | `__cover-1.png` … `__cover-4.png` |
| `export` | Centre-crops each cover to exactly 2:3 and writes WebP at native size plus each `sizes` entry | `__cover-1.webp`, `__cover-1-800x1200.webp`, `__cover-1-400x600.webp` … |

Each generation folder also has `__meta.json`: recipe snapshot, prompt versions, models and settings,
attempts, finish reason, token usage, ElevenLabs generation ids, timings and the parent generation (when stages were reused).

## Identity and naming

| Part | Meaning |
|---|---|
| `type` | `summary`, `gita_insight`, `journey`, `bite` |
| `source_id` | Language-independent id: `summaries.source_id`, `bytes.source_id`, `journeys.id`, `journey_steps.id` (Gita, English only today) |
| `lang` | Language of the content (`en` for now) |
| `gen_id` | `YYYYMMDDTHHMMSSZ-xxxx` (UTC time + 4 random hex): unique per pipeline pass, sortable by time |
| `slug` | From the title, for humans only; identity is `source_id` |

Re-running from a later stage (`--start-at … --from …`) creates a **new** `gen_id`; the reused files are copied
in under the new id and `meta.json` records `parent_gen_id` and `reused_from`.

## Prompts and recipes (versioned, never edited in place)

```
prompts/design/generic/v1.txt          book design prompt (title, author, tagline) - baseline for summaries
prompts/design/gita_insight/v1.txt     Practical Gita design prompt (ribbon + pillar) - baseline for Gita
prompts/assembly/{footer,no_footer}/v1.txt   how the Gemini input is put together
prompts/synopsis/v1.txt (+ v1_retry)   condense long text to a synopsis
prompts/facts_block/<type>/v1.txt      layout of the facts block
prompts/facts_gen/v1.txt               optional: generate facts with Gemini
prompts/split/v1.txt                   crop one cell of the grid
recipes/<type>/v1.toml                 which prompt versions + model settings each node uses
overrides/gita_insight.csv             reworked titles, genre, audience (not in App Prod), keyed by step id
```

`v1` = the prompts in use on 2026-10-28 (frozen baseline). To experiment, add `vN+1` files and either a new
recipe (`recipes/summary/v2.toml`, run with `--recipe v2`) or a one-off `--set section.key=value`.
The Gita v1 recipe rebuilds the exact Gemini input that produced the first Gita covers (checked byte-for-byte).

Summary recipes: `v1` original generic prompt; `v2`-`v6` cut-paper prompt iterations (`design/cutpaper/v1`-`v5`, matching the
Kitab thumbnail spec's look); `v7` = `cutpaper/v5` + short title (`facts_block/summary/v3`, text before any colon) + local crop.
Measured ElevenLabs cost per book: grid 1K ~268, 2K ~352, 4K ~736 credits; split by model ~495 per cell.

Baseline settings: Gemini `gemini-3.5-flash` (thinking minimal), cover `gpt-image-2.5-sunburst` 2:3 1K high,
split `gpt-image-2` 2:3 1K medium, export WebP q90.

## Content types

| Type | Source | Recipe |
|---|---|---|
| `summary` | `summaries` + `summary_chapters` (chapter title + text) | `recipes/summary/v7.toml` (cut-paper style, current); `v1` = original baseline |
| `gita_insight` | `journey_steps` of "Gita In A Day", Key Insight steps only (107) + `overrides/gita_insight.csv` | `recipes/gita_insight/v1.toml` |
| `journey` | `journey_translations` overview + step outline | fetch only, no recipe yet |
| `bite` | `bytes` | fetch only, no recipe yet |

## Legacy scripts

`cover_pipeline.py` + `upload_covers.py` are the original workbook-driven Gita scripts (kept unchanged for
reference; they read `Title Revamp - with insights.xlsx` and write to `books/`).
