<!-- BEGIN:half-life -->
Fitted by `python -m horus.eval.nowcast_bench fit` at commit `c44746b` on 29 train days (2025-12-02 → 2025-12-30), 37,891,768 passes (aircraft × H3 res-4 cell × 2,000 ft bin). Detected pass: B3 T ≥ 3.25 (the 10 FA/1000 h threshold); base rate 0.0007.

Model: P(next pass by another aircraft detects | a detection dt ago) = base + A·exp(−ln2·dt/t½); weighted least squares on 5-min bins up to 120 min; 95% interval from 200 day-block bootstraps.

| FL band | t½ (min) | 95% interval (min) | A | Base rate | Pairs |
|---|---|---|---|---|---|
| FL200–290 | 11.6 | 3.3–12.5 | 0.027 | 0.0002 | 8,986 |
| FL290–370 | 50.3 | 39.4–62.5 | 0.030 | 0.0008 | 68,416 |
| FL370–460 | 49.9 | 36.9–61.2 | 0.032 | 0.0009 | 40,544 |
<!-- END:half-life -->
