# Progress

One entry per milestone gate. A milestone is **complete** only when its `make gate-mN` is fully green.

## M0 — Setup ✅ complete (2026-11-15)

- `make gate-m0`: ruff ✔, mypy (core) ✔, pytest 26/26 ✔, imports ✔.
- CLAUDE.md, config.yaml (all SPEC parameters, strict loader), canonical track schema, uv + Python 3.12.

## Decisions log

- **2026-11-15: M2 benchmark data.** Use adsb.lol daily history (winter 2025–26, ~16–20 s irregular cadence) now, and re-run the same code at 1 Hz when Trino access arrives. Every reported number names its source and rate. (Rohit)
- **2026-11-15: Gate-order exception.** M2's data-independent core code (synth, geo, segment, residual + synthetic tests) may start before the M1 gate passes. **No M2 benchmark rows until `make gate-m1` is green.** (Rohit)

- **2026-11-15: Trino access approved.** Fetch **CONUS** (SPEC), not the WMKK/OKKK airspaces in the pasted request: only US PIREPs give labels. **Milestones first:** Trino feeds the B0–B3 rows at 1 Hz (M2/M3); LightGBM stays B4 (optional, classification metrics only). (Rohit)
- **2026-11-15: Trino fetch design.** `Trino.history()` only (no raw SQL); explicit columns; server-side FL180 filter; dates ≥ 2023; `cached=True` with each hour's cache file deleted after our Parquet is written (otherwise the raw cache doubles disk use); **1-hour chunks, not 6-hour** (~9M rows/h at CONUS scale; 6 h would exceed 8 GB RAM); stale rows (`time − lastcontact > 15 s`) dropped; 20 GB free-disk floor. Full download size will be decided from the smoke-test numbers.
- **2026-11-15: First Trino smoke run refused.** The login worked, but OpenSky returned `PERMISSION_DENIED: Cannot execute query` for every query. This is an account-permission problem on OpenSky's side; no data came back. Fixes: the fetcher stops on the first USER_ERROR instead of retrying 4× per hour (each retry triggered another browser login); the log directory is created; `keyring` caches the browser-login token so one login covers every hour; `--forget-login` clears it. Waiting on OpenSky.
- **2026-11-15: adsb.lol history download finished.** 43 of 45 days, 12 GB, ~510 M rows. Dec 25 failed because that release is one `.tar` file, not split parts (fixed and re-fetched: 9,939 aircraft, 8.6 M rows; now 44/45 days). **Dec 31 has no release in either the 2025 or 2026 repo**, so it is a known gap in the train split; it is reported, not filled.

- **2026-11-15: M1 gate reading (Rohit).** SPEC §10 says "one full day of CONUS tracks" from OpenSky historical *or Plan B archive*, so the full-day check uses an adsb.lol history day (default 2025-12-02, the first study day), not a live-recorded day. "Recorder survives 24 h" means the process stays alive 24 h. Gaps are accepted only when macOS's power log (`pmset -g log`) shows the laptop asleep for them; they are counted and printed by the gate. Live-recorded days are still held to the strict rule (no unplanned gaps inside the day) if `SRC=opensky` is used.

- **2026-11-15: Project renamed TurbCast → Horus (Rohit).** Package `turbcast` → `horus` (`git mv`, history kept), imports, tests, scripts, docs, config and demo. **The repository folder stays `~/turbcast` until the live recorder stops** (after the M1 gate): the recorder's working directory and its absolute output paths point there, so moving the folder mid-run would break it. Rename the folder afterwards with `mv ~/turbcast ~/horus` and run `uv sync`.

