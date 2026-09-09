#!/usr/bin/env python3
"""
sort2own.py — turn a MakeMKV rip (DVD or Blu-ray) into a Jellyfin-ready copy
you actually own.

Part of a "buy it once at the flea market, keep it forever" film library —
sorting the disc's main feature and extras is the last step between a
secondhand disc and a self-hosted collection that no subscription can take
away.

WHAT IT DOES
  Takes a directory of *.mkv files produced by MakeMKV, works out which one is
  the main feature, and places every file into a folder structure Jellyfin can
  parse without mistaking the extras for extra copies of the film:

      <library>/Title (Year)/
      ├── Title (Year).mkv                       main feature
      ├── Title (Year) - Director's Cut.mkv      an alternative cut ("version")
      ├── trailers/…  featurettes/…  extras/…    extras, typed by folder name
      └── .sort2own.json                         manifest of what was done

  TV discs are supported too (--tv): titles become
      <library>/Show (Year)/Season 01/Show (Year) S01E03.mkv

NON-DESTRUCTIVE BY DESIGN
  * The source directory is never modified. Files are HARDLINKED into place
    (zero extra disk space, instant). Hardlinks only work when source and
    library are on the SAME filesystem; if they are not, the script warns and
    asks before falling back to a copy (unattended mode requires an explicit
    --copy or --move). Only --move relocates files, and even then nothing is
    deleted.
  * Existing destination files are never overwritten; a numeric suffix is
    added instead.
  * Titles classed as "skip" simply stay where they are.
  * Re-running on the same rip is a no-op: anything an earlier run already
    placed here is skipped, so nothing is duplicated (--force overrides).
  * --dry-run prints the plan and touches nothing.

TWO WAYS TO RUN IT
  Interactive (default when run in a terminal):
      sort2own.py /path/to/rip
  Unattended (for scripts, ARM post-processing hooks, cron):
      sort2own.py /path/to/rip --yes --name "A Film (2024)"

  In unattended mode the heuristics decide everything. In the TUI you see the
  heuristics' proposal and can override any title with a few keystrokes.

CONFIGURATION
  Anything you would otherwise repeat on every run — the library path, the
  thresholds, Jellyfin credentials — can live in a config file:
      ~/.config/sort2own/config.toml   (or --config, or $SORT2OWN_CONFIG)
  A command-line flag beats an environment variable, which beats the config
  file, which beats the built-in default.

DEPENDENCIES
  Python 3.11+ (for tomllib) and `ffprobe` (part of ffmpeg). Nothing else.
  Deliberately one file: copy it to the NAS or into an ARM container and run
  it — there is nothing to install.

HOW THE HEURISTICS WORK  (see classify())
  1. Every title's duration, size, chapter count and embedded title tag are
     read with ffprobe.
  2. Movie mode: the main feature is the longest title, unless --runtime is
     given, in which case it is the title whose length is closest to the
     stated runtime (protects against "play all extras" titles and against
     picking the wrong cut).
  3. Titles matching another title's length to within a couple of seconds and
     its size to within a percent are the same content reached by a second
     playlist (multi-angle / language variants) and are skipped.
  4. Titles almost as long as the main feature (>= --version-ratio of it) are
     alternative cuts and become Jellyfin "versions".
  5. Titles shorter than --min-extra are menu loops / logos and are skipped.
  6. Everything else is an extra. Its name comes from the MKV title tag if
     MakeMKV preserved one, else from the duration, so files stay
     distinguishable ("Extra 12m40s.mkv").
  7. TV mode: titles whose length clusters around the median (episodes) are
     numbered in disc order; the rest are treated as above.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from collections import Counter

try:
    import hints_ofdb
except Exception:        # noqa: BLE001 — a missing or broken sidecar must not
    hints_ofdb = None    # stop a disc being sorted; hints are advisory only
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Folder names Jellyfin recognises for extras. The folder decides the type;
# the file name inside it is free. "extras" is the generic, untyped bucket.
# https://jellyfin.org/docs/general/server/media/movies/#extras
EXTRA_TYPES = [
    "extras",            # generic, shown simply as "Extras"
    "trailers",
    "featurettes",
    "behind the scenes",
    "deleted scenes",
    "interviews",
    "scenes",
    "shorts",
    "clips",
    "samples",
    "other",
]

# Classification labels a title can end up with.
MAIN, VERSION, EXTRA, EPISODE, SKIP = "main", "version", "extra", "episode", "skip"

# What a disc calls its extras, mapped to the folder Jellyfin wants. German and
# English both, because the library is both. Matched case-insensitively as
# substrings, so "Deutscher Trailer" and "Making of ..." both land.
EXTRA_KEYWORDS = [
    ("trailers", ["trailer", "teaser", "tv spot", "kinospot", "vorschau"]),
    ("interviews", ["interview", "gespräch", "im gespräch", "q&a"]),
    ("behind the scenes", ["behind the scenes", "hinter den kulissen",
                           "am set", "b-roll"]),
    ("featurettes", ["making of", "making-of", "featurette", "dokumentation",
                     "entstehung", "die story", "reality-check"]),
    ("deleted scenes", ["deleted", "entfallene szene", "geschnittene szene",
                        "alternate ending", "alternatives ende"]),
    ("scenes", ["musikvideo", "music video", "soundtrack-video",
                "soundtrack video"]),
    ("other", ["outtakes", "pannen", "bloopers", "versprecher"]),
]

# An audio track named like this means the whole title is a commentary track
# over the film, which Jellyfin models as a version rather than an extra.
COMMENTARY_WORDS = ["commentary", "kommentar", "audiokommentar"]

MANIFEST_NAME = ".sort2own.json"

# Sampled-hash geometry: four 1 MiB blocks, evenly spread.
SAMPLE_BLOCK = 1 << 20
SAMPLE_POINTS = 4


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Title:
    """One ripped .mkv file plus what we have decided to do with it."""
    path: Path
    duration: float          # seconds
    size: int                # bytes
    chapters: int
    tag: str                 # title tag embedded by MakeMKV (may be "")
    # Everything the disc said about this title, for type_extra() to read.
    audio_titles: List[str] = field(default_factory=list)
    audio_langs: List[str] = field(default_factory=list)
    chapter_titles: List[str] = field(default_factory=list)
    streams: "Counter[str]" = field(default_factory=Counter)  # codec_type → count
    kind: str = EXTRA        # MAIN / VERSION / EXTRA / EPISODE / SKIP
    extra_type: str = "extras"   # which EXTRA_TYPES folder, when kind == EXTRA
    label: str = ""          # version name / extra file name / episode number
    note: str = ""           # why the heuristic chose this (shown in TUI)

    @property
    def hms(self) -> str:
        s = int(self.duration)
        return f"{s // 3600:d}:{(s % 3600) // 60:02d}:{s % 60:02d}"

    @property
    def gib(self) -> str:
        return f"{self.size / 2**30:.2f}G"


@dataclass
class Outcome:
    """What a run actually did, so the caller can react to it."""
    placed: int = 0
    failures: int = 0


@dataclass
class Plan:
    """Everything the TUI edits and the executor consumes."""
    name: str                # "Title (Year)" — folder and main file name
    library: Path
    tv: bool = False
    season: int = 1
    titles: List[Title] = field(default_factory=list)
    provider_id: str = ""    # "tmdbid-12345" / "imdbid-tt1234567", or ""
    supplement: bool = False # a further disc: the feature is already placed
    main_duration: float = 0.0   # that feature's length, for judging versions
    start_episode: int = 1   # first episode number this disc contributes
    disc_label: str = ""     # free text for the manifest, e.g. "Disc 2"

    @property
    def folder(self) -> str:
        """
        The folder name. A provider ID pins the film for Jellyfin so a remake
        or a same-title film cannot be matched instead. It is kept out of
        `name` so tag hygiene and the year regex keep working on the bare
        title.
        """
        base = safe_name(self.name)
        return f"{base} [{self.provider_id}]" if self.provider_id else base


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------

def ffprobe(path: Path) -> Title:
    """
    Read everything the disc has to say about one title in a single call.

    The `stream=` selector pulls the streams in on its own — no `-show_streams`
    is needed alongside it — so the audio track names and languages that let
    type_extra() tell a commentary from a featurette cost nothing extra.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries",
        "format=duration:format_tags=title"
        ":stream=codec_type:stream_tags=title,language"
        ":chapter_tags=title",
        "-show_chapters",
        "-of", "json", str(path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        data = json.loads(out)
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError) as e:
        sys.exit(f"ffprobe failed on {path}: {e}")

    fmt = data.get("format", {})
    chapters = data.get("chapters", [])
    streams = data.get("streams", [])
    audio = [s for s in streams if s.get("codec_type") == "audio"]

    def tags(entry: dict) -> dict:
        return entry.get("tags") or {}

    return Title(
        path=path,
        duration=float(fmt.get("duration", 0) or 0),
        size=path.stat().st_size,
        chapters=len(chapters),
        tag=tags(fmt).get("title", "").strip(),
        audio_titles=[t for t in (tags(s).get("title", "").strip() for s in audio) if t],
        audio_langs=[l for l in (tags(s).get("language", "").strip() for s in audio) if l],
        chapter_titles=[t for t in (tags(c).get("title", "").strip() for c in chapters) if t],
        streams=Counter(s.get("codec_type", "?") for s in streams),
    )


