# sort2own — design notes

Why the code looks the way it does. The README covers using the tool; this
covers the decisions behind it, the invariants that must survive a change, and
the Jellyfin naming rules the whole thing is built around.

## Shape of the project

`sort2own.py` is a single file, with one optional sidecar, `hints_ofdb.py`.
That is a decision rather than an accident. Being able to `scp sort2own.py` to
a NAS, or drop it into an ARM container, with nothing to install, is worth more
than tidier module boundaries — and planned ARM integration assumes exactly
that. The only dependency outside the standard library is `ffprobe`.

Python 3.11 is the floor, because the config loader uses stdlib `tomllib`.

The sidecar is separate for fault isolation, not modularity: it parses someone
else's HTML, which will change without warning. `sort2own.py` imports it in a
`try/except` and carries on without hints when it is missing or broken. Delete
the file and the tool still sorts discs.

The sections of `sort2own.py` are marked with banner comments. Revisit the
single-file decision if a section grows its own dependencies or stops being
readable in one sitting — not on a line count, which is a number nobody
maintains.

## The problem

MakeMKV emits `Title_t00.mkv … Title_t10.mkv` with no semantic labelling. Drop
those into a Jellyfin movie library and Jellyfin scans each one as a separate
movie, or as multiple copies of the same film. Jellyfin has no metadata source
for disc extras — the labels exist only in the disc's menu graphics — so the
*path* has to tell Jellyfin what each file is.

## Architecture

```
config_path(argv)         → Path | None      --config / $SORT2OWN_CONFIG / XDG default
load_config(path, known)  → dict             TOML → argparse defaults; fatal on typos
parse_args(argv)          → Namespace        built-in < config < env < CLI
scan(src)                 → List[Title]      ffprobe every .mkv (duration, size, chapters, tags, audio)
match_keyword(text)       → folder | None    disc wording (DE/EN) → a Jellyfin extras folder
type_extra(t, plan, main) → (folder, sure, why)  what kind of extra this is, and how sure
classify(plan, …)         → mutates Titles   heuristics → kind / extra_type / label / note
  _classify_movie()                          longest (or closest to --runtime) = MAIN
                                             ≥ --version-ratio of main → VERSION
                                             < --min-extra → SKIP
                                             duplicates (±--dup-seconds and ±--dup-tolerance size) → SKIP
  _classify_tv()                             titles within ±25 % of median → EPISODE, numbered from plan.start_episode
                                             a title ≈ the episodes' combined length → SKIP (play-all)
library_root(plan)        → Path             <library>/<plan.folder>
destination(plan, title)  → Path | None      the Jellyfin path for a title
next_episode(plan)        → int              highest SxxEyy in the season folder + 1
sample_hash(path, full)   → str              blake2b over 4×1 MiB blocks + length
verify_copy(...)          → (problem, hash)  size then fingerprint, after every copy
read_manifest(folder)     → (runs, readable) parses .sort2own.json; never raises
mark_already_placed(plan) → int              runs AFTER classify, BEFORE the TUI; re-placement → SKIP
reuse_decisions(plan, folder) → int          adopt another folder's kind/type/label choices
recorded_naming(action)   → (label, type)    explicit fields, else read back off the destination
undo(folder, run, all, dry) → exit code      reverses recorded runs; refuses anything it cannot prove is ours
open_in_player(path)      → problem | None   xdg-open, detached and silenced, for the TUI's `p`
run_tui(plan)             → bool             curses editor over the plan (only when stdin/stdout are TTYs and no --yes)
resolve_method(...)       → mode | None      settles hardlink/copy/move; None aborts
copy_file(src, dst, show)                    chunked copy with progress on stderr
execute(plan, mode, dry, full) → Outcome     hardlink | copy | move, never overwrite, writes the manifest
jellyfin_refresh(url, key) → problem | None  POST /Library/Refresh; advisory, never fatal
apply_release_hints(plan, ref, apply)        OFDb names onto extras; unique length match only
```

`hints_ofdb.py` exposes one function: `lookup(ref, cache) -> [(name, seconds|None)]`.

Dataclasses: `Title` (one file plus what has been decided about it), `Plan`
(name, library, tv, season, titles, provider_id, supplement, main_duration,
start_episode, disc_label), `Outcome` (placed, failures).
Kinds: `main | version | extra | episode | skip`.

