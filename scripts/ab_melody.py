"""
旋律 A/B/C 對比：同一份完整歌詞（約三分鐘），只換 caption 與 BPM／調性決定方式。

  A：現行長 caption＋現場分析的 BPM／調性
  B：新短 caption（主副歌對比）＋BPM 限 88–118
  C：新短 caption＋BPM／調性交給 ACE 5Hz LM

用法：.venv/bin/python scripts/ab_melody.py [--route nanfangao] [--style ambient] [--bpm 74] [--key A]
輸出：ab_runs/<時間>/A_current.mp3、B_v2_clamped.mp3、C_v2_lm.mp3、report.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.audio.mastering import master_to_mp3  # noqa: E402
from app.journey.service import _generate_lyrics  # noqa: E402
from app.voice import acestep as ace  # noqa: E402

VARIANTS = [
    ("A_current", "production", "fixed"),
    ("B_v2_clamped", "v2", "clamped"),
    ("C_v2_lm", "v2", "lm"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--route", default="nanfangao")
    ap.add_argument("--style", default="ambient")
    ap.add_argument("--bpm", type=float, default=74)
    ap.add_argument("--key", default="A")
    ap.add_argument("--singer", default="female_bright")
    ap.add_argument(
        "--keywords",
        default="南方澳漁港,海風,家人,白色浪花,夕陽",
        help="逗號分隔",
    )
    ap.add_argument("--only", default="", help="只跑部分變體，例如 B,C")
    args = ap.parse_args()

    out_dir = ROOT / "ab_runs" / time.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    t = time.time()
    lyrics = _generate_lyrics(keywords, args.style)
    lyrics.pop("llm_usage", None)
    lyric_sec = round(time.time() - t, 1)
    print(f"[ab] lyrics source={lyrics.get('source')} in {lyric_sec}s full_song={ace.is_full_song(lyrics)}")
    print(ace.format_lyrics(lyrics))

    material = {
        "style_id": args.style,
        "contour": "平穩",
        "energy": 1.2,
        "root": args.key,
        "mood": "大調（明亮/開朗）",
    }
    duration = ace.duration_for_lyrics(lyrics)
    report = {
        "keywords": keywords,
        "lyrics": lyrics,
        "lyrics_sec": lyric_sec,
        "formatted_lyrics": ace.format_lyrics(lyrics),
        "duration_sec": duration,
        "route": args.route,
        "style": args.style,
        "input_bpm": args.bpm,
        "input_key": args.key,
        "variants": [],
    }
    only = {s.strip().upper() for s in args.only.split(",") if s.strip()}

    for name, caption_mode, metas_mode in VARIANTS:
        if only and name[0] not in only:
            continue
        stats: dict = {}
        tmp = out_dir / f"_{name}"
        tmp.mkdir(exist_ok=True)
        t0 = time.time()
        row = {"name": name, "caption_mode": caption_mode, "metas_mode": metas_mode}
        try:
            cands = ace.generate_candidates(
                lyrics=lyrics,
                bpm=args.bpm,
                key=args.key,
                singer_id=args.singer,
                engine_style=args.style,
                duration_sec=duration,
                out_dir=tmp,
                batch_size=1,
                audio_format="wav",
                force_local=True,
                stats=stats,
                material=material,
                route_id=args.route,
                caption_mode=caption_mode,
                metas_mode=metas_mode,
            )
            mp3 = out_dir / f"{name}.mp3"
            master_to_mp3(cands[0]["path"], out_mp3=str(mp3))
            row.update(
                {
                    "file": str(mp3),
                    "seconds": round(time.time() - t0, 1),
                    "sent_bpm": stats.get("sent_bpm"),
                    "sent_key": stats.get("sent_key"),
                    "result_bpm": stats.get("result_bpm"),
                    "result_key": stats.get("result_key"),
                    "caption_chars": stats.get("caption_chars"),
                    "caption": stats.get("caption"),
                }
            )
        except Exception as e:
            row.update({"error": str(e), "seconds": round(time.time() - t0, 1)})
        print(f"[ab] {name}: {json.dumps({k: v for k, v in row.items() if k != 'caption'}, ensure_ascii=False)}")
        report["variants"].append(row)
        (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        for p in tmp.glob("*"):
            p.unlink(missing_ok=True)
        tmp.rmdir()

    print(f"[ab] done → {out_dir}")


if __name__ == "__main__":
    main()
