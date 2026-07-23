"""
音符提取 - 從 WAV 檔案中提取音符事件

原本規劃優先使用 aubio / Basic Pitch。
實務上在 macOS (ARM) 安裝 aubio 需要編譯 C 延伸套件，容易在教學現場踩坑，
因此這裡改為 **純 numpy + soundfile 的簡化單音偵測**：

- 假設輸入是「單音旋律」
- 用能量門檻切出一段一段的音（粗略 onset）
- 每段用簡單 FFT 找出主頻率，再轉成 MIDI number

這樣安裝只需要 wheel 套件，對課堂現場比較穩定、可解釋。
"""

import numpy as np
import soundfile as sf


def _frame_signal(x: np.ndarray, frame_size: int, hop_size: int) -> np.ndarray:
    """將訊號切成多個 frame（重疊窗）"""
    if len(x) < frame_size:
        x = np.pad(x, (0, frame_size - len(x)))
    num_frames = 1 + (len(x) - frame_size) // hop_size
    frames = np.stack(
        [x[i * hop_size : i * hop_size + frame_size] for i in range(num_frames)],
        axis=0,
    )
    return frames


def _freq_to_midi(freq: float) -> int:
    """頻率轉 MIDI（A4=440Hz）"""
    if freq <= 0:
        return 60  # fallback C4
    midi = 69 + 12 * np.log2(freq / 440.0)
    return int(round(midi))


def extract_notes_from_audio(audio_path: str) -> dict:
    """
    從音訊檔案中提取簡化版「單音」音符
    
    Args:
        audio_path: WAV 檔案路徑
    
    Returns:
        dict: {
            "notes": [{"start": float, "end": float, "midi": int, "velocity": int}],
            "bpm": float
        }
    """
    # 讀取音訊檔案
    audio_data, sample_rate = sf.read(audio_path)
    
    # 如果是立體聲，轉為單聲道
    if len(audio_data.shape) > 1:
        audio_data = np.mean(audio_data, axis=1)
    
    # 轉為 float32
    audio_data = audio_data.astype(np.float32)
    
    # 基本參數
    frame_size = 2048
    hop_size = 512
    frames = _frame_signal(audio_data, frame_size, hop_size)
    window = np.hanning(frame_size).astype(np.float32)
    
    # 能量計算，用來做簡單的「有聲 / 無聲」區分
    energies = np.mean(frames**2, axis=1)
    energy_threshold = 0.02 * np.max(energies) if np.max(energies) > 0 else 0.0
    voiced = energies > energy_threshold
    
    # 對每個有聲 frame 做簡單 FFT 找主頻
    freqs = np.fft.rfftfreq(frame_size, d=1.0 / sample_rate)
    mags_all = np.abs(np.fft.rfft(frames * window[None, :], axis=1))
    
    frame_midi = []
    for i, (is_voiced, mags) in enumerate(zip(voiced, mags_all)):
        if not is_voiced:
            frame_midi.append(None)
            continue
        # 找最大能量的頻率（避開 DC 成分）
        idx = np.argmax(mags[1:]) + 1
        freq = freqs[idx]
        midi = _freq_to_midi(freq)
        frame_midi.append(midi)

    # 中值濾波平滑音高：消除雜訊造成的逐 frame 亂跳
    smoothed = list(frame_midi)
    for i in range(len(frame_midi)):
        if frame_midi[i] is None:
            continue
        window = [m for m in frame_midi[max(0, i - 2): i + 3] if m is not None]
        if window:
            smoothed[i] = int(np.median(window))
    frame_midi = smoothed
    
    # 將連續相同 MIDI 的 frame 合併成一個 note
    notes = []
    current_midi = None
    current_start_frame = None
    
    for i, midi in enumerate(frame_midi):
        if midi is None:
            # 結束目前 note
            if current_midi is not None:
                start_time = current_start_frame * hop_size / sample_rate
                end_time = i * hop_size / sample_rate
                notes.append(
                    {
                        "start": float(start_time),
                        "end": float(end_time),
                        "midi": int(current_midi),
                        "velocity": 90,
                    }
                )
                current_midi = None
                current_start_frame = None
            continue
        
        if current_midi is None:
            # 開始新的 note
            current_midi = midi
            current_start_frame = i
        else:
            # 如果音高變化太大，視為新的 note
            if abs(midi - current_midi) >= 1:
                start_time = current_start_frame * hop_size / sample_rate
                end_time = i * hop_size / sample_rate
                notes.append(
                    {
                        "start": float(start_time),
                        "end": float(end_time),
                        "midi": int(current_midi),
                        "velocity": 90,
                    }
                )
                current_midi = midi
                current_start_frame = i
    
    # 補上最後一個 note
    if current_midi is not None and current_start_frame is not None:
        start_time = current_start_frame * hop_size / sample_rate
        end_time = len(audio_data) / sample_rate
        notes.append(
            {
                "start": float(start_time),
                "end": float(end_time),
                "midi": int(current_midi),
                "velocity": 90,
            }
        )
    
    # ---- 後處理：過濾雜訊音符 ----
    MIN_NOTE_DURATION = 0.08   # 短於 80ms 視為雜訊
    MIDI_MIN, MIDI_MAX = 36, 96  # 合理的人聲/樂器音域（C2 ~ C7）
    MAX_NOTES = 200            # 安全上限

    cleaned = []
    for n in notes:
        if n["end"] - n["start"] < MIN_NOTE_DURATION:
            continue
        if not (MIDI_MIN <= n["midi"] <= MIDI_MAX):
            continue
        # 相同音高且間隔很近：合併成一個音
        if cleaned and cleaned[-1]["midi"] == n["midi"] and n["start"] - cleaned[-1]["end"] < 0.05:
            cleaned[-1]["end"] = n["end"]
            continue
        cleaned.append(n)
    notes = cleaned[:MAX_NOTES]

    # 如果還是沒有音符，建立一個預設音符
    if not notes:
        duration = len(audio_data) / sample_rate
        notes.append(
            {
                "start": 0.0,
                "end": float(duration),
                "midi": 60,  # C4
                "velocity": 90,
            }
        )
    
    # 估算 BPM（簡化版：根據平均 note 長度）
    if len(notes) > 1:
        avg_note_duration = float(
            np.mean([n["end"] - n["start"] for n in notes])
        )
        estimated_bpm = 60.0 / avg_note_duration if avg_note_duration > 0 else 90.0
        estimated_bpm = max(60.0, min(150.0, estimated_bpm))
    else:
        estimated_bpm = 90.0
    
    return {
        "notes": notes,
        "bpm": float(estimated_bpm),
    }
