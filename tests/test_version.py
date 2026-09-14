"""The release version: exposed on the CLI and stamped into every manifest."""

import json
import os
import re
from pathlib import Path

import pytest

import sort2own


# --- the string itself -----------------------------------------------------

def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", sort2own.__version__)


def test_version_flag_prints_it_and_exits_clean(capsys):
    with pytest.raises(SystemExit) as e:
        sort2own.parse_args(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == f"sort2own {sort2own.__version__}"


def test_version_is_not_a_config_setting():
    """
    argparse's version action leaves no namespace entry, which is what keeps
    `version` out of the config keyspace. If that ever changed, a config file
    could set it and load_config would silently accept the nonsense.
    """
    parser = sort2own.build_parser()
    assert "version" not in set(vars(parser.parse_known_args([])[0]))


def test_a_config_naming_version_is_still_rejected():
    # The autouse isolation fixture already points discovery at a temp dir.
    cfg = Path(os.environ["XDG_CONFIG_HOME"]) / "sort2own" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('version = "9.9.9"\n')
    with pytest.raises(SystemExit) as e:
        sort2own.parse_args(["/rip"])
    assert "version" in str(e.value)


# --- the manifest ----------------------------------------------------------

def test_a_run_records_the_version(make_plan, library):
    plan = make_plan([{"duration": 7200}])
    sort2own.classify(plan, None, 90, 0.85, 0.01)
    sort2own.execute(plan, "copy", False, False)

    runs = json.loads((library / "A Film (2024)"
                       / sort2own.MANIFEST_NAME).read_text())
    assert runs[-1]["version"] == sort2own.__version__


def test_a_manifest_without_a_version_still_loads(tmp_path):
    """Additive only — see DESIGN.md. Runs written before 1.0.0 have no field."""
    (tmp_path / sort2own.MANIFEST_NAME).write_text(json.dumps(
        [{"when": "2026-09-09T12:00:00", "name": "Old Film (2001)",
          "tv": False, "actions": []}]))
    runs, readable = sort2own.read_manifest(tmp_path)
    assert readable and runs[0].get("version") is None
