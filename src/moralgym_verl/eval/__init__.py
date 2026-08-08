"""Evaluation package: three runnable experiments plus shared machinery.

Experiment entry points (each runnable via ``python3 -m moralgym_verl.eval.<name>``;
scripts/slurm/eval_teacher_signal.sh chains all three per (game, moral_value) cell):

    behavioral             Rollout eval: what the model DOES. Plays episodes
                           against scripted opponents, reports cooperation /
                           conditioning / reward-regret metrics.
                           -> behavioral.json (+ .responses.jsonl)
    probe_a                Probe A: deterministic answer-token log-odds shift,
                           teacher vs student prompt, per fabricated state.
                           -> probe_a.json
    probe_b                Probe B: student reasoning traces scored under both
                           prompts (the SDPO distillation pressure); fabricated
                           states or live episodes (per-round signal decay).
                           -> probe_b.json / probe_b_episode.json (+traces)

Canonical naming (one identifier per experiment, used verbatim as output
filename, top-level JSON result key, metadata `probe` tag, and summary
section in scripts/analysis/summarize_eval_cells.py):

    experiment              canonical id      launcher toggle
    behavioral              behavioral        (always runs)
    answer-token probe      probe_a           RUN_PROBES
    reasoning-trace probe   probe_b           RUN_PROBES
    probe B over episodes   probe_b_episode   RUN_PROBE_B_EPISODE

The teacher-student shift on the answer token is `answer_delta` in BOTH
probes. Presentation axes are labels / layout / label_order / role /
payoffs everywhere (CLI: --eval-<axis>; YAML: evaluation.<axis>; training
dataset: prompt.randomize_<axis>). `prompt.representation` (matrix |
prose | list) is a prompt variant, NOT a presentation axis.

Shared machinery (imported by the entry points, not runnable):

    config           YAML loading, protocol presets, per-episode EpisodeConfig
    model_loading    checkpoint dispatch (base / LoRA merge / full model)
    generation       chat rendering, sampling, policy functions
    metrics          trajectory aggregation for behavioral.json
    scoring          per-decision reward streams + regret baselines;
                     iter_decisions holds the single state-freeze convention
    teacher_context  SDPO reprompt-template wrapping (torch-free)
    teacher_forcing  logprob measurement primitives shared by the probes
"""
