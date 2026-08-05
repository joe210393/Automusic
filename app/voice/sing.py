"""
歌聲合成（步驟 6）：
把使用者唸的歌詞錄音，依旋律的音高／節奏「唱」出來。

1. 依步驟 4 的完整歌詞排程（沒錄到的句子用其他聲紋音節補上，不會整句消失）
2. 每個字做成「短子音起音＋拉長母音」——比較像唱歌，不像變調說話
3. 長音加輕微顫音；音符之間交疊一點做連音
4. 組成整軌後交給 Seed-VC（neural_vc.py）做神經音色轉換
"""
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def count_syllables(text: str) -> int:
    return len(CJK_RE.findall(text))


def load_mono(path: str, target_fs: int = 44100) -> np.ndarray:
    x, fs = sf.read(path, dtype="float64")
    if x.ndim > 1:
        x = x.mean(axis=1)
    if fs != target_fs:
        n_target = int(len(x) * target_fs / fs)
        x = np.interp(np.linspace(0, len(x) - 1, n_target), np.arange(len(x)), x)
    return x


def trim_silence(x: np.ndarray, fs: int, threshold_ratio: float = 0.06) -> np.ndarray:
    frame = max(1, int(fs * 0.02))
    n_frames = len(x) // frame
    if n_frames < 3:
        return x
    rms = np.array([
        float(np.sqrt(np.mean(x[i * frame:(i + 1) * frame] ** 2)))
        for i in range(n_frames)
    ])
    peak = rms.max()
    if peak <= 0:
        return x
    active = np.where(rms > peak * threshold_ratio)[0]
    if len(active) == 0:
        return x
    start = active[0] * frame
    end = min(len(x), (active[-1] + 1) * frame)
    return x[start:end]


def midi_to_freq(midi: float) -> float:
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def freq_to_midi(freq: float) -> float:
    return 69.0 + 12.0 * np.log2(max(freq, 1.0) / 440.0)


def estimate_speaker_midi(voiceprint_dir: Path, manifest: dict, fs: int = 44100) -> Optional[float]:
    try:
        import pyworld as pw
    except Exception:
        return None

    f0s = []
    for line in manifest.get("lines", []):
        path = voiceprint_dir / line.get("filename", "")
        if not path.exists():
            continue
        try:
            x = trim_silence(load_mono(str(path), fs), fs)
        except Exception:
            continue
        if len(x) < fs // 5:
            continue
        f0, _ = pw.harvest(
            np.ascontiguousarray(x, dtype=np.float64), fs,
            frame_period=5.0, f0_floor=70.0, f0_ceil=500.0,
        )
        voiced = f0[f0 > 0]
        if len(voiced):
            f0s.append(voiced)
    if not f0s:
        return None
    # 說話中位再上 5 個半音，比較接近唱歌音域
    return float(freq_to_midi(float(np.median(np.concatenate(f0s)))) + 5.0)


def fold_notes_to_speaker_range(notes: list, speaker_midi: Optional[float]) -> list:
    if not notes:
        return notes
    center = float(speaker_midi) if speaker_midi is not None else 60.0
    low = max(48, center - 7)
    high = min(76, center + 7)
    folded = []
    for n in notes:
        m = int(n["midi"])
        while m < low:
            m += 12
        while m > high:
            m -= 12
        if abs(m - center) > abs(m - 12 - center) and m - 12 >= 48:
            m -= 12
        if abs(m - center) > abs(m + 12 - center) and m + 12 <= 79:
            m += 12
        folded.append({**n, "midi": m})
    print(f"[sing] 人聲音域折疊至 MIDI {low:.0f}-{high:.0f}（中心 ≈ {center:.0f}）")
    return folded


def _atempo_chain(ratio: float) -> str:
    if ratio <= 0:
        ratio = 1.0
    filters = []
    r = ratio
    while r > 2.0:
        filters.append("atempo=2.0")
        r /= 2.0
    while r < 0.5:
        filters.append("atempo=0.5")
        r /= 0.5
    filters.append(f"atempo={r:.6f}")
    return ",".join(filters)


