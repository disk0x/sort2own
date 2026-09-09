"""Item 5 — the config file, and the precedence around it."""

import os
from pathlib import Path

import pytest

import sort2own


@pytest.fixture
def write_config(tmp_path):
    """
    Write a config file and return its path. By default it goes to the
    discovery location, which the autouse isolation fixture has pointed at a
    temp directory.
    """
    def _write(text, at_default=True):
        if at_default:
            cfg = Path(os.environ["XDG_CONFIG_HOME"]) / "sort2own" / "config.toml"
        else:
            cfg = tmp_path / "elsewhere.toml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(text)
        return cfg
    return _write


# --- discovery -------------------------------------------------------------

def test_no_config_anywhere_leaves_the_builtin_defaults():
    a = sort2own.parse_args(["/rip"])
    assert a.library == Path("/media/movies")
    assert a.min_extra == 90


def test_the_default_location_is_picked_up(write_config):
    write_config('library = "/tank/movies"\nmin_extra = 30\n')
    a = sort2own.parse_args(["/rip"])
    assert a.library == Path("/tank/movies")
    assert a.min_extra == 30


def test_config_flag_points_somewhere_else(write_config):
    cfg = write_config('library = "/elsewhere"\n', at_default=False)
    a = sort2own.parse_args(["/rip", "--config", str(cfg)])
    assert a.library == Path("/elsewhere")


def test_sort2own_config_env_var_is_honoured(write_config, monkeypatch):
    cfg = write_config('library = "/from/env/path"\n', at_default=False)
    monkeypatch.setenv("SORT2OWN_CONFIG", str(cfg))
    assert sort2own.parse_args(["/rip"]).library == Path("/from/env/path")


def test_a_named_config_that_does_not_exist_is_an_error(tmp_path):
    with pytest.raises(SystemExit):
        sort2own.parse_args(["/rip", "--config", str(tmp_path / "nope.toml")])


# --- precedence ------------------------------------------------------------

def test_cli_beats_env_beats_config(write_config, monkeypatch):
    write_config('library = "/from/config"\n')
    assert sort2own.parse_args(["/rip"]).library == Path("/from/config")

    monkeypatch.setenv("SORT2OWN_LIBRARY", "/from/env")
    assert sort2own.parse_args(["/rip"]).library == Path("/from/env")

    a = sort2own.parse_args(["/rip", "--library", "/from/cli"])
    assert a.library == Path("/from/cli")


def test_env_alone_beats_the_builtin(monkeypatch):
    monkeypatch.setenv("SORT2OWN_LIBRARY", "/from/env")
    assert sort2own.parse_args(["/rip"]).library == Path("/from/env")


# --- types -----------------------------------------------------------------

def test_a_string_path_from_toml_arrives_as_a_path(write_config):
    """
    argparse applies type= to string defaults. The whole scheme rests on it,
    so it is asserted rather than assumed.
    """
    write_config('library = "/tank/movies"\n')
    assert isinstance(sort2own.parse_args(["/rip"]).library, Path)


def test_numbers_keep_their_toml_types(write_config):
    write_config("min_extra = 45\nversion_ratio = 0.9\ndup_tolerance = 0.02\n")
    a = sort2own.parse_args(["/rip"])
    assert a.min_extra == 45
    assert a.version_ratio == 0.9
    assert a.dup_tolerance == 0.02


def test_a_choice_setting_works_from_config(write_config):
    write_config('verify = "full"\n')
    assert sort2own.parse_args(["/rip"]).verify == "full"


# --- errors ----------------------------------------------------------------

def test_broken_toml_is_fatal(write_config):
    write_config("library = [unclosed\n")
    with pytest.raises(SystemExit):
        sort2own.parse_args(["/rip"])


def test_an_unknown_setting_is_named_in_the_error(write_config):
    """A typo that silently does nothing is the failure worth preventing."""
    write_config('libary = "/typo"\n')
    with pytest.raises(SystemExit) as e:
        sort2own.parse_args(["/rip"])
    assert "libary" in str(e.value)


def test_an_unknown_section_key_is_named_too(write_config):
    write_config('[jellyfin]\nurl = "http://x"\nnope = 1\n')
    with pytest.raises(SystemExit) as e:
        sort2own.parse_args(["/rip"])
    assert "jellyfin_nope" in str(e.value)


# --- sections and secrets --------------------------------------------------

def test_a_section_flattens_onto_the_matching_flag(write_config):
    write_config('[jellyfin]\nurl = "http://jellyfin.nas.example:8096"\nkey = "abc"\n')
    a = sort2own.parse_args(["/rip"])
    assert a.jellyfin_url == "http://jellyfin.nas.example:8096"
    assert a.jellyfin_key == "abc"


def test_key_file_is_read_and_stripped(write_config, tmp_path):
    secret = tmp_path / "jellyfin.key"
    secret.write_text("  s3cret\n")
    write_config(f'[jellyfin]\nkey_file = "{secret}"\n')
    assert sort2own.parse_args(["/rip"]).jellyfin_key == "s3cret"


def test_key_and_key_file_together_are_an_error(write_config, tmp_path):
    secret = tmp_path / "jellyfin.key"
    secret.write_text("s3cret")
    write_config(f'[jellyfin]\nkey = "inline"\nkey_file = "{secret}"\n')
    with pytest.raises(SystemExit):
        sort2own.parse_args(["/rip"])


def test_an_unreadable_key_file_is_an_error(write_config, tmp_path):
    write_config(f'[jellyfin]\nkey_file = "{tmp_path / "missing.key"}"\n')
    with pytest.raises(SystemExit):
        sort2own.parse_args(["/rip"])


def test_a_tmdb_key_is_accepted_for_later_use(write_config):
    """Reserved for the TMDB search feature; must not error today."""
    write_config('[tmdb]\nkey = "reserved"\n')
    assert sort2own.parse_args(["/rip"]).library == Path("/media/movies")


# --- tv_library ------------------------------------------------------------

def test_tv_library_is_used_only_for_tv(make_rip, src_dir, tmp_path,
                                        write_config):
    movies, shows = tmp_path / "movies", tmp_path / "shows"
    write_config(f'library = "{movies}"\ntv_library = "{shows}"\n')
    make_rip([{"duration": 20}])

    sort2own.main([str(src_dir), "--yes", "--name", "A Film (2024)",
                   "--copy", "--min-extra", "3"])
    assert (movies / "A Film (2024)").is_dir()
    assert not shows.exists()

    sort2own.main([str(src_dir), "--yes", "--tv", "--name", "A Show (2024)",
                   "--copy", "--min-extra", "3"])
    assert (shows / "A Show (2024)").is_dir()


def test_without_tv_library_tv_uses_the_main_library(make_rip, src_dir,
                                                     tmp_path, write_config):
    movies = tmp_path / "movies"
    write_config(f'library = "{movies}"\n')
    make_rip([{"duration": 20}])
    sort2own.main([str(src_dir), "--yes", "--tv", "--name", "A Show (2024)",
                   "--copy", "--min-extra", "3"])
    assert (movies / "A Show (2024)").is_dir()
