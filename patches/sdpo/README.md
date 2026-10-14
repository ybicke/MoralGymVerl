# SDPO patches

Training uses [`lasgroup/SDPO`](https://github.com/lasgroup/SDPO) (SDPO built on verl) at
commit `7c457fc`, plus four small patches:

```bash
git clone https://github.com/lasgroup/SDPO && cd SDPO
git checkout 7c457fc && git am /path/to/patches/sdpo/*.patch
```

| patch | why |
|---|---|
| 0001 | log reward extras (cooperation rate, parse failures) during training, not only at validation |
| 0002 | without tools, Llama-3.1's chat template added a tool-calling instruction to every prompt |
| 0003 | a start-up check in verl crashed with Gemma-2/3 chat templates |
| 0004 | checkpoints now include the SDPO teacher, so resumed runs keep it |

Each patch file explains its change in its message.
