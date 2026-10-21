"""Checkpoint loading for evaluation: base models, LoRA adapters, full checkpoints."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

logger = logging.getLogger(__name__)


def load_model_for_eval(
    checkpoint: Optional[str],
    base_model: Optional[str] = None,
):
    """Load a trained model for evaluation.

    Dispatch (on checkpoint contents, not argument shape):
    - checkpoint=None: base model only (no adapter)
    - checkpoint dir containing adapter_config.json: LoRA adapter kept
      separate on top of base_model (PEFT). Merging into bf16 weights
      perturbs the trained policy, so it is never merged here.
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
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, checkpoint)
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


def load_vllm_for_eval(checkpoint: Optional[str], base_model: str,
                       max_model_len: int, seed: int):
    """vLLM engine for batched evaluation, dispatching like
    load_model_for_eval. A LoRA adapter is served through vLLM's LoRA
    support rather than merged. Returns (llm, tokenizer, lora_request)."""
    from vllm import LLM
    from vllm.lora.request import LoRARequest

    lora_request, lora_rank = None, None
    model_path = base_model
    if checkpoint is not None:
        if (Path(checkpoint) / "adapter_config.json").exists():
            with open(Path(checkpoint) / "adapter_config.json") as f:
                lora_rank = json.load(f)["r"]
            lora_request = LoRARequest("adapter", 1, str(checkpoint))
        else:
            model_path = checkpoint
    llm = LLM(
        model=model_path, dtype="bfloat16", seed=seed,
        max_model_len=max_model_len,
        gpu_memory_utilization=0.85,
        enable_lora=lora_request is not None,
        **({"max_lora_rank": lora_rank} if lora_rank else {}),
    )
    tokenizer = AutoTokenizer.from_pretrained(model_path)
    return llm, tokenizer, lora_request
