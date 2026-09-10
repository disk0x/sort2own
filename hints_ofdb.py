#!/usr/bin/env python3
"""
Extras names from OFDb.de — an optional sidecar for sort2own.

MakeMKV gives extras no names, and Jellyfin has no metadata source for them,
so on a disc whose titles carry no tags there is nothing to go on but
duration. OFDb records what each release's extras are called *and* how long
each one runs, to the second, which is enough to put a name to a ripped file.

This lives outside sort2own.py on purpose. It parses someone else's HTML,
which will change without warning; sort2own imports it in a try/except and
carries on without hints when it is missing or broken. Nothing here is
required for sorting a disc.

Only factual data is used — the extras' names and runtimes — which OFDb's FAQ
explicitly permits reusing. Reviews and other authored text are left alone.
One page is fetched per run and cached.
"""

from __future__ import annotations

import json
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Optional, Tuple

# Bump whenever the parser changes what it extracts. Cached results are keyed
# on it, so a fix reaches anyone who already looked a release up — otherwise a
# bad parse is served from disk forever and looks like the bug was never fixed.
CACHE_VERSION = 2

BASE = "https://www.ofdb.de/fassung/"
USER_AGENT = "sort2own (personal media sorter; one page per run, cached)"
TIMEOUT = 15.0

# "Die Story (2:30 Min.)" → the runtime. Deliberately not anchored to the end
# of the line: entries often carry a footnote marker after it, as in
# "Filming Zone (32:02 Min.) **", and anchoring silently matched nothing.
RUNTIME = re.compile(r"\((\d+):(\d{2})\s*Min\.?\)")

# The id pair in an OFDb Fassung URL: /fassung/123456,789012,A-Film/
REFERENCE = re.compile(r"(\d+),(\d+)")

Listing = Tuple[str, Optional[int]]      # (name, seconds or None)


class _ExtrasParser(HTMLParser):
    """
    Pull the Extras list out of a Fassung page.

    There is no consistent markup for an extra, so this does not look for one.
    Releases differ wildly:

        <b>Extras:</b> … <div class="… fassung-absatz">      ← the block
            <strong>Featurettes:</strong>                     ← a sub-heading
            <ul><li>Filming Zone (32:02 Min.) **</li></ul>    ← a list item
            <strong>Kinotrailer </strong>(1:17 Min.) ***      ← bare text
            <div><em>* = englisch …</em></div>                ← footnote legend

    So: find the block that follows the "Extras:" heading, split its text into
    lines on the block-level tags, and keep a line if it carries a runtime or
    came from a list item. Everything else — sub-headings, the footnote
    legend, stray whitespace — falls away without needing to be recognised.

    Matching on element type instead missed both of that page's bare-text
    entries, and stopping at the next bold heading would have stopped at
    "Featurettes:". The block boundary is the only reliable landmark, so it
    counts div depth rather than guessing.
    """

    BREAKS = {"br", "li", "p", "ul", "ol", "div", "table", "tr", "h1", "h2",
              "h3", "h4"}

    def __init__(self) -> None:
        super().__init__()
        self.items: List[str] = []
        self._heading: Optional[List[str]] = None
        self._awaiting_block = False
        self._depth = 0                  # div nesting inside the extras block
        self._line: List[str] = []
        self._in_item = False
        self._finished = False

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._line)).strip()
        if text and (self._in_item or RUNTIME.search(text)):
            self.items.append(text)
        self._line = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if self._finished:
            return
        if tag == "b":
            self._heading = []
            return
        if self._awaiting_block and tag == "div":
            self._awaiting_block, self._depth = False, 1
            return
        if not self._depth:
            return
        if tag in self.BREAKS:
            self._flush()
        if tag == "div":
            self._depth += 1
        elif tag == "li":
            self._in_item = True

    def handle_endtag(self, tag: str) -> None:
        if self._finished:
            return
        if tag == "b" and self._heading is not None:
            heading = "".join(self._heading).strip().rstrip(":").casefold()
            self._heading = None
            if heading == "extras":
                self._awaiting_block = True
            return
        if not self._depth:
            return
        if tag in self.BREAKS:
            self._flush()
            if tag == "li":
                self._in_item = False
        if tag == "div":
            self._depth -= 1
            if self._depth == 0:
                self._finished = True

    def handle_data(self, data: str) -> None:
        if self._heading is not None:
            self._heading.append(data)
        elif self._depth:
            self._line.append(data)


def split_runtime(text: str) -> Listing:
    """
    "Die Story (2:30 Min.)" → ("Die Story", 150). No runtime → None.

    The name is whatever precedes the runtime; anything after it is a footnote
    marker referring to a note elsewhere on the page, not part of the name.
    """
    found = None
    for found in RUNTIME.finditer(text):
        pass                                  # the last one, if a name has two
    if found is None:
        return text.strip(), None
    minutes, seconds = int(found.group(1)), int(found.group(2))
    return text[:found.start()].strip(), minutes * 60 + seconds


def parse(page: str) -> List[Listing]:
    """Every extra the page lists, in page order, named and timed."""
    parser = _ExtrasParser()
    parser.feed(page)
    return [split_runtime(item) for item in parser.items]


def reference(ref: str) -> str:
    """Normalise a URL or a bare "123456,789012" to the id pair."""
    found = REFERENCE.search(ref)
    if not found:
        raise ValueError(
            f"cannot read an OFDb reference from {ref!r} — give the Fassung "
            f"URL, or the two numbers from it as 123456,789012")
    return f"{found.group(1)},{found.group(2)}"


def fetch(ref: str) -> str:
    request = urllib.request.Request(f"{BASE}{ref}/",
                                     headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read().decode("utf-8", "replace")


def lookup(ref: str, cache_dir: Optional[Path] = None) -> List[Listing]:
    """
    The extras OFDb lists for one release, from cache when we have it.

    Raises on a network or parsing failure; the caller decides that hints are
    optional, not this module.
    """
    ident = reference(ref)
    cache = None
    if cache_dir is not None:
        cache = Path(cache_dir).expanduser() / f"{ident.replace(',', '_')}.json"
        if cache.is_file():
            try:
                stored = json.loads(cache.read_text())
                if stored.get("version") == CACHE_VERSION:
                    return [(name, secs) for name, secs in stored["items"]]
            except (OSError, ValueError, AttributeError, KeyError, TypeError):
                pass                # unreadable or older layout: just refetch

    listings = parse(fetch(ident))
    if cache is not None and listings:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"version": CACHE_VERSION,
                                         "items": listings},
                                        ensure_ascii=False))
        except OSError:
            pass                              # a cache we cannot write is fine
    return listings
