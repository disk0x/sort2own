# sort2own

Sort your [MakeMKV](https://www.makemkv.com/) disc rips into a
[Jellyfin](https://jellyfin.org/)-ready library — automatically, without
guessing which title is the movie and which is a menu loop, and without
touching your original files.

```
A Film (2024)/
├── A Film (2024).mkv          ← main feature, auto-detected
├── extras/
│   ├── Trailer.mkv
│   └── Making of.mkv
└── .sort2own.json                      ← manifest of everything it did
```

## Why

MakeMKV rips a disc into a folder of `Title_t00.mkv`, `Title_t01.mkv`,
`Title_t02.mkv`… with no indication of which one is the film and which are
menu loops, trailers, or making-of featurettes. Drop that straight into a
Jellyfin library and every title gets scanned as its own movie, usually
several of them misidentified as duplicates of the same film.

`sort2own` figures out which title is the main feature, which are
alternate cuts, and which are extras, and lays the result out the way
Jellyfin expects — so a disc you just ripped shows up correctly the first
time.

This is meant for people building a personal, self-hosted collection from
media they physically own — an alternative to relying on streaming
catalogues that can change or disappear. **Only rip discs you own**, and
check that doing so is legal where you live; `sort2own` doesn't touch DRM
or copy protection, it only organizes files MakeMKV has already produced.

## How it decides

- **Main feature** — the longest title, or (with `--runtime`) whichever
  title is closest to the film's real runtime. This protects against discs
  where a "play all extras" title happens to be longer than the film.
- **Alternate cuts** (theatrical vs. director's cut, etc.) — titles at
  least 85% as long as the main feature become Jellyfin
  ["versions"](https://jellyfin.org/docs/general/server/media/movies/),
  grouped with the main feature and switchable in the player.
- **Duplicates** — titles with near-identical duration *and* file size
  (common with multi-angle or multi-language playlists) are skipped.
- **Junk** — titles under 90 seconds (menu loops, logos) are skipped.
- **Extras** — everything else, named from the disc's own title tag when
  MakeMKV preserved one, dropped into an `extras/` subfolder Jellyfin
  recognizes.
- **TV mode** (`--tv`) — titles clustering around the median duration are
  treated as episodes and numbered in disc order; the rest are handled as
  above.

None of this is magic — discs don't carry machine-readable labels for
their extras, so classification is a best-effort guess based on length,
size, and whatever metadata MakeMKV kept. Anything it gets wrong can be
fixed by hand in the interactive mode below.

## Non-destructive

- Your rip folder is **never modified**. Files are hardlinked into place
  by default — instant, and using no extra disk space — or copied if the
  library is on a different filesystem (see [Hardlinks](#hardlinks-and-filesystems)
  below).
- Existing files at the destination are **never overwritten** — a
  colliding name gets a `(2)` suffix instead.
- Every run is logged to a `.sort2own.json` manifest in the movie folder,
  so you can see exactly what was placed and how.
- `--dry-run` shows the full plan and writes nothing.

`--move` is available if you'd rather relocate the files outright, but the
default is designed so a mistake costs you nothing.

## Install

Requires **Python 3.8+** and **ffprobe** (part of [ffmpeg](https://ffmpeg.org/)).
No other dependencies.

```bash
git clone https://github.com/disk0x/sort2own.git
cd sort2own
chmod +x sort2own.py
```

## Usage

### Interactive (recommended for discs you haven't sorted before)

```bash
./sort2own.py ~/rips/A_FILM
```

Opens a terminal UI showing every title's length, size, chapter count,
embedded tag, and the heuristic's proposed destination — so you can check
its guess before anything is written.

| Key | Action |
|---|---|
| `↑` / `↓` | move selection |
| `k` | cycle kind: main / version / extra / episode / skip |
| `e` | cycle the extras subfolder (trailers, featurettes, deleted scenes, …) |
| `l` | edit the label / file name |
| `n` | edit the movie or show title |
| `s` | edit season number (TV mode) |
| `t` | toggle movie / TV mode |
| `Enter` | apply the plan |
| `q` | abort, write nothing |

### Unattended (for scripts, cron, or an ARM post-processing hook)

```bash
./sort2own.py ~/rips/A_FILM \
  --yes --name "A Film (2024)" --runtime 119 \
  --library /media/movies
```

Unattended mode requires `--name` explicitly — a folder named after a raw
disc label is exactly the kind of thing that gets misidentified, so the
script refuses to guess when nobody is there to check it.

TV example:

```bash
./sort2own.py ~/rips/DISC1 --yes --tv --season 2 \
  --name "A Series (2016)" --library /media/tv
```

## Options

| Flag | Default | Meaning |
|---|---|---|
| `--library PATH` | `$SORT2OWN_LIBRARY` or `/media/movies` | root of the Jellyfin library |
| `--name "Title (Year)"` | guessed from the source folder | required with `--yes` |
| `--tv` | off | treat the disc as TV episodes |
| `--season N` | `1` | season number in TV mode |
| `--runtime MIN` | — | pick the title closest to this runtime as the main feature, instead of the longest |
| `--min-extra SEC` | `90` | titles shorter than this are skipped as junk |
| `--version-ratio X` | `0.85` | titles at least this fraction of the main length become alternate versions |
| `--dup-tolerance X` | `0.01` | duration/size tolerance (1%) for duplicate detection |
| `--copy` | — | always copy, never hardlink |
| `--move` | — | move files instead of linking |
| `--yes`, `-y` | — | unattended mode; apply the heuristics without opening the TUI |
| `--dry-run` | — | print the plan, write nothing |

## Hardlinks and filesystems

Hardlinking only works when the source and destination are on the **same
filesystem**. If they're not — for example, ripping to local disk and
pointing `--library` at an SMB-mounted NAS — `sort2own` warns you rather
than silently falling back to a slow, space-doubling copy:

```
WARNING: source and library are on different filesystems, so files
cannot be hardlinked — they would be COPIED instead.
```

In the TUI it asks before proceeding; in unattended mode it exits with an
error unless you pass `--copy` or `--move` explicitly. Your options at
that point:

- run `sort2own` **on the NAS itself** (or point MakeMKV's output at a
  scratch folder that's already on the NAS), so hardlinking applies, or
- accept a real copy with `--copy`, or
- sort into a local folder and copy the *result* to the NAS yourself.

## Jellyfin layout reference

`sort2own` follows Jellyfin's documented conventions:

```
Movies/
└── Title (Year)/
    ├── Title (Year).mkv
    ├── Title (Year) - Director's Cut.mkv     ← alternate version
    ├── trailers/
    ├── featurettes/
    ├── deleted scenes/
    └── extras/                                ← generic, untyped bucket
```

See Jellyfin's own docs for the full spec:
[Movies](https://jellyfin.org/docs/general/server/media/movies/) ·
[TV Shows](https://jellyfin.org/docs/general/server/media/shows/)

## Limitations

- Extras are typed generically (`extras/`) unless MakeMKV preserved a
  usable title tag — there's no reliable way to tell a trailer from a
  making-of by duration alone. Use the TUI to reclassify by hand for discs
  you care about.
- Only `.mkv` input is supported (MakeMKV's only output format).
- TV episode numbering follows disc order; multi-disc box sets currently
  need `--season`/numbering handled per disc.

## Related tools

For fully hands-off ripping (disc detection, ejection, MakeMKV
invocation), pair `sort2own` with
[Automatic Ripping Machine](https://github.com/automatic-ripping-machine/automatic-ripping-machine)
or a similar tool — `sort2own` is deliberately just the sorting step, so it
composes with whatever rips the disc.

## License

[AGPLv3](LICENSE). If you run a modified version of `sort2own` as a
network service (unlikely for a local sorting script, but the obligation
exists), you must make the modified source available to users of that
service — see the license for the exact terms.