`Title` also carries what the disc said about it — `audio_titles`,
`audio_langs`, `chapter_titles` — all from the one `ffprobe()` call. A
`stream=` selector inside `-show_entries` pulls the streams in by itself: do
not add `-show_streams`, and do not add a second probe.

The last four `Plan` fields are how multi-disc works. They are set in `main()`
before `classify()`, so classification stays free of I/O:
`supplement`/`main_duration` say "the feature is already in the library, this
disc only adds to it", and `start_episode` seeds `_classify_tv`'s numbering.

`Plan.folder` composes `safe_name(name)` with the provider ID
(`Title (Year) [tmdbid-12345]`). The ID is deliberately *not* folded into
`plan.name`, because `useful_tag()`'s year regex and film-name comparison
operate on the bare title.

## Invariants

These are the properties the tool is built to guarantee. Changing one is a
design decision, not a refactor.

- **Non-destructive.** The source directory is never modified. The default is
  hardlink; `--copy` and `--move` are opt-in. Destinations are never
  overwritten — `unique()` adds ` (2)`. Skipped titles are left in place.

- **Unattended mode refuses to guess the film name.** `--yes` requires
  `--name "Title (Year)"`. A folder named after the disc label is exactly what
  gets misidentified.

- **Hardlinking needs the same filesystem.** `resolve_method()` compares
  `st_dev`; in the TUI it asks, and under `--yes` it exits 1 unless `--copy` or
  `--move` was explicit. It never silently falls back — whether to run on the
  NAS or copy by hand is the operator's call. It returns the method actually
  used, so consenting to a fallback reports `method: copy`: a run that copies
  every file must not announce itself as a hardlink run.

- **An identical file already at the destination is adopted, not resent.**
  `execute()` verifies it against the source with the same size-and-fingerprint
  check that accepts a fresh copy, records it with `method: "adopted"`, and
  moves on — 4 MiB read instead of 31 GB over the network. This covers a folder
  assembled by hand, a lost manifest, or a run that died half way. `undo()`
  leaves adopted files alone: they were there before the run, so putting things
  back means not touching them. `--force` bypasses adoption.

- **Metadata copying is best effort.** `carry_over_metadata()` wraps
  `shutil.copystat`, because a gvfs/SMB share can refuse `chmod` outright
  (errno 95), and the raise once killed a run *after* 31 GB had landed, leaving
  an orphaned file with no manifest entry. Timestamps are retried on their own;
  mode bits are decoration and must never cost a transfer.

- **Big copies show progress.** `copy_file()` replaces `shutil.copy2` so a
  30 GB remux over SMB is not several silent minutes. Progress goes to stderr,
  so redirected stdout stays a clean record, and starts only after half a
  second so quick copies never flicker. `--verify full` on a huge file is still
  silent, and would want the same treatment.

- **The manifest.** `<movie folder>/.sort2own.json` is a list of runs, each
  with `when`, `version` (the `__version__` that laid the folder out — runs
  written before 1.0.0 have no such field, so read it with `.get()`), `name`,
  `tv`, `provider_id`, `disc`, and `actions[]` of `{source, destination, kind,
  method (hardlink/copy/move/adopted), note, size, duration, sample_hash,
  full_hash, extra_type, label}`, plus `skipped[]` of `{source, kind, note,
  size, duration}` — deliberate skips, so `--reuse-from` can honour them.
  `undo()` reads only `actions`. Changes are additive only, and readers use
  `.get()` so older manifests keep working. An unreadable manifest is never
  overwritten: `execute()` renames it to `.sort2own.json.corrupt` first.

- **Re-runs are idempotent.** `mark_already_placed()` marks any title an
  earlier run placed here as SKIP, matching on inode first and on exact
  `(size, duration)` second. It first drops any recorded action whose
  destination no longer exists — "already placed" has to mean *is still there*,
  or deleting an extra would skip it forever instead of restoring it. Both
  tests are exact on purpose: a false positive silently drops a real title,
  which is worse than the duplicate a miss creates. `--force` bypasses the
  whole check.

- **Undo refuses when unsure.** It deletes a placed file only when it can prove
  that file is the one it put there — same inode for a hardlink, the recorded
  size *and* `sample_hash` for a copy — and that the source still exists.
  Anything else is reported and left alone. It never touches a file the
  manifest does not list, and it only ever `rmdir`s directories that are
  already empty. The containment check that keeps a doctored manifest from
  reaching outside the folder resolves both paths first: `Path.relative_to` is
  lexical, and treats `<folder>/../../elsewhere` as inside `<folder>`.

