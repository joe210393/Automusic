#!/usr/bin/env python3
"""
Automusic → DiffSinger CLI
用法：
  PYTHONPATH=. .venv/bin/python infer_cli.py --input job.json --output out.wav

job.json:
  {
    "text": "小酒窝长睫毛",
    "notes": "C4 | D4 | E4 | F4 | G4 | A4",
    "notes_duration": "0.4 | 0.4 | 0.4 | 0.4 | 0.4 | 0.4",
    "input_type": "word"
  }
"""
import argparse
import json
import os
import sys

# 確保可 import 專案模組
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="JSON job file")
    parser.add_argument("--output", required=True, help="output wav path")
    parser.add_argument(
        "--config",
        default="usr/configs/midi/e2e/opencpop/ds100_adj_rel.yaml",
    )
    parser.add_argument("--exp_name", default="0228_opencpop_ds100_rel")
    args = parser.parse_args()

    # 模擬 DiffSinger 的 hparams CLI（set_hparams 讀 sys.argv）
    sys.argv = [
        sys.argv[0],
        "--config", args.config,
        "--exp_name", args.exp_name,
        "--infer",
    ]

    from utils.hparams import set_hparams, hparams
    from utils.audio import save_wav
    from inference.svs.ds_e2e import DiffSingerE2EInfer

    set_hparams(print_hparams=False)
    with open(args.input, "r", encoding="utf-8") as f:
        inp = json.load(f)
    if "input_type" not in inp:
        inp["input_type"] = "word"

    infer = DiffSingerE2EInfer(hparams)
    out = infer.infer_once(inp)
    if out is None:
        print("[infer_cli] inference failed", file=sys.stderr)
        sys.exit(2)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    save_wav(out, args.output, hparams["audio_sample_rate"])
    print(args.output)


if __name__ == "__main__":
    main()
