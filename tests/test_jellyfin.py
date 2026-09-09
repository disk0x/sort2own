"""Item 1 — asking Jellyfin to scan after a run."""

import http.server
import socket
import threading

import pytest

import sort2own

KEY = "s3cret-api-key"


class Recorder(http.server.BaseHTTPRequestHandler):
    status = 204
    seen = []

    def do_POST(self):
        Recorder.seen.append({"path": self.path,
                              "auth": self.headers.get("Authorization")})
        self.send_response(Recorder.status)
        self.end_headers()

    def log_message(self, *args):
        pass                      # keep the test output clean


@pytest.fixture
def server():
    """A throwaway Jellyfin stand-in; yields (url, requests it received)."""
    Recorder.seen = []
    Recorder.status = 204
    httpd = http.server.HTTPServer(("127.0.0.1", 0), Recorder)
    # Without a short poll interval every shutdown() costs half a second.
    thread = threading.Thread(target=httpd.serve_forever, args=(0.01,),
                              daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}", Recorder
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def dead_url():
    """A port with nothing listening, for the connection-refused path."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


# --- the request itself ----------------------------------------------------

def test_a_refresh_posts_to_the_library_endpoint(server):
    url, rec = server
    assert sort2own.jellyfin_refresh(url, KEY) is None
    assert len(rec.seen) == 1
    assert rec.seen[0]["path"] == "/Library/Refresh"


def test_the_authorization_header_has_the_shape_jellyfin_wants(server):
    url, rec = server
    sort2own.jellyfin_refresh(url, KEY)
    auth = rec.seen[0]["auth"]
    assert auth.startswith("MediaBrowser ")
    assert f'Token="{KEY}"' in auth
    assert 'Client="sort2own"' in auth          # some versions require it


def test_a_trailing_slash_on_the_url_is_tolerated(server):
    url, rec = server
    sort2own.jellyfin_refresh(url + "/", KEY)
    assert rec.seen[0]["path"] == "/Library/Refresh"


def test_a_200_is_accepted_as_well_as_204(server):
    url, rec = server
    rec.status = 200
    assert sort2own.jellyfin_refresh(url, KEY) is None


# --- failures are advisory -------------------------------------------------

def test_an_http_error_is_reported_not_raised(server):
    url, rec = server
    rec.status = 401
    assert sort2own.jellyfin_refresh(url, KEY) == "HTTP 401"


def test_an_unreachable_server_is_reported_not_raised(dead_url):
    assert sort2own.jellyfin_refresh(dead_url, KEY, timeout=2) is not None


def test_a_key_that_cannot_go_in_a_header_is_refused(server):
    url, rec = server
    problem = sort2own.jellyfin_refresh(url, 'bad"key')
    assert "cannot go in a header" in problem
    assert rec.seen == []                        # nothing was sent


# --- wiring into a run -----------------------------------------------------

def run(src, library, url, *extra):
    return sort2own.main([str(src), "--library", str(library), "--yes",
                          "--name", "Test Film (2024)", "--copy",
                          "--min-extra", "3", "--jellyfin-url", url,
                          "--jellyfin-key", KEY, *extra])


def test_placing_files_triggers_a_scan(make_rip, src_dir, library, server):
    url, rec = server
    make_rip([{"duration": 20}])
    assert run(src_dir, library, url) == 0
    assert len(rec.seen) == 1


def test_a_dry_run_never_touches_the_server(make_rip, src_dir, library, server):
    url, rec = server
    make_rip([{"duration": 20}])
    run(src_dir, library, url, "--dry-run")
    assert rec.seen == []


def test_a_second_run_that_places_nothing_does_not_scan(make_rip, src_dir,
                                                        library, server):
    url, rec = server
    make_rip([{"duration": 20}])
    run(src_dir, library, url)
    assert len(rec.seen) == 1
    run(src_dir, library, url)                   # everything already placed
    assert len(rec.seen) == 1


def test_an_undo_also_triggers_a_scan(make_rip, src_dir, library, server):
    url, rec = server
    make_rip([{"duration": 20}])
    run(src_dir, library, url)
    folder = library / "Test Film (2024)"
    assert sort2own.main(["--undo", str(folder), "--jellyfin-url", url,
                          "--jellyfin-key", KEY]) == 0
    assert len(rec.seen) == 2


def test_a_failed_refresh_leaves_the_exit_code_alone(make_rip, src_dir,
                                                     library, dead_url):
    """The files are already placed; a missed scan is not a failed run."""
    make_rip([{"duration": 20}])
    assert run(src_dir, library, dead_url) == 0
    assert (library / "Test Film (2024)" / "Test Film (2024).mkv").exists()


def test_no_url_configured_means_no_scan_and_no_noise(make_rip, src_dir,
                                                      library, capsys):
    make_rip([{"duration": 20}])
    sort2own.main([str(src_dir), "--library", str(library), "--yes", "--copy",
                   "--name", "Test Film (2024)", "--min-extra", "3"])
    out = capsys.readouterr()
    assert "Jellyfin" not in out.out + out.err


def test_a_url_without_a_key_says_so_rather_than_failing(make_rip, src_dir,
                                                         library, capsys):
    make_rip([{"duration": 20}])
    code = sort2own.main([str(src_dir), "--library", str(library), "--yes",
                          "--copy", "--name", "Test Film (2024)",
                          "--min-extra", "3", "--jellyfin-url", "http://x"])
    assert code == 0
    assert "no API key configured" in capsys.readouterr().err


# --- the key must not leak -------------------------------------------------

def test_the_key_never_appears_in_output(make_rip, src_dir, library, server,
                                         capsys):
    url, rec = server
    make_rip([{"duration": 20}])
    run(src_dir, library, url)
    out = capsys.readouterr()
    assert KEY not in out.out
    assert KEY not in out.err


def test_the_key_never_appears_in_output_on_failure(make_rip, src_dir, library,
                                                    dead_url, capsys):
    make_rip([{"duration": 20}])
    run(src_dir, library, dead_url)
    out = capsys.readouterr()
    assert "Jellyfin refresh failed" in out.err
    assert KEY not in out.out
    assert KEY not in out.err
