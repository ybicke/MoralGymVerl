"""Share of a LoRA update lost when folded into bf16 base weights.

Usage (container): python3 scripts/analysis/lora_fold_loss.py <adapter_dir> <base_model_id>
Reports relative error of the folded delta, share of entries rounded to
zero, and |delta| in units of the bf16 step of W. Numbers quoted in
docs/notes_gpu_execution_and_determinism.md section 8.
"""
import json, sys, torch
from pathlib import Path
from safetensors import safe_open
from huggingface_hub import snapshot_download

adapter, base = sys.argv[1], sys.argv[2]
cfg = json.load(open(Path(adapter) / "adapter_config.json"))
scale = cfg["lora_alpha"] / cfg["r"]
ad = {}
with safe_open(Path(adapter) / "adapter_model.safetensors", "pt") as f:
    for k in f.keys(): ad[k] = f.get_tensor(k)
bdir = Path(snapshot_download(base, allow_patterns=["*.safetensors", "*.json"]))
idx = json.load(open(bdir / "model.safetensors.index.json"))["weight_map"]
handles = {}
def get(k):
    fn = idx[k]
    if fn not in handles: handles[fn] = safe_open(bdir / fn, "pt")
    return handles[fn].get_tensor(k)

tot_d2 = tot_e2 = tot_zero = tot_n = 0
ratios = []
for k in sorted(ad):
    if ".lora_B." not in k: continue
    bk = k.replace("base_model.model.", "").replace(".lora_B.weight", ".weight")
    W = get(bk)                                   # bf16
    d = scale * (ad[k].float() @ ad[k.replace(".lora_B.", ".lora_A.")].float())
    merged = (W.float() + d).to(torch.bfloat16)
    kept = merged.float() - W.float()             # what the bf16 merge actually added
    err = kept - d
    tot_d2 += (d * d).sum().item(); tot_e2 += (err * err).sum().item()
    tot_zero += (kept == 0).sum().item(); tot_n += d.numel()
    ulp = (W.float().abs() * 2.0 ** -8).clamp_min(1e-30)
    ratios.append((d.abs() / ulp).flatten()[::997])
r = torch.cat(ratios); r = r[torch.randperm(r.numel())[:2_000_000]]
print(f"relative error of folded delta ||kept-delta||/||delta|| = {(tot_e2 / tot_d2) ** 0.5:.3f}")
print(f"elements where the delta vanished entirely: {tot_zero / tot_n:.1%}")
print(f"|delta| / (bf16 resolution of W): median {r.median():.3f}, 90th pct {r.quantile(0.9):.3f}")
