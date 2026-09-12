# MoralGymVerl

## Commits

Every commit is one logical, tested change, described so that a reader of
`git log --oneline` a year from now knows what changed and why. Commit only
when asked; never commit generated outputs (figures, PDFs, eval results).

### Message shape

```
<scope>: <imperative summary, <= 72 chars, no trailing period>

<body, wrapped at 72: why the change was needed and what it replaces.
Omit for self-explanatory one-liners. Never restate the diff.>
```

- `scope` is the area touched, lowercase, one word or a path stem:
  `analysis`, `eval`, `training`, `configs`, `tests`, `docs`, `readme`,
  `make_report`, `train_verl`. Use `repo` for cross-cutting changes.
- Summary is an imperative verb phrase: `add`, `drop`, `rename`, `fix`,
  `split`, not `added`, not a noun phrase, not a sentence with a period.
- One change per commit. If the summary needs a semicolon, `+`, or `and`
  to join unrelated items, split the commit. If the items are one change
  (a figure and its spec), name the change and list the parts in the body.
- The body states the reason, not the mechanics: which figure was
  unreadable, which test was stale, which decision this implements.
- Do not append tool attribution or Co-Authored-By trailers.

### Before / after (from this repo's history)

Bad, a changelog of a session stuffed into the subject:

```
qwen3_4b control: GRPO/SDPO training + eval configs, post-training and
transfer results; combined in-play figure (base + Hanabi-RL + SDPO
s80/s150) and FourB number macros in make_report; Qwen3-4B-Instruct-2507
in template preflight
```

Good, three commits:

```
configs: add qwen3_4b GRPO and SDPO control arms

Same trainer config as the 8B runs; only model and RUN_NAME differ.
Needed to test whether the Hanabi null transfer is a capacity artifact.
```
```
make_report: combined 4B in-play figure with FourB number macros
```
```
eval: add Qwen3-4B-Instruct-2507 to the template preflight
```

Bad, one change recorded as five commits in ten minutes:

```
make_report: util training grid
util joins the GRPO training grid as fourth panel
GRPO grid: drop Llama panel (no downstream evals), keep util control
GRPO training grid: qwen deon vs qwen util only (same-model norm contrast)
transfer final: wrap to 2x2 for 4+ runs
```

Good, squashed before pushing:

```
make_report: GRPO training grid compares qwen deon vs qwen util

Drops the Llama panel (no downstream evals) so the grid reads as a
same-model norm contrast. Transfer-final figure wraps to 2x2 for 4+ runs.
```

### Workflow

- Iterate in small local commits while tuning; squash them into logical
  units before pushing (`git rebase -i origin/main`, or `git commit
  --fixup <sha>` then `git rebase -i --autosquash`). Never rewrite pushed
  history.
- Stage with `git add -p` when a working tree holds more than one change.
- Branch per study or feature, named for the change (`pgg-transfer-eval`,
  not a month-old screen name). Merge into main with a summarizing
  `--no-ff` message and delete the branch. Small fixes go straight to main.
- Tag frozen states that a report or paper depends on:
  `git tag -a report-2026-09-figs -m "..."`.