def scan(src: Path) -> List[Title]:
    """Probe every .mkv in the source directory, in MakeMKV's t00, t01… order."""
    files = sorted(p for p in src.iterdir() if p.suffix.lower() == ".mkv")
    if not files:
        sys.exit(f"No .mkv files found in {src}")
    return [ffprobe(p) for p in files]


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------

def safe_name(s: str) -> str:
    """Strip characters that are illegal or awkward in file names."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s).strip(" .")


def useful_tag(t: Title, plan: "Plan") -> str:
    """
    The MKV title tag, unless it is just the film's own name (MakeMKV writes
    the disc label into every title on some discs, which is useless for
    telling extras apart). Returns "" when unhelpful.
    """
    tag = t.tag.strip()
    if not tag:
        return ""
    film = re.sub(r"\s*\(\d{4}\)\s*$", "", plan.name).strip().lower()
    if tag.lower() in (film, plan.name.lower()) or tag.lower().replace("_", " ") == film:
        return ""
    return tag


def duration_label(t: Title) -> str:
    """Fallback name for an extra when the disc gave us no title tag."""
    m, s = divmod(int(t.duration), 60)
    return f"Extra {m}m{s:02d}s"


def guess_name_from_source(src: Path) -> str:
    """
    MakeMKV names its output folder after the disc label ("A_FILM")
    or after the first file ("A Film_t00.mkv"). Turn either into
    something usable as a starting point; the user confirms it in the TUI.
    """
    base = src.name
    if base.lower() in ("", ".", "makemkv", "output", "rips"):
        # Uninformative folder name → derive from a file name instead.
        base = re.sub(r"_t\d+$", "", next(src.glob("*.mkv")).stem)
    base = base.replace("_", " ").strip()
    # ALL CAPS disc labels → Title Case
    if base.isupper():
        base = base.title()
    return base


def match_keyword(text: str) -> Optional[str]:
    """The extras folder a piece of disc text names, if any."""
    lowered = text.lower()
    for folder, words in EXTRA_KEYWORDS:
        if any(word in lowered for word in words):
            return folder
    return None


def is_commentary(t: Title) -> bool:
    return any(word in title.lower()
               for title in t.audio_titles for word in COMMENTARY_WORDS)


def type_extra(t: Title, plan: Plan,
               main: Optional[Title]) -> Tuple[Optional[str], bool, str]:
    """
    Work out what kind of extra a title is from what the disc says about it.

    Returns (folder, confident, why). `confident` is the whole point: a
    keyword the disc itself wrote is worth acting on, while a guess from
    shape alone is only worth showing the user. A mistyped extra still
    plays, but silently overriding a real label would be worse.
    """
    named = match_keyword(useful_tag(t, plan))
    if named:
        return named, True, f"disc calls it “{useful_tag(t, plan)}”"

    for chapter in t.chapter_titles:
        named = match_keyword(chapter)
        if named:
            return named, True, f"chapter “{chapter}”"

    # Shape alone: something short with one video, one audio and no subtitles
    # is usually a trailer. Not certain enough to apply on its own.
    if (t.duration <= 180 and t.streams.get("video", 0) == 1
            and t.streams.get("audio", 0) == 1
            and not t.streams.get("subtitle", 0)):
        return "trailers", False, "short, one audio track, no subtitles"

    # A lone extra in a language the feature does not have is often an
    # interview with an international guest. Weak on its own — never applied.
    if main and t.audio_langs and main.audio_langs \
            and not set(t.audio_langs) & set(main.audio_langs):
        return "interviews", False, f"only {'/'.join(sorted(set(t.audio_langs)))} audio"

    return None, False, ""


def classify(plan: Plan, runtime_min: Optional[int], min_extra: int,
             version_ratio: float, dup_tolerance: float,
             dup_seconds: float = 2.0) -> None:
    """
    Fill in kind / extra_type / label / note for every title.
    Runs on the raw scan; the TUI lets the user override afterwards.
    """
    ts = plan.titles
    for t in ts:                      # reset, so the function is idempotent
        t.kind, t.label, t.note = EXTRA, "", ""

    # --- 3. duplicates: the same content exposed by two playlists ------------
    # Length is matched in absolute seconds, not as a fraction: a duplicate is
    # the same frames, so its duration is identical to well under a second,
    # while a percentage window grows with the runtime. At 1% a 45-minute
    # episode matched anything within 27 seconds, which quietly swallowed the
    # other episodes on a TV disc. Getting this wrong in the loose direction
    # destroys a real title silently; too tight merely leaves a visible extra
    # copy, so it errs tight.
    for i, a in enumerate(ts):
        for b in ts[:i]:
            if b.kind == SKIP:
                continue
            same_length = abs(a.duration - b.duration) <= dup_seconds
            same_size = abs(a.size - b.size) <= dup_tolerance * max(b.size, 1)
            if same_length and same_size:
                a.kind, a.note = SKIP, f"duplicate of {b.path.name}"
                break

    live = [t for t in ts if t.kind != SKIP]

    if plan.tv:
        _classify_tv(plan, live, min_extra)
    else:
        _classify_movie(plan, live, runtime_min, min_extra, version_ratio)

    # --- 6. type the extras from what the disc says --------------------------
    main = next((t for t in ts if t.kind == MAIN), None)
    for t in ts:
        if t.kind != EXTRA:
            continue
        folder, confident, why = type_extra(t, plan, main)
        if not folder:
            continue
        if confident:
            t.extra_type, t.note = folder, why
        else:
            # Shown in the TUI, where `e` cycles the type; not applied, because
            # a guess from shape alone is not worth overriding "extras" for.
            t.note = f"maybe {folder}: {why}"

    # --- 7. name the extras --------------------------------------------------
    seen: set = set()
    for t in ts:
        if t.kind != EXTRA:
            continue
        base = safe_name(useful_tag(t, plan)) or duration_label(t)
        label, n = base, 2
        while label.lower() in seen:          # keep names unique
            label, n = f"{base} ({n})", n + 1
        seen.add(label.lower())
        t.label = label


def _classify_movie(plan: Plan, live: List[Title], runtime_min: Optional[int],
                    min_extra: int, version_ratio: float) -> None:
    if not live:
        return

    if plan.supplement:
        # A further disc carries no feature of its own; anything long enough
        # to be an alternate cut is judged against the one already placed.
        main, reference = None, plan.main_duration
    else:
        # --- 2. pick the main feature -----------------------------------------
        if runtime_min:
            target = runtime_min * 60
            main = min(live, key=lambda t: abs(t.duration - target))
            main.note = f"closest to --runtime {runtime_min} min"
        else:
            main = max(live, key=lambda t: t.duration)
            main.note = "longest title"
        main.kind = MAIN
        reference = main.duration

    for t in live:
        if t is main:
            continue
        # --- 4. alternative cuts --------------------------------------------
        if reference and t.duration >= version_ratio * reference:
            t.kind = VERSION
            if is_commentary(t):
                # The film again with a commentary track over it. Jellyfin has
                # no commentary type, and a version is what this actually is.
                t.label, t.note = "Commentary", "audio track named as a commentary"
                continue
            t.label = safe_name(useful_tag(t, plan)) or f"Alternate cut {int(t.duration // 60)}min"
            t.note = f"≥{int(version_ratio*100)}% of main length → version"
        # --- 5. junk -----------------------------------------------------------
        elif t.duration < min_extra:
            t.kind, t.note = SKIP, f"shorter than {min_extra}s"
        else:
            t.note = "extra"


def _classify_tv(plan: Plan, live: List[Title], min_extra: int) -> None:
    """
    Episodes on a disc are all roughly the same length. Take the median
    duration of the longer titles and call anything within ±25% of it an
    episode, numbered in disc order.
    """
    candidates = [t for t in live if t.duration >= min_extra]
    if not candidates:
        return
    med = median(t.duration for t in candidates)
    ep = plan.start_episode
    for t in live:
        if t.duration < min_extra:
            t.kind, t.note = SKIP, f"shorter than {min_extra}s"
        elif abs(t.duration - med) <= 0.25 * med:
            t.kind, t.label, t.note = EPISODE, str(ep), f"≈ median episode length ({int(med//60)} min)"
            ep += 1
        else:
            t.note = "extra (length unlike the episodes)"

    # Box-set discs often carry a "play all" title: every episode end to end.
    # Placed, it would give Jellyfin a second copy of the whole disc.
    episodes = [t for t in live if t.kind == EPISODE]
    if len(episodes) > 1:
        total = sum(t.duration for t in episodes)
        for t in live:
            if t.kind != EPISODE and abs(t.duration - total) <= 0.02 * total:
                t.kind, t.note = SKIP, "play-all (every episode end to end)"


# ---------------------------------------------------------------------------
# Destination naming
# ---------------------------------------------------------------------------

def library_root(plan: Plan) -> Path:
    """The film's / show's folder. May not exist yet."""
    return plan.library / plan.folder


def destination(plan: Plan, t: Title) -> Optional[Path]:
    """Where a title will land, or None if it is skipped."""
    root = library_root(plan)
    # Main and version file names must start with the *exact* folder name,
    # provider ID included, or Jellyfin reads them as separate movies.
    if t.kind == MAIN:
        return root / f"{plan.folder}.mkv"
    if t.kind == VERSION:
        # "Name (Year) - Label.mkv" next to the main file = Jellyfin version
        return root / f"{plan.folder} - {safe_name(t.label) or 'Alternate cut'}.mkv"
    if t.kind == EPISODE:
        # Episodes are matched on SxxEyy, so they keep the bare show name.
        ep = int(t.label or 0)
        return root / f"Season {plan.season:02d}" / \
            f"{safe_name(plan.name)} S{plan.season:02d}E{ep:02d}.mkv"
    if t.kind == EXTRA:
        return root / t.extra_type / f"{safe_name(t.label) or duration_label(t)}.mkv"
    return None


def next_episode(plan: Plan) -> int:
    """
    The first free episode number in this season.

    Read from the file names already in the season folder rather than from the
    manifest, so episodes put there by hand, or by some earlier tool, are
    counted too — the folder is the truth about what Jellyfin will see.
    """
    season_dir = library_root(plan) / f"Season {plan.season:02d}"
    if not season_dir.is_dir():
        return 1
    numbering = re.compile(rf"S{plan.season:02d}E(\d+)", re.IGNORECASE)
    highest = 0
    for episode in season_dir.glob("*.mkv"):
        found = numbering.search(episode.name)
        if found:
            highest = max(highest, int(found.group(1)))
    return highest + 1


def unique(path: Path) -> Path:
    """Never overwrite: 'x.mkv' → 'x (2).mkv' → 'x (3).mkv' …"""
    if not path.exists():
        return path
    n = 2
    while True:
        cand = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not cand.exists():
            return cand
        n += 1


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def read_manifest(folder: Path) -> Tuple[List[dict], bool]:
    """
    (runs, readable) for one folder's manifest.

    Missing is the normal first-run case: readable and empty. Unreadable is
    reported but not fatal — losing the history costs at worst a duplicate
    placement, whereas raising would abort a legitimate run, and would do so
    after execute() had already placed the files. The flag lets a caller that
    is about to rewrite the file preserve the old bytes first.
    """
    manifest = folder / MANIFEST_NAME
    if not manifest.exists():
        return [], True
    try:
        runs = json.loads(manifest.read_text())
    except (OSError, ValueError) as e:
        print(f"WARNING: cannot read {manifest} ({e}); treating it as empty.",
              file=sys.stderr)
        return [], False
    if not isinstance(runs, list):
        print(f"WARNING: {manifest} is not a list of runs; treating it as empty.",
              file=sys.stderr)
        return [], False
    return runs, True


def load_manifest(folder: Path) -> List[dict]:
    """The runs recorded for one folder; [] when missing or unreadable."""
    return read_manifest(folder)[0]


def placed_actions(folder: Path) -> List[dict]:
    """Every action from every run recorded for this folder."""
    return [a for run in load_manifest(folder) for a in run.get("actions", [])]


def match_placed(t: Title, actions: List[dict]) -> Optional[dict]:
    """
    Find the action that already placed this title, if any.

    Being literally the same file is proof, and covers the common "I ran it
    twice" case whatever the method was. A re-rip of the same disc title has
    a new path and inode but reproduces the byte count and duration exactly,
    so that is the fallback. Both tests are exact: a false positive silently
    drops a real title, which is worse than the duplicate a miss produces.
    """
    for a in actions:
        src = a.get("source")
        if src and Path(src).exists():
            try:
                if os.path.samefile(src, t.path):
                    return a
            except OSError:
                pass
    for a in actions:
        if a.get("size") == t.size and a.get("duration") is not None \
                and int(a["duration"]) == int(t.duration):
            return a
    return None


def mark_already_placed(plan: Plan) -> int:
    """
    Skip titles an earlier run already placed here, so re-running the script
    on the same rip is a no-op rather than a second copy.

    Runs after classify(), whose verdict it overrides, and before the TUI, so
    the user sees the decision and can reverse it with `k`. Returns how many
    titles it marked.
    """
    # Only count a past action if its file is still there. The manifest
    # records what was done, not what survives: delete an extra and the record
    # of it remains, and without this check the title would be skipped as
    # "already placed" forever and never restored.
    actions = [a for a in placed_actions(library_root(plan))
               if Path(a.get("destination", "")).exists()]
    if not actions:
        return 0

    marked = 0
    for t in plan.titles:
        if t.kind == SKIP:                  # leave classify's own reason alone
            continue
        found = match_placed(t, actions)
        if found is None:
            continue
        dst = found.get("destination", "?")
        try:
            where = Path(dst).relative_to(plan.library)
        except ValueError:
            where = dst                     # library moved since that run
        t.kind, t.note = SKIP, f"already placed as {where}"
        marked += 1
    return marked


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def sample_hash(path: Path, full: bool = False) -> str:
    """
    A cheap fingerprint of a file: four 1 MiB blocks spread through it, plus
    its length. Hashing a 30 GB remux in full takes minutes and buys little —
    size equality already catches truncation, and sampling catches the rest
    for a few MiB of reads. `full` hashes every byte when the user asks.
    """
    h = hashlib.blake2b(digest_size=16)
    size = path.stat().st_size
    with path.open("rb") as f:
        if full or size <= SAMPLE_BLOCK * SAMPLE_POINTS:
            for chunk in iter(lambda: f.read(SAMPLE_BLOCK), b""):
                h.update(chunk)
        else:
            last = size - SAMPLE_BLOCK
            for i in range(SAMPLE_POINTS):
                f.seek(round(i * last / (SAMPLE_POINTS - 1)))
                h.update(f.read(SAMPLE_BLOCK))
    h.update(str(size).encode())
    return h.hexdigest()


def verify_copy(src: Path, dst: Path, expect_size: int,
                full: bool) -> Tuple[Optional[str], str]:
    """
    Check a copy we just made against its source, since shutil.copy2 verifies
    nothing and a half-written file over SMB looks like a real one. Returns
    (problem or None, fingerprint of dst).
    """
    got = dst.stat().st_size
    if got != expect_size:
        return f"copied {got} bytes, expected {expect_size}", ""
    digest = sample_hash(dst, full)
    if digest != sample_hash(src, full):
        return "copy does not match the source", digest
    return None, digest


def place(src: Path, dst: Path, mode: str) -> str:
    """
    Put src at dst using the requested mode. Returns the mode actually used
    (hardlink silently falls back to copy across filesystems).
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if mode == "move":
        shutil.move(str(src), str(dst))
        return "move"
    if mode == "hardlink":
        try:
            os.link(src, dst)
            return "hardlink"
        except OSError:
            pass                       # different filesystem → copy
    shutil.copy2(src, dst)
    return "copy"


