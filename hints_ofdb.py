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
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Optional, Tuple

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

    The page labels each section with a bold heading and follows it with a
    sibling div of single-item lists:

        <b>Extras:</b> … <div class="… fassung-absatz">
            <ul><li>Die Story (2:30 Min.)</li></ul>
            <ul><li>Trailershow</li></ul>
            <ol><li>Erster Entwurf (2:17 Min.)</li>…</ol>

    Rather than track that div nesting — which is malformed anyway, the page
    never closes its <p> tags — this waits for the "Extras:" heading, takes
    every <li> after it, and stops at the next heading.
    """

    def __init__(self) -> None:
        super().__init__()
        self.items: List[str] = []
        self._heading: Optional[List[str]] = None
        self._item: Optional[List[str]] = None
        self._collecting = False
        self._finished = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if self._finished:
            return
        if tag == "b":
            self._heading = []
        elif tag == "li" and self._collecting:
            self._item = []

    def handle_endtag(self, tag: str) -> None:
        if self._finished:
            return
        if tag == "b" and self._heading is not None:
            heading = "".join(self._heading).strip().rstrip(":").casefold()
            self._heading = None
            if heading == "extras":
                self._collecting = True
            elif self._collecting:
                self._finished = True        # next section — stop here
        elif tag == "li" and self._item is not None:
            text = re.sub(r"\s+", " ", "".join(self._item)).strip()
            if text:
                self.items.append(text)
            self._item = None

    def handle_data(self, data: str) -> None:
        if self._heading is not None:
            self._heading.append(data)
        elif self._item is not None:
            self._item.append(data)


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
                return [(name, secs) for name, secs in json.loads(cache.read_text())]
            except (OSError, ValueError):
                pass                          # unreadable cache: just refetch

    listings = parse(fetch(ident))
    if cache is not None and listings:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(listings, ensure_ascii=False))
        except OSError:
            pass                              # a cache we cannot write is fine
    return listings
