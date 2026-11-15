# Horus demo — design brief (restart, 2026-11-15)

The previous six demo iterations were judged "vibe coded": decoration accumulated faster than meaning.
This brief restarts the front end from a blank page, grounded in how related sites actually work.

## 1. What was studied

| Site | Read how | What it is |
|---|---|---|
| aviationweather.gov GFA + PIREP pages | text + rendered screenshot | The US reference for turbulence products |
| Aireon — AireonINSIGHTS Turbulence | text + screenshot | Commercial ADS-B turbulence (NCAR EDR algorithm) |
| SkyPath (home, pilots) | text + screenshot | Commercial crowdsourced turbulence platform |
| Maserati Trident Stars | rendered screenshot + CSS | Cinematic night-sky campaign (user reference) |
| withwise.com | full text + CSS | Calm, structured product page (user reference) |
| microdose.studio | full text + CSS | Low-density studio page (user reference) |
| Flightradar24, FlightAware, Turbli | **not studied** | They refuse automated access (HTTP 403); that refusal was respected |
| Windy | not studied | JavaScript-only; rendered blank |
| IATA Turbulence Aware | not studied | Both known URLs return 404 |

## 2. Findings

**Aviation conventions (aviationweather.gov) — follow these so aviation readers trust the demo**
- Dark grey basemap; data drawn on top. Layer toolbar as a vertical icon strip on the right; a "Legend" button.
- Time is UTC with a `Z` suffix; a **bottom timeline** with tick labels and a `Now` marker; a clock in the header.
- Altitude in flight levels (FL), selected with a top/base range.
- PIREPs are their own layer with their own symbols (turbulence = caret `^`), filters for type, minimum intensity and maximum age (0.25–12 h), and the flight level printed next to the symbol.
- Intensity classes Light / Moderate / Severe (/ Extreme). Chart legend colours seen: Moderate `#FF9900`, Severe `#CC0000` (the demo brightens Severe to `#EC2A2A` so it reads on black).
- Official turbulence is expressed as EDR (m^2/3 s^-1, shown ×100). **Horus does not compute EDR** — it reports σ_corr in ft/min — so the demo must say so instead of borrowing EDR's authority.

**Comparable products (SkyPath, Aireon)**
- Both pitch "aircraft as sensors"; SkyPath even lists "ADS-B VR" as a source, Aireon uses NCAR's EDR algorithm per aircraft type.
- Neither shows a validation method on its public pages; SkyPath quotes accuracy percentages without a definition, and its headline numbers disagree across pages.
- **Horus's honest differentiator:** an explainable method, every number computed live from data, and a stated validation plan against PIREPs with a time split. Lead with transparency, not with claims.

**Storytelling (Maserati, Wise, microdose)**
- One idea per section: a small label, one headline, 20–40 words, at most one action.
- Short declarative headlines ("Stay compliant. Pay on time."; "Every pixel matters.").
- Proof is brief and concrete, mid-to-late page; trust and limits near the end.
- One primary call to action, repeated at top, middle and end with the same label.
- Maserati's hook: a night-side Earth horizon, enormous ultralight type split across the screen, one rectangular "Start the experience" button. 8–13 sections in total on Wise/microdose; Maserati is a single scene.

## 3. Decisions

1. **Palette.** Black. Off-white text. Turbulence colours follow aviation: Light `#E9D46A`, Moderate `#FF9900`, Severe `#CC0000` (AWC's Moderate/Severe; Light is our choice and labelled as such). Smooth air is **neutral grey**, not green — attention belongs on turbulence. No other accent colour: interactive emphasis is white.
2. **Type.** Geist (bundled, OFL). Display: weight 200–300, large, uppercase only for short scene lines; body 300–400. Never uppercase anything containing σ.
3. **Landing (8 sections).** Scene (Earth-limb hook, split headline, one button) → Problem → Method in four steps (Listen, Clean, Score, Map) with one lab each → Proof (four computed numbers + caveat) → Try it (four scenarios) → How it compares (published facts only, dated) → Where it stands (roadmap + limits + sources) → Start the radar.
4. **Radar console = aviation layout.** Full-bleed dark map; header with `Z` clock and scenario; right vertical layer strip (aircraft, trails, PIREPs, turbulence field, labels) + Legend; right-edge FL range; **bottom Z-style timeline** with play and speed; left "Roughest now" list; selected-flight panel (summary sentence, telemetry, σ_corr vs thresholds, altitude profile, detector values, matching PIREPs).
5. **PIREPs** drawn as caret symbols coloured by intensity with the FL beside them, filterable by minimum intensity and maximum age.
6. **Honesty.** Every number on the page is computed from the bundled data or read from `config.yaml`. Synthetic data is labelled where it appears. Competitor statements are quoted from their sites with the date checked.
7. **Motion.** Slow fades for text; the scene's aircraft drift; nothing else moves on its own.
