"""Checkpoint loading for evaluation: base models, LoRA adapters, full checkpoints."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

logger = logging.getLogger(__name__)


def merge_lora_weights(model, checkpoint_path: str):
    """Merge LoRA adapter weights into the base model in-place:
    W' = W + (lora_alpha / r) * lora_B @ lora_A per adapted layer. Same
    math as PeftModel.merge_and_unload(), without the peft dependency
    (not installed in the NeMo-RL container).
    """
    from safetensors.torch import load_file

    ckpt = Path(checkpoint_path)
    with open(ckpt / "adapter_config.json") as f:
        lora_cfg = json.load(f)

    r = lora_cfg["r"]
    alpha = lora_cfg["lora_alpha"]
    scale = alpha / r

    adapters = load_file(ckpt / "adapter_model.safetensors")
    state_dict = model.state_dict()

    merged_count = 0
    for key in list(adapters.keys()):
        if ".lora_B." not in key:
            continue
        a_key = key.replace(".lora_B.", ".lora_A.")
        # PEFT keys: "base_model.model.<module>.lora_B.weight"
        # Base model keys: "<module>.weight"
        base_key = (
            key.replace("base_model.model.", "")
            .replace(".lora_B.weight", ".weight")
        )
        if base_key not in state_dict:
            raise KeyError(
                f"LoRA target '{base_key}' not found in base model. "
                f"Adapter key: '{key}'"
            )
        B = adapters[key].to(device=state_dict[base_key].device)    # (out_dim, r)
        A = adapters[a_key].to(device=state_dict[base_key].device)  # (r, in_dim)
        state_dict[base_key] += (scale * (B @ A)).to(state_dict[base_key].dtype)
        merged_count += 1

    model.load_state_dict(state_dict)
    logger.info("Merged %d LoRA adapters (r=%d, alpha=%d, scale=%.1f)", merged_count, r, alpha, scale)
    return model


def load_model_for_eval(
    checkpoint: Optional[str],
    base_model: Optional[str] = None,
):
    """Load a trained model for evaluation.

    Dispatch (on checkpoint contents, not argument shape):
    - checkpoint=None: base model only (no adapter)
    - checkpoint dir containing adapter_config.json: LoRA adapter merged
      onto base_model
    - any other checkpoint: full-model HF checkpoint (base_model unused)
    """
    if checkpoint is None:
        model = AutoModelForCausalLM.from_pretrained(
            base_model, torch_dtype=torch.bfloat16, device_map="auto",
        )
        tokenizer = AutoTokenizer.from_pretrained(base_model)
    elif (Path(checkpoint) / "adapter_config.json").exists():
        model = AutoModelForCausalLM.from_pretrained(
            base_model, torch_dtype=torch.bfloat16, device_map="auto",
        )
        model = merge_lora_weights(model, checkpoint)
        tokenizer = AutoTokenizer.from_pretrained(base_model)
    else:
        model = AutoModelForCausalLM.from_pretrained(
            checkpoint, torch_dtype=torch.bfloat16, device_map="auto",
        )
        tokenizer = AutoTokenizer.from_pretrained(checkpoint)

    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model.eval()
    return model, tokenizer
