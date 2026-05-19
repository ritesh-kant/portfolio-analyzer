"""Feature store — point-in-time join helpers + parquet readers.

NOTE: Plan §11 lists this under `packages/feature-store/`. Placed here
under quant/ instead because the existing packages/ are all TypeScript
(pnpm-managed); creating a sibling Python package adds tooling overhead
for a solo founder with only one Python app. Promote to packages/ when
a second Python consumer appears.

This sub-package provides:
    pit_join.py — the as_of join primitive used by L2 feature builders.
                  Given a features table and an inference date, returns
                  only rows where features.data_available_at <= inference_date.

The PIT join is the single most important invariant in this rebuild.
Plan §4.3: "eliminates 80% of phantom-alpha bugs".
"""
