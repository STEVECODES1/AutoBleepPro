"""A fresh Python 3.14 install stopped the whole project.

    Collecting numpy<2.0,>=1.24 (from -r requirements.txt (line 15))
      Downloading numpy-1.26.4.tar.gz (15.8 MB)
      ..\\meson.build:1:0: ERROR: Unknown compiler(s): [['icl'], ['cl'], ...]
    error: metadata-generation-failed
    Installation FAILED

numpy, scipy and Pillow ship one compiled wheel per Python version. The
pinned versions predate 3.13, so pip found no wheel, fell back to the
source tarball, and tried to invoke a C compiler that is not on a normal
Windows machine.

pip installs a requirements file as a unit: numpy failing meant NOTHING
was installed. That is why every package disappeared at once and why the
uploader then died on the first import it reached - dotenv - fifteen
seconds apart, forever. One compiled dependency, and the whole system was
down.

Environment markers fix it without anyone choosing a Python: the tested
pins apply on the versions they were tested on, and the newest wheels
apply on anything newer. These tests check that the split is complete and
that no interpreter ends up with both halves or neither.
"""

from __future__ import annotations

import os

import pytest

from packaging.requirements import Requirement

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES = ("requirements.txt", os.path.join("auto_uploader", "requirements.txt"))

# Written twice: the version that has wheels for the Python in use.
#
# numpy/scipy/pillow are compiled directly. playwright and moviepy are pure
# Python but PIN something compiled - playwright 1.47 hard-pins
# greenlet==3.0.3, moviepy 2.0 requires pillow<11 - and neither of those
# pins has a wheel for 3.13+. A pure-Python package can still need a
# compiler through what it asks for, which is how greenlet got missed the
# first time through: only the direct dependencies had been checked.
SPLIT = ("numpy", "scipy", "pillow", "playwright", "moviepy")

# The subset that is compiled C. These are the ones that genuinely cannot
# install without a toolchain; playwright and moviepy are pure Python and
# only fail through what they pin.
COMPILED = ("numpy", "scipy", "pillow")

# Present ONLY on the new Pythons, rather than written twice: audioop was
# in the standard library until 3.13 and is a PyPI package after it.
NEW_ONLY = ("audioop-lts",)

PYTHONS = ("3.11", "3.12", "3.13", "3.14")


