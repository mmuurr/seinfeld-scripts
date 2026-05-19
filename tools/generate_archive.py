#!/usr/bin/env python3

import argparse
from datetime import datetime, UTC
import html
import json
import re
import string
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


# Hugging Face dataset identifier (owner/name slug), resolved via ROWS_API with
# the dataset=... query parameter.
SCRIPT_DATASET = "yregelman/unprocessed-seinfeld-scripts"

# TVMaze show id 530 corresponds to Seinfeld; this full URL is the retrieval
# form for the upstream episode metadata.
EPISODES_API = "https://api.tvmaze.com/shows/530/episodes"

ROWS_API = "https://datasets-server.huggingface.co/rows"

PAGE_SIZE = 100

# Keys are TVMaze episode ids from EPISODES_API responses, not season/episode
# numbers or local archive ids.
MANUAL_METADATA_NAME_OVERRIDES = {
    48111: "The Wallet",
    48112: "The Watch",
}


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PROCESSED_DIR = DATA_DIR / "processed"
EPISODES_DIR = PROCESSED_DIR / "episodes"
RAW_DIR = DATA_DIR / "raw"
HF_RAW_DIR = RAW_DIR / "huggingface"
TVMAZE_RAW_DIR = RAW_DIR / "tvmaze"
HF_FETCH_MANIFEST = HF_RAW_DIR / "fetch-manifest.json"
TVMAZE_EPISODES_FILE = TVMAZE_RAW_DIR / "episodes.json"
TVMAZE_FETCH_MANIFEST = TVMAZE_RAW_DIR / "fetch-manifest.json"

TAG_RE = re.compile(r"<[^>]+>")
MULTISPACE_RE = re.compile(r"[ \t]+")
SPEAKER_RE = re.compile(r"^[A-Z][A-Z0-9 '&./-]{1,50}$")
LEADING_PAREN_RE = re.compile(r"^\(([^()]*)\)\s*(.*)$", re.DOTALL)
INLINE_SPEAKER_RE = re.compile(r"^([A-Z][A-Z0-9 '&./-]{1,50})[;:]\s+(.*)$", re.DOTALL)


@dataclass
class EpisodeRow:
    # Hugging Face rows API row_idx, used as a stable per-row source identifier.
    dataset_index: int
    # Source-provided linear episode number from the Hugging Face dataset row.
    episode_num: int
    air_date: str | None
    title: str
    text: str


@dataclass
class MatchResult:
    metadata: dict | None
    reason: str
    notes: list[str]


def fetch_json(url: str) -> dict | list:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True)
        handle.write("\n")


def snapshot_script_rows() -> None:
    HF_RAW_DIR.mkdir(parents=True, exist_ok=True)
    for path in HF_RAW_DIR.glob("rows-offset-*.json"):
        path.unlink()

    rows_fetched = 0
    page_files: list[str] = []
    offset = 0
    while True:
        params = urllib.parse.urlencode(
            {
                "dataset": SCRIPT_DATASET,
                "config": "default",  # Hugging Face dataset config identifier.
                "split": "train",  # Hugging Face split identifier.
                "offset": offset,
                "length": PAGE_SIZE,
            }
        )
        payload = fetch_json(f"{ROWS_API}?{params}")
        path = HF_RAW_DIR / f"rows-offset-{offset:03d}.json"
        write_json(path, payload)
        page_files.append(path.name)
        rows_fetched += len(payload["rows"])
        offset += payload["num_rows_per_page"]
        if offset >= payload["num_rows_total"]:
            break

    write_json(
        HF_FETCH_MANIFEST,
        {
            "dataset": SCRIPT_DATASET,
            "rows_api": ROWS_API,
            "fetched_at_utc": datetime.now(UTC).isoformat(),
            "page_size": PAGE_SIZE,
            "page_files": page_files,
            "row_count": rows_fetched,
        },
    )


def snapshot_episode_metadata() -> None:
    payload = fetch_json(EPISODES_API)
    TVMAZE_RAW_DIR.mkdir(parents=True, exist_ok=True)
    write_json(TVMAZE_EPISODES_FILE, payload)
    write_json(
        TVMAZE_FETCH_MANIFEST,
        {
            "episodes_api": EPISODES_API,
            "fetched_at_utc": datetime.now(UTC).isoformat(),
            "episode_count": len(payload),
            "file": TVMAZE_EPISODES_FILE.name,
        },
    )


