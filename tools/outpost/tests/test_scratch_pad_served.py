"""A pad published into a camp workspace, served over HTTP under Outpost's sites headers.

The test publishes a real pad through the pad script's workspace path, serves
``<ws>/sites/`` from a loopback server that sends the headers Outpost's sites
handler sends, and opens the pad in a headless browser the way a teammate would:
the top-level page, then on to ``round-1/``.
"""

from __future__ import annotations

import importlib.util
import json
import socket
from pathlib import Path

import pytest

import chromium_harness as ch

SKILL_DIR = Path(__file__).parent.parent / "plugins" / "outpost" / "skills" / "scratch-pad"

_spec = importlib.util.spec_from_file_location("scratch_pad", SKILL_DIR / "scratch_pad.py")
scratch_pad = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scratch_pad)


@pytest.fixture(autouse=True)
def _outside_any_workspace(tmp_path, monkeypatch):
    """The repo checkout sits inside a live camp workspace; run from a folder that does not."""
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)


SCRIPT = "<script>window.pwned=1</script>"
IMG = "<img src=x onerror=window.pwned=1>"
HOSTILE = SCRIPT + IMG


def _option(name, cost, pick, layout):
    return {"name": name, "cost": cost, "pick": pick, "layout": layout}


ROUND = {
    "title": "Hostile " + HOSTILE,
    "intro": "intro",
    "questions": [
        {
            "prompt": "Header",
            "options": [
                _option(
                    HOSTILE,
                    HOSTILE,
                    True,
                    {"box": [{"text": HOSTILE}, {"button": HOSTILE}, {"field": HOSTILE}, {"rule": True}]},
                ),
                _option("Plain", "Simple", False, {"box": [{"button": "Go"}, {"field": "Email"}]}),
            ],
        }
    ],
}

PROBE = r"""
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
await sleep(200);
const drawing = (o) => document.querySelector('.pad-drawing[data-question="0"][data-option="' + o + '"]');
const boxes = (o) => [...drawing(o).querySelectorAll('svg.pad-ink g')].map((g) => {
  const b = g.getBBox();
  return { kind: g.getAttribute('data-kind'), w: b.width, h: b.height, paths: g.querySelectorAll('path').length };
});
const card = document.querySelectorAll('.pad-card')[0];
const result = {
  url: location.href,
  boxes0: boxes(0),
  boxes1: boxes(1),
  faces: [...document.fonts].filter((f) => f.family.replace(/"/g, '') === 'Architects Daughter').map((f) => f.status),
  nameText: card.querySelector('.pad-name').textContent,
  costText: card.querySelector('.pad-cost').textContent,
  layoutText: drawing(0).textContent,
  injected: document.querySelectorAll('#pad img, #pad script, #pad [onerror]').length,
};
[...card.querySelectorAll('button')].find((b) => b.textContent === 'This one').click();
await sleep(100);
document.querySelector('.pad-reply-entry').click();
await sleep(200);
result.reply = document.getElementById('pad-reply-box').value;
result.pwned = window.pwned === undefined ? null : window.pwned;
return result;
"""


def _publish(tmp_path: Path) -> Path:
    root = tmp_path / "camp-state"
    member = root / "grp" / "worktrees" / "ws" / "repo"
    member.mkdir(parents=True)
    env = {
        "CAMP_STATE_DIR": str(root),
        "CAMP_CONFIG_DIR": str(tmp_path / "camp-config"),
        "HOME": str(tmp_path / "home"),
        "OUTPOST_STATE_DIR": str(tmp_path / "outpost-state"),
    }
    round_file = tmp_path / "round.json"
    round_file.write_text(json.dumps(ROUND))
    code = scratch_pad.main(["--pad", "p", "--round", str(round_file)], env=env, cwd=member)
    assert code == 0
    return root.resolve() / "grp" / "worktrees" / "ws" / "sites"


def test_a_workspace_pad_draws_answers_and_stays_inert_when_served_over_http(tmp_path):
    sites = _publish(tmp_path)

    got = ch.run_probe(
        sites,
        PROBE,
        serve=True,
        entry="p/",
        inject_into="p/round-1/index.html",
        virtual_time_budget=8000,
    )

    assert got["url"].startswith("http://127.0.0.1:")
    assert got["url"].endswith("/p/round-1/index.html")
    for drawn in (got["boxes0"], got["boxes1"]):
        assert drawn
        assert all(b["w"] > 0 and b["h"] > 0 and b["paths"] > 0 for b in drawn), drawn
    assert [b["kind"] for b in got["boxes0"]].count("rule") == 1
    assert got["faces"] == ["loaded"]
    assert '"pick": "A"' in got["reply"]
    assert got["nameText"] == HOSTILE
    assert got["costText"] == HOSTILE
    assert SCRIPT in got["layoutText"] and IMG in got["layoutText"]
    assert got["injected"] == 0
    assert got["pwned"] is None


def test_the_loopback_server_reports_a_port_it_cannot_bind(tmp_path):
    (tmp_path / "index.html").write_text("<html><body></body></html>")
    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen()
    port = taken.getsockname()[1]
    try:
        with pytest.raises(ch.ServerStartError) as err:
            with ch.serve_folder(tmp_path, port=port):
                pass
    finally:
        taken.close()
    assert f"127.0.0.1:{port}" in str(err.value)
