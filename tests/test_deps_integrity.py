"""A package can be installed and still be unusable (10-20).

find_spec says present; the first import dies on a missing module or data
file. That is what kept the uploader in a keepalive loop for two days.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from utils import deps  # noqa: E402


def test_an_importable_package_is_not_broken():
    assert deps.broken_imports() == {}


def test_a_present_but_unimportable_module_is_broken(monkeypatch):
    monkeypatch.setattr(deps, "IMPORT_PROBES",
                        {"module_that_is_not_there_xyz": "some-package"})
    broken = deps.broken_imports()
    assert list(broken) == ["module_that_is_not_there_xyz"]
    assert "some-package" in deps.repair_command(broken)


def test_pillow_is_capped_for_moviepy():
    """Pillow 12 is what made the thumbnail tests fail; moviepy 2.0
    requires <11, so the repair command must not install the latest."""
    assert deps.IMPORT_PROBES["PIL.Image"] == "Pillow<11"
