# Teaching TowerAI's model (LM Studio)

LM Studio *runs* models; it can't train them. TowerAI therefore learns in two
steps (see `learning.py`):

1. **Right away, with any model — in-context learning.** People rate the
   assistant's replies 👍/👎 in chat and can write the answer it should have
   given. Before each request the assistant is shown the most similar
   well-rated or corrected past answers as examples (`AGENT_FEW_SHOT`, default 2).
   No training, no GPU.
2. **Later — fine-tuning a LoRA adapter** on those examples, so the model itself
   improves (shorter prompts, better at your group's questions and style).
   Worth it once you have roughly **150+ good examples**; before that, step 1
   gives most of the benefit.

## Fine-tuning, step by step

Do this on a machine with a GPU (an NVIDIA card, a free Colab GPU, or an
Apple-Silicon Mac) — not necessarily the TowerAI box.

1. **Export** — as an admin open **🧠 Training data** → *Export fine-tuning
   dataset*. You get `towerai-finetune.jsonl`: one `{"messages": [...]}`
   conversation per good answer, the chat format LoRA trainers accept.
2. **Split** — `python training/split.py towerai-finetune.jsonl data/`
   → `data/train.jsonl` and `data/valid.jsonl`.
3. **Train** — use the same base model you run in LM Studio (e.g.
   *Qwen2.5-7B-Instruct*), in its original (non-GGUF) form:
   - **Apple Silicon** — `pip install mlx-lm`, then
     `mlx_lm.lora --model Qwen/Qwen2.5-7B-Instruct --train --data data --iters 600 --batch-size 2`
     and merge the adapter: `mlx_lm.fuse --model Qwen/Qwen2.5-7B-Instruct --adapter-path adapters --save-path fused`.
   - **NVIDIA / Colab** — Unsloth's "conversational" notebook for your base
     model: load the dataset with `load_dataset("json", data_files="data/train.jsonl")`,
     train, then save straight to GGUF with
     `model.save_pretrained_gguf("towerai-gguf", tokenizer, quantization_method="q4_k_m")`.
4. **Convert to GGUF** (if your trainer didn't) — with llama.cpp:
   `python convert_hf_to_gguf.py fused --outfile towerai.gguf --outtype q8_0`.
5. **Load in LM Studio** — `lms import towerai.gguf` (or copy the file into
   LM Studio's models folder), load it, and set `LLM_MODEL` to its id.
6. **Measure** — compare old and new with the evaluation set:
   `python evals/run.py --model <old-id>` vs `python evals/run.py --model <new-id>`.
   Keep the new model only if it scores at least as well.

Notes: keep a few hundred examples per training round; re-export and retrain
as feedback accumulates. Training data contains your rooms' conversations —
keep it on machines you trust.