- **2026-11-15: Offline demo + push bundle (Rohit's request; not a milestone).** `demo/` is a stdlib server with a single-page macOS-style UI (Apple HIG styling, light/dark, flat 2D Albers map, no external requests, CSP same-origin). **Synthetic data only**: four scenarios of synthetic aircraft and turbulence, scored by the real `horus.core` (segment → residual → Sheppard, B1); severity thresholds are labelled placeholders until M3; no real track or PIREP metric is shown before the M1 gate. Self-check on the synthetic truth: recovered/injected σ median 0.97–0.98, calm-air p95 σ_corr 48–49 ft/min. `make bundle` → `dist/horus-push-bundle.tar.gz` (+ manifest), tested by extracting into a clean directory and running the suite there. `make gate-demo` = lint, types, demo tests, bundle tests, bundle build, clean git tree. Also fixed: `.gitignore` `data/` matched `demo/data/` (now `/data/`); repository has an MIT `LICENSE`.

- **2026-11-15: Demo restructured as a product flow (Rohit).** Landing = Overview (hero, 4 KPI cards, pipeline stepper, maths from config.yaml, benchmark plan with empty cells); second tab = Live Radar Demo (always dark, flight strips + map + inspector, auto-selects the most turbulent flight by 70 s mean σ_corr). KPIs are computed from the synthetic scenarios (`demo/overview.py`): PIREP correlation 39/39 (synthetic, checks the matching pipeline only), σ recovery 0.97–0.98, calm-air σ_corr p95 48 ft/min (raw 51; median −19%), calm-air false alarms ≥ Light 0.011% with or without Sheppard. Requested wording not used: "adsb.lol 1 Hz" (measured ~20 s), "Horus Classifier" (SPEC: type-normalised detector), "False Positive Reduction" (not supported at the Light threshold).

- **2026-11-15: Demo restyled (Rohit): dark sky theme, less clutter, depth like Argus.** Dark only (toggle removed); twilight-sky backdrop; scroll reveals, breathing hero glow, tilting live mini-radar, staged stepper, KPI count-up (all disabled under prefers-reduced-motion). KPI cards show one number + one line, details on demand; inspector opens with a plain-English summary, secondary metrics folded (open state kept during playback); flight list shows severity words. Quantisation playground shows Sheppard *expected values* and the σ < 40 ft/min limit.

- **2026-11-15: Demo finished (detail pass).** Overview adds The problem (from SPEC §1, no injury statistics), a scenario gallery (stats counted from the data, full-hour thumbnails, opens the scenario in the radar), How to read the radar, Roadmap M0–M7 (status as of today), Honest limits. Radar adds a full-hour altitude profile (scored stretches shaded), PIREP hover cards and a one-time tip. README screenshots in docs/img. Tagged demo-v2.

- **2026-11-15: Demo restyle (Rohit): black theme, less clutter.** Blue sky backdrop removed (pure black, neutral greys, Apple-blue accent only); landing reduced from 10 to 7 sections (how-to-read guide dropped, benchmark plan + limits folded under the roadmap); radar labels only selected/hovered + 3 roughest flights, scenario blurb moved to a tooltip, inspector has one 'More detail' fold.

- **2026-11-15: Demo colour + explanation pass (Rohit).** Black background kept; colour comes from Apple system accents per section (kickers, KPI gradients, pipeline icon tiles, scenario chips, glossary). Pipeline stages are selectable and open a what / why / by-the-numbers panel (facts from data_notes.md); glossary of 10 terms; radar 'Guide & sources' sheet with 6 reading cards; hover explanations on every inspector row and tile.

- **2026-11-15: Demo interactive labs + studio restyle (Rohit).** Each pipeline stage has a lab computed from config.yaml or the scenario data (credit budget 90 s → 3,840/4,000 credits; samples per 15 s window per feed; m/s → 64 ft/min rounding; raw → trend → residual → σ_corr on SYN216; threshold slider: at 90 ft/min 99.6% of rough vs 0.01% of smooth synthetic samples). Restyled after microdose.studio's design language, dark: Geist + Instrument Serif italic (OFL, bundled, licences in demo/fonts-licenses), left-aligned editorial headings, variable-weight hover headline, draggable scenario carousel, charcoal cards with glass sheen.

- **2026-11-15: Demo redesign: de-'vibe-coded' (Rohit).** References: tridentstars.maserati.com and withwise.com (both: black/white + one gold accent, deep navy, hairlines, tiny uppercase labels over plain statements, light display type, slow reveals). Stylesheet rewritten from scratch (the layered 778-line version removed): no glows, gradients, glass sheen, serif italics, hover-weight or coloured dots; KPIs, problem cards and glossary as hairline columns; pipeline as numbered tabs with a gold underline; full-bleed hero with the live sky. σ is never uppercased (it would become Σ).

- **2026-11-15: Demo front end restarted from a blank page (Rohit).** Research first (docs/design-brief.md): aviationweather.gov GFA/PIREP, Aireon, SkyPath read and rendered; Maserati Trident Stars, withwise.com, microdose.studio for structure; Flightradar24/FlightAware/Turbli refused automated access (403) and were not contacted further. New front end: night-sky opening scene; problem; five steps with labs; proof; four skies; dated comparison table (PIREPs, Aireon, SkyPath, Horus; published facts only); roadmap with folds. Radar follows AWC conventions (layer strip, T+ timeline with peak marker, FL range, caret PIREPs, legend; smooth air neutral grey; Moderate #FF9900, Severe AWC #CC0000 brightened to #EC2A2A). Serif font removed.

- **2026-11-15: M2 input = held 1 s series from the adsb.lol archive.** readsb stores a vertical-rate point only when it moves ≥ 200 ft/min from the last stored value, plus a ~20 s heartbeat (source read; on 2025-12-02, 99.5% of heartbeat pairs differ by < 200 ft/min). Each stored value is held until the next point (max gap 25 s). ADS-B rows only; MLAT is excluded (8 ft/min step, no dead band). Trino full-rate data, once available, reruns the same bench.
- **2026-11-15: M2 evaluation design.** PIREP windows and no-PIREP windows have the same shape (±10 min, 40 km, ±2,000 ft), and both need ≥ 5 aircraft in the window. No-PIREP windows are centred on random aircraft-minutes with no turbulence PIREP within ±2 h / 150 km (1,000 candidates per test day, fixed seed). A window's score is the max over aircraft-minutes inside it; a detector with no value there scores 0, so it is charged with the miss. tau per row = lowest score giving ≤ 1 alarm event per 1,000 clear flight-hours on **train days only**. An event = a run of consecutive alarmed minutes on one aircraft (minute resolution, replacing the 30 s merge for the bench). Test-day metrics: PR-AUC, ROC-AUC, recall at tau, test FA rate, precision at recall 0.5.
- **2026-11-15: Bug fixed in `core/segment.py`.** "Level" was assigned up to half a window beyond the observed data, so the 30 s trim was spent on empty grid points and climbs/descents next to data edges and coverage gaps were not trimmed. Found because B2 coverage changed with chunk size. Points must now lie inside observed data. Flicker is no longer detected across held pieces, and B1 round-off is snapped to 0. Scores are now identical under different chunk sizes; demo scenarios regenerated.

## M1 — Ingestion ✅ gate passed (2026-11-15 22:30 UTC)

`make gate-m1 DAY=2026-11-14` (2026-11-15 01:5x AEDT): ruff ✔, mypy ✔, **pytest 110 passed** (1 Trino test deselected until access), then:

| Criterion | Status |
|---|---|
| Full UTC day of CONUS tracks | ✔ adsb.lol history 2025-12-02 (24/24 h) |
| PIREPs for that day | ✔ 5,741 reports (2025-12-02) |
| DuckDB per-aircraft series | ✔ |
| Recorder survives 24 h | ✔ process alive; span 23.67 h; 2 sleep gaps (3.00 h) excused by the power log |
| Quantisation measured (Open Q1) | ✔ 64 ft/min confirmed on real data |

**2026-11-15 07:40 UTC, `make gate-m1` (adsb.lol day 2025-12-02):** ruff ✔ mypy ✔ pytest ✔; full day ✔ (24/24 h, 12.3 M rows, 13,991 aircraft); PIREPs ✔ (5,741); per-aircraft series ✔; quantisation ✔; **recorder 24 h ❌ only on span (8.73 h)**. The process is alive; 2 gaps (3.00 h, lid closed 03:46–06:47 UTC) are accepted because the power log shows the laptop asleep. Fixed while getting here: `store` loaders failed on day-only partitions (now select canonical columns by name).

**2026-11-15 22:30 UTC, `make gate-m1`: PASS.** ruff ✔ mypy ✔ pytest 206 passed ✔; full day ✔ (2025-12-02, 24.00 h, 24/24 h, 12,283,366 rows, 13,991 aircraft); PIREPs ✔ (5,741); per-aircraft series ✔ (ad0074, 22,853 samples); recorder ✔ (OpenSky, process alive, 832 polls, ok 0.989, span 23.67 h, 2 gaps = 3.00 h excused as laptop sleep); quantisation ✔ (adsb_icao: 10.66 M samples, 99.6% on the 64 ft/min grid; MLAT is 8 ft/min, so it is excluded from Sheppard's Δ).

Built: `ingest/live.py` (OpenSky OAuth2 + adsb.lol tiling, credit-aware pacing, backoff, hourly Parquet), `ingest/pireps.py` (IEM + AWC, free-text TB parser), `ingest/aircraft_db.py`, `ingest/historical.py` (Trino, tested offline), `ingest/store.py`, `ingest/quantisation.py`, `ingest/audit.py`, scripts, `reports/data_notes.md`.

Key findings (details in `reports/data_notes.md`):
- Free live sources allow about **90 s** per-aircraft cadence for CONUS, not the SPEC's 5–10 s.
- 64 ft/min vertical-rate quantisation survives OpenSky processing.
- Aircraft DB is 2 years stale; 28% of aircraft at FL200 or above have no type.
- AWC's own PIREP decoder is inconsistent on mixed intensities; we store max and min.
- Also built after the first gate run: `ingest/adsblol_history.py` (streams adsb.lol daily tar releases, CONUS ≥ FL180 to Parquet with bounded memory), `scripts/fetch_adsblol_history.py`. The IEM PIREP ingest is now resumable with retries; an earlier run had silently stopped partway when IEM dropped a connection.
- Study period fixed: 2025-12-02 → 2026-01-15 (most MOD+ PIREPs); train Dec 2–31, test Jan 1–15. History download started 2026-11-15 ~02:13 AEDT (`data/raw/adsblol_hist/_log.csv`).

## M2 — Segmentation, residual, B0–B2 ✅ gate passed (2026-11-16 ~01:30 UTC)

`make gate-m2`: ruff ✔, mypy strict (core + eval) ✔, pytest ✔; 44/44 days scored; B0/B1/B2 rows ✔; thresholds fitted on the 29 train days only ✔.

Results (held 1 s adsb.lol series, plausibility-gated; test 2026-01-01 → 01-15; 1,788 MOD+ and 4,388 no-PIREP windows; prevalence 0.35). Full tables: `reports/detector_ablation.md`.

| Row | PR-AUC | ROC-AUC | Recall @ 1 FA/1000 h | Recall @ 10 FA/1000 h |
|---|---|---|---|---|
| B0 textbook RMS accel | 0.479 | 0.710 | 0.001 | 0.011 |
| B1 + quantisation noise removed | 0.479 | 0.710 | 0.001 | 0.011 |
| B2 + level segments + residual σ | 0.509 | 0.686 | 0.001 | 0.024 |

Findings:
- **B1 = B0** on this source: the 64 ft/min noise floor is a constant shift on a 200 ft/min dead-band series (data_notes.md).
- **Detection at a strict alarm rate is very weak.** "Clear air" means only "no PIREP within 2 h / 150 km", and most turbulence is never reported, so the FA rates are upper bounds. The threshold-free ranking (PR-AUC) improves B0 → B2.
- **Glitch gate.** The first run had thresholds set by decode glitches (±32,640 ft/min field-max code). A fixed physical gate was added; the ungated run is kept as an ablation section.
- **Bugs fixed on the way:** segment trim at data edges, a config edit during a run (crash), PIREPs without a position.

## M3 — Normalisation, B3, rates, window sensitivity ✅ gate passed (2026-11-16 ~01:35 UTC)

`scripts/gate_m3.py`: B3 rows at 1 / 5 / 10 s ✔, 9-cell window sweep ✔, `models/type_norm.parquet` ✔, 6 artefacts leakage-free ✔.

| Rate | B0 PR-AUC | B2 PR-AUC | B3 PR-AUC | B3 recall @ 10 FA/1000 h |
|---|---|---|---|---|
| 1 s | 0.479 | 0.509 | **0.566** | 0.057 |
| 5 s | 0.494 | 0.478 | **0.536** | 0.040 |
| 10 s | 0.461 | 0.482 | **0.550** | 0.043 |

- **B3 normalisation = σ / q99 of that type's smooth-air σ** (fallback size class → global; ≥ 200 aircraft-days per group). The SPEC's robust z is undefined here (90.2% of smooth σ is exactly 0); B3-pct ranks similarly but saturates at 1.0, so its threshold is meaningless. Both are reported.
- **Rate gap.** On this source 5 s and 10 s lose little versus 1 s, because the archive is already dead-band thinned. To be repeated on full-rate Trino data.
- **Window sensitivity.** B3 beats B2 in all 9 cells (±5/10/20 min × 20/40/80 km). PR-AUC grows with window size (more aircraft per window).