def execute(plan: Plan, mode: str, dry_run: bool,
            full_verify: bool = False) -> Outcome:
    """Apply the plan and write a manifest so every action is auditable."""
    actions = []
    failures = 0
    for t in plan.titles:
        dst = destination(plan, t)
        if dst is None:
            print(f"  skip      {t.path.name:40s} ({t.note})")
            continue
        dst = unique(dst)
        rel = dst.relative_to(plan.library)
        if dry_run:
            print(f"  {mode:9s} {t.path.name:40s} → {rel}")
            continue
        used = place(t.path, dst, mode)
        if used == "copy":
            problem, digest = verify_copy(t.path, dst, t.size, full_verify)
            if problem:
                # Our own file, written seconds ago, and known bad.
                dst.unlink()
                print(f"  FAILED    {t.path.name:40s} {problem} — removed")
                failures += 1
                continue
        else:
            # A hardlink is the same inode, and a move leaves nothing behind
            # to compare against; fingerprint the result so undo can check it.
            digest = sample_hash(dst, full_verify)
        print(f"  {used:9s} {t.path.name:40s} → {rel}")
        actions.append({"source": str(t.path), "destination": str(dst),
                        "kind": t.kind, "method": used, "note": t.note,
                        "size": t.size, "duration": t.duration,
                        "sample_hash": digest, "full_hash": full_verify})

    if dry_run:
        print("\n(dry run — nothing written)")
        return Outcome()

    # Manifest: lets you see later exactly where each disc title went.
    root = library_root(plan)
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / MANIFEST_NAME
    existing, readable = read_manifest(root)
    if not readable:
        # We are about to replace it, and nothing here destroys what the user
        # might still want to salvage by hand.
        kept = unique(manifest.with_name(MANIFEST_NAME + ".corrupt"))
        manifest.rename(kept)
        print(f"Unreadable manifest kept as {kept.name}", file=sys.stderr)
    existing.append({"when": datetime.now().isoformat(timespec="seconds"),
                     "name": plan.name, "tv": plan.tv,
                     "provider_id": plan.provider_id, "disc": plan.disc_label,
                     "actions": actions})
    manifest.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
    print(f"\nDone. Manifest: {manifest}")
    if failures:
        print(f"{failures} file(s) failed verification and were removed; the "
              f"sources are untouched, so re-running is safe.", file=sys.stderr)
    return Outcome(placed=len(actions), failures=failures)


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------

