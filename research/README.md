# Research

Hypothesis-first research log for the quant signal platform.

## Discipline

Every strategy starts with a **written hypothesis file** in `hypotheses/`
before any model code is written. A hypothesis without a numeric
falsification criterion cannot be tested — this is the rule that
prevents this rebuild from becoming a `full_run_v8`.

See `~/.claude/plans/based-on-the-full-harmonic-gosling.md` §8.2 for
the workflow context.

## Files

- `hypotheses/TEMPLATE.md` — copy this when starting a new hypothesis
- `hypotheses/YYYY-MM-DD-<slug>.md` — one file per hypothesis
- `hypotheses/INDEX.md` — running list of active / killed / shipped hypotheses

## Lifecycle

```
draft (writing the hypothesis)
   ↓
registered (final=false; experimentation allowed on train + dev)
   ↓
final (final=true; ONE shot at hold-out)
   ↓
ship | kill (decision recorded in the file; never re-opened)
```

Once a hypothesis is **killed on hold-out**, that hypothesis is dead.
There is no "let me adjust one parameter and re-run". The rebuild plan
§14 explains why this is the single most important discipline.
