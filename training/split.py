"""Split TowerAI's exported dataset into train/valid files for LoRA trainers.

    python training/split.py towerai-finetune.jsonl data/      # -> data/train.jsonl, data/valid.jsonl
"""
import os
import random
import sys


def split(src, out_dir, valid_share=0.1, seed=0):
    with open(src, encoding="utf-8") as f:
        rows = [line for line in f if line.strip()]
    random.Random(seed).shuffle(rows)
    n_valid = max(1, int(len(rows) * valid_share)) if len(rows) > 1 else 0
    os.makedirs(out_dir, exist_ok=True)
    for name, part in (("valid", rows[:n_valid]), ("train", rows[n_valid:])):
        with open(os.path.join(out_dir, f"{name}.jsonl"), "w", encoding="utf-8") as f:
            f.writelines(part)
    return len(rows) - n_valid, n_valid


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    train, valid = split(sys.argv[1], sys.argv[2])
    print(f"{train} training and {valid} validation examples in {sys.argv[2]}")