def _requirements(name: str) -> list[Requirement]:
    parsed = []
    with open(os.path.join(_REPO, name), encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parsed.append(Requirement(line))
    return parsed


@pytest.fixture(params=FILES)
def requirements(request) -> list[Requirement]:
    return _requirements(request.param)


def _selected(reqs: list[Requirement], python: str) -> list[Requirement]:
    """What pip would actually install on that interpreter."""
    chosen = []
    for req in reqs:
        if req.marker is None or req.marker.evaluate({"python_version": python}):
            chosen.append(req)
    return chosen


def test_every_line_is_a_valid_requirement(requirements):
    """A typo in a marker makes pip reject the whole file, which fails the
    same way the compiler error did: nothing installed."""
    assert requirements


@pytest.mark.parametrize("python", PYTHONS)
def test_exactly_one_version_of_each_split_package(python):
    """Both halves selected means pip resolves two conflicting pins and
    refuses. Neither means the package is silently absent."""
    reqs = _requirements("requirements.txt")
    chosen = _selected(reqs, python)

    present = {r.name.lower() for r in reqs}
    for package in SPLIT:
        if package not in present:
            continue      # lives in the other requirements file
        matches = [r for r in chosen if r.name.lower() == package]
        assert len(matches) == 1, (
            f"on Python {python}, {package} resolves to {len(matches)} "
            f"entries: {[str(m) for m in matches]}")


def test_the_new_pythons_are_not_capped_below_the_wheels_that_exist():
    """numpy 1.26 has no wheel past cp312. Leaving <2.0 in place on 3.13+
    is exactly the failure this file is fixing."""
    chosen = _selected(_requirements("requirements.txt"), "3.14")
    numpy = [r for r in chosen if r.name.lower() == "numpy"][0]

    assert numpy.specifier.contains("2.5.2"), (
        f"on 3.14 numpy resolves to {numpy} - no wheel exists for that, so "
        f"pip will try to compile it")


def test_the_tested_pins_still_apply_where_they_were_tested():
    """3.11 and 3.12 must keep the exact versions this project has been
    running on - a working machine must not be changed by this."""
    chosen = _selected(_requirements("requirements.txt"), "3.11")
    numpy = [r for r in chosen if r.name.lower() == "numpy"][0]
    scipy = [r for r in chosen if r.name.lower() == "scipy"][0]

    assert numpy.specifier.contains("1.26.4")
    assert not numpy.specifier.contains("2.5.2"), (
        "3.11 would move to numpy 2, which is not what has been running")
    assert scipy.specifier.contains("1.11.4")


@pytest.mark.parametrize("python", PYTHONS)
def test_nothing_else_disappears_on_a_newer_python(python):
    """Only the three compiled ones are allowed to be conditional. A
    marker accidentally left on anything else means a package that is
    quietly not installed, which is how this whole outage started."""
    for name in FILES:
        for req in _requirements(name):
            if req.marker is None:
                continue
            assert req.name.lower() in SPLIT + NEW_ONLY, (
                f"{name}: {req} is conditional but is neither a version "
                f"split nor a new-Python-only package - it would vanish "
                f"on some Python without anyone deciding that")


@pytest.mark.parametrize("python", PYTHONS)
def test_the_transitive_pins_are_split_too(python):
    """playwright 1.47 -> greenlet==3.0.3 and moviepy 2.0 -> pillow<11 both
    reach a package with no wheel for 3.13+. The direct requirement looks
    innocent; what it pins is what fails."""
    for name in FILES:
        chosen = _selected(_requirements(name), python)
        for package in ("playwright", "moviepy"):
            matches = [r for r in chosen if r.name.lower() == package]
            if not matches:
                continue
            assert len(matches) == 1, (
                f"{name} on {python}: {package} resolves to "
                f"{[str(m) for m in matches]}")


def test_the_new_pythons_get_the_versions_whose_pins_have_wheels():
    chosen = _selected(_requirements(FILES[1]), "3.14")
    playwright = [r for r in chosen if r.name.lower() == "playwright"][0]

    assert not playwright.specifier.contains("1.47.0"), (
        "1.47.0 pins greenlet==3.0.3, which has to be compiled on 3.14")
    assert playwright.specifier.contains("1.62.0")

    reel = _selected(_requirements(FILES[0]), "3.14")
    moviepy = [r for r in reel if r.name.lower() == "moviepy"][0]
    pillow = [r for r in reel if r.name.lower() == "pillow"][0]

    assert not moviepy.specifier.contains("2.0.0"), (
        "2.0.0 requires pillow<11, and no pillow below 11 has a 3.14 wheel")
    assert pillow.specifier.contains("11.3.0"), (
        "moviepy requires pillow<12, so 11.x is the only version that "
        "satisfies both it and 3.14")
    assert not pillow.specifier.contains("12.3.0"), (
        "moviepy requires pillow<12 - leaving the cap off resolves to 12 "
        "and pip refuses the whole file")


def test_the_uploader_list_needs_no_compiler_at_all():
    """Everything the uploader itself imports is pure Python or ships an
    abi-none wheel, which is why the uploader can be brought back in a
    minute without touching the AutoReel half."""
    names = {r.name.lower() for r in _requirements(FILES[1])}

    for pure in ("python-dotenv", "google-api-python-client", "playwright",
                 "watchdog", "requests"):
        assert pure in names, pure
    assert not (names & set(COMPILED)), (
        "the uploader list pulled in a compiled package - it can no longer "
        "be installed on its own when the AutoReel half is broken")


# ── the two files must not fight ─────────────────────────────────────────

def _by_name(path: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for req in _requirements(path):
        found.setdefault(req.name.lower(), []).append(str(req))
    return found


def test_a_package_in_both_files_is_specified_the_same_way():
    """INSTALL.bat runs both files. Where they disagree, the second run
    undoes the first:

        Successfully uninstalled faster-whisper-1.2.1
        Successfully installed ... faster-whisper-1.1.1 ...

    Harmless when it finishes. Not harmless when the second run stops
    partway, which leaves a half-swapped set that matches neither file.
    """
    reel, uploader = _by_name(FILES[0]), _by_name(FILES[1])

    for name in sorted(set(reel) & set(uploader)):
        assert sorted(reel[name]) == sorted(uploader[name]), (
            f"{name} is '{reel[name]}' in one file and '{uploader[name]}' "
            f"in the other - whichever installs second wins")


def test_curl_cffi_is_left_to_yt_dlp():
    """It was pinned to 0.15.0 here while the uploader installs
    yt-dlp[curl-cffi], which resolves what THAT yt-dlp was built against.
    This file ran second and downgraded it - on the one package Kick
    recording depends on. yt-dlp's supported range moves; a fixed pin
    goes stale silently, so it is measured by LINKS.bat instead."""
    for path in FILES:
        names = {r.name.lower().replace("_", "-") for r in _requirements(path)}
        assert "curl-cffi" not in names, (
            f"{path} pins curl_cffi directly again - it will fight yt-dlp's "
            f"own resolution")


# ── the module Python took away ──────────────────────────────────────────

def test_the_audioop_backport_is_installed_on_new_pythons():
    """audioop was removed from the standard library in 3.13 (PEP 594).
    pydub imports it and falls back to `pyaudioop`, which has never
    existed, so censoring dies with

        ModuleNotFoundError: No module named 'pyaudioop'

    naming a module nobody has ever installed on purpose. Muting a word
    is the whole product, so this is not a degraded feature - it is the
    product not working."""
    for path in FILES:
        chosen = {r.name.lower() for r in _selected(_requirements(path), "3.14")}
        if "pydub" not in chosen:
            continue
        assert "audioop-lts" in chosen, (
            f"{path} installs pydub on 3.14 with no audioop - censoring "
            f"will raise on the first clip")


def test_the_backport_is_not_forced_on_older_pythons():
    """audioop-lts requires Python >=3.13. Installing it below that fails
    the whole requirements file."""
    for path in FILES:
        for python in ("3.11", "3.12"):
            chosen = {r.name.lower()
                      for r in _selected(_requirements(path), python)}
            assert "audioop-lts" not in chosen, (
                f"{path} would install audioop-lts on {python}, which "
                f"refuses to install there")


def test_wherever_pydub_goes_audioop_follows():
    """Both files install pydub. Fixing one and not the other leaves the
    failure in place on whichever half runs the censor."""
    for path in FILES:
        names = {r.name.lower() for r in _requirements(path)}
        assert ("pydub" in names) == ("audioop-lts" in names), path
