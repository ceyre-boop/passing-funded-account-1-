# Correction — commit `65009f4` claimed four changes and contained one

**Found:** 2026-09-01, while verifying data for a later test.
**Severity:** the commit message described work that did not exist in the repo.

## What happened

`65009f4` ("Postmortem transfer: first-order gate, machine-ran assertions,
sealed holdout, pre-push") claimed four changes.

| claimed | actually in the commit |
|---|---|
| first-order signal gate in `scripts/self_play.py` | **absent** |
| machine-ran assertions in `scripts/build_experience.py` | **absent** |
| sealed 100-session holdout | **absent** |
| `.githooks/pre-push` | present |

`git show --stat 65009f4` is one file, 33 insertions.

## Cause

While proving the new pre-push hook worked, I made a deliberately-red commit,
confirmed the push was refused, and cleaned up with:

```
git reset --hard HEAD~1
```

Three of the four changes were **uncommitted working-tree edits at that moment**.
A hard reset discards them. `.githooks/pre-push` survived only because it was
untracked at the time, and untracked files are not touched by a hard reset.

I then wrote the commit message from memory of what I had done, not from the
diff.

## Why this is the repo's own recurring failure

A claim believed because it carries the right name, with nothing behind it —
recorded inside a commit whose subject was preventing exactly that. The same
shape as the cost monitor's 412 empty samples, the `.githooks` directory that
never existed, the guard that tested a helper instead of the path, and the
`sl_trigger_price` fix that passed every test while reverted.

It also had a second cause worth naming: one of the three patches failed
silently because that particular `.replace()` had no `assert count == 1`. Two of
the three were destroyed by the reset; the `savez` edit had already failed on
its own and I did not notice, because nothing checked that the keys landed.

## Fix

All three changes are restored, each patch asserted, and the check that was
missing is now run explicitly:

```
keys: ['X','r_long','r_short','day','ts','n_sessions','sealed_from','sealed_to','n_sealed']
sealed: 2026-04-06 .. 2026-08-26  (100 sessions)
```

`scripts/self_play.py` refuses on the first-order gate, verified.

## Process change, not a note

- **Never `git reset --hard` as a cleanup step.** Use `git restore <paths>` or
  `git stash`. A hard reset is a whole-tree operation used here for a
  single-file problem.
- **Write commit messages from `git show --stat`, not from memory.**
- **Every scripted patch asserts its match count.** The one that did not is the
  one that vanished silently.
