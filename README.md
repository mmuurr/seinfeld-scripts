# Seinfeld Script Archive

Seinfeld scripts in a normalized format (one JSON file per episode) along with the workflow used to generate these data --- ["it's all pipes!"](https://www.youtube.com/watch?v=s09pfBEJYHc).

Quick repo TOC:
- Schema (JSON) for each episode: [episode-script.schema.json]
- Main (Python) script/deps to generate the archive: [tools]
- Additional tool documentation: [dataset-tooling.md]
- Raw source (input) snapshots: [data/raw](data/raw)
- Output: [data/processed/episodes](data/processed/episodes)
- Coverage manifest: [data/processed/manifest.json](data/processed/manifest.json)

## Sources

The generator joins two external sources:

- Hugging Face dataset `yregelman/unprocessed-seinfeld-scripts`
- TVMaze episode metadata for Seinfeld

For reproducibility, local snapshots of both sources are stored in the repo:

- Hugging Face pages under [data/raw/huggingface](data/raw/huggingface)
- TVMaze episode list at [data/raw/tvmaze/episodes.json](data/raw/tvmaze/episodes.json)

The Hugging Face source provides:

- raw script text
- source title
- source air date
- overall episode index

The TVMaze source provides:

- season number
- episode number within season
- a canonical episode list for coverage checks
- a more reliable air date than the Hugging Face source

## Why TVMaze is needed

The Hugging Face source is the better transcript source, but it does not provide a reliable full metadata model for this archive.

In particular, it does not provide:

- season number
- episode number within season
- a canonical episode inventory for checking which episodes are missing

It does have an `episode_num`, but that is just a linear index within the dataset, not enough to derive the full `SxxExx` mapping safely.

## Field provenance

Final dataset fields come from these sources:

- `episode.series_title`: hard-coded literal
- `episode.episode_number_overall`: Hugging Face row order
- `episode.title`: simplified from the Hugging Face source title
- `episode.season`: TVMaze
- `episode.episode_number_in_season`: TVMaze
- `episode.air_date`: TVMaze when a metadata match exists, otherwise Hugging Face fallback
- `episode.written_by`: currently empty because neither joined source was used as a reliable writer source
- `episode.directed_by`: currently empty because neither joined source was used as a reliable director source
- `episode.source_title`: raw Hugging Face title
- `episode.source_dataset`: hard-coded dataset identifier
- `entries[*].text` and `entries[*].raw_text`: derived from the Hugging Face script text after source cleaning and parser normalization
- `entries[*].speaker`, `entries[*].location`, `entries[*].normalized_*`, `entries[*].confidence`, `entries[*].inference_notes`: derived locally by the parser

## Text normalization

Entry generation happens in two stages:

1. Source cleaning:
   - HTML entities are unescaped
   - stray HTML tags and a known `<pre>` extraction artifact are removed
   - line endings are normalized
   - repeated inline whitespace is collapsed
   - blank lines are dropped
2. Structural parsing:
   - cleaned lines are grouped into entry-sized blocks
   - speaker labels are separated from dialogue text
   - dialogue parentheticals may be extracted into `parenthetical`
   - parenthetical scene lines may be converted into `scene_heading` entries
   - some mixed scene/action parentheticals are split into separate `scene_heading` and `action` entries
   - location and speaker helper tokens are normalized into `normalized_location` and `normalized_speaker`

Because of that:

- `entries[*].raw_text` is not the pristine upstream dataset string. It is the cleaned source block that the parser worked from for that entry.
- `entries[*].text` is the parser's normalized presentation of that entry's content.
- `entries[*].raw_text` and `entries[*].text` are often identical for straightforward dialogue, but they can differ when the parser strips wrapper punctuation, extracts structure, or rewrites a source block into a more explicit entry form.

The exact per-entry semantics and edge cases are defined by the parser implementation in `tools/generate_archive.py`; this document only describes the general rules.

## Schema reference

Each episode JSON file has this top-level shape:

```js
{
  "episode": {
    "series_title": "Seinfeld",
    "episode_number_overall": number, // linear position in the generated source corpus
    "title": string, // normalized episode title used in the archive
    "season": number | null, // canonical season number
    "episode_number_in_season": number | null, // canonical episode number within season
    "air_date": "YYYY-MM-DD" | null, // canonical air date used by the generator
    "written_by": string[], // currently empty; reserved for future metadata enrichment
    "directed_by": string[], // currently empty; reserved for future metadata enrichment
    "source_title": string, // raw title from the Hugging Face source row
    "source_dataset": string // dataset identifier for provenance
  },
  "entries": [
    {
      "entry_id": number, // 1-based order within the episode
      "type": "scene_heading" | "action" | "dialogue" | "transition" | "end_marker",
      "text": string, // normalized text for this script entry

      // Optional, depending on entry type / parser inference:
      "raw_text": string, // cleaned source block before entry-level restructuring
      "speaker": string, // present for dialogue entries
      "normalized_speaker": string, // machine-friendly speaker token
      "parenthetical": string, // dialogue qualifier like "into phone" or "whispering"
      "location": string, // extracted scene location for scene headings
      "normalized_location": string, // machine-friendly location token
      "confidence": number, // parser confidence when structure was inferred rather than explicit
      "inference_notes": string[] // short explanations of notable parsing or normalization decisions
    }
  ]
}
```

The `episode` object includes:

- `series_title`: always `Seinfeld`
- `episode_number_overall`: linear position in the generated source corpus
- `title`: normalized episode title
- `season`: season number
- `episode_number_in_season`: episode number within the season
- `air_date`: canonical air date used by the generator
- `written_by`: array, currently empty
- `directed_by`: array, currently empty
- `source_title`: raw title from the Hugging Face source
- `source_dataset`: source dataset identifier

The `entries` array preserves script order. Every entry has:

- `entry_id`: 1-based sequence number within the episode
- `type`: one of `scene_heading`, `action`, `dialogue`, `transition`, `end_marker`
- `text`: normalized entry text

Optional entry fields appear when relevant:

- `raw_text`: cleaned source block for that entry before entry-level restructuring
- `speaker`: speaker label for dialogue
- `normalized_speaker`: normalized speaker token
- `parenthetical`: dialogue parenthetical when one is inferred or preserved
- `location`: extracted location for scene headings
- `normalized_location`: normalized location token
- `confidence`: parser confidence for inferred structure
- `inference_notes`: short notes explaining non-trivial parsing decisions

## Manual corrections log

The TVMaze data appears to over-label two season 4 episodes as parts:

- `The Wallet (1)` was manually corrected to `The Wallet`
- `The Watch (2)` was manually corrected to `The Watch`

These corrections are applied in the generator and recorded in the manifest.

So far, these are the only hand-applied metadata corrections. Other anomalies, such as bad Hugging Face air dates, are documented in the manifest under `source_match_notes` but are not manually rewritten at the source level.

## Notes on source quality

Joining the sources is not entirely mechanical because both sources have issues:

- The Hugging Face dataset contains several bad `air_date` values.
- Some titles differ slightly across sources, for example `The Strong Box` vs `The Strongbox`.
- The Hugging Face source contains 176 rows, while the TVMaze episode list contains 180 rows.
- The missing four episodes are recap / clip-style episodes that do not appear to have full script text in the source corpus.

Examples of problematic source metadata:

- `The Watch` appears in the Hugging Face source with air date `1992-09-23`, but the canonical air date is `1992-09-30`.
- Several other rows also have source air dates that disagree with TVMaze; these are recorded in `source_match_notes` inside the manifest.
- TVMaze appears to label `The Wallet` and `The Watch` as `(1)` and `(2)` respectively; those two titles are corrected by hand during ingest.

Because of that, the generator matches primarily on normalized title and uses air date as supporting evidence rather than the only key.

## Running

Generate the full archive:

```bash
python3 tools/generate_archive.py --refresh-raw
```

Generate a small test batch:

```bash
python3 tools/generate_archive.py --limit 2
```

If the raw snapshots are already present and you do not want to re-fetch them:

```bash
python3 tools/generate_archive.py
```

## TODOs

- Docs are spread across this README and [dataset-tooling.md] ... maybe consolidate?
- There are still many pieces of 'action' text 'dialogue' entries. Improve the parsing to detect these (possibly with confidence). Then, for a dialogue entry like, "foo bar (George gets irritated) baz qux" decide on:
  - Splitting the entry into three entries: `dialogue: "foo bar"`, `action (or maybe "direction"?): George gets irritated`, `dialogue: "baz qux"`
	- Add another 'action'-like prop to dialogue entries and strip the action/direction from the dialogue?
	- Or just ignore ... look through all dialogue parenthetical text blocks and if they are always direction, then they can be removed from `text` (while retaining in `raw_text`).
