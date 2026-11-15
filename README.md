# Horus

Live turbulence detection and nowcasting from public aircraft broadcasts (ADS-B), validated against pilot reports (PIREPs) over the continental US.

> **Research prototype. Validated over continental US only. Not for operational use.**

Status: under construction (see `SPEC.md` and `PROGRESS.md`). Headline results will be filled in from `reports/` once benchmarks exist.

## Data sources and attribution

**OpenSky Network.** Live and historical (Trino) state vectors are used under the OpenSky Network terms of use for academic, non-commercial research: <https://opensky-network.org/about/terms-of-use>. As the terms require, please cite:

> Matthias Schäfer, Martin Strohmeier, Vincent Lenders, Ivan Martinovic and Matthias Wilhelm. "Bringing Up OpenSky: A Large-scale ADS-B Sensor Network for Research". In *Proceedings of the 13th IEEE/ACM International Symposium on Information Processing in Sensor Networks (IPSN)*, pages 83–94, April 2014.

**adsb.lol** historical traces (`adsblol/globe_history_*`), © adsb.lol contributors, licensed under the [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).

**PIREPs:** Iowa Environmental Mesonet PIREP archive (Iowa State University) and the NOAA Aviation Weather Center Data API (aviationweather.gov).

**Aircraft types:** OpenSky aircraft database and ICAO Doc 8643 type designators.

**Winds:** NOAA GFS (via Open-Meteo).
