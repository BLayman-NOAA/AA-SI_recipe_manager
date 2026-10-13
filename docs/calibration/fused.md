# One-step calibration recipe

[`generate_standardized_cal_mapping`](../ops/generate_standardized_cal_mapping.md)
runs the whole calibration stream, scan, standardize and map, as one step. It
is the simplest way to calibrate a survey, and what the
[HB2407 pipeline](../levels/level-0.md#hb2407) uses in its level 0.

The trade-off is caching: one step is one cache entry. A crash part way
through the raw file scan loses the whole scan, and a corrected calibration
file re-runs the scan along with everything else. The
[staged pipeline](index.md#the-complete-pipeline-on-its-own) caches each stage
separately, which is worth it for a long survey.

The step is marked `regenerate: always`, because its real product is the
files it writes for NCEI: a cached result would return the dictionaries
without rewriting them.

--8<-- "data/partials/calibration-fused.md"