- **Duplicate detection errs tight.** Two titles are the same content only if
  their lengths agree within `--dup-seconds` *and* their sizes within
  `--dup-tolerance`. Both windows are narrow by default (0.2 s, 0.1 %) because
  a duplicate is the same frames remuxed: identical duration, size differing
  only by container overhead. Looser settings have destroyed real content
  twice — at 1 % of runtime it ate three of four episodes on a TV disc, and at
  2 s with 1 % of size it dropped two extras from a film disc whose trailer
  reel carries clips 1.5 s and 1.6 MB apart. A false positive destroys a real
  title; a false negative only leaves a visible extra copy.

  Do not "improve" this with `sample_hash()`. Measured: the same content
  remuxed twice has different bytes and a different size, so genuine duplicates
  would stop matching. Duration and chapter timestamps survive a remux exactly;
  bytes do not.

- **Paths are filesystem paths, never URLs.** `local_path()` rejects anything
  matching `scheme:/`. pathlib treats `smb://nas/movies` as a *relative* path,
  so without the guard it resolved against the working directory and the rip
  landed in a folder literally named `smb:` — created silently, no error. A
  network share has to be mounted (fstab or a systemd `.mount`) and the mount
  point given instead. The regex matches the collapsed `smb:/`, because pathlib
  eats the second slash before argparse hands the Path over.

- **The manifest is also the answers.** `--reuse-from FOLDER` adopts the kind,
  extras folder and label decided for another folder, matched on content the
  same way as the already-placed check. Sorting one rip into a second library —
  a local trial, then the NAS — must not mean re-answering questions that took
  watching the file to settle. `recorded_naming()` reads older manifests back
  off the destination path, since an extra's folder *is* its type and its file
  name *is* its label.

- **Title tag hygiene.** `useful_tag()` discards an MKV title tag that merely
  repeats the film name, because MakeMKV writes the disc label into every title
  on some discs. Use it wherever a tag is turned into a name.

## Jellyfin naming

Verified against jellyfin.org's documentation. These drive `destination()` and
any future naming work.

- Movie folder `Title (Year)/`, main file `Title (Year).mkv`. The file name
  must equal the folder name.
- **Versions:** files in the same folder whose name starts with the folder name
  followed by ` - <label>`, e.g. `Title (Year) - Director's Cut.mkv`. Jellyfin
  orders versions alphabetically unless the label ends in `p` or `i`
  (resolution), in which case highest first. The first version is the default.
- **Provider IDs:** `Title (Year) [tmdbid-12345]` or `[imdbid-tt1234567]` in
  the folder name — square brackets, `tmdbid-`/`imdbid-`/`tvdbid-` prefix.
  Plex's `{tmdb-…}` braces are *not* read by Jellyfin.

  Because version and main file names must begin with the *exact* folder name,
  adding `[tmdbid-…]` to the folder means the files carry it too:
  `Title (Year) [tmdbid-12345].mkv`. `destination()` derives those names from
  `Plan.folder`; `tests/test_provider_id.py` covers it.
- **Extras by folder:** subfolders of the movie folder named exactly `extras`,
  `trailers`, `featurettes`, `behind the scenes`, `deleted scenes`,
  `interviews`, `scenes`, `shorts`, `clips`, `samples`, `other`. File names
  inside are free. `extras` is the untyped catch-all.
- **Extras by suffix** (`Title-trailer.mkv`, `-featurette`, `-deleted`, no
  spaces) also works but is not used here — the folder method keeps file names
  free.