def pitch_and_stretch(x: np.ndarray, fs: int, semitones: float, target_dur: float) -> np.ndarray:
    if len(x) < 32:
        return np.zeros(max(1, int(target_dur * fs)))

    src_dur = len(x) / fs
    tempo = src_dur / max(0.08, target_dur)
    rate = 2.0 ** (semitones / 12.0)
    af = f"asetrate={fs * rate:.4f},aresample={fs},{_atempo_chain(tempo / rate)}"

    in_path = tempfile.mktemp(prefix="syl_in_", suffix=".wav")
    out_path = tempfile.mktemp(prefix="syl_out_", suffix=".wav")
    try:
        sf.write(in_path, x.astype(np.float32), fs)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", in_path, "-af", af, out_path],
            check=True, capture_output=True, timeout=60,
        )
        y, out_fs = sf.read(out_path, dtype="float64")
        if y.ndim > 1:
            y = y.mean(axis=1)
        if out_fs != fs:
            n_target = int(len(y) * fs / out_fs)
            y = np.interp(np.linspace(0, len(y) - 1, n_target), np.arange(len(y)), y)
    except Exception as e:
        print(f"[sing] ffmpeg 變調失敗：{e}")
        n_target = max(1, int(target_dur * fs))
        y = np.interp(np.linspace(0, len(x) - 1, n_target), np.arange(len(x)), x)
    finally:
        for p in (in_path, out_path):
            try:
                os.unlink(p)
            except Exception:
                pass

    n_target = max(1, int(target_dur * fs))
    if len(y) < n_target:
        y = np.pad(y, (0, n_target - len(y)))
    return y[:n_target]


def split_into_syllables(x: np.ndarray, n: int) -> list:
    if n <= 0 or len(x) == 0:
        return []
    seg = len(x) / n
    return [x[int(i * seg):int((i + 1) * seg)] for i in range(n)]


def estimate_src_midi(x: np.ndarray, fs: int) -> float:
    try:
        import pyworld as pw
        f0, _ = pw.harvest(
            np.ascontiguousarray(x, dtype=np.float64), fs,
            frame_period=5.0, f0_floor=70.0, f0_ceil=500.0,
        )
        voiced = f0[f0 > 0]
        if len(voiced) >= 3:
            return float(freq_to_midi(float(np.median(voiced))))
    except Exception:
        pass
    return 60.0


