"""The CUDA libraries were installed and Windows still could not find them.

    D:\\AutoBleepPro-git>python -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
    Requirement already satisfied: nvidia-cublas-cu12 ... (12.9.2.10)
    Requirement already satisfied: nvidia-cudnn-cu12 ... (9.24.0.43)

and the censor pass had died on:

    Library cublas64_12.dll is not found or cannot be loaded

Both true at once. pip puts those DLLs in

    ...\\site-packages\\nvidia\\cublas\\bin\\cublas64_12.dll
    ...\\site-packages\\nvidia\\cudnn\\bin\\cudnn64_9.dll

and the Windows loader searches PATH, not site-packages. So the install
succeeds, pip says it is satisfied, and ctranslate2 reports a missing
library - which reads as a broken install and is not one.

PyTorch registers these directories when imported, which is why the
problem looks intermittent: it depends on whether anything pulled torch
in first. This project does not always.

The cost of getting it wrong is a four-hour VOD transcribing on a CPU.
"""

from __future__ import annotations

import os
import sys
import types

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from autoreel import transcription  # noqa: E402


@pytest.fixture(autouse=True)
def _forget_what_was_registered():
    transcription._REGISTERED_DLL_DIRS.clear()
    yield
    transcription._REGISTERED_DLL_DIRS.clear()


@pytest.fixture
def windows_with_cuda(tmp_path, monkeypatch):
    """A stand-in for the layout pip actually produces."""
    root = tmp_path / "site-packages" / "nvidia"
    for package in ("cublas", "cudnn", "cuda_nvrtc"):
        (root / package / "bin").mkdir(parents=True)
    # Not every nvidia subpackage ships a bin/ directory.
    (root / "cuda_runtime" / "lib").mkdir(parents=True)

    module = types.ModuleType("nvidia")
    module.__path__ = [str(root)]
    monkeypatch.setitem(sys.modules, "nvidia", module)
    monkeypatch.setattr(os, "name", "nt")

    added = []
    monkeypatch.setattr(os, "add_dll_directory",
                        lambda path: added.append(path) or object(),
                        raising=False)
    return root, added


# ── finding them ─────────────────────────────────────────────────────────

def test_it_finds_every_bin_directory(windows_with_cuda):
    root, _added = windows_with_cuda

    found = transcription.cuda_dll_directories()

    assert len(found) == 3
    assert str(root / "cublas" / "bin") in found
    assert str(root / "cudnn" / "bin") in found


def test_subpackages_without_a_bin_folder_are_skipped(windows_with_cuda):
    root, _added = windows_with_cuda

    found = transcription.cuda_dll_directories()

    assert not any("cuda_runtime" in path for path in found)


def test_nothing_happens_off_windows(windows_with_cuda, monkeypatch):
    """Linux and macOS resolve these through the normal loader path."""
    monkeypatch.setattr(os, "name", "posix")

    assert transcription.cuda_dll_directories() == []


def test_no_nvidia_packages_is_not_an_error(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setitem(sys.modules, "nvidia", None)

    assert transcription.cuda_dll_directories() == []


# ── registering them ─────────────────────────────────────────────────────

def test_the_directories_are_handed_to_the_loader(windows_with_cuda):
    _root, added = windows_with_cuda

    registered = transcription.register_cuda_dlls()

    assert sorted(added) == sorted(registered)
    assert len(registered) == 3


def test_they_go_on_PATH_as_well(windows_with_cuda, monkeypatch):
    """Some loaders still consult it."""
    root, _added = windows_with_cuda
    monkeypatch.setenv("PATH", "C:\\existing")

    transcription.register_cuda_dlls()

    assert str(root / "cublas" / "bin") in os.environ["PATH"]
    assert "C:\\existing" in os.environ["PATH"]


def test_registering_twice_does_not_add_them_twice(windows_with_cuda):
    _root, added = windows_with_cuda

    transcription.register_cuda_dlls()
    transcription.register_cuda_dlls()

    assert len(added) == 3


def test_a_directory_the_loader_refuses_does_not_stop_the_others(
        windows_with_cuda, monkeypatch, capsys):
    """Without the GPU the CPU fallback still produces a transcript, so
    this can never be fatal."""
    _root, added = windows_with_cuda

    def picky(path):
        if "cudnn" in path:
            raise OSError("refused")
        added.append(path)

    monkeypatch.setattr(os, "add_dll_directory", picky, raising=False)

    registered = transcription.register_cuda_dlls()

    assert len(registered) == 2
    assert "Could not register" in capsys.readouterr().out


# ── it has to happen before ctranslate2 loads ────────────────────────────

def test_registration_precedes_the_faster_whisper_import():
    """ctranslate2 resolves its CUDA libraries as the module loads. A
    directory added afterwards is too late, and this is the whole reason
    the fix works at all."""
    source = open(os.path.join(_REPO, "autoreel", "transcription.py"),
                  encoding="utf-8").read()
    spot = source.index("from faster_whisper import WhisperModel")
    before = source[spot - 400:spot]

    assert "register_cuda_dlls()" in before


def test_only_when_the_gpu_is_actually_being_used():
    source = open(os.path.join(_REPO, "autoreel", "transcription.py"),
                  encoding="utf-8").read()
    spot = source.index("register_cuda_dlls()\n            from faster_whisper")

    assert '_resolved_device == "cuda"' in source[spot - 300:spot]


# ── and the message when it still fails has to be honest ─────────────────

def test_it_stops_telling_you_to_install_what_is_already_installed():
    """"pip install nvidia-cublas-cu12" is unhelpful advice to somebody
    who has just been told the requirement is already satisfied."""
    source = open(os.path.join(_REPO, "autoreel", "transcription.py"),
                  encoding="utf-8").read()
    spot = source.index("GPU unavailable for")
    after = source[spot:spot + 1200]

    assert "not cuda_dll_directories()" in after
    assert "ARE installed" in after
