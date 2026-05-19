# Dataset Tooling

## External dependencies

The generator uses only Python standard library modules locally, but it depends on two external HTTP APIs:

- Hugging Face dataset server `rows` API:
  `https://datasets-server.huggingface.co/rows`
- TVMaze episode metadata API:
  `https://api.tvmaze.com/shows/530/episodes`

The fetched responses are stored locally so future runs can rebuild from a frozen copy of the upstream data.

## What the Hugging Face `rows` API is

The `rows` API is a hosted JSON endpoint provided by Hugging Face's dataset server. It exposes dataset contents row-by-row without needing to clone a repo, download parquet manually, or scrape the dataset viewer HTML.

For this project it is queried with:

```text
https://datasets-server.huggingface.co/rows?dataset=yregelman/unprocessed-seinfeld-scripts&config=default&split=train&offset=0&length=100
```

That returns paginated JSON containing:

- dataset rows
- row indices
- total row count
- page size metadata

In practice, this is just a convenient read API over the hosted dataset.

For this repo, the raw paginated responses are saved under [data/raw/huggingface](../data/raw/huggingface).

## What each source provides

- Hugging Face dataset:
  raw script text, source title, and source air date
- TVMaze:
  season/episode numbering, canonical air dates, and a canonical episode list for coverage checks

The Hugging Face source is the important one for parsing, because it preserves scene/action/dialogue structure much better than the flattened CSVs already in this repo.

## What comes from where

- Transcript structure comes from Hugging Face raw text.
- `season` and `episode_number_in_season` come from TVMaze.
- `air_date` comes from TVMaze when a metadata match exists, because the Hugging Face source has several bad dates.
- `title` currently comes from the Hugging Face source title after light normalization.
- Manual title corrections are applied to two TVMaze rows before matching:
  `The Wallet (1)` -> `The Wallet`
  `The Watch (2)` -> `The Watch`

## High-level algorithm

1. Fetch all script rows from the Hugging Face `rows` API in pages of 100.
2. Fetch the full Seinfeld episode list from TVMaze.
3. Match each source row to TVMaze metadata using normalized titles, with air date as supporting evidence rather than the only key.
4. Clean the raw script text by removing HTML tags and whitespace noise.
5. Parse the cleaned text into ordered entries:
   - `scene_heading`
   - `action`
   - `dialogue`
   - `transition`
   - `end_marker`
6. Write one JSON file per episode plus a manifest summarizing coverage and match anomalies.

## Text normalization and entry shaping

The parser does more than simple cleanup. At a high level it:

- unescapes HTML entities
- removes HTML tags and a known source extraction artifact
- normalizes line endings and collapses repeated inline whitespace
- drops blank lines and leading title/writer boilerplate
- groups cleaned lines into larger entry blocks
- recognizes speaker labels and separates them from dialogue payloads
- extracts some dialogue parentheticals into structured fields
- converts some parenthetical source lines into explicit `scene_heading` entries
- splits some combined scene/action parentheticals into separate `scene_heading` and `action` entries

This means episode entries preserve two related but different text views:

- `text`: the parser-normalized entry content
- `raw_text`: the cleaned source block the parser used for that entry

`raw_text` is therefore not always the untouched upstream dataset text, and `raw_text` and `text` may diverge when structure is inferred or rewritten into a clearer archive form.

The exact per-entry semantics and edge cases are intentionally left to the parser implementation in `tools/generate_archive.py`; this document only captures the general normalization rules.

## Why the manifest matters

The source data is not perfectly consistent. The manifest is there to show:

- metadata rows that have no corresponding source script
- source rows that matched metadata with caveats
- source-vs-metadata disagreements, such as mismatched air dates

One concrete example is `The Watch`: the source row exists, but its source air date disagrees with TVMaze, so the manifest records that as a match anomaly rather than treating it as a totally missing episode.

So far, the only hand-corrected metadata values are the two TVMaze titles above. Other mismatches are logged, not silently rewritten.

## Running the generator

```bash
python3 tools/generate_archive.py --refresh-raw
```

For a smaller test pass:

```bash
python3 tools/generate_archive.py --limit 2
```

If you want to rebuild from the already-snapshotted raw files without refetching:

```bash
python3 tools/generate_archive.py
```
