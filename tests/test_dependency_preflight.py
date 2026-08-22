"""The uploader died in a restart loop over a package nobody installed.

    ModuleNotFoundError: No module named 'dotenv'
    [Keepalive] The uploader STOPPED at 12:52:44.53 (exit 1).
    [Keepalive] Restart #1 in 15 seconds.

Not a code bug - an install one, and a permanent one. There are two
requirements files:

    requirements.txt                 AutoReel
    auto_uploader/requirements.txt   the uploader

INSTALL.bat installed the first. Everything the uploader imports is in
the second: dotenv, the Google API clients, playwright, watchdog, yt-dlp.
A machine that had only ever run INSTALL.bat could never start the
uploader, and the failure looked like a code crash rather than a missing
install.

Three things had to be true afterwards, and each has tests here:

  * INSTALL.bat installs BOTH lists, through this interpreter
  * something checks before the failing import and says what is missing
    in words, instead of a traceback fifteen seconds apart forever
  * dotenv specifically cannot take the program down again - reading
    KEY=VALUE out of a file is not worth an outage
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from utils import deps  # noqa: E402


def _read(name: str) -> str:
    with open(os.path.join(_REPO, name), encoding="utf-8") as fh:
        return fh.read()


def _body(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines()
                     if not ln.strip().lower().startswith("rem"))


# ── the installer covers both halves of the project ──────────────────────

def test_install_covers_both_requirements_files():
    body = _body(_read("INSTALL.bat"))

    assert "-r \"%~dp0requirements.txt\"" in body
    assert "-r \"%~dp0auto_uploader\\requirements.txt\"" in body


def test_install_uses_this_interpreter_not_a_bare_pip():
    """A bare `pip` on a machine with two Pythons installs into the other
    one, and every import fails with the packages visibly present."""
    body = _body(_read("INSTALL.bat"))

    for line in body.splitlines():
        if "pip install" in line:
            assert line.strip().startswith("python -m pip"), line


def test_install_gets_the_browser_rumble_needs():
    body = _body(_read("INSTALL.bat"))

    assert "playwright install chromium" in body


def test_install_verifies_itself_rather_than_trusting_pip():
    body = _body(_read("INSTALL.bat"))

    assert "deps.py" in body and "--check" in body


def test_a_missing_browser_does_not_fail_the_whole_install():
    """Everything except Rumble still works without it."""
    body = _body(_read("INSTALL.bat"))
    after = body.split("playwright install chromium", 1)[1]

    assert "WARNING" in after.split("goto :eof")[0]


# ── every package the uploader imports is declared ───────────────────────

def test_the_thing_that_actually_broke_is_covered():
    assert deps.REQUIRED["dotenv"] == "python-dotenv"


def test_install_names_differ_from_import_names_where_they_have_to():
    """`pip install dotenv` installs a different, abandoned package and
    leaves the error exactly where it was."""
    assert deps.REQUIRED["googleapiclient"] == "google-api-python-client"
    assert deps.REQUIRED["yt_dlp"].startswith("yt-dlp")


def test_every_required_package_is_in_a_requirements_file():
    """Otherwise the preflight installs both lists and the package still
    is not there, which is the one failure it cannot explain."""
    declared = "\n".join(_read(os.path.relpath(path, _REPO)).lower()
                         for path in deps.REQUIREMENT_FILES)

    for module, package in deps.REQUIRED.items():
        stem = package.split("[")[0].split("==")[0].lower()
        assert stem in declared, f"{package} is required but never installed"


def test_optional_packages_are_not_required():
    """Desktop notifications and a CPU readout are not worth refusing to
    upload over."""
    assert not (set(deps.OPTIONAL) & set(deps.REQUIRED))
    for module in ("plyer", "psutil"):
        assert module in deps.OPTIONAL


# ── the preflight itself ─────────────────────────────────────────────────

def test_nothing_missing_means_nothing_happens(monkeypatch):
    monkeypatch.setattr(deps, "missing", lambda names: [])
    monkeypatch.setattr(deps, "install", lambda: pytest.fail("installed anyway"))

    deps.ensure()


def test_a_missing_package_is_installed(monkeypatch):
    calls = []
    monkeypatch.setattr(deps, "_recently_attempted", lambda: False)
    monkeypatch.setattr(deps, "_record_attempt", lambda: None)
    monkeypatch.setattr(deps, "install", lambda: calls.append("pip") or True)

    seen = iter([["dotenv"], []])
    monkeypatch.setattr(deps, "missing",
                        lambda names: next(seen) if names is deps.REQUIRED else [])

    deps.ensure()

    assert calls == ["pip"]


def test_it_does_not_reinstall_the_world_every_fifteen_seconds(monkeypatch):
    """The keepalive restarts on a loop. Without the cooldown each restart
    would start a fresh pip run against whatever is broken."""
    monkeypatch.setattr(deps, "missing",
                        lambda names: ["dotenv"] if names is deps.REQUIRED else [])
    monkeypatch.setattr(deps, "_recently_attempted", lambda: True)
    monkeypatch.setattr(deps, "install", lambda: pytest.fail("retried inside the cooldown"))

    with pytest.raises(SystemExit) as exit_info:
        deps.ensure()

    assert exit_info.value.code == deps.EXIT_MISSING_DEPS


def test_an_install_that_did_not_help_says_so_and_stops(monkeypatch):
    monkeypatch.setattr(deps, "_recently_attempted", lambda: False)
    monkeypatch.setattr(deps, "_record_attempt", lambda: None)
    monkeypatch.setattr(deps, "install", lambda: True)
    monkeypatch.setattr(deps, "missing",
                        lambda names: ["dotenv"] if names is deps.REQUIRED else [])

    with pytest.raises(SystemExit) as exit_info:
        deps.ensure()

    assert exit_info.value.code == deps.EXIT_MISSING_DEPS


def test_it_can_be_told_not_to_install(monkeypatch):
    monkeypatch.setattr(deps, "missing",
                        lambda names: ["dotenv"] if names is deps.REQUIRED else [])
    monkeypatch.setattr(deps, "install", lambda: pytest.fail("installed anyway"))

    with pytest.raises(SystemExit):
        deps.ensure(auto=False)


def test_the_install_command_runs_this_interpreter():
    command = deps.install_command()

    assert command[:3] == [sys.executable, "-m", "pip"]
    assert command.count("-r") == len(
        [p for p in deps.REQUIREMENT_FILES if os.path.exists(p)])


def test_the_preflight_only_runs_when_main_is_the_program():
    """The tests import main.py. A test run must never start pip."""
    body = _read(os.path.join("auto_uploader", "main.py"))
    guard = body.index('if __name__ == "__main__":')
    call = body.index("_deps.ensure()")

    assert guard < call
    assert call < body.index("from utils.config import")


# ── dotenv can never take the uploader down again ────────────────────────

def _fallback_load_dotenv():
    """The definition that runs when python-dotenv is absent."""
    source = _read(os.path.join("auto_uploader", "utils", "config.py"))
    head = source.split("@dataclass", 1)[0].replace(
        "from dotenv import load_dotenv", "raise ImportError()")
    namespace = {"os": os}
    exec(compile(head, "config.py", "exec"), namespace)
    return namespace["load_dotenv"]


@pytest.fixture
def load_dotenv(monkeypatch):
    for key in ("FOO", "QUOTED", "EMPTY", "ALREADY"):
        monkeypatch.delenv(key, raising=False)
    return _fallback_load_dotenv()


def _env_file(text: str) -> str:
    path = os.path.join(tempfile.mkdtemp(), ".env")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def test_the_fallback_reads_a_plain_env(load_dotenv):
    load_dotenv(_env_file("FOO=bar\n"))

    assert os.environ["FOO"] == "bar"


def test_the_fallback_skips_comments_and_blanks(load_dotenv):
    load_dotenv(_env_file("# a note\n\nFOO=bar\n"))

    assert os.environ["FOO"] == "bar"


def test_the_fallback_handles_export_and_quotes(load_dotenv):
    load_dotenv(_env_file('export FOO=bar\nQUOTED="has space"\n'))

    assert os.environ["FOO"] == "bar"
    assert os.environ["QUOTED"] == "has space"


def test_the_fallback_does_not_clobber_the_real_environment(load_dotenv,
                                                            monkeypatch):
    """python-dotenv leaves an already-set variable alone unless told
    otherwise, and a credential exported by hand must win."""
    monkeypatch.setenv("ALREADY", "from the shell")

    load_dotenv(_env_file("ALREADY=from the file\n"))

    assert os.environ["ALREADY"] == "from the shell"


def test_the_fallback_overrides_when_asked(load_dotenv):
    os.environ["ALREADY"] = "old"

    load_dotenv(_env_file("ALREADY=new\n"), override=True)

    assert os.environ["ALREADY"] == "new"


def test_a_missing_env_file_is_not_an_error(load_dotenv):
    assert load_dotenv("/nowhere/at/all/.env") is False


def test_a_junk_line_does_not_stop_the_rest(load_dotenv):
    """One malformed line must not cost every credential after it."""
    load_dotenv(_env_file("nonsense\nFOO=bar\n"))

    assert os.environ["FOO"] == "bar"


def test_the_uploader_is_installed_before_the_clipping_half():
    """pip installs a requirements file as a unit. numpy failing to compile
    took the uploader down with it - not because the uploader needed numpy,
    but because they were in the same run. Nothing in the uploader's list
    needs a compiler, so it goes first and on its own."""
    body = _body(_read("INSTALL.bat"))

    uploader = body.index("auto_uploader\\requirements.txt")
    autoreel = body.index('-r "%~dp0requirements.txt"')

    assert uploader < autoreel, (
        "the clipping half installs first - a compiler failure there would "
        "stop the uploader being installed at all, which is the outage")


def test_a_failed_clipping_install_does_not_abort_the_run():
    """Recording and uploading must come back even when clipping cannot."""
    body = _body(_read("INSTALL.bat"))
    after = body.split('-r "%~dp0requirements.txt"', 1)[1]
    next_check = after.split("\n\n", 1)[0]

    assert "goto failed" not in next_check, (
        "a clipping failure aborts the whole install")
    assert "REEL_FAILED" in next_check


def test_the_python_version_is_reported():
    """The version in use is the first thing worth knowing when a package
    will not install, and it was invisible."""
    body = _body(_read("INSTALL.bat"))

    assert "sys.version" in body
