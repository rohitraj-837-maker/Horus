"""Aircraft DB tests on real slices of the OpenSky DB and ICAO Doc 8643."""

import pandas as pd
import pytest

from horus.ingest import aircraft_db as adb


@pytest.fixture
def types(fixtures_dir):
    return adb.build_type_table(pd.read_csv(fixtures_dir / "doc8643_sample.csv", dtype=str))


@pytest.fixture
def aircraft(fixtures_dir, types):
    db = pd.read_csv(fixtures_dir / "aircraft_db_sample.csv", dtype=str)
    return adb.build_aircraft_table(db, types)


@pytest.mark.parametrize(
    "typecode,expected",
    [
        ("A388", "super"),
        ("B77W", "heavy"),
        ("B789", "heavy"),
        ("A320", "medium_jet"),
        ("B738", "medium_jet"),
        ("CRJ7", "medium_jet"),
        ("E75L", "medium_jet"),
        ("DH8D", "medium_prop"),
        ("C172", "light"),
    ],
)
def test_size_classes_from_real_doc8643(types, typecode, expected):
    assert types.set_index("typecode").loc[typecode, "size_class"] == expected


def test_type_table_unique(types):
    assert types["typecode"].is_unique


def test_aircraft_table_cleans_rows(aircraft):
    assert aircraft["icao24"].str.fullmatch(r"[0-9a-f]{6}").all()  # blank row dropped
    assert aircraft["icao24"].is_unique
    assert aircraft.set_index("icao24").loc["aa3487", "size_class"] == "UNK"  # BE36 not in sample
    assert aircraft.set_index("icao24").loc["4d21cf", "size_class"] == "medium_jet"


def test_attach_types_inline_wins(aircraft, types):
    rec = pd.DataFrame(
        {
            "icao24": ["4d21cf", "06a2ec", "ffffff"],
            "typecode": [None, "A388", None],  # second: feed says A388, DB says B789
        }
    )
    out = adb.attach_types(rec, aircraft, types).set_index("icao24")
    assert out.loc["4d21cf", "typecode_resolved"] == "B738"
    assert out.loc["06a2ec", "typecode_resolved"] == "A388"
    assert out.loc["06a2ec", "size_class"] == "super"
    assert pd.isna(out.loc["ffffff", "typecode_resolved"])
    assert out.loc["ffffff", "size_class"] == "UNK"


def test_size_class_function():
    assert adb.size_class(None, None, None) == "UNK"
    assert adb.size_class("XXXX", "L/M", "Piston") == "light"
    assert set(adb.SIZE_CLASSES) >= {"super", "heavy", "medium_jet", "medium_prop", "light"}
