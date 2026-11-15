"""PIREP parser tests. TB strings are real ones from the IEM archive (2026-11-10)
unless marked; expected values are hand-labelled."""

import json
import math

import numpy as np
import pytest

from horus.ingest import pireps as p

NAN = math.nan

# (tb text, report FL, tb_max, tb_min, fl_lo, fl_hi, tb_type)
LABELLED = [
    ("NEG", 350, 0, 0, 350, 350, None),
    ("MOD", 350, 4, 4, 350, 350, None),
    ("LGT CHOP", 350, 2, 2, 350, 350, "CHOP"),
    ("CONS LGT OCNL MOD", 350, 4, 2, 350, 350, None),
    ("LGT-MOD CHOP", 350, 3, 3, 350, 350, "CHOP"),
    ("LT CHP", 350, 2, 2, 350, 350, "CHOP"),
    ("MD TURB", 350, 4, 4, 350, 350, None),
    ("LIGHT TO MOD TURB", 350, 3, 3, 350, 350, None),
    ("MOD CHOP FL280-240", 350, 4, 4, 240, 280, "CHOP"),
    ("CONS MOD CHOP 320-360", 350, 4, 4, 320, 360, "CHOP"),
    ("LGT BLO 040", 350, 2, 2, 0, 40, None),
    ("MOD TURB ABV 070", 120, 4, 4, 70, 120, None),
    ("MOD CHOP SFC-025", 350, 4, 4, 0, 25, "CHOP"),
    ("MOD090", 90, 4, 4, 90, 90, None),
    ("MOD-SEV", 350, 5, 5, 350, 350, None),
    ("OCNL LGT - MOD 380", 380, 3, 3, 380, 380, None),
    ("CONS LGT-OCNL MOD CHOP", 350, 4, 2, 350, 350, "CHOP"),
    ("NEG CHOP NEG CAT", 350, 0, 0, 350, 350, "CHOP"),
    ("INTMT LG TCHOP", 350, 2, 2, 350, 350, None),
    ("LGT OCCNL MOD CHOP", 350, 4, 2, 350, 350, "CHOP"),
    ("SMOOTH RIDE", 350, 0, 0, 350, 350, None),
    ("MOD MTN WAVE", 350, 4, 4, 350, 350, "MTN WAVE"),
    ("LGT-MOD 400-395", 400, 3, 3, 395, 400, None),
    ("CONS MOD 035-010", 30, 4, 4, 10, 35, None),
    ("NEG BLO 100", NAN, 0, 0, 0, 100, None),
    ("LGT TB SFC-170", 120, 2, 2, 0, 170, None),
    ("MOD-SEV CAT FL370-390", 380, 5, 5, 370, 390, "CAT"),  # constructed
    ("SEV TURB", 330, 6, 6, 330, 330, None),  # constructed
    ("SO FAR  SO GOOD", 125, -1, -1, 125, 125, None),
]


@pytest.mark.parametrize("tb,rfl,tmax,tmin,lo,hi,ttype", LABELLED)
def test_labelled_tb_strings(tb, rfl, tmax, tmin, lo, hi, ttype):
    fl = "UNKN" if math.isnan(rfl) else f"{int(rfl):03d}"
    r = p.parse_report(f"XXX UA /OV ABC/TM 1200/FL{fl}/TP B738/TB {tb}/RM TEST")
    assert (r["tb_max"], r["tb_min"]) == (tmax, tmin)
    assert r["fl_lo"] == pytest.approx(lo, nan_ok=True)
    assert r["fl_hi"] == pytest.approx(hi, nan_ok=True)
    assert r["tb_type"] == ttype
    assert r["parse_ok"] == (tmax != -1)


def test_labelled_table_is_big_enough():
    assert len(LABELLED) >= 20


def test_icing_group_does_not_leak_into_turbulence():
    raw = "BRW UA /OV BRW/TM 0000/FL080/TP BE20/SK TOP080 OVC020 /WX M15/TB NEG/IC MOD RIME/RM DURD. ZAN"
    r = p.parse_report(raw)
    assert r["tb_max"] == 0 and r["report_fl"] == 80 and r["tp"] == "BE20"
    assert r["phase"] == "DURD"


def test_no_tb_group():
    raw = "JNU UUA /OV JNU /TM 0000 /FLUNKN /TP C208 /RM DURD RWY 8 SMOOTH TILL CUT LLWS ON SHORT FINAL"
    r = p.parse_report(raw)
    assert not r["has_tb"] and r["parse_ok"] and r["tb_max"] == -1
    assert math.isnan(r["report_fl"])