def undo_action(action: dict, folder: Path, dry_run: bool) -> Tuple[str, bool]:
    """
    Reverse one placement. Returns (message, ok).

    This is the only part of the tool that deletes, so it refuses whenever it
    cannot prove the file is the one it put there: a mismatch means something
    else wrote it, and removing it would destroy work that is not ours.
    """
    dst = Path(action.get("destination", ""))
    src = Path(action.get("source", ""))
    method = action.get("method", "")
    name = dst.name or "?"

    try:
        dst.relative_to(folder)
    except ValueError:
        return f"REFUSED       {dst} is outside {folder}", False

    if not dst.exists():
        return f"already gone  {name}", True

    if method == "move":
        # Moving back cannot destroy anything, so the only question is whether
        # the old location is free.
        if src.exists():
            return f"REFUSED       {name}: {src} is occupied", False
        if not dry_run:
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst), str(src))
        return f"move back     {name} → {src}", True

    if method == "hardlink":
        if not src.exists():
            return (f"REFUSED       {name}: {src} is gone, so this may be the "
                    f"only copy", False)
        if not os.path.samefile(src, dst):
            return f"REFUSED       {name}: no longer the same file as {src}", False
    elif method == "copy":
        if not src.exists():
            return (f"REFUSED       {name}: {src} is gone, so this is the only "
                    f"copy — remove it by hand if that is what you want", False)
        recorded = action.get("sample_hash")
        if action.get("size") is None or recorded is None:
            return (f"REFUSED       {name}: the manifest predates copy "
                    f"verification, so this cannot be checked", False)
        if dst.stat().st_size != action["size"]:
            return f"REFUSED       {name}: size changed since it was placed", False
        if sample_hash(dst, action.get("full_hash", False)) != recorded:
            return f"REFUSED       {name}: contents changed since it was placed", False
    else:
        return f"REFUSED       {name}: unknown method {method!r}", False

    if not dry_run:
        dst.unlink()
    return f"remove        {name}", True


