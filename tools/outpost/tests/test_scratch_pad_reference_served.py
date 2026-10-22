"""A reference published to a vault, served over HTTP under Outpost's sites headers.

The test builds a real pad on disk, publishes a reference of it to a tmp vault through the pad script (a stub
``lore`` stands in for the sync), serves the vaults root from a loopback server that sends the headers Outpost's
sites handler sends, and opens ``/<vault>/<site>/`` in a headless browser the way a teammate would.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
from pathlib import Path


import chromium_harness as ch

SKILL_DIR = Path(__file__).parent.parent / "plugins" / "outpost" / "skills" / "scratch-pad"

_spec = importlib.util.spec_from_file_location("scratch_pad", SKILL_DIR / "scratch_pad.py")
scratch_pad = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scratch_pad)

SCRIPT = "<script>window.pwned=1</script>"
IMG = "<img src=x onerror=window.pwned=1>"
HOSTILE = SCRIPT + IMG


def _option(name, cost, pick, layout):
    return {"name": name, "cost": cost, "pick": pick, "layout": layout}


def _round(hostile):
    text = HOSTILE if hostile else "Plain"
    return {
        "title": text,
        "questions": [
            {
                "prompt": text,
                "options": [
                    _option(
                        text,
                        text,
                        True,
                        {"box": [{"text": text}, {"button": text}, {"field": text}, {"rule": True}]},
                    ),
                    _option("Other", "Cheap", False, {"box": [{"button": "Go"}]}),
                ],
            }
        ],
    }


PROBE = r"""
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
await sleep(300);
const cards = [...document.querySelectorAll('.ref-card')];
return {
  url: location.href,
  title: document.querySelector('h1').textContent,
  cards: cards.length,
  strokes: cards.map((c) => c.querySelectorAll('svg.pad-ink path').length),
  prompt: cards[0].querySelector('.pad-prompt').textContent,
  round: cards[0].querySelector('.ref-round').textContent,
  name: cards[0].querySelector('.pad-name').textContent,
  cost: cards[0].querySelector('.pad-cost').textContent,
  labels: cards[0].querySelector('.pad-drawing').textContent,
  pwned: window.pwned === undefined ? null : window.pwned,
  injected: document.querySelectorAll('img, [onerror]').length,
  inline: [...document.querySelectorAll('script')].filter((s) => !s.src).length,
};
"""


def _publish(tmp_path, monkeypatch, hostile):
    scratch = tmp_path / "scratch-tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    work = tmp_path / "cwd"
    work.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "lore"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    vaults = tmp_path / "xdg" / "lore" / "vaults"
    vault = vaults / "acme"
    vault.mkdir(parents=True)
    env = {
        "OUTPOST_STATE_DIR": str(tmp_path / "outpost-state"),
        "CAMP_STATE_DIR": str(tmp_path / "camp-state"),
        "XDG_STATE_HOME": str(tmp_path / "xdg"),
        "HOME": str(tmp_path / "home"),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "TMPDIR": str(scratch),
    }
    round_file = tmp_path / "round.json"
    round_file.write_text(json.dumps(_round(hostile)))
    assert scratch_pad.main(["--pad", "p", "--round", str(round_file)], env=env, cwd=work) == 0
    text = HOSTILE if hostile else "Plain"
    ref = {"site": "chosen", "title": text, "drawings": [{"round": 1, "question": 1, "option": "A"}]}
    ref_file = tmp_path / "ref.json"
    ref_file.write_text(json.dumps(ref))
    argv = ["--pad", "p", "--reference", str(ref_file), "--vault-path", str(vault), "--vault", "acme"]
    assert scratch_pad.main(argv, env=env, cwd=work) == 0
    assert list(scratch.iterdir()) == []
    served = tmp_path / "served"
    shutil.copytree(vault / "sites" / "chosen", served / "acme" / "chosen")
    return served


def _open(vaults):
    return ch.run_probe(
        vaults,
        PROBE,
        serve=True,
        entry="acme/chosen/",
        inject_into="acme/chosen/index.html",
        virtual_time_budget=8000,
    )


def test_a_published_reference_draws_its_cards_when_served_under_outposts_sites_policy(tmp_path, monkeypatch):
    got = _open(_publish(tmp_path, monkeypatch, hostile=False))

    assert got["url"].startswith("http://127.0.0.1:")
    assert got["url"].endswith("/acme/chosen/")
    assert got["title"] == "Plain"
    assert got["cards"] == 1
    assert got["strokes"][0] > 0
    assert got["name"] == "Plain"


def test_a_published_reference_of_hostile_text_shows_it_as_characters_with_nothing_run(tmp_path, monkeypatch):
    got = _open(_publish(tmp_path, monkeypatch, hostile=True))

    assert got["cards"] == 1 and got["strokes"][0] > 0
    for shown in (got["title"], got["prompt"], got["name"], got["cost"]):
        assert HOSTILE in shown
    assert HOSTILE in got["round"]
    assert SCRIPT in got["labels"] and IMG in got["labels"]
    assert got["pwned"] is None
    assert got["injected"] == 0
    assert got["inline"] == 0
