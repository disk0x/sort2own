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

## Naming the extras

Where MakeMKV kept the disc's own labels, `sort2own` reads them and files each
extra by type — in German or English:

```
Deutscher Trailer            -> trailers/
Die Story                    -> featurettes/
Hinter den Kulissen          -> behind the scenes/
Musikvideo EINE BAND …       -> scenes/
Entfallene Szenen            -> deleted scenes/
```

A title the length of the feature carrying an audio track named
*Audiokommentar* becomes a Jellyfin version called "Commentary" rather than an
extra, which is what it actually is.

Only labels the disc itself supplies are acted on. Weak signals — an extra
whose audio is in a language the feature doesn't have, say — are shown in the
TUI marked `?` and left in `extras/` until you accept them with `e`. A wrong
guess applied silently is worse than no guess.

### When the disc kept no labels at all

Most discs don't. Point `--ofdb` at the release's page on
[OFDb](https://www.ofdb.de/), which lists each extra with its runtime to the
second, and the names are matched to your files by length:

```bash
./sort2own.py ~/rips/A_FILM --name "A Film (2024)" \
  --ofdb 123456,789012
```

```
150s  ->  featurettes/Die Story.mkv
152s  ->  trailers/Trailer.mkv
172s  ->  scenes/Musikvideo EINE BAND "Ein Lied".mkv
137s  ->  OFDb: could be Behind the Scenes / Erster Entwurf
```

A name is applied only when exactly one listing matches within a second.
Discs routinely include a trailer reel whose entries share runtimes with the
real extras, so the rest are marked `?` in the TUI rather than guessed at.

For those, the quickest way to decide is to look: press `p` to open the file
in your media player, watch a couple of seconds, then `c` to pick from the
names it could be.

```
Which is A Film_t02.mkv?  (0:02:16, 0.26G)
  1  Behind the Scenes
  2  Erster Entwurf
  0  leave it as "Extra 2m16s"
```

Unattended runs report the matches but change nothing unless you add
`--ofdb-apply`.

This lives in `hints_ofdb.py`, apart from the main script, because it parses
someone else's HTML and that will break one day. Delete the file and
everything else still works.

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
- Existing files at the destination are **never overwritten** — a colliding
  name gets a `(2)` suffix instead. A file that already matches the source
  byte-for-byte is adopted rather than sent again, so an interrupted transfer
  can just be re-run.
- Every run is logged to a `.sort2own.json` manifest in the movie folder,
  so you can see exactly what was placed and how.
- **Running it twice does nothing the second time.** Anything an earlier run
  already placed is skipped, so re-running after adding one more title never
  duplicates the rest. `--force` overrides.
- **Every copy is checked** against its source on size and a sampled hash. A
  copy that does not match is deleted rather than left in your library, and
  the run exits non-zero so a script notices.
- **`--undo` reverses a run**, using the manifest. It removes a file only
  when it can prove it put it there, so anything you have edited or replaced
  since is reported and left alone.
- `--dry-run` shows the full plan and writes nothing.

`--move` is available if you'd rather relocate the files outright, but the
default is designed so a mistake costs you nothing.

## Install

Requires **Python 3.11+** and **ffprobe** (part of [ffmpeg](https://ffmpeg.org/)).
No other dependencies.

It is deliberately a single file — copy `sort2own.py` to your NAS or into a
container and run it; there is nothing to install.

```bash
git clone https://github.com/disk0x/sort2own.git
cd sort2own
chmod +x sort2own.py
```

If you also want `hints_ofdb.py` (optional OFDb extras naming), keep it in the
same directory as `sort2own.py` — it is imported by name, and nothing else
changes if it is absent.

### Installing it properly

```bash
sudo make install              # /usr/share/sort2own + /usr/bin/sort2own
make install PREFIX=~/.local   # or a home install, no root needed
sudo make uninstall
```

### Fedora / RPM

```bash
make dist
rpmbuild -ta sort2own-*.tar.gz
sudo dnf install ~/rpmbuild/RPMS/noarch/sort2own-*.rpm
```

The package is `noarch` and depends on `python3 >= 3.11` and `/usr/bin/ffprobe`
— a file dependency, so either RPM Fusion's `ffmpeg` or Fedora's own
`ffmpeg-free` satisfies it.

`sort2own --version` reports the build, and every run records it in the
manifest, so a folder sorted months ago can say what laid it out.

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
| `p` | play the selected file in your default media player |
| `c` | choose from the candidate names for this file |
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

## Configuration

Anything you would otherwise retype every run can live in
`~/.config/sort2own/config.toml` (or wherever `--config` / `$SORT2OWN_CONFIG`
points):

```toml
library = "/tank/media/movies"
tv_library = "/tank/media/tv"      # shows usually live in their own library
min_extra = 30

[jellyfin]
url = "http://jellyfin.nas.example:8096"
key_file = "~/.config/sort2own/jellyfin.key"   # keeps the key out of the config
```

With that in place the earlier example shortens to:

```bash
./sort2own.py ~/rips/A_FILM --yes --name "A Film (2024)"
```

A command-line flag beats an environment variable, which beats the config
file, which beats the built-in default. A misspelt setting is an error rather
than being quietly ignored — a typo that silently does nothing is worse than
one that stops you.

> **Running from a Flatpak or Snap terminal?** Those set `XDG_CONFIG_HOME` to
> a sandboxed directory, so `~/.config/sort2own/config.toml` is not where the
> tool will look and your settings appear to be ignored. Check with
> `echo $XDG_CONFIG_HOME`; if it points somewhere unexpected, pass
> `--config ~/.config/sort2own/config.toml` or export
> `SORT2OWN_CONFIG=~/.config/sort2own/config.toml`.

## Getting the film identified correctly

Remakes, films sharing a title, and non-English releases are the usual
misidentifications. Pass the ID and Jellyfin has nothing to guess about:

```bash
./sort2own.py ~/rips/A_FILM --yes \
  --name "A Film (2024)" --tmdb 112233
```

which produces `A Film (2024) [tmdbid-112233]/`. `--imdb tt…` works
the same way.

## Box sets and second discs

A season spread over several discs is several MakeMKV runs. `--continue`
carries the numbering on from whatever is already in the season folder:

```bash
./sort2own.py ~/rips/DISC1 --yes --tv --name "A Series (2016)"
./sort2own.py ~/rips/DISC2 --yes --tv --name "A Series (2016)" --continue
```

giving `S01E01`–`S01E03` and then `S01E04`–`S01E06`. It reads the file names
in the folder, not its own records, so episodes you put there by hand are
counted too. `--start-episode N` sets the number yourself.

A disc that carries a "play all" title — every episode end to end — has it
skipped rather than filed as a mystery extra.

For a film's second disc of extras, `--supplement` places everything as
extras or alternate cuts and does not go looking for a feature:

```bash
./sort2own.py ~/rips/DISC2 --yes --name "A Film (2024)" \
  --supplement --disc-label "Disc 2"
```

Alternate cuts are judged against the feature already in the folder, so a
110-minute cut next to a 120-minute feature is recognised as a version
rather than an extra. Anything the first disc already contributed — the
trailer, typically — is skipped instead of placed twice.

## Not answering the same questions twice

Every run records what it decided — kind, extras folder, and the name of each
file — in the `.sort2own.json` alongside the results. `--reuse-from` reads
those decisions back:

```bash
# try it locally, settle the ambiguous extras in the TUI
./sort2own.py ~/rips/A_FILM --library ~/Videos/Sorted --name "A Film (2024)"

# then the real thing, keeping every choice you made
./sort2own.py ~/rips/A_FILM --library /mnt/movies --name "A Film (2024)" \
  --reuse-from "~/Videos/Sorted/A Film (2024)"
```

Titles are matched by content, not by file name, so a re-rip of the same disc
is recognised too. Deliberate skips are remembered along with the placements.

Re-running against the *same* library needs none of this — anything already
there is skipped, and anything since deleted is put back.

A file that is already sitting at its destination and matches the source
byte-for-byte is adopted rather than transferred again, even if `sort2own` has
no record of putting it there. So a run interrupted halfway through a large
copy can simply be repeated: it picks up where it left off instead of sending
30 GB over the network a second time.

## Undoing a run

```bash
./sort2own.py --undo "/media/movies/A Film (2024)"
```

Reverses the most recent run recorded in that folder — `--run N` picks an
earlier one, `--all` reverses everything, and `--dry-run` shows what would
go without touching anything. It deletes a file only when it can still prove
that file is the one it placed: same inode for a hardlink, matching size and
fingerprint for a copy, and the source still present. A file you have since
re-encoded or replaced is reported and left where it is.

## Options

| Flag | Default | Meaning |
|---|---|---|
| `--library PATH` | `/media/movies` | root of the Jellyfin library |
| `--tv-library PATH` | — | separate root used when `--tv` is passed |
| `--config PATH` | `$XDG_CONFIG_HOME/sort2own/config.toml` | config file |
| `--reuse-from FOLDER` | — | adopt the kinds, types and names decided for FOLDER |
| `--name "Title (Year)"` | guessed from the source folder | required with `--yes` |
| `--tmdb ID` / `--imdb ID` | — | pin the film, e.g. `[tmdbid-112233]` in the folder name |
| `--tv` | off | treat the disc as TV episodes |
| `--season N` | `1` | season number in TV mode |
| `--continue` | — | with `--tv`: carry on numbering after the episodes already there |
| `--start-episode N` | — | with `--tv`: number this disc's first episode N |
| `--supplement` | — | a film's further disc: everything is an extra or an alternate cut |
| `--disc-label TEXT` | — | note recorded in the manifest, e.g. `"Disc 2"` |
| `--runtime MIN` | — | pick the title closest to this runtime as the main feature, instead of the longest |
| `--min-extra SEC` | `90` | titles shorter than this are skipped as junk |
| `--version-ratio X` | `0.85` | titles at least this fraction of the main length become alternate versions |
| `--dup-tolerance X` | `0.001` | size tolerance (0.1%) for duplicate detection |
| `--dup-seconds SEC` | `0.2` | how close two lengths must be to count as the same content |
| `--copy` | — | always copy, never hardlink |
| `--move` | — | move files instead of linking |
| `--verify sample\|full` | `sample` | how thoroughly to check each copy; `full` hashes every byte |
| `--force` | — | place titles even if an earlier run already placed them |
| `--undo FOLDER` | — | reverse what an earlier run placed there |
| `--run N` / `--all` | last run | with `--undo`: which run(s) to reverse |
| `--jellyfin-url URL` | — | ask this server to scan after the run |
| `--jellyfin-key KEY` | — | its API key (prefer `jellyfin.key_file` in the config) |
| `--yes`, `-y` | — | unattended mode; apply the heuristics without opening the TUI |
| `--dry-run` | — | print the plan, write nothing |

Environment overrides: `SORT2OWN_LIBRARY`, `SORT2OWN_TV_LIBRARY`,
`SORT2OWN_JELLYFIN_URL`, `SORT2OWN_JELLYFIN_KEY`, `SORT2OWN_CONFIG`.

## NAS shares

Paths are filesystem paths, so a network share has to be mounted first —
`smb://nas/movies` is rejected rather than quietly resolved into a local
folder named `smb:`.

**A desktop (gvfs) mount works.** If you have the share open in your file
manager, it is already a real path and can be used as-is:

```
/run/user/1000/gvfs/smb-share:server=nas.example,share=media/media/movies
```

**A system (fstab) mount is better for anything automated**, because a gvfs
mount only exists while your desktop session does — cron jobs, systemd units
and containers will not see it:

```
//nas/movies  /mnt/movies  cifs  credentials=/root/.smbcred,uid=1000,gid=1000,_netdev  0  0
```

Either way the share is a *different filesystem* from your local rip folder,
so hardlinking is impossible and `sort2own` says so rather than silently
copying — see below. Over gvfs specifically, `link()` fails outright
(`Operation not permitted`) no matter where the source lives, so `--copy` or
`--move` is required.

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
└── Title (Year) [tmdbid-12345]/               ← ID optional, see above
    ├── Title (Year) [tmdbid-12345].mkv
    ├── Title (Year) [tmdbid-12345] - Director's Cut.mkv   ← alternate version
    ├── trailers/
    ├── featurettes/
    ├── deleted scenes/
    ├── extras/                                ← generic, untyped bucket
    └── .sort2own.json                         ← what was placed, and how
```

Version files have to begin with the *exact* folder name, provider ID
included — otherwise Jellyfin scans them as separate films.

See Jellyfin's own docs for the full spec:
[Movies](https://jellyfin.org/docs/general/server/media/movies/) ·
[TV Shows](https://jellyfin.org/docs/general/server/media/shows/)

## Limitations

- Extras are typed generically (`extras/`) unless MakeMKV preserved a
  usable title tag — there's no reliable way to tell a trailer from a
  making-of by duration alone. Use the TUI to reclassify by hand for discs
  you care about.
- Only `.mkv` input is supported (MakeMKV's only output format).
- Episodes are found by clustering around the median length, so a long
  featurette on an episode disc gets numbered as an episode. Press `k` in
  the TUI to correct it.
- On a film disc, a "play all extras" title longer than the feature itself
  will be taken for the feature. Pass `--runtime` to settle it.
- `--tv-library` is decided from the command line, so toggling `t` inside
  the TUI switches the layout but not the library root.

## Related tools

For fully hands-off ripping (disc detection, ejection, MakeMKV
invocation), pair `sort2own` with
[Automatic Ripping Machine](https://github.com/automatic-ripping-machine/automatic-ripping-machine)
or a similar tool — `sort2own` is deliberately just the sorting step, so it
composes with whatever rips the disc.

## Development

```bash
python3 -m pytest -q          # the test suite
make check                    # the same thing
```

The tests need no media and no network: files are sparse placeholders, and the
few cases that genuinely parse video generate their own clips with ffmpeg and
skip when ffmpeg is absent. `DESIGN.md` covers the architecture, the invariants
a change has to preserve, and the Jellyfin naming rules behind `destination()`.

Written with AI assistance, reviewed by a human, and covered by the test suite.

## License

[AGPLv3](LICENSE). If you run a modified version of `sort2own` as a
network service (unlikely for a local sorting script, but the obligation
exists), you must make the modified source available to users of that
service — see the license for the exact terms.

© 2026 dkr ([github.com/disk0x](https://github.com/disk0x)).

Jellyfin, MakeMKV, OFDb, TMDB and IMDb are trademarks of their respective
owners. This project is not affiliated with or endorsed by any of them.
