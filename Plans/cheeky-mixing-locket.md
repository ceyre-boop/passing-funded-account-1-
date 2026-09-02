# Clear the board: restore what a hard reset destroyed, then run the three open tests

## Context

Last request of the night: run the remaining open questions in order and come
back with one report.

While verifying the data for those tests I found that **commit `65009f4` claims
four changes and contains one.** During the pre-push proof I ran
`git reset --hard HEAD~1`, which discarded three *uncommitted* patches, and then
wrote a commit message describing them anyway. Only `.githooks/pre-push`
survived, because untracked files are not touched by a hard reset.

Destroyed and still missing:

| claimed in `65009f4` | actual state |
|---|---|
| first-order signal gate in `scripts/self_play.py` | **absent** |
| machine-ran assertions in `scripts/build_experience.py` | **absent** |
| sealed 100-session holdout | **absent** — `experience.npz` has no seal keys and holds the pre-seal 16,800 rows |
| `.githooks/pre-push` | present, tracked, working |

This is the failure class the repo keeps finding — a claim believed because it
has the right name with nothing behind it — committed inside a change about
preventing it. The record gets corrected in the same commit that restores the
work; it does not get quietly amended.

**Root cause worth fixing, not just noting:** I used `git reset --hard` as a
cleanup step with uncommitted work in the tree. Cleanup should be
`git restore <specific paths>` or a `git stash`, never a hard reset of the whole
tree.

## Part 0 — restore and correct

Re-apply the three lost patches, each with the assertion its patcher was missing
the first time (the `savez` edit failed silently because that one `.replace()`
had no `assert count == 1`):

- `scripts/self_play.py` — first-order gate: strongest single-feature
  `|corr|` computed and printed **before** learning, refusing below `0.05`;
  `--ignore-first-order` as a recorded decision, not a default.
- `scripts/build_experience.py` — verify-the-machine-ran assertions: the
  `e.ts[11:16]` slice must yield `HH:MM`; decisions-per-session in range;
  outcomes beyond the stop under 25%.
- `scripts/build_experience.py` — `--seal-holdout N`, withholding the most
  recent sessions **before** the table is built, with the boundary written into
  the archive. Verify the keys are actually in the `.npz` afterwards — that is
  the check that was missing.

Then rebuild `experience.npz` and confirm the seal keys read back.

`artifacts/CORRECTION_2026-09-01.md` records what `65009f4` claimed, what it
contained, the cause, and the process fix.

## Part 1 — MECH-006, the last open hypothesis on the entry layer

*"The entry veto carries information a rate-matched coin does not: the days it
refuses are worse than average, not merely fewer."*

Data is already on disk: `data/daytrade/disengagement/` — 39 `ENTRY` rows and 39
rate-matched `VETO_CONTROL` rows, each with a sealed `forward_range_atr`.

Compare forward magnitude on entered bars against refused bars. Report the
effect **with its detection floor beside it** (`gate/discovery.py`) at
n = 39 per arm, which is thin and will probably dominate the answer. A verdict
either way closes the last standing entry-layer hypothesis.

## Part 2 — the semantic lane, sized honestly

Every null in this repo is on **price-derived features**. The operator is the one
component reading a different information source (news, evidence, events) and it
has 13 resolved judgments.

Not a search — an arithmetic check on what 13 buys: what effect size is even
detectable at n=13 versus the gate's n=50 requirement, and what the 13 resolved
rows show against their own baselines. The output is a number that says how far
from a real test that lane is.

## Part 3 — magnitude versus direction

The repo's one surviving positive finding is that **magnitude is detectable and
direction is not** — four event studies cleared their detection floor, all three
directional ones failed.

Test it directly on the experience table: the 13 features fail to predict signed
outcome (max `|corr|` 0.0378). Do they predict `max(r_long, r_short)` — how big
the opportunity was, regardless of side? If yes, that is a real asymmetry and it
points at direction-free instruments. If no, the magnitude finding does not
survive into this feature set and that closes it too.

## Files

| file | change |
|---|---|
| `scripts/self_play.py` | restore the first-order gate |
| `scripts/build_experience.py` | restore assertions + `--seal-holdout`, verify keys land |
| `scripts/mech006_test.py` | new — veto vs rate-matched control |
| `scripts/semantic_readiness.py` | new — what n=13 buys |
| `scripts/magnitude_vs_direction.py` | new — signed vs unsigned predictability |
| `artifacts/CORRECTION_2026-09-01.md` | the false-commit record |
| `artifacts/CLEAR_THE_BOARD.md` | the three results in one place |

Reuse: `gate/discovery.py::Finding` for every effect-with-floor, the existing
`disengagement` readers, and `body/features.py`. No new statistics are written.

## Verification

```bash
python3 -c "import numpy as np; d=np.load('data/daytrade/experience.npz'); print(list(d.keys()))"
.venv-v1/bin/python scripts/self_play.py            # must REFUSE on the first-order gate
.venv-v1/bin/python scripts/mech006_test.py
.venv-v1/bin/python scripts/semantic_readiness.py
.venv-v1/bin/python scripts/magnitude_vs_direction.py
python3 -m pytest gate/ az/ daytrade/ scripts/ -q && .venv-v1/bin/python -m pytest body/ -q
```

Every effect is reported with its detection floor in the same line. No result is
quoted without n.

## Out of scope

No new model, no policy change, no touching the sealed holdout, no fourth
price-feature search. The three tests are diagnostics that close questions; none
of them opens a build.