def extract_onset_and_vowel(x: np.ndarray, fs: int) -> tuple:
    """
    把一個字拆成短子音起音 + 母音核心。
    唱歌的感覺主要來自「拉長母音」；整段說話拉伸只會像慢速朗讀。
    """
    x = trim_silence(x, fs, threshold_ratio=0.08)
    if len(x) < int(fs * 0.04):
        return x[: max(1, len(x) // 4)], x

    frame = max(1, int(fs * 0.01))
    n_frames = max(1, len(x) // frame)
    rms = np.array([
        float(np.sqrt(np.mean(x[i * frame:(i + 1) * frame] ** 2)) + 1e-12)
        for i in range(n_frames)
    ])
    # 母音核心：能量最高的連續區，略偏後半（子音通常在開頭）
    peak_i = int(np.argmax(rms))
    # 從 peak 往左右擴到能量掉到 35% 以下
    thr = rms[peak_i] * 0.35
    left = peak_i
    while left > 0 and rms[left] >= thr:
        left -= 1
    right = peak_i
    while right < n_frames - 1 and rms[right] >= thr:
        right += 1
    v0 = max(0, left * frame)
    v1 = min(len(x), (right + 1) * frame)
    vowel = x[v0:v1]
    if len(vowel) < int(fs * 0.04):
        # 後備：取中段 50%
        a, b = int(len(x) * 0.25), int(len(x) * 0.75)
        vowel = x[a:b]

    onset_len = min(int(fs * 0.055), max(1, v0), int(len(x) * 0.35))
    onset = x[:onset_len] if onset_len > 0 else x[: max(1, int(fs * 0.02))]
    return onset, vowel


def loop_sustain(vowel: np.ndarray, fs: int, target_len: int) -> np.ndarray:
    """用 overlap-add 把母音拉長到目標長度（唱歌的長音）。"""
    if target_len <= 0:
        return np.zeros(0)
    if len(vowel) >= target_len:
        return vowel[:target_len].copy()
    if len(vowel) < 8:
        return np.resize(vowel, target_len)

    # 取母音中段做可循環核心，避免頭尾子音殘留
    core = vowel[int(len(vowel) * 0.15):int(len(vowel) * 0.85)]
    if len(core) < int(fs * 0.03):
        core = vowel
    hop = max(1, len(core) - int(fs * 0.01))  # 10ms overlap
    out = np.zeros(target_len)
    pos = 0
    fade = min(int(fs * 0.01), len(core) // 3)
    while pos < target_len:
        piece = core.copy()
        if fade > 0:
            piece[:fade] *= np.linspace(0, 1, fade)
            piece[-fade:] *= np.linspace(1, 0, fade)
        end = min(pos + len(piece), target_len)
        out[pos:end] += piece[: end - pos]
        pos += hop
    peak = float(np.max(np.abs(out))) or 1.0
    out *= (float(np.max(np.abs(vowel))) or 0.2) / peak
    return out


def add_vibrato(y: np.ndarray, fs: int, depth_cents: float = 28.0, rate_hz: float = 5.5) -> np.ndarray:
    """
    對長音加輕微顫音。用微小的時間彎曲模擬音高微顫
    （避免再走一次 WORLD 重建）。
    """
    if len(y) < int(fs * 0.35):
        return y
    t = np.arange(len(y)) / fs
    # 0.2 秒後淡入
    ramp = np.clip((t - 0.2) / 0.25, 0.0, 1.0)
    # 音高顫動 ≈ 對取樣位置做正弦偏移
    depth = (depth_cents / 1200.0) * ramp  # 相對頻率偏移
    # 相位累積：dφ/dt = 2π f0 * (1 + depth*sin) → 簡化成樣本索引彎曲
    mod = depth * np.sin(2 * np.pi * rate_hz * t)
    # 累積調制變成讀取位置偏移
    idx = np.arange(len(y), dtype=float)
    # 積分頻率偏移 ≈ 對 index 加減
    idx = idx + np.cumsum(mod) * 0.5
    idx = np.clip(idx, 0, len(y) - 1)
    return np.interp(idx, np.arange(len(y)), y)


def make_sung_syllable(syl: np.ndarray, fs: int, target_midi: int, duration: float) -> np.ndarray:
    """一個字 → 唱歌音節：短起音 + 拉長母音 +（長音）顫音。"""
    onset, vowel = extract_onset_and_vowel(syl, fs)
    src_midi = estimate_src_midi(vowel if len(vowel) > len(onset) else syl, fs)
    semitones = float(np.clip(target_midi - src_midi, -7.0, 7.0))

    onset_dur = min(0.06, duration * 0.22, max(0.02, len(onset) / fs))
    vowel_dur = max(0.06, duration - onset_dur)

    # 先把母音拉到目標長度（維持說話音色的循環），再整體變調
    vowel_sustained = loop_sustain(vowel, fs, int(vowel_dur * fs))
    # 起音稍微加速、母音接在後面
    body = np.concatenate([onset, vowel_sustained]) if len(onset) else vowel_sustained
    y = pitch_and_stretch(body, fs, semitones, duration)

    if duration >= 0.4:
        y = add_vibrato(y, fs)

    fade = min(len(y) // 5, int(fs * 0.012))
    if fade > 0:
        y[:fade] *= np.linspace(0, 1, fade)
        y[-fade:] *= np.linspace(1, 0, fade)
    return y


def build_syllable_bank(voiceprint_dir: Path, manifest: dict, fs: int = 44100) -> list:
    """把所有已錄聲紋切成音節池，給「沒錄到的句子」補字用。"""
    bank = []
    for line in sorted(manifest.get("lines", []), key=lambda l: (l.get("section"), l.get("index", 0))):
        path = voiceprint_dir / line.get("filename", "")
        n = count_syllables(line.get("text", ""))
        if n == 0 or not path.exists():
            continue
        try:
            x = trim_silence(load_mono(str(path), fs), fs)
        except Exception:
            continue
        bank.extend(s for s in split_into_syllables(x, n) if len(s) > fs // 50)
    return bank


def parse_lyric_lines(lyrics: Optional[dict], section: str) -> list:
    """從步驟 4 歌詞文字取出主歌／副歌各句。"""
    if not lyrics:
        return []
    text = lyrics.get(section) or lyrics.get("verse" if section == "verse" else "chorus") or ""
    return [ln.strip() for ln in str(text).split("\n") if ln.strip() and count_syllables(ln) > 0]


def resolve_section_lines(
    lyrics: Optional[dict],
    section: str,
    voiceprint_dir: Path,
    manifest: dict,
    syllable_bank: list,
    fs: int,
) -> list:
    """
    回傳 [(syllable_count, audio_or_None_filled), ...]。
    以歌詞全文為準；沒錄音的句子用音節池拼出同字數的音訊。
    """
    lyric_lines = parse_lyric_lines(lyrics, section)
    # 若請求沒帶歌詞，退回只使用已錄音的句子
    if not lyric_lines:
        recorded = []
        for line in sorted(
            [l for l in manifest.get("lines", []) if l.get("section") == section],
            key=lambda l: l.get("index", 0),
        ):
            path = voiceprint_dir / line.get("filename", "")
            n = count_syllables(line.get("text", ""))
            if n == 0 or not path.exists():
                continue
            try:
                x = trim_silence(load_mono(str(path), fs), fs)
            except Exception:
                continue
            if len(x) >= fs // 10:
                recorded.append((n, x, line.get("text", "")))
        return recorded

    # 建立 index → 錄音
    rec_map = {}
    for line in manifest.get("lines", []):
        if line.get("section") != section:
            continue
        path = voiceprint_dir / line.get("filename", "")
        if path.exists():
            try:
                rec_map[int(line.get("index", -1))] = trim_silence(load_mono(str(path), fs), fs)
            except Exception:
                pass

    result = []
    bank_i = 0
    for i, text in enumerate(lyric_lines):
        n = count_syllables(text)
        if i in rec_map and len(rec_map[i]) >= fs // 10:
            result.append((n, rec_map[i], text))
            continue
        # 沒錄音：從音節池拼出 n 個字
        if not syllable_bank:
            continue
        parts = []
        for _ in range(n):
            parts.append(syllable_bank[bank_i % len(syllable_bank)])
            bank_i += 1
        # 音節之間加一點縫隙，比較不像同一句被重複
        gap = np.zeros(int(fs * 0.03))
        audio = parts[0]
        for p in parts[1:]:
            audio = np.concatenate([audio, gap, p])
        print(f"[sing] 「{text}」未錄音 → 用其他聲紋音節補上（{n} 字）")
        result.append((n, audio, text))
    return result


def allocate_notes_to_lines(notes: list, syllable_counts: list) -> list:
    total_syll = sum(syllable_counts)
    n_notes = len(notes)
    if total_syll == 0 or n_notes == 0:
        return [[] for _ in syllable_counts]
    groups, cum, prev = [], 0, 0
    for s in syllable_counts:
        cum += s
        cut = round(n_notes * cum / total_syll)
        groups.append(notes[prev:cut])
        prev = cut
    return groups


def sing_line(x: np.ndarray, fs: int, notes: list, n_chars: int) -> np.ndarray:
    """
    一句話 → 對應一組音符。
    字數與音符對齊；每個字做成「唱歌音節」，音符間交疊 30ms 連音。
    """
    if not notes:
        return np.zeros(1)
    total_dur = notes[-1]["end"] + 0.03
    out = np.zeros(int(total_dur * fs) + 1)

    n_syl = max(n_chars, 1)
    syllables = split_into_syllables(trim_silence(x, fs), n_syl)
    if not syllables:
        return out

    # 音符數與字數對齊：字多則多字共用後面的音；音多則字循環
    for i, note in enumerate(notes):
        syl = syllables[min(i, len(syllables) - 1)] if i < len(syllables) else syllables[i % len(syllables)]
        # 若音符比字多，把多的音分給最後幾個字循環
        if len(notes) > n_syl:
            syl = syllables[i % n_syl]
        dur = max(0.12, note["end"] - note["start"])
        # 連音：每個音多留 30ms 與下一個交疊
        y = make_sung_syllable(syl, fs, note["midi"], dur + 0.03)
        start = int(note["start"] * fs)
        end = min(start + len(y), len(out))
        if start >= len(out):
            continue
        out[start:end] += y[: end - start]

    peak = float(np.max(np.abs(out)))
    if peak > 0.95:
        out *= 0.95 / peak
    return out


def build_vocal_track(
    notes: list,
    bpm: float,
    structure: dict,
    voiceprint_dir: Path,
    manifest: dict,
    total_samples: int,
    fs: int = 44100,
    lyrics: Optional[dict] = None,
) -> Optional[np.ndarray]:
    from app.audio.quantize import quantize_notes

    bank = build_syllable_bank(voiceprint_dir, manifest, fs)
    if not bank and not manifest.get("lines"):
        return None

    verse_lines = resolve_section_lines(lyrics, "verse", voiceprint_dir, manifest, bank, fs)
    chorus_lines = resolve_section_lines(lyrics, "chorus", voiceprint_dir, manifest, bank, fs)
    if not verse_lines and not chorus_lines:
        return None
    if not verse_lines:
        verse_lines = chorus_lines
    if not chorus_lines:
        chorus_lines = verse_lines

    speaker_midi = estimate_speaker_midi(voiceprint_dir, manifest, fs)
    notes = fold_notes_to_speaker_range(notes, speaker_midi)
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
        lines = verse_lines if section == "verse" else chorus_lines
        offset = (intro + rep * melody_bars) * bar
        groups = allocate_notes_to_lines(quantized, [n for n, _, _ in lines])
        for line_idx, ((n_chars, audio, text), group) in enumerate(zip(lines, groups)):
            if not group:
                continue
            g_start = group[0]["start"]
            rel = [
                {"start": n["start"] - g_start, "end": n["end"] - g_start, "midi": n["midi"]}
                for n in group
            ]
            key = (section, line_idx, text, tuple((n["midi"], round(n["start"], 3), round(n["end"], 3)) for n in rel))
            if key not in cache:
                cache[key] = sing_line(audio, fs, rel, n_chars)
            y = cache[key]
            start = int((offset + g_start) * fs)
            end = min(start + len(y), total_samples)
            if start >= total_samples:
                continue
            mix[start:end] += y[: end - start]

    peak = float(np.max(np.abs(mix)))
    if peak > 0.95:
        mix *= 0.95 / peak
    return mix


def apply_reverb(x: np.ndarray, fs: int = 44100) -> np.ndarray:
    d1, d2 = int(0.09 * fs), int(0.17 * fs)
    rev = x.copy()
    rev[d1:] += x[:-d1] * 0.22
    rev[d2:] += x[:-d2] * 0.12
    peak = float(np.max(np.abs(rev)))
    if peak > 0.95:
        rev *= 0.95 / peak
    return rev
