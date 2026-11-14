import h3
import numpy as np
import pytest

from turbcast.core import geo


@pytest.mark.parametrize(
    "alt_ft,p_hpa",
    [
        (0, 1013.25),
        (30000, 300.9),
        (34000, 250.0),
        (36089.24, 226.32),
        (39000, 196.8),
        (45000, 147.5),
    ],
)
def test_isa_pressure(alt_ft, p_hpa):
    assert geo.pressure_hpa(alt_ft) == pytest.approx(p_hpa, rel=0.003)


def test_isa_roundtrip_and_continuity():
    h = np.linspace(0, 60000, 601)
    np.testing.assert_allclose(geo.altitude_ft(geo.pressure_hpa(h)), h, atol=0.5)
    below, above = geo.pressure_hpa([36089.0, 36089.5])
    assert abs(below - above) < 0.01


def test_haversine_melbourne_sydney():
    d = geo.haversine_km(-37.8136, 144.9631, -33.8688, 151.2093)
    assert d == pytest.approx(713.4, abs=1.0)
    assert geo.haversine_km(40, -100, 40, -100) == 0


def test_fl_bin():
    got = geo.fl_bin([35000, 19999, 20000, 45999, 46000, np.nan], 200, 460, 20)
    assert got.tolist() == [7, -1, 0, 12, -1, -1]


def test_h3_cells():
    c = geo.h3_cells([40.0, np.nan, 40.0001], [-100.0, -100.0, -100.0001], 4)
    assert c[0] == c[2] and c[1] is None
    assert h3.get_resolution(c[0]) == 4
    # SPEC says "~22 km edge" (old H3 v3 table); h3 v4 gives 26.07 km, area ~1,770 km^2.
    assert h3.average_hexagon_edge_length(4, unit="km") == pytest.approx(26.07, abs=0.05)
    assert h3.average_hexagon_area(4, unit="km^2") == pytest.approx(1770, rel=0.01)