def prune_empty(folder: Path) -> None:
    """
    Drop the extras/season subfolders an undo emptied. Only ever removes a
    directory that is already empty, so nothing can be lost here.
    """
    for d in sorted(folder.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()


def undo(folder: Path, run_index: Optional[int], undo_all: bool,
         dry_run: bool) -> int:
    """Reverse recorded runs, newest first. Returns a process exit code."""
    if not folder.is_dir():
        sys.exit(f"{folder} is not a directory")
    runs, readable = read_manifest(folder)
    if not readable:
        sys.exit(f"{folder / MANIFEST_NAME} is unreadable; refusing to guess "
                 f"what to remove.")
    if not runs:
        sys.exit(f"No runs recorded in {folder / MANIFEST_NAME}")

    if undo_all:
        chosen = list(range(len(runs)))
    elif run_index is not None:
        if not 0 <= run_index < len(runs):
            sys.exit(f"--run {run_index} is out of range (0…{len(runs) - 1})")
        chosen = [run_index]
    else:
        pending = [i for i, r in enumerate(runs) if not r.get("undone")]
        if not pending:
            sys.exit("Every recorded run has already been undone.")
        chosen = [pending[-1]]

    refused = 0
    for i in sorted(chosen, reverse=True):
        run = runs[i]
        actions = run.get("actions", [])
        print(f"Run {i} — {run.get('when', '?')}, {len(actions)} action(s)"
              + ("  [already undone]" if run.get("undone") else ""))
        clean = True
        for action in reversed(actions):
            message, ok = undo_action(action, folder, dry_run)
            print(f"  {message}")
            if not ok:
                refused += 1
                clean = False
        if clean and not dry_run:
            run["undone"] = datetime.now().isoformat(timespec="seconds")

    if dry_run:
        print("\n(dry run — nothing removed)")
        return 0

    prune_empty(folder)
    manifest = folder / MANIFEST_NAME
    if not [p for p in folder.iterdir() if p != manifest]:
        manifest.unlink(missing_ok=True)
        folder.rmdir()
        print(f"\nRemoved the now-empty {folder}")
    else:
        manifest.write_text(json.dumps(runs, indent=2, ensure_ascii=False))
    if refused:
        print(f"\n{refused} file(s) were left in place; see the reasons above.",
              file=sys.stderr)
    return 1 if refused else 0


# ---------------------------------------------------------------------------
# TUI  (curses; stdlib only)
# ---------------------------------------------------------------------------

def run_tui(plan: Plan) -> bool:
    """
    Show the proposed plan as a table and let the user edit it.
    Returns True to apply, False to abort.
    """
    import curses

    KINDS = [MAIN, VERSION, EXTRA, EPISODE, SKIP] if plan.tv else [MAIN, VERSION, EXTRA, SKIP]
    HELP = ("↑↓ select  k kind  e extra-type  l label  n movie name  "
            "s season  t tv/movie  Enter apply  q quit")

    def prompt(stdscr, question: str, default: str = "") -> Optional[str]:
        """Single-line text input at the bottom of the screen."""
        h, w = stdscr.getmaxyx()
        curses.echo()
        curses.curs_set(1)
        stdscr.move(h - 1, 0)
        stdscr.clrtoeol()
        stdscr.addstr(h - 1, 0, f"{question} [{default}]: "[: w - 1])
        try:
            s = stdscr.getstr().decode("utf-8", "replace").strip()
        except KeyboardInterrupt:
            s = None
        curses.noecho()
        curses.curs_set(0)
        if s is None:
            return None
        return s or default

    def draw(stdscr, cur: int) -> None:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        kind_word = "TV series" if plan.tv else "Movie"
        season = f"  season {plan.season:02d}" if plan.tv else ""
        stdscr.addstr(0, 0, f"sort2own  —  {kind_word}: {plan.name}{season}"[: w - 1], curses.A_BOLD)
        stdscr.addstr(1, 0, f"library: {plan.library}"[: w - 1])
        hdr = f"{'#':>3} {'file':28s} {'length':>8} {'size':>7} {'ch':>3}  {'kind':8s} {'→ destination / note'}"
        stdscr.addstr(3, 0, hdr[: w - 1], curses.A_UNDERLINE)

        for i, t in enumerate(plan.titles):
            y = 4 + i
            if y >= h - 3:
                break
            dst = destination(plan, t)
            where = str(dst.relative_to(plan.library)) if dst else f"(skip: {t.note})"
            kind = t.kind if t.kind != EXTRA else f"extra/{t.extra_type}"
            line = (f"{i:>3} {t.path.name[:28]:28s} {t.hms:>8} {t.gib:>7} {t.chapters:>3}  "
                    f"{kind[:14]:14s} {where}")
            attr = curses.A_REVERSE if i == cur else curses.A_NORMAL
            if t.kind == SKIP:
                attr |= curses.A_DIM
            stdscr.addstr(y, 0, line[: w - 1], attr)

        t = plan.titles[cur]
        info = f"tag: {t.tag or '—'}   heuristic: {t.note}"
        stdscr.addstr(h - 3, 0, info[: w - 1])
        stdscr.addstr(h - 2, 0, HELP[: w - 1], curses.A_DIM)
        stdscr.refresh()

    def loop(stdscr) -> bool:
        curses.curs_set(0)
        cur = 0
        while True:
            draw(stdscr, cur)
            key = stdscr.getch()
            t = plan.titles[cur]

            if key == curses.KEY_UP:
                cur = max(0, cur - 1)
            elif key == curses.KEY_DOWN:
                cur = min(len(plan.titles) - 1, cur + 1)

            elif key == ord("k"):                                # cycle kind
                t.kind = KINDS[(KINDS.index(t.kind) + 1) % len(KINDS)]
                if t.kind == MAIN:                               # only one main
                    for o in plan.titles:
                        if o is not t and o.kind == MAIN:
                            o.kind = EXTRA
                if t.kind == VERSION and not t.label:
                    t.label = useful_tag(t, plan) or "Alternate cut"
                if t.kind == EPISODE and not t.label.isdigit():
                    # Counts on from this disc's first number, not from 1, so
                    # --continue keeps working when the user edits by hand.
                    t.label = str(plan.start_episode
                                  + sum(1 for o in plan.titles
                                        if o.kind == EPISODE and o is not t))
                if t.kind == EXTRA and (not t.label or t.label.isdigit()):
                    t.label = useful_tag(t, plan) or duration_label(t)
                t.note = "set by user"

            elif key == ord("e"):                                # cycle extra type
                t.extra_type = EXTRA_TYPES[(EXTRA_TYPES.index(t.extra_type) + 1) % len(EXTRA_TYPES)]
                if t.kind != EXTRA:
                    t.kind = EXTRA
                    t.label = t.label or useful_tag(t, plan) or duration_label(t)

            elif key == ord("l"):                                # edit label
                what = "episode number" if t.kind == EPISODE else "label / file name"
                s = prompt(stdscr, what, t.label)
                if s is not None:
                    t.label = s

            elif key == ord("n"):                                # movie / show name
                s = prompt(stdscr, 'name as "Title (Year)"', plan.name)
                if s is not None:
                    plan.name = s

            elif key == ord("s") and plan.tv:                    # season number
                s = prompt(stdscr, "season number", str(plan.season))
                if s and s.isdigit():
                    plan.season = int(s)

            elif key == ord("t"):                                # toggle tv/movie
                plan.tv = not plan.tv
                KINDS[:] = [MAIN, VERSION, EXTRA, EPISODE, SKIP] if plan.tv else [MAIN, VERSION, EXTRA, SKIP]
                for o in plan.titles:
                    if o.kind == EPISODE and not plan.tv:
                        o.kind = EXTRA
                        o.label = useful_tag(o, plan) or duration_label(o)

            elif key in (curses.KEY_ENTER, 10, 13):
                return True
            elif key in (ord("q"), 27):
                return False

    return curses.wrapper(loop)


# ---------------------------------------------------------------------------
# Release hints
# ---------------------------------------------------------------------------

# How close a ripped extra's length must be to a listed one to be the same
# thing. Near-exact on purpose: listed extras sit as little as two seconds
# apart, so a wider window makes most of a disc ambiguous rather than more
# matchable. Measured — see CLAUDE.md §6 item 8.
HINT_SECONDS = 1.0


def hint_cache() -> Path:
    root = Path(os.environ.get("XDG_CACHE_HOME") or "~/.cache").expanduser()
    return root / "sort2own" / "ofdb"


def apply_release_hints(plan: Plan, ref: str, apply: bool) -> None:
    """
    Put the disc's own names to extras, using a release listing.

    Matching is on duration alone, so a name is applied only when exactly one
    listing fits. Where several fit — a disc's trailer reel routinely repeats
    the lengths of its featurettes — the candidates go in the note for the
    user to choose between, because picking one at random is worse than the
    duration-derived name already there.
    """
    if hints_ofdb is None:
        print("OFDb hints unavailable: hints_ofdb.py is missing or unreadable",
              file=sys.stderr)
        return
    try:
        listings = hints_ofdb.lookup(ref, hint_cache())
    except Exception as e:   # noqa: BLE001 — network, HTML and encoding all
        print(f"OFDb lookup failed: {e}", file=sys.stderr)   # fail the same way
        return
    if not listings:
        print("OFDb listed no extras for that release", file=sys.stderr)
        return

    timed = [(name, seconds) for name, seconds in listings if seconds is not None]
    named = ambiguous = 0
    for t in plan.titles:
        if t.kind not in (EXTRA, VERSION):
            continue
        fits = [name for name, seconds in timed
                if abs(t.duration - seconds) <= HINT_SECONDS]
        if not fits:
            continue
        if len(fits) > 1:
            t.note = f"OFDb: could be {' / '.join(fits[:3])}"
            ambiguous += 1
            continue
        t.note = f"OFDb: {fits[0]}"
        if apply:
            t.label = safe_name(fits[0]) or t.label
            folder = match_keyword(fits[0])
            if folder and t.kind == EXTRA:
                t.extra_type = folder
            named += 1

    print(f"OFDb: {len(listings)} extras listed, {named} matched by length"
          + (f", {ambiguous} ambiguous (see the notes)" if ambiguous else "")
          + ("" if apply else " — not applied, pass --ofdb-apply"))


# ---------------------------------------------------------------------------
# Jellyfin
# ---------------------------------------------------------------------------

def jellyfin_refresh(url: str, key: str, timeout: float = 10.0) -> Optional[str]:
    """
    Ask Jellyfin to scan its libraries, so a new film shows up now instead of
    whenever the scheduled task next runs. Returns None on success, or a short
    reason — never the key, which must not reach a log.

    A whole-library scan is heavy, but the targeted /Items/{id}/Refresh needs
    an item ID, and a folder Jellyfin has never seen does not have one yet.
    """
    if any(c == '"' or ord(c) < 0x20 for c in key):
        return "the API key contains characters that cannot go in a header"

    request = urllib.request.Request(
        url.rstrip("/") + "/Library/Refresh", data=b"", method="POST")
    # The legacy X-Emby-Token header and api_key query parameter were removed
    # in Jellyfin 12. Some versions want Client alongside Token, so send the
    # full set (jellyfin/jellyfin#12990).
    request.add_header("Authorization",
                       'MediaBrowser Client="sort2own", Device="sort2own", '
                       f'DeviceId="sort2own", Version="1.0", Token="{key}"')
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status not in (200, 204):
                return f"HTTP {response.status}"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    except (urllib.error.URLError, OSError) as e:
        return str(getattr(e, "reason", e))
    return None


def refresh_if_configured(a: argparse.Namespace) -> None:
    """
    Trigger a scan when the user has configured one. Always advisory: the
    files are already in place, so a refresh that fails is a warning and
    never changes the exit code.
    """
    if not a.jellyfin_url:
        return
    if not a.jellyfin_key:
        print("Jellyfin refresh skipped: no API key configured", file=sys.stderr)
        return
    problem = jellyfin_refresh(a.jellyfin_url, a.jellyfin_key)
    if problem:
        print(f"Jellyfin refresh failed: {problem}", file=sys.stderr)
    else:
        print("Jellyfin scan triggered")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Settings the config file may carry that are not command-line flags: a key
# read from a separate file (so it need not sit in the config itself), and
# the TMDB key, reserved for the search feature.
CONFIG_ONLY_KEYS = {"jellyfin_key_file", "tmdb_key"}

# Environment overrides, by the argparse destination they fill in.
ENV_VARS = {
    "library": "SORT2OWN_LIBRARY",
    "tv_library": "SORT2OWN_TV_LIBRARY",
    "jellyfin_url": "SORT2OWN_JELLYFIN_URL",
    "jellyfin_key": "SORT2OWN_JELLYFIN_KEY",
}


def config_path(argv: List[str]) -> Optional[Path]:
    """
    Locate the config file. It has to be read before the real parser exists,
    because its values become that parser's defaults — hence the throwaway
    parser that knows only --config.

    A file named explicitly must exist; the default location simply not being
    there is the normal case and stays quiet.
    """
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path)
    named = pre.parse_known_args(argv)[0].config
    if named is None and os.environ.get("SORT2OWN_CONFIG"):
        named = Path(os.environ["SORT2OWN_CONFIG"])
    if named is not None:
        named = named.expanduser()
        if not named.is_file():
            sys.exit(f"config file not found: {named}")
        return named

    home = Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser()
    default = home / "sort2own" / "config.toml"
    return default if default.is_file() else None


