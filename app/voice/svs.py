"""
系統代唱（DiffSinger SVS）：
把步驟 4 歌詞 + 步驟 2 旋律，交給本機 ~/diffsinger 的虛擬歌手唱成乾聲 WAV，
再交給 Seed-VC 換成使用者聲紋。

DiffSinger 安裝在獨立目錄（預設 ~/diffsinger），用 subprocess 呼叫 infer_cli.py。
雲端可經 /svs/synthesize 委託本地 Mac（與 Seed-VC 相同模式）。
"""
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

DIFFSINGER_DIR = Path(os.getenv("DIFFSINGER_DIR", str(Path.home() / "diffsinger")))
CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# 雲端委託（預設走同一條 ngrok）
_default_svs_urls = "https://tactually-venerable-inez.ngrok-free.dev"
SVS_REMOTE_URLS = [
    u.strip() for u in os.getenv("SVS_REMOTE_URLS", _default_svs_urls).split(",") if u.strip()
]

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def is_available() -> bool:
    return (
        (DIFFSINGER_DIR / "infer_cli.py").exists()
        and (DIFFSINGER_DIR / ".venv" / "bin" / "python").exists()
        and (DIFFSINGER_DIR / "checkpoints" / "0228_opencpop_ds100_rel" / "model_ckpt_steps_160000.ckpt").exists()
    )


def midi_to_note_name(midi: int) -> str:
    midi = int(max(0, min(127, midi)))
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def extract_chars(text: str) -> list:
    return CJK_RE.findall(text or "")


def parse_lyric_chars(lyrics: Optional[dict], section: str) -> list:
    if not lyrics:
        return []
    text = lyrics.get(section) or ""
    chars = []
    for line in str(text).split("\n"):
        chars.extend(extract_chars(line))
    return chars


def allocate_notes_to_chars(notes: list, n_chars: int) -> list:
    """把音符分給每個漢字（連續分組）；回傳長度 = n_chars 的 note 子序列 list。"""
    if n_chars <= 0 or not notes:
        return []
    n_notes = len(notes)
    groups = []
    cum = 0
    prev = 0
    for i in range(n_chars):
        cum = round(n_notes * (i + 1) / n_chars)
        chunk = notes[prev:cum] or [notes[min(prev, n_notes - 1)]]
        groups.append(chunk)
        prev = cum
    return groups


def build_ds_job(chars: list, note_groups: list) -> Optional[dict]:
    """組成 DiffSinger word-level job。每個字對應一組音符（多音則 slur）。"""
    if not chars or len(chars) != len(note_groups):
        return None
    note_parts = []
    dur_parts = []
    text_chars = []
    for ch, group in zip(chars, note_groups):
        names = []
        durs = []
        for n in group:
            names.append(midi_to_note_name(int(n["midi"])))
            durs.append(f'{max(0.12, float(n["end"] - n["start"])):.5f}')
        note_parts.append(" ".join(names))
        dur_parts.append(" ".join(durs))
        text_chars.append(ch)
    return {
        "text": "".join(text_chars),
        "notes": " | ".join(note_parts),
        "notes_duration": " | ".join(dur_parts),
        "input_type": "word",
    }


def _run_diffsinger_local(job: dict, timeout: int = 600) -> Optional[str]:
    if not is_available():
        return None
    job_path = tempfile.mktemp(prefix="ds_job_", suffix=".json")
    out_path = tempfile.mktemp(prefix="ds_out_", suffix=".wav")
    with open(job_path, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False)
    env = {
        **os.environ,
        "PYTHONPATH": str(DIFFSINGER_DIR),
        "HOME": os.environ.get("HOME") or str(Path.home()),
        "PYTORCH_ENABLE_MPS_FALLBACK": "1",
    }
    cmd = [
        str(DIFFSINGER_DIR / ".venv" / "bin" / "python"),
        "infer_cli.py",
        "--input", job_path,
        "--output", out_path,
    ]
    try:
        subprocess.run(
            cmd, cwd=str(DIFFSINGER_DIR), env=env,
            check=True, capture_output=True, timeout=timeout,
        )
    except subprocess.CalledProcessError as e:
        print(f"[svs] DiffSinger 失敗：{(e.stderr or b'').decode(errors='ignore')[-800:]}")
        return None
    except Exception as e:
        print(f"[svs] DiffSinger 失敗：{e}")
        return None
    if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
        return out_path
    return None