@pytest.mark.parametrize(
    "raw,tb,fl",
    [
        ("ARP UAL1758 3909N 11231W 2229 F360 MS52 314/096KT TB NONE SK CLEAR IC NONE", 0, 360),
        ("ARP UAL2851 3513N 10702W 2211 F330 306/103KT TB SMTH IC", 0, 330),
    ],
)
def test_airep_format(raw, tb, fl):
    r = p.parse_report(raw)
    assert r["tb_max"] == tb and r["report_fl"] == fl and r["fl_lo"] == fl


def test_durd_with_blo():
    raw = "000 UBCO90 KWBC 142230 CO 142230 DEN UA /OV DEN/TM 2227/FLDURD/TP CRJ7/TB NEG BLO 100"
    r = p.parse_report(raw)
    assert r["phase"] == "DURD" and (r["fl_lo"], r["fl_hi"]) == (0, 100)


def test_intensity_word_boundaries():
    assert p.parse_intensity("MODE S") == (-1, -1)
    assert p.parse_intensity("LGT-MOD") == (3, 3)
    assert p.parse_intensity("MOD TO SEV") == (5, 5)


# ---------------------------------------------------------------- whole-file fixtures


@pytest.fixture
def iem(fixtures_dir):
    return p.read_iem_csv((fixtures_dir / "iem_pireps_sample.csv").read_text())


def test_iem_fixture_parses(iem):
    assert len(iem) == 514
    assert iem["raw"].str.contains('"').any()  # stray-quote rows survived
    t0, t1 = 1_794_268_800, 1_794_355_200  # 2026-11-10 UTC
    assert iem["time"].between(t0, t1).all()
    assert iem["lat"].between(-90, 90).all() and iem["lon"].between(-180, 180).all()
    ok = iem["fl_lo"].notna()
    assert (iem.loc[ok, "fl_lo"] <= iem.loc[ok, "fl_hi"]).all()
    stats = p.parse_stats(iem)
    assert stats["tb_parse_fail_rate"] < 0.05
    assert stats["mod_plus"] > 0


def test_iem_fallback_fl_from_feet_column(iem):
    # Row with "/FLUNKN" but IEM FL column None stays NaN; rows with FL feet get it.
    row = iem[iem["raw"].str.contains("/FL080/TP BE20")].iloc[0]
    assert row["report_fl"] == 80


def test_dedupe_removes_reissued_products(iem):
    d = p.dedupe(iem)
    assert len(d) < len(iem)
    assert len(p.dedupe(d)) == len(d)


def test_awc_fixture_agrees_with_awc_decoder(fixtures_dir):
    items = json.loads((fixtures_dir / "awc_pireps_sample.json").read_text())
    df = p.read_awc_json(items)
    awc_level = {"NEG": 0, "SMTH": 0, "LGT": 2, "LGT-MOD": 3, "MOD": 4, "MOD-SEV": 5, "SEV": 6}
    checked = 0
    for item, (_, row) in zip(items, df.iterrows(), strict=True):
        lvl = awc_level.get(item["tbInt1"])
        if lvl is None:
            continue
        checked += 1
        # AWC picks one value inside the reported range; ours brackets it.
        assert row["tb_min"] <= lvl <= row["tb_max"], item["rawOb"]
    assert checked >= 20


def test_empty_inputs():
    assert p.read_awc_json([]).empty
    assert p.read_iem_csv("").empty
    assert np.dtype(p.read_iem_csv("").dtypes["tb_max"]) == np.int8


def test_iem_no_results_reply_is_empty():
    # real IEM reply for a window with no reports (e.g. a day that has not started)
    df = p.read_iem_csv("ERROR: no results found for your query\n")
    assert df.empty and list(df.columns) == [f.name for f in p.PIREP_SCHEMA]


def test_iem_other_error_or_bad_header_raises():
    with pytest.raises(ValueError, match="IEM returned an error"):
        p.read_iem_csv("ERROR: invalid sts\n")
    with pytest.raises(ValueError, match="unexpected IEM CSV header"):
        p.read_iem_csv("A,B,C\n1,2,3\n")


def test_ingest_iem_refuses_future_day(tmp_path):
    from datetime import UTC, datetime, timedelta

    future = datetime.now(UTC).date() + timedelta(days=2)
    with pytest.raises(ValueError, match="has not started yet"):
        p.ingest_iem(future, future + timedelta(days=1), out_dir=str(tmp_path))