def load_config(path: Optional[Path], known: set) -> dict:
    """
    Read the config into argparse defaults. A `[section] key` becomes
    `section_key`.

    A broken or misspelt config is fatal, unlike a broken manifest: the
    manifest is history we can do without, but this is the user stating how
    the run should behave, and quietly ignoring it would place files
    somewhere they did not ask for.
    """
    if path is None:
        return {}
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        sys.exit(f"cannot read config {path}: {e}")

    flat = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            for sub, subvalue in value.items():
                flat[f"{key}_{sub}"] = subvalue
        else:
            flat[key] = value

    unknown = sorted(set(flat) - known - CONFIG_ONLY_KEYS)
    if unknown:
        sys.exit(f"unknown setting(s) in {path}: {', '.join(unknown)}")

    if "jellyfin_key_file" in flat:
        if "jellyfin_key" in flat:
            sys.exit(f"{path}: set jellyfin.key or jellyfin.key_file, not both")
        key_file = Path(str(flat.pop("jellyfin_key_file"))).expanduser()
        try:
            flat["jellyfin_key"] = key_file.read_text().strip()
        except OSError as e:
            sys.exit(f"cannot read jellyfin.key_file {key_file}: {e}")

    return {k: v for k, v in flat.items() if k in known}


def env_config(known: set) -> dict:
    """Settings taken from the environment; these outrank the config file."""
    return {dest: os.environ[var] for dest, var in ENV_VARS.items()
            if dest in known and os.environ.get(var)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Sort MakeMKV output into a Jellyfin-friendly folder layout (non-destructive).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  %(prog)s ~/rips/A_FILM\n"
               "  %(prog)s ~/rips/A_FILM --yes --name 'A Film (2024)' --runtime 119\n"
               "  %(prog)s ~/rips/DISC1 --yes --tv --name 'A Series (2016)' --season 2\n"
               "  %(prog)s --undo '/media/movies/A Film (2024)'\n")
    p.add_argument("source", type=Path, nargs="?",
                   help="directory containing MakeMKV's *.mkv output")
    p.add_argument("--library", type=Path, default=Path("/media/movies"),
                   help="root of the Jellyfin library (default: /media/movies)")
    p.add_argument("--tv-library", type=Path,
                   help="separate library root used when --tv is given on the command "
                        "line (toggling t in the TUI does not switch libraries)")
    p.add_argument("--config", type=Path, metavar="PATH",
                   help="config file (default: $SORT2OWN_CONFIG or "
                        "$XDG_CONFIG_HOME/sort2own/config.toml)")
    p.add_argument("--name", help='"Title (Year)"; required with --yes, guessed otherwise')
    p.add_argument("--tv", action="store_true", help="treat the disc as TV episodes")
    p.add_argument("--season", type=int, default=1, help="season number for --tv (default 1)")
    episodes = p.add_mutually_exclusive_group()
    episodes.add_argument("--continue", dest="continue_episodes", action="store_true",
                          help="with --tv: carry on numbering after the episodes "
                               "already in the season folder (for the next disc of a set)")
    episodes.add_argument("--start-episode", type=int, metavar="N",
                          help="with --tv: number this disc's first episode N")
    p.add_argument("--supplement", action="store_true",
                   help="a further disc of a set: place everything as extras or "
                        "alternate cuts, and do not look for a main feature here")
    p.add_argument("--disc-label", metavar="TEXT",
                   help="free-text note recorded in the manifest, e.g. 'Disc 2'")
    p.add_argument("--ofdb", metavar="REF",
                   help="name the extras from an OFDb release page: give its URL, "
                        "or the two numbers from it as 123456,789012")
    p.add_argument("--ofdb-apply", action="store_true",
                   help="with --yes: actually apply the unambiguous OFDb names "
                        "(without it they are only reported)")
    ids = p.add_mutually_exclusive_group()
    ids.add_argument("--tmdb", metavar="ID",
                     help="TMDB id, appended to the folder name as [tmdbid-ID] so "
                          "Jellyfin cannot misidentify the film (e.g. 112233)")
    ids.add_argument("--imdb", metavar="ID",
                     help="IMDb id, appended as [imdbid-ID] (e.g. tt7654321)")
    p.add_argument("--runtime", type=int, metavar="MIN",
                   help="expected main-feature runtime in minutes; picks the closest title instead of the longest")
    p.add_argument("--min-extra", type=int, default=90, metavar="SEC",
                   help="titles shorter than this are skipped as junk (default 90)")
    p.add_argument("--version-ratio", type=float, default=0.85,
                   help="titles at least this fraction of the main length are alternate cuts (default 0.85)")
    p.add_argument("--dup-tolerance", type=float, default=0.01,
                   help="size tolerance for duplicate detection (default 0.01 = 1%%)")
    p.add_argument("--dup-seconds", type=float, default=2.0, metavar="SEC",
                   help="how close two titles' lengths must be to count as the "
                        "same content (default 2.0 seconds)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--copy", action="store_true", help="always copy (default: hardlink, copy if not possible)")
    mode.add_argument("--move", action="store_true", help="move files instead of linking (still never overwrites)")
    p.add_argument("--verify", choices=["sample", "full"], default="sample",
                   help="how thoroughly to check a copy against its source: "
                        "'sample' reads a few MiB (default), 'full' hashes "
                        "every byte")
    p.add_argument("--force", action="store_true",
                   help="place titles even if an earlier run already placed them")
    p.add_argument("--yes", "-y", action="store_true", help="unattended: apply the heuristics without the TUI")
    p.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    p.add_argument("--undo", type=Path, metavar="FOLDER",
                   help="reverse what an earlier run placed in FOLDER, using its manifest")
    which = p.add_mutually_exclusive_group()
    which.add_argument("--run", type=int, metavar="N",
                       help="with --undo: undo run N (0-based; default is the last one left)")
    which.add_argument("--all", action="store_true",
                       help="with --undo: undo every recorded run")
    p.add_argument("--jellyfin-url", metavar="URL",
                   help="Jellyfin server to ask for a library scan after a run")
    p.add_argument("--jellyfin-key", metavar="KEY",
                   help="Jellyfin API key; prefer jellyfin.key_file in the config "
                        "so it stays out of your shell history")
    return p


