"""Landing-page content: KPIs are computed from the bundled synthetic scenarios (never
typed in), maths parameters come from config.yaml, and no benchmark cell is filled
before real PIREP-validated runs exist."""

import json
import threading
import urllib.request

import pytest

from demo import overview, server
from horus import config


def test_kpis_are_computed_and_labelled_synthetic():
    k = {x["label"]: x for x in overview.kpis()}
    assert set(k) == {
        "PIREP Correlation Rate",
        "σ_corr Recovery",
        "σ_corr Calm-Air Baseline",
        "Calm-Air False-Alarm Rate",
    }
    matched, total = overview.pirep_correlation([overview._load(s) for s in overview.TURBULENT])
    assert total > 0 and k["PIREP Correlation Rate"]["value"] == f"{matched / total:.0%}"
    assert f"{matched} / {total} synthetic" in k["PIREP Correlation Rate"]["detail"]
    for x in k.values():
        assert x["caveat"]  # every card says what it does not show


def test_pirep_correlation_respects_the_spec_window():
    d = overview._load("pirep-replay")
    m = d["match"]
    far = dict(d["pireps"][0], tb="MOD", lat=0.0, lon=0.0)  # nowhere near any aircraft
    late = dict(d["pireps"][0], tb="MOD", t=d["duration_s"] + m["window_s"] + 1)
    probe = {**d, "pireps": [far, late]}
    assert overview.pirep_correlation([probe]) == (0, 2)


def test_maths_uses_config_values():
    cfg = config.load()
    rows = {m["title"]: m for m in overview.maths()}
    sheppard = rows["Sheppard quantisation correction"]
    step = cfg.units.vr_step_fpm
    assert sheppard["highlight"] and f"{step**2 / 12:.1f}" in sheppard["formula"]
    assert f"median_{cfg.residual.w_trend_s:.0f}s" in rows["Vertical-rate residual"]["formula"]


def test_benchmark_cells_stay_empty_until_real_runs():
    for row in overview.BENCHMARKS["rows"]:
        assert row[2] is None and row[3] is None


def test_adsblol_is_not_described_as_1hz():
    stage = next(p for p in overview.PIPELINE if "adsb.lol" in p["title"])
    assert "1 Hz" not in stage["detail"] and "20 s" in stage["detail"]


@pytest.fixture(scope="module")
def base_url():
    srv = server.make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def test_overview_endpoint(base_url):
    with urllib.request.urlopen(f"{base_url}/api/overview", timeout=10) as r:
        body = json.loads(r.read())
    assert [len(body[k]) for k in ("kpis", "pipeline", "maths")] == [4, 5, 5]


def test_landing_detail_sections():
    o = overview.overview()
    assert len(o["problem"]) == 3 and len(o["read_guide"]) == 6 and len(o["glossary"]) >= 8
    assert [m["m"] for m in o["milestones"]] == [f"M{i}" for i in range(8)]
    assert {m["status"] for m in o["milestones"]} <= {"done", "active", "planned"}
    assert any("synthetic" in x.lower() for x in o["limitations"])
    assert any("placeholder" in x.lower() for x in o["limitations"])


def test_scenario_cards_are_counted_from_the_data():
    for card in overview.scenario_cards():
        d = overview._load(card["id"])
        assert card["aircraft"] == len(d["aircraft"]) and card["pireps"] == len(d["pireps"])
        assert card["mod_plus"] == sum(p["tb"] in ("MOD", "SEV") for p in d["pireps"])
        assert card["recovery"] == d["check"]["median_recovered_over_injected"]
    calm = next(c for c in overview.scenario_cards() if c["id"] == "calm")
    assert calm["patches"] == 0 and calm["mod_plus"] == 0


def test_every_pipeline_stage_explains_itself():
    for stage in overview.PIPELINE:
        assert stage["what"] and stage["why"] and len(stage["facts"]) >= 2
    detector = overview.PIPELINE[-1]
    assert detector["status"] == "planned" and "placeholder" in " ".join(detector["facts"])
