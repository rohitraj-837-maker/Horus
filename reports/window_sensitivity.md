### Ungated ablation (no plausibility gate; scores in data/derived/scores)

<!-- BEGIN:window-sensitivity-1s-nogate -->
MOD+ test windows, data rate 1 s, tau fixed from train (it does not depend on the match window). Altitude ±2,000 ft throughout. Same window centres in every cell; >= 5 aircraft required.

| Window | Radius | Positives | Negatives | B2 PR-AUC | B3 PR-AUC | B2 recall @ tau | B3 recall @ tau |
|---|---|---|---|---|---|---|---|
| ±5 min | 20 km | 118 | 483 | 0.383 | 0.493 | 0.000 | 0.000 |
| ±5 min | 40 km | 802 | 2199 | 0.468 | 0.519 | 0.000 | 0.000 |
| ±5 min | 80 km | 2901 | 6163 | 0.542 | 0.595 | 0.000 | 0.000 |
| ±10 min | 20 km | 496 | 1650 | 0.391 | 0.440 | 0.000 | 0.000 |
| ±10 min | 40 km | 1788 | 4419 | 0.499 | 0.557 | 0.000 | 0.000 |
| ±10 min | 80 km | 4010 | 8539 | 0.545 | 0.603 | 0.000 | 0.000 |
| ±20 min | 20 km | 1395 | 3885 | 0.436 | 0.506 | 0.000 | 0.000 |
| ±20 min | 40 km | 3186 | 7324 | 0.520 | 0.584 | 0.001 | 0.001 |
| ±20 min | 80 km | 4993 | 10947 | 0.543 | 0.603 | 0.001 | 0.001 |
<!-- END:window-sensitivity-1s-nogate -->

<!-- BEGIN:window-sensitivity-1s -->
MOD+ test windows, data rate 1 s, tau fixed from train (it does not depend on the match window). Altitude ±2,000 ft throughout. Same window centres in every cell; >= 5 aircraft required.

| Window | Radius | Positives | Negatives | B2 PR-AUC | B3 PR-AUC | B2 recall @ tau | B3 recall @ tau |
|---|---|---|---|---|---|---|---|
| ±5 min | 20 km | 118 | 497 | 0.366 | 0.510 | 0.000 | 0.000 |
| ±5 min | 40 km | 802 | 2207 | 0.474 | 0.535 | 0.000 | 0.001 |
| ±5 min | 80 km | 2901 | 6159 | 0.549 | 0.604 | 0.001 | 0.001 |
| ±10 min | 20 km | 496 | 1677 | 0.394 | 0.442 | 0.000 | 0.000 |
| ±10 min | 40 km | 1788 | 4388 | 0.509 | 0.566 | 0.001 | 0.002 |
| ±10 min | 80 km | 4010 | 8565 | 0.551 | 0.613 | 0.001 | 0.002 |
| ±20 min | 20 km | 1395 | 3917 | 0.433 | 0.503 | 0.001 | 0.001 |
| ±20 min | 40 km | 3186 | 7348 | 0.524 | 0.590 | 0.002 | 0.003 |
| ±20 min | 80 km | 4993 | 10945 | 0.552 | 0.615 | 0.004 | 0.004 |
<!-- END:window-sensitivity-1s -->
