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
  * --dry-run prints the plan and touches nothing.

TWO WAYS TO RUN IT
  Interactive (default when run in a terminal):
      sort2own.py /path/to/rip
  Unattended (for scripts, ARM post-processing hooks, cron):
      sort2own.py /path/to/rip --yes --name "A Film (2024)"

  In unattended mode the heuristics decide everything. In the TUI you see the
  heuristics' proposal and can override any title with a few keystrokes.

DEPENDENCIES
  Python 3.8+ and `ffprobe` (part of ffmpeg). Nothing else.

HOW THE HEURISTICS WORK  (see classify())
  1. Every title's duration, size, chapter count and embedded title tag are
     read with ffprobe.
  2. Movie mode: the main feature is the longest title, unless --runtime is
     given, in which case it is the title whose length is closest to the
     stated runtime (protects against "play all extras" titles and against
     picking the wrong cut).
  3. Titles of near-identical length and size to another title are duplicates
     (multi-angle / language-variant playlists) and are skipped.
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
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import List, Optional

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

MANIFEST_NAME = ".sort2own.json"


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
class Plan:
    """Everything the TUI edits and the executor consumes."""
    name: str                # "Title (Year)" — folder and main file name
    library: Path
    tv: bool = False
    season: int = 1
    titles: List[Title] = field(default_factory=list)
    provider_id: str = ""    # "tmdbid-12345" / "imdbid-tt1234567", or ""

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
    """Read duration, chapter count and title tag from one file via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:format_tags=title",
        "-show_chapters",
        "-of", "json", str(path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        data = json.loads(out)
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError) as e:
        sys.exit(f"ffprobe failed on {path}: {e}")

    fmt = data.get("format", {})
    return Title(
        path=path,
        duration=float(fmt.get("duration", 0) or 0),
        size=path.stat().st_size,
        chapters=len(data.get("chapters", [])),
        tag=(fmt.get("tags") or {}).get("title", "").strip(),
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


def classify(plan: Plan, runtime_min: Optional[int], min_extra: int,
             version_ratio: float, dup_tolerance: float) -> None:
    """
    Fill in kind / extra_type / label / note for every title.
    Runs on the raw scan; the TUI lets the user override afterwards.
    """
    ts = plan.titles
    for t in ts:                      # reset, so the function is idempotent
        t.kind, t.label, t.note = EXTRA, "", ""

    # --- 3. duplicates: same length (±tolerance) and same size (±tolerance) --
    for i, a in enumerate(ts):
        for b in ts[:i]:
            if b.kind == SKIP:
                continue
            close_dur = abs(a.duration - b.duration) <= dup_tolerance * max(b.duration, 1)
            close_size = abs(a.size - b.size) <= dup_tolerance * max(b.size, 1)
            if close_dur and close_size:
                a.kind, a.note = SKIP, f"duplicate of {b.path.name}"
                break

    live = [t for t in ts if t.kind != SKIP]

    if plan.tv:
        _classify_tv(plan, live, min_extra)
    else:
        _classify_movie(plan, live, runtime_min, min_extra, version_ratio)

    # --- 6. name the extras --------------------------------------------------
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
    # --- 2. pick the main feature ---------------------------------------------
    if runtime_min:
        target = runtime_min * 60
        main = min(live, key=lambda t: abs(t.duration - target))
        main.note = f"closest to --runtime {runtime_min} min"
    else:
        main = max(live, key=lambda t: t.duration)
        main.note = "longest title"
    main.kind = MAIN

    for t in live:
        if t is main:
            continue
        # --- 4. alternative cuts --------------------------------------------
        if t.duration >= version_ratio * main.duration:
            t.kind = VERSION
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
    ep = 1
    for t in live:
        if t.duration < min_extra:
            t.kind, t.note = SKIP, f"shorter than {min_extra}s"
        elif abs(t.duration - med) <= 0.25 * med:
            t.kind, t.label, t.note = EPISODE, str(ep), f"≈ median episode length ({int(med//60)} min)"
            ep += 1
        else:
            t.note = "extra (length unlike the episodes)"


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
# Execution
# ---------------------------------------------------------------------------

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


def execute(plan: Plan, mode: str, dry_run: bool) -> None:
    """Apply the plan and write a manifest so every action is auditable."""
    actions = []
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
        print(f"  {used:9s} {t.path.name:40s} → {rel}")
        actions.append({"source": str(t.path), "destination": str(dst),
                        "kind": t.kind, "method": used, "note": t.note})

    if dry_run:
        print("\n(dry run — nothing written)")
        return

    # Manifest: lets you see later exactly where each disc title went.
    root = library_root(plan)
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / MANIFEST_NAME
    existing = json.loads(manifest.read_text()) if manifest.exists() else []
    existing.append({"when": datetime.now().isoformat(timespec="seconds"),
                     "name": plan.name, "tv": plan.tv,
                     "provider_id": plan.provider_id, "actions": actions})
    manifest.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
    print(f"\nDone. Manifest: {manifest}")


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
                    t.label = str(1 + sum(1 for o in plan.titles if o.kind == EPISODE and o is not t))
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
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Sort MakeMKV output into a Jellyfin-friendly folder layout (non-destructive).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  %(prog)s ~/rips/A_FILM\n"
               "  %(prog)s ~/rips/A_FILM --yes --name 'A Film (2024)' --runtime 119\n"
               "  %(prog)s ~/rips/DISC1 --yes --tv --name 'A Series (2016)' --season 2\n")
    p.add_argument("source", type=Path, help="directory containing MakeMKV's *.mkv output")
    p.add_argument("--library", type=Path, default=Path(os.environ.get("SORT2OWN_LIBRARY", "/media/movies")),
                   help="root of the Jellyfin library (default: $SORT2OWN_LIBRARY or /media/movies)")
    p.add_argument("--name", help='"Title (Year)"; required with --yes, guessed otherwise')
    p.add_argument("--tv", action="store_true", help="treat the disc as TV episodes")
    p.add_argument("--season", type=int, default=1, help="season number for --tv (default 1)")
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
                   help="duration/size tolerance for duplicate detection (default 0.01 = 1%%)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--copy", action="store_true", help="always copy (default: hardlink, copy if not possible)")
    mode.add_argument("--move", action="store_true", help="move files instead of linking (still never overwrites)")
    p.add_argument("--yes", "-y", action="store_true", help="unattended: apply the heuristics without the TUI")
    p.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    return p.parse_args(argv)


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
    src = a.source.expanduser().resolve()
    if not src.is_dir():
        sys.exit(f"{src} is not a directory")
    if shutil.which("ffprobe") is None:
        sys.exit("ffprobe not found — install ffmpeg")

    plan = Plan(name=a.name or guess_name_from_source(src),
                library=a.library.expanduser().resolve(),
                tv=a.tv, season=a.season, titles=scan(src),
                provider_id=provider_id(a.tmdb, a.imdb))
    classify(plan, a.runtime, a.min_extra, a.version_ratio, a.dup_tolerance)

    mode = "move" if a.move else "copy" if a.copy else "hardlink"
    interactive = not a.yes and sys.stdin.isatty() and sys.stdout.isatty()

    if mode == "hardlink" and not check_hardlink_feasible(src, plan.library, interactive):
        print("Aborted, nothing written.")
        return 1

    if interactive:
        if not run_tui(plan):
            print("Aborted, nothing written.")
            return 1
    elif not a.name:
        sys.exit("--yes requires --name 'Title (Year)' (a guessed name is too risky unattended)")

    print(f"\n{'TV' if plan.tv else 'Movie'}: {plan.name}   library: {plan.library}   method: {mode}\n")
    execute(plan, mode, a.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
