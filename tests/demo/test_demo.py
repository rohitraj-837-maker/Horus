"""Offline demo: server endpoints, offline-only assets, and the synthetic scenarios.

The scenario checks run the real core on synthetic signals (no mocks): the bundled
files must be exactly what the generator produces, and the detector must recover the
injected sigma and stay quiet in calm air.
"""

import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from demo import scenarios, server
from demo.about import ABOUT

STATIC = Path(server.STATIC)
DATA = Path(server.DATA)


@pytest.fixture(scope="module")
def base_url():
    srv = server.make_server(0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_health_and_pages(base_url):
    code, headers, body = get(f"{base_url}/api/health")
    assert code == 200 and json.loads(body) == {"ok": True, "offline": True, "scenarios": 4}
    code, headers, body = get(f"{base_url}/")
    assert code == 200 and b"<title>Horus Turbulence Demo</title>" in body
    csp = headers["Content-Security-Policy"]
    assert (
        "default-src 'self'" in csp and "connect-src 'self'" in csp and "script-src 'self'" in csp
    )
    for name in ("app.js", "style.css", "icon.svg"):
        assert get(f"{base_url}/static/{name}")[0] == 200


def test_api_routes(base_url):
    index = json.loads(get(f"{base_url}/api/scenarios")[2])
    assert [s["id"] for s in index] == ["jet-cat", "convective", "calm", "pirep-replay"]
    for s in index:
        code, _, body = get(f"{base_url}/api/scenario/{s['id']}")
        d = json.loads(body)
        assert code == 200 and d["synthetic"] is True and d["severity"]["placeholder"] is True
    basemap = json.loads(get(f"{base_url}/api/basemap")[2])
    assert len(basemap["states"]) == 49  # lower 48 + DC
    about = json.loads(get(f"{base_url}/api/about")[2])
    assert about["name"] == "Horus"


@pytest.mark.parametrize(
    "path", ["/api/scenario/nope", "/static/../server.py", "/static/missing.js", "/etc/passwd"]
)
def test_unknown_and_traversal_paths_404(base_url, path):
    assert get(f"{base_url}{path}")[0] == 404


def test_static_assets_make_no_external_requests():
    """Nothing in the page may load from the internet (links to attributions are
    plain <a href>, rendered from /api/about, never fetched)."""
    for name in ("index.html", "app.js", "style.css", "icon.svg"):
        text = (STATIC / name).read_text()
        if name == "icon.svg":
            text = text.replace('xmlns="http://www.w3.org/2000/svg"', "")
        assert not re.search(r"https?://", text), f"{name} references an external URL"
        assert "@import" not in text and "<script>" not in text
        assert not re.search(r"\sstyle=", text), f"{name} uses inline style (blocked by CSP)"


def test_attribution_and_no_email_anywhere():
    urls = {a["url"] for a in ABOUT["attributions"]}
    assert "https://opensky-network.org/about/terms-of-use" in urls
    assert "https://opendatacommons.org/licenses/odbl/1-0/" in urls
    assert "Rohit Raj" in ABOUT["license"]
    email = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
    blobs = [json.dumps(ABOUT)] + [(STATIC / n).read_text() for n in ("index.html", "app.js")]
    assert not any(email.search(b) for b in blobs)


@pytest.mark.parametrize("spec", scenarios.SPECS, ids=lambda s: s.id)
def test_bundled_scenarios_match_generator(spec):
    built = json.loads(json.dumps(scenarios.build(spec)))
    on_disk = json.loads((DATA / f"scenario-{spec.id}.json").read_text())
    assert built == on_disk, "regenerate with: uv run python -m demo.scenarios"


def test_core_recovers_injected_sigma_and_stays_quiet_in_calm_air():
    edges = scenarios.SEVERITY_EDGES
    for spec in scenarios.SPECS:
        d = json.loads((DATA / f"scenario-{spec.id}.json").read_text())
        ck = d["check"]
        assert ck["smooth_p95_sigma_corr"] < edges[0]  # smooth air stays below "Light"
        if spec.patches:
            assert 0.85 <= ck["median_recovered_over_injected"] <= 1.15
    calm = json.loads((DATA / "scenario-calm.json").read_text())
    sev = [s for a in calm["aircraft"] for s in a["cols"]["sev"]]
    assert max(sev) == 0  # no false alarms anywhere in calm air


def test_scenario_columns_are_aligned():
    d = json.loads((DATA / "scenario-pirep-replay.json").read_text())
    for a in d["aircraft"]:
        lengths = {k: len(v) for k, v in a["cols"].items()}
        assert len(set(lengths.values())) == 1, lengths
        assert re.fullmatch(r"SYN\d{3}", a["callsign"])
    assert all(p["text"].startswith("SYNTHETIC ") for p in d["pireps"])