def parse_args(argv: List[str]) -> argparse.Namespace:
    """
    Build the parser, then layer the defaults under it:
    built-in < config file < environment < command line.
    """
    p = build_parser()
    # Every destination the parser knows, without reaching into its internals.
    known = set(vars(p.parse_known_args([])[0]))

    defaults = load_config(config_path(argv), known)
    defaults.update(env_config(known))
    p.set_defaults(**defaults)
    return p.parse_args(argv)


def local_path(value: Path, what: str) -> Path:
    """
    Resolve a path the user gave us, refusing URLs.

    `Path("smb://nas/movies")` is not an error to pathlib — it is a *relative*
    path, so it resolves against the working directory and the rip lands in a
    folder literally named `smb:`. Silently sorting into the wrong place is
    the worst outcome available here, so say plainly what is wrong instead.

    Matched against the collapsed form (`smb:/…`), because pathlib has already
    eaten the second slash by the time argparse hands the Path over.
    """
    if re.match(r"[A-Za-z][A-Za-z0-9+.\-]*:/", str(value)):
        sys.exit(f"{what} looks like a URL: {value}\n"
                 f"sort2own reads and writes through the filesystem, so a "
                 f"network share has to be mounted first (fstab, or a systemd "
                 f".mount unit). Point it at the mount — /mnt/movies, say — "
                 f"rather than at smb://…")
    return value.expanduser().resolve()