- **TV:** `Show (Year)/Season 01/Show (Year) S01E01.mkv`. Specials go in
  `Season 00`. A season folder may also contain the extras subfolders above.
  Jellyfin 10.8 had a bug (issue #9773) where `clips` and `other` folders
  inside a season were treated as episodes and `trailers` was ignored; avoid
  those three for TV extras and prefer `extras`, `featurettes`, `interviews`,
  `behind the scenes`.
- Reserved characters that break Jellyfin: `< > : " / \ | ? *`. `safe_name()`
  strips them.
- Two files in the same folder that do not share the folder-name prefix are
  scanned as **separate movies**. That is the original "eleven copies" failure
  mode this tool exists to prevent.

## Testing

Run the suite from the repository root:

```sh
python3 -m pytest -q
```

`tests/conftest.py` puts the repository root on `sys.path`; there is no
packaging step.

- `make_titles` / `make_plan` build `Title`s over **sparse placeholder files**.
  `classify()`, `destination()` and `execute()` never read a file's contents,
  so these need no ffmpeg and the suite runs in about a second. Sizes default
  to `duration × 1000`, so duplicate detection behaves as it does on a real rip.
- `make_rip` shells out to ffmpeg and `pytest.skip`s when ffmpeg is absent, so
  anything that goes through `scan()` or `ffprobe()` belongs in
  `tests/test_scan.py` or behind that fixture. Check both ways after a change:
  `python3 -m pytest -q`, and again with ffmpeg off `PATH`.
- Pages fed to the OFDb parser are hand-written fragments carrying the shapes
  it has to survive. Nothing in the suite touches the network, and no test
  depends on a file that is not in the repository.

Every new feature gets its own `tests/test_<feature>.py`. Prefer the
placeholder fixtures; reach for `make_rip` only when the code under test
actually parses media.

If you need a synthetic rip by hand:

```sh
mk(){ ffmpeg -v error -y -f lavfi -i "color=c=black:s=64x36:r=1" \
      -t "$2" -metadata title="$3" -c:v libx264 -preset ultrafast "Film_t$1.mkv"; }
mk 00 120 ""          # main (longest)
mk 01 5   ""          # junk (< --min-extra)
mk 02 118 "Film"      # version (≥85 % of main; tag == film name → discarded)
mk 03 40  "Trailer"   # extra, named from tag
mk 04 120 ""          # duplicate of t00 (same duration and size)
```

Run it with `--min-extra 30 --dry-run` against a scratch `--library`. To
exercise the cross-filesystem warning, point `--library` at a tmpfs such as
`/dev/shm/lib`.

## Known rough edges

- `curses.getstr()` text entry is crude — no cursor movement. Fine on Linux and
  over SSH; Windows would want a line-prompt fallback. The TUI's `c` avoids it
  where a release listing already knows the candidate names.
- **Escape quits the TUI**, the same as `q`, discarding any choices made during
  the session. Nothing on disk is touched, but the editing is lost.
- Driving the TUI from `pty.fork()` needs **application-mode arrow keys**
  (`\x1bOB`, not `\x1bB`). curses sends smkx at startup, so the normal-mode
  sequence arrives as a bare ESC and quits the program. This catches out
  everyone who writes a TUI test.
- Only `.mkv` is scanned. MakeMKV emits nothing else, so this is deliberate.
- **TV clustering is a ±25 % window around the median**, so a long featurette
  on an episode disc gets numbered as an episode. The TUI's `k` fixes it.
  Tightening the window starts dropping short episodes, which is worse.
- **A movie disc's "play all extras" title can outlast the feature** and so be
  picked as the main feature. `--runtime` is the workaround. A proper fix is
  circular — the extras are not known until the main is chosen — and would put
  the tool's most important decision at risk to catch a rarer case. The
  play-all rule in `_classify_tv` works only because episodes are identified
  first.
- A failed transfer part way through a run raises before the manifest is
  written, so files already placed are invisible to `--undo`. The manifest
  write is not atomic either. Tracked as an issue.

## Related tools

None of these classify or name disc extras, which is the gap `sort2own` fills.

- **Automatic Ripping Machine (ARM)** — detects disc insertion via udev, looks
  the title up, rips with MakeMKV, and can put non-main titles into an
  `extras/` subfolder (`MAINFEATURE: false`, `EXTRAS_SUB: extras`). It does not
  classify or name them.
- **jellyrip** — interactive CLI; longest title is the main feature, the rest
  go to `extras/` named by duration.
- **makemkv-auto-rip** — web UI with main-title detection, no extras handling.
- **FileBot / tinyMediaManager** — not open source, and neither identifies disc
  extras either.

## ARM integration

ARM runs scripts on job completion; the hook receives the job's output path and
the looked-up title and year. The intended invocation is:

```sh
sort2own.py "$ARM_OUTPUT_DIR" --yes --name "$ARM_TITLE ($ARM_YEAR)" \
    --config /etc/sort2own/config.toml
```

Leave ARM's own `EXTRAS_SUB` unset, so `sort2own` sees a flat set of MKVs.
Check ARM's current hook variable names before wiring this up — they have
changed between ARM versions.