def _run_diffsinger_remote(job: dict, timeout: int = 600) -> Optional[str]:
    if not SVS_REMOTE_URLS:
        return None
    import requests

    for base in SVS_REMOTE_URLS:
        url = base.rstrip("/") + "/svs/synthesize"
        try:
            resp = requests.post(
                url,
                headers={"ngrok-skip-browser-warning": "1", "Content-Type": "application/json"},
                json=job,
                timeout=(8, timeout),
            )
            if resp.status_code != 200 or len(resp.content) < 1000:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            out_path = tempfile.mktemp(prefix="svs_remote_", suffix=".wav")
            with open(out_path, "wb") as f:
                f.write(resp.content)
            return out_path
        except Exception as e:
            print(f"[svs] 遠端代唱失敗（{url}）：{e}")
            continue
    return None


def synthesize_phrase(chars: list, notes: list) -> Optional[np.ndarray]:
    """唱一小段（一串漢字 + 對應音符），回傳 float64 mono @ 目標可重取樣。"""
    groups = allocate_notes_to_chars(notes, len(chars))
    job = build_ds_job(chars, groups)
    if not job:
        return None
    print(f"[svs] 代唱「{job['text'][:20]}…」共 {len(chars)} 字")
    path = None
    if is_available():
        path = _run_diffsinger_local(job)
    if not path:
        path = _run_diffsinger_remote(job)
    if not path:
        return None
    x, fs = sf.read(path, dtype="float64")
    if x.ndim > 1:
        x = x.mean(axis=1)
    if fs != 44100:
        n = int(len(x) * 44100 / fs)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
    return x


def build_svs_vocal_track(
    notes: list,
    bpm: float,
    structure: dict,
    lyrics: Optional[dict],
    total_samples: int,
    fs: int = 44100,
    speaker_midi: float = 64.0,
) -> Optional[np.ndarray]:
    """
    依歌曲結構生成整軌代唱乾聲（主歌段唱 verse、副歌段唱 chorus）。
    時間軸與 generate_full_midi / Seed-VC 一致。
    """
    from app.audio.quantize import quantize_notes
    from app.voice.sing import fold_notes_to_speaker_range

    verse_chars = parse_lyric_chars(lyrics, "verse")
    chorus_chars = parse_lyric_chars(lyrics, "chorus")
    if not verse_chars and not chorus_chars:
        return None
    if not verse_chars:
        verse_chars = chorus_chars
    if not chorus_chars:
        chorus_chars = verse_chars

    # DiffSinger Opencpop 女聲音域偏高；依模板 speaker_midi 折疊
    notes = fold_notes_to_speaker_range(notes, speaker_midi=float(speaker_midi))
    quantized = quantize_notes(notes, bpm, grid="1/8")
    if not quantized:
        return None

    bar = structure["bar_duration"]
    intro = structure["intro_bars"]
    melody_bars = structure["melody_bars"]
    repeats = structure["repeats"]
    quiet = structure.get("quiet_repeats", 0)

    mix = np.zeros(total_samples)
    cache = {}

    for rep in range(repeats):
        section = "verse" if rep < quiet else "chorus"
        chars = verse_chars if section == "verse" else chorus_chars
        offset = (intro + rep * melody_bars) * bar
        key = section
        if key not in cache:
            audio = synthesize_phrase(chars, quantized)
            cache[key] = audio
        audio = cache[key]
        if audio is None:
            continue
        # 對齊到該次主旋律段落起點；長度裁到段落
        section_len = int(melody_bars * bar * fs)
        start = int(offset * fs)
        end = min(start + min(len(audio), section_len), total_samples)
        if start >= total_samples:
            continue
        mix[start:end] += audio[: end - start]

    peak = float(np.max(np.abs(mix)))
    if peak < 1e-6:
        return None
    if peak > 0.95:
        mix *= 0.95 / peak
    return mix


def synthesize_job_to_wav(job: dict) -> Optional[str]:
    """給 /svs/synthesize 用：直接跑一個 DiffSinger job，回傳 wav 路徑。"""
    if is_available():
        return _run_diffsinger_local(job)
    return None