def same_filesystem(a: Path, b: Path) -> bool:
    """Hardlinks only work within one filesystem; compare device IDs."""
    # Walk up until a path exists (the library folder may not exist yet).
    while not b.exists():
        b = b.parent
    return a.stat().st_dev == b.stat().st_dev


def check_hardlink_feasible(src: Path, library: Path, interactive: bool) -> bool:
    """
    Default mode is hardlink, which silently becomes a copy across
    filesystems. Rather than surprise the user with a slow, space-doubling
    copy (e.g. over SMB), warn and let them choose. Returns True to proceed.
    """
    if same_filesystem(src, library):
        return True
    print(
        "WARNING: source and library are on different filesystems, so files\n"
        "cannot be hardlinked — they would be COPIED instead.\n"
        f"   source : {src}\n"
        f"   library: {library}\n"
        "Options:\n"
        "  * run this script on the NAS / with both paths on the same volume\n"
        "    (e.g. rip directly into a scratch folder on the NAS), or\n"
        "  * re-run with --copy or --move to accept a real copy, or\n"
        "  * sort into a local library folder and copy that to the NAS yourself.",
        file=sys.stderr,
    )
    if not interactive:
        print("Unattended mode: refusing to guess. Pass --copy or --move explicitly.", file=sys.stderr)
        return False
    ans = input("Continue with a copy anyway? [y/N] ").strip().lower()
    return ans in ("y", "yes")


def provider_id(tmdb: Optional[str], imdb: Optional[str]) -> str:
    """Validate --tmdb / --imdb into the suffix Jellyfin parses, or ""."""
    if tmdb:
        if not re.fullmatch(r"\d+", tmdb):
            sys.exit(f"--tmdb takes a numeric id like 112233 (got {tmdb!r})")
        return f"tmdbid-{tmdb}"
    if imdb:
        if not re.fullmatch(r"tt\d+", imdb):
            sys.exit(f"--imdb takes an id like tt7654321 (got {imdb!r})")
        return f"imdbid-{imdb}"
    return ""


def main(argv: List[str]) -> int:
    a = parse_args(argv)

    if a.undo:
        code = undo(local_path(a.undo, "--undo"), a.run, a.all, a.dry_run)
        # Removing files leaves the same stale entries a new film does.
        if code == 0 and not a.dry_run:
            refresh_if_configured(a)
        return code
    if (a.run is not None or a.all) and not a.undo:
        sys.exit("--run and --all only apply to --undo")
    if (a.continue_episodes or a.start_episode is not None) and not a.tv:
        sys.exit("--continue and --start-episode only apply to --tv")
    if a.supplement and a.tv:
        sys.exit("--supplement is for a film's extra disc; for a further disc "
                 "of a series use --tv --continue")
    if a.source is None:
        sys.exit("a source directory is required (or --undo FOLDER)")

    src = local_path(a.source, "the source directory")
    if not src.is_dir():
        sys.exit(f"{src} is not a directory")
    if shutil.which("ffprobe") is None:
        sys.exit("ffprobe not found — install ffmpeg")

    # TV shows and films normally live in separate Jellyfin libraries. This is
    # settled once, here, because check_hardlink_feasible() and the TUI both
    # depend on it — toggling t in the TUI does not move the library.
    library = a.tv_library if (a.tv and a.tv_library) else a.library

    plan = Plan(name=a.name or guess_name_from_source(src),
                library=local_path(library, "the library path"),
                tv=a.tv, season=a.season, titles=scan(src),
                provider_id=provider_id(a.tmdb, a.imdb),
                supplement=a.supplement, disc_label=a.disc_label or "")

    if a.supplement:
        # Judge alternate cuts against the feature already in the library.
        feature = library_root(plan) / f"{plan.folder}.mkv"
        if feature.exists():
            plan.main_duration = ffprobe(feature).duration
        elif a.dry_run:
            print(f"(dry run: {feature.name} is not there yet, so alternate "
                  f"cuts cannot be told apart from extras)\n")
        else:
            sys.exit(f"--supplement expects the main feature to be placed "
                     f"already, but {feature} is not there. Sort the first "
                     f"disc first.")

    if plan.tv:
        if a.start_episode is not None:
            plan.start_episode = a.start_episode
        elif a.continue_episodes:
            plan.start_episode = next_episode(plan)
            print(f"Continuing season {plan.season:02d} at episode "
                  f"{plan.start_episode}.")

    classify(plan, a.runtime, a.min_extra, a.version_ratio, a.dup_tolerance,
             a.dup_seconds)

    if not a.force:
        already = mark_already_placed(plan)
        if already:
            print(f"{already} title(s) were already placed by an earlier run "
                  f"and will be skipped (--force places them again).")

    mode = "move" if a.move else "copy" if a.copy else "hardlink"
    interactive = not a.yes and sys.stdin.isatty() and sys.stdout.isatty()

    if mode == "hardlink" and not check_hardlink_feasible(src, plan.library, interactive):
        print("Aborted, nothing written.")
        return 1

    if a.ofdb:
        # After the feasibility check, so an aborted run costs no page fetch.
        # In the TUI a match can be applied straight away because the user
        # sees it and can undo it; unattended, it needs saying so explicitly.
        apply_release_hints(plan, a.ofdb, apply=interactive or a.ofdb_apply)

    if interactive:
        if not run_tui(plan):
            print("Aborted, nothing written.")
            return 1
    elif not a.name:
        sys.exit("--yes requires --name 'Title (Year)' (a guessed name is too risky unattended)")

    print(f"\n{'TV' if plan.tv else 'Movie'}: {plan.name}   library: {plan.library}   method: {mode}\n")
    outcome = execute(plan, mode, a.dry_run, a.verify == "full")
    if outcome.placed and not a.dry_run:
        refresh_if_configured(a)
    return 1 if outcome.failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
