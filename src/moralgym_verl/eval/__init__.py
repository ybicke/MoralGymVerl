"""Evaluation package: three runnable experiments plus shared machinery.

Experiment entry points (each runnable via ``python3 -m moralgym_verl.eval.<name>``;
scripts/slurm/eval_teacher_signal.sh chains all three per (game, moral_value) cell):

    behavioral             Rollout eval: what the model DOES. Plays episodes
                           against scripted opponents, reports cooperation /
                           conditioning / reward-regret metrics.
                           -> behavioral.json (+ .responses.jsonl)
    probe_answer_token     Probe A: deterministic answer-token log-odds shift,
                           teacher vs student prompt, per fabricated state.
                           -> logprob_a.json
    probe_reasoning_trace  Probe B: student reasoning traces scored under both
                           prompts (the SDPO distillation pressure); fabricated
                           states or live episodes (per-round signal decay).
                           -> logprob_b.json / logprob_multiturn.json (+traces)

Shared machinery (imported by the entry points, not runnable):

    config           YAML loading, protocol presets, per-episode EpisodeConfig
    model_loading    checkpoint dispatch (base / LoRA merge / full model)
    generation       chat rendering, sampling, policy functions
    metrics          trajectory aggregation for behavioral.json
    scoring          per-decision reward streams; iter_decisions holds the
                     single state-freeze conditioning convention
    baselines        per-game moral maxima/minima -> regret
    teacher_context  SDPO reprompt-template wrapping (torch-free)
    teacher_forcing  logprob measurement primitives shared by the probes
"""
