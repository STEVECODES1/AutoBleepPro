"""GAMES and CLIPS push viewers to wopovod and Rumble (owner, 10-18)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import funnel_setup as fs  # noqa: E402


def test_doubled_name_is_dropped_once():
    assert fs.fixed_title("Stackswopo - STACKSWOPO EXPOSED - (BEST MOMENTS)") == "STACKSWOPO EXPOSED - (BEST MOMENTS)"
    assert fs.fixed_title("Stackswopo - EXPOSING - (BEST MOMENTS)") == "Stackswopo - EXPOSING - (BEST MOMENTS)"


def test_every_about_says_fan_run_once():
    once = fs.disclosed("Full streams of Stackswopo")
    assert once.startswith(fs.DISCLOSURE) and once.endswith("Full streams of Stackswopo")
    assert fs.disclosed(once) == once
    assert fs.GAMES_ABOUT.startswith(fs.DISCLOSURE) and "@stackswopo_" in fs.DISCLOSURE


def test_no_link_to_the_removed_clips_channel():
    assert "WOPOCLIPPER" not in fs.GAMES_ABOUT.upper()


def test_only_missing_recaps_are_added_up_to_cap():
    assert fs.to_add(["a", "b", "c", "d"], ["b"], 2) == ["a", "c"]
    assert fs.to_add(["a"], ["a"], 5) == []


def test_games_about_links_both_money_pages():
    assert fs.WOPOVOD in fs.GAMES_ABOUT and fs.RUMBLE in fs.GAMES_ABOUT