def ensure_raw_sources(refresh_raw: bool) -> None:
    if refresh_raw or not HF_FETCH_MANIFEST.exists():
        snapshot_script_rows()
    if refresh_raw or not TVMAZE_EPISODES_FILE.exists():
        snapshot_episode_metadata()


def fetch_script_rows() -> list[EpisodeRow]:
    payload = json.loads(HF_FETCH_MANIFEST.read_text(encoding="utf-8"))
    rows: list[EpisodeRow] = []
    for page_file in payload["page_files"]:
        page_payload = json.loads((HF_RAW_DIR / page_file).read_text(encoding="utf-8"))
        for item in page_payload["rows"]:
            row = item["row"]
            rows.append(
                EpisodeRow(
                    dataset_index=item["row_idx"],
                    episode_num=int(row["episode_num"]),
                    air_date=row.get("air_date"),
                    title=row["title"],
                    text=row["text"],
                )
            )
    return rows


def fetch_episode_metadata() -> list[dict]:
    raw_episodes = json.loads(TVMAZE_EPISODES_FILE.read_text(encoding="utf-8"))
    episodes = []
    for item in raw_episodes:
        if item.get("type") == "insignificant_special":
            continue
        original_name = item["name"]
        corrected_name = MANUAL_METADATA_NAME_OVERRIDES.get(item["id"], original_name)
        episodes.append(
            {
                "id": item["id"],
                "name": corrected_name,
                "season": item["season"],
                "number": item["number"],
                "airdate": item["airdate"],
                "original_name": original_name,
            }
        )
    return episodes


def clean_source_text(raw_text: str) -> str:
    text = html.unescape(raw_text)
    text = text.replace("<bound method Tag.get_text of <pre>", "")
    text = TAG_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [MULTISPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def simplify_title(source_title: str) -> str:
    title = html.unescape(source_title).strip().strip('"').strip()
    title = re.sub(r"\s+Script$", "", title, flags=re.IGNORECASE).strip()
    return title


def normalize_token(value: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return token or "unknown"


def slugify_title(value: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "-", simplify_title(value).lower()).strip("-")
    return token or "untitled"


def canonical_title(value: str) -> str:
    title = simplify_title(value).lower()
    title = title.replace("&", "and")
    title = re.sub(r"\bpart\s+(\d+)\b", r"\1", title)
    title = re.sub(r"\((\d+)\)", r" \1 ", title)
    title = title.translate(str.maketrans("", "", string.punctuation))
    title = re.sub(r"\s+", " ", title).strip()
    return title


def base_title(value: str) -> str:
    title = canonical_title(value)
    title = re.sub(r"\b\d+\b", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def is_scene_heading_text(content: str) -> bool:
    lowered = content.lower()
    action_verbs = {
        "approaches",
        "arrives",
        "comes",
        "enters",
        "exits",
        "gets",
        "goes",
        "hangs",
        "looks",
        "opens",
        "puts",
        "reaches",
        "rises",
        "runs",
        "says",
        "sits",
        "smiles",
        "stands",
        "stares",
        "starts",
        "stops",
        "takes",
        "turns",
        "walks",
        "waves",
    }
    if lowered in {"scene ends", "scene end", "end scene"}:
        return False
    if lowered.startswith("to be continued"):
        return False
    if content and content[0].islower():
        return False
    if "." in content:
        first, _ = content.split(".", 1)
    else:
        first = content
    first = first.strip()
    if not first:
        return False
    first_words = first.lower().split()
    if any(word in action_verbs for word in first_words):
        return False
    if len(first.split()) <= 8:
        return True
    return False


def split_scene_heading(content: str) -> tuple[str | None, str | None]:
    parts = [part.strip() for part in content.split(".", 1)]
    location = parts[0].strip() if parts and parts[0].strip() else None
    action = parts[1].strip() if len(parts) == 2 and parts[1].strip() else None
    return location, action


def is_speaker_line(line: str) -> bool:
    if not SPEAKER_RE.fullmatch(line):
        return False
    if line in {"Written by", "Story by"}:
        return False
    return True


def parse_dialogue_payload(payload: str) -> tuple[str | None, str]:
    match = LEADING_PAREN_RE.match(payload)
    if match:
        parenthetical = match.group(1).strip()
        text = match.group(2).strip()
        if text:
            return parenthetical, text
    return None, payload.strip()


def parse_inline_speaker(payload: str) -> tuple[str, str] | None:
    match = INLINE_SPEAKER_RE.match(payload)
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


def coalesce_lines(lines: list[str]) -> list[str]:
    merged: list[str] = []
    buffer: list[str] = []
    current_kind: str | None = None

    def flush() -> None:
        nonlocal buffer, current_kind
        if buffer:
            merged.append(" ".join(buffer).strip())
        buffer = []
        current_kind = None

    for line in lines:
        if is_speaker_line(line):
            flush()
            merged.append(line)
            continue

        paren_line = line.startswith("(") and line.endswith(")")
        if paren_line:
            flush()
            merged.append(line)
            continue

        kind = "text"
        if current_kind == kind:
            buffer.append(line)
        else:
            flush()
            buffer = [line]
            current_kind = kind

    flush()
    return merged


def strip_leading_metadata(lines: list[str], normalized_title: str) -> list[str]:
    cleaned: list[str] = []
    title_token = normalized_title.upper()
    seen_written_by = False
    for line in lines:
        upper = line.upper().strip('" ')
        if not cleaned and upper == title_token:
            continue
        if upper == "WRITTEN BY":
            seen_written_by = True
            continue
        if seen_written_by and not line.startswith("(") and not is_speaker_line(line):
            seen_written_by = False
            continue
        cleaned.append(line)
    return cleaned


def parse_entries(source_text: str) -> list[dict]:
    cleaned = clean_source_text(source_text)
    lines = [line for line in cleaned.split("\n") if line]
    title_line = simplify_title(lines[0]) if lines else ""
    lines = strip_leading_metadata(lines, title_line)
    blocks = coalesce_lines(lines)

    entries: list[dict] = []
    pending_speaker: str | None = None
    pending_parenthetical: str | None = None
    for block in blocks:
        if block == "THE END":
            entries.append({"type": "end_marker", "text": block, "raw_text": block})
            pending_speaker = None
            pending_parenthetical = None
            continue

        if is_speaker_line(block):
            if pending_speaker and pending_parenthetical:
                entries.append(
                    {
                        "type": "dialogue",
                        "speaker": pending_speaker,
                        "normalized_speaker": normalize_token(pending_speaker),
                        "text": f"({pending_parenthetical})",
                        "raw_text": f"({pending_parenthetical})",
                        "confidence": 0.5,
                        "inference_notes": [
                            "Speaker line was followed only by a parenthetical in the source."
                        ],
                    }
                )
            pending_speaker = block
            pending_parenthetical = None
            continue

        if pending_speaker:
            if block.startswith("(") and block.endswith(")"):
                pending_parenthetical = block[1:-1].strip()
                continue
            parenthetical, text = parse_dialogue_payload(block)
            entry = {
                "type": "dialogue",
                "speaker": pending_speaker,
                "normalized_speaker": normalize_token(pending_speaker),
                "text": text,
                "raw_text": block,
            }
            if pending_parenthetical:
                entry["parenthetical"] = pending_parenthetical
            elif parenthetical:
                entry["parenthetical"] = parenthetical
            entries.append(entry)
            pending_speaker = None
            pending_parenthetical = None
            continue

        inline_speaker = parse_inline_speaker(block)
        if inline_speaker:
            speaker, text = inline_speaker
            parenthetical, text = parse_dialogue_payload(text)
            entry = {
                "type": "dialogue",
                "speaker": speaker,
                "normalized_speaker": normalize_token(speaker),
                "text": text,
                "raw_text": block,
                "confidence": 0.7,
                "inference_notes": [
                    "Speaker label was recovered from an inline malformed source line."
                ],
            }
            if parenthetical:
                entry["parenthetical"] = parenthetical
            entries.append(entry)
            continue

        if block.startswith("(") and block.endswith(")"):
            content = block[1:-1].strip()
            lowered = content.lower()
            if lowered in {"scene ends", "scene end", "end scene"}:
                entries.append({"type": "transition", "text": content, "raw_text": block})
                continue
            if is_scene_heading_text(content):
                location, action = split_scene_heading(content)
                scene_entry = {
                    "type": "scene_heading",
                    "text": f"[Setting: {location or content}]",
                    "raw_text": block,
                    "confidence": 0.9 if location else 0.7,
                }
                if location:
                    scene_entry["location"] = location
                    scene_entry["normalized_location"] = normalize_token(location)
                if action:
                    scene_entry["inference_notes"] = [
                        "Scene heading and opening action were split from a single parenthetical source line."
                    ]
                    entries.append(scene_entry)
                    entries.append(
                        {
                            "type": "action",
                            "text": action,
                            "raw_text": block,
                            "confidence": 0.75,
                        }
                    )
                else:
                    entries.append(scene_entry)
                continue

            entries.append({"type": "action", "text": content, "raw_text": block})
            continue

        entries.append({"type": "action", "text": block, "raw_text": block, "confidence": 0.6})

    if pending_speaker and pending_parenthetical:
        entries.append(
            {
                "type": "dialogue",
                "speaker": pending_speaker,
                "normalized_speaker": normalize_token(pending_speaker),
                "text": f"({pending_parenthetical})",
                "raw_text": f"({pending_parenthetical})",
                "confidence": 0.5,
                "inference_notes": [
                    "Speaker line was followed only by a parenthetical in the source."
                ],
            }
        )

    numbered_entries: list[dict] = []
    for index, entry in enumerate(entries, start=1):
        numbered_entry = {"entry_id": index}
        numbered_entry.update(entry)
        numbered_entries.append(numbered_entry)
    return numbered_entries


def find_unique_match(
    row: EpisodeRow,
    metadata_rows: list[dict],
    *,
    use_base_title: bool,
    restrict_to_air_date: bool,
) -> tuple[dict | None, list[str]]:
    title_fn = base_title if use_base_title else canonical_title
    row_title = title_fn(row.title)
    candidates = metadata_rows
    notes: list[str] = []

    if restrict_to_air_date and row.air_date:
        candidates = [item for item in candidates if item["airdate"] == row.air_date]
        notes.append(f"Restricted metadata candidates to source air date {row.air_date}.")

    matches = [item for item in candidates if title_fn(item["name"]) == row_title]
    if len(matches) == 1:
        return matches[0], notes
    return None, notes


def match_metadata(row: EpisodeRow, metadata_rows: list[dict]) -> MatchResult:
    strategies = [
        ("exact_title", False, False),
        ("base_title", True, False),
        ("exact_title_same_airdate", False, True),
        ("base_title_same_airdate", True, True),
    ]
    for reason, use_base_title, restrict_to_air_date in strategies:
        metadata, notes = find_unique_match(
            row,
            metadata_rows,
            use_base_title=use_base_title,
            restrict_to_air_date=restrict_to_air_date,
        )
        if metadata is not None:
            if row.air_date and metadata["airdate"] != row.air_date:
                notes.append(
                    f"Source air date {row.air_date} disagrees with metadata air date {metadata['airdate']}."
                )
            return MatchResult(metadata=metadata, reason=reason, notes=notes)

    if row.air_date:
        same_date = [item for item in metadata_rows if item["airdate"] == row.air_date]
        if len(same_date) == 1:
            return MatchResult(
                metadata=same_date[0],
                reason="unique_airdate",
                notes=[f"Matched on source air date {row.air_date} after title matching failed."],
            )

    return MatchResult(metadata=None, reason="unmatched", notes=[])


def generate_archive(limit: int | None = None) -> dict:
    script_rows = fetch_script_rows()
    if limit is not None:
        script_rows = script_rows[:limit]
    metadata_rows = fetch_episode_metadata()

    generated = []
    missing_metadata = []
    matched_metadata_ids: set[int] = set()
    source_match_notes = []
    for overall_number, row in enumerate(script_rows, start=1):
        episode_title = simplify_title(row.title)
        match = match_metadata(row, metadata_rows)
        matched_metadata = match.metadata
        if matched_metadata is None:
            missing_metadata.append(episode_title)
        else:
            matched_metadata_ids.add(matched_metadata["id"])
            if match.notes or match.reason not in {"exact_title", "exact_title_same_airdate"}:
                source_match_notes.append(
                    {
                        "source_title": episode_title,
                        "source_air_date": row.air_date,
                        "matched_metadata_title": matched_metadata["name"],
                        "matched_metadata_air_date": matched_metadata["airdate"],
                        "match_reason": match.reason,
                        "notes": match.notes,
                    }
                )
        episode_payload = {
            "episode": {
                "series_title": "Seinfeld",
                "episode_number_overall": overall_number,
                "title": episode_title,
                "season": matched_metadata["season"] if matched_metadata else None,
                "episode_number_in_season": matched_metadata["number"] if matched_metadata else None,
                "air_date": matched_metadata["airdate"] if matched_metadata else row.air_date,
                "written_by": [],
                "directed_by": [],
                "source_title": row.title,
                "source_dataset": SCRIPT_DATASET,
            },
            "entries": parse_entries(row.text),
        }
        generated.append(episode_payload)

    uncovered_metadata_rows = [
        item for item in metadata_rows if item["id"] not in matched_metadata_ids
    ]
    source_base_titles = {base_title(row.title) for row in script_rows}
    missing_from_source = []
    metadata_mismatch_titles = []
    for item in uncovered_metadata_rows:
        if base_title(item["name"]) in source_base_titles:
            metadata_mismatch_titles.append(item["name"])
        else:
            missing_from_source.append(item["name"])
    return {
        "episodes": generated,
        "missing_metadata": sorted(set(missing_metadata)),
        "metadata_episode_count": len(metadata_rows),
        "missing_from_source_titles": sorted(missing_from_source),
        "metadata_mismatch_titles": sorted(metadata_mismatch_titles),
        "source_match_notes": source_match_notes,
        "source_dataset": SCRIPT_DATASET,
        "metadata_source": EPISODES_API,
        "episode_count": len(generated),
        "source_episode_count": len(script_rows),
        "raw_source_files": {
            "huggingface_fetch_manifest": str(HF_FETCH_MANIFEST.relative_to(ROOT)),
            "huggingface_row_pages": [
                str((HF_RAW_DIR / page_file).relative_to(ROOT))
                for page_file in json.loads(HF_FETCH_MANIFEST.read_text(encoding="utf-8"))["page_files"]
            ],
            "tvmaze_fetch_manifest": str(TVMAZE_FETCH_MANIFEST.relative_to(ROOT)),
            "tvmaze_episodes": str(TVMAZE_EPISODES_FILE.relative_to(ROOT)),
        },
        "manual_metadata_corrections": [
            {
                "id": item["id"],
                "season": item["season"],
                "episode_number_in_season": item["number"],
                "original_name": item["original_name"],
                "corrected_name": item["name"],
            }
            for item in metadata_rows
            if item["original_name"] != item["name"]
        ],
    }


def write_archive(payload: dict) -> None:
    EPISODES_DIR.mkdir(parents=True, exist_ok=True)
    for path in EPISODES_DIR.glob("*.json"):
        path.unlink()
    for episode in payload["episodes"]:
        season = episode["episode"]["season"]
        number = episode["episode"]["episode_number_in_season"]
        overall = episode["episode"]["episode_number_overall"]
        title_slug = slugify_title(episode["episode"]["title"])
        if season is not None and number is not None:
            filename = f"{overall:03d}-s{season:02d}e{number:02d}-{title_slug}.json"
        else:
            filename = f"{overall:03d}-{title_slug}.json"
        with (EPISODES_DIR / filename).open("w", encoding="utf-8") as handle:
            json.dump(episode, handle, indent=2, ensure_ascii=True)
            handle.write("\n")

    manifest = {
        "series_title": "Seinfeld",
        "episode_count": payload["episode_count"],
        "source_episode_count": payload["source_episode_count"],
        "metadata_episode_count": payload["metadata_episode_count"],
        "source_dataset": payload["source_dataset"],
        "metadata_source": payload["metadata_source"],
        "raw_source_files": payload["raw_source_files"],
        "manual_metadata_corrections": payload["manual_metadata_corrections"],
        "missing_metadata_titles": payload["missing_metadata"],
        "missing_from_source_titles": payload["missing_from_source_titles"],
        "metadata_mismatch_titles": payload["metadata_mismatch_titles"],
        "source_match_notes": payload["source_match_notes"],
    }
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with (PROCESSED_DIR / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=True)
        handle.write("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--refresh-raw", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_raw_sources(refresh_raw=args.refresh_raw)
    payload = generate_archive(limit=args.limit)
    write_archive(payload)
    print(
        json.dumps(
            {
                "episode_count": payload["episode_count"],
                "missing_metadata_count": len(payload["missing_metadata"]),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
