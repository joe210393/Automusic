from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
from pathlib import Path
from datetime import datetime
import os
import re
import shutil
import subprocess
import tempfile
import json
import requests

app = FastAPI(title="Music Education MVP", version="1.0.0")

# 提供簡單前台（錄音＋編曲 demo）
frontend_dir = Path(__file__).parent / "frontend"
if frontend_dir.exists():
    app.mount("/web", StaticFiles(directory=str(frontend_dir), html=True), name="web")

# LM Studio 設定：依序嘗試多個網址（區網優先、再走 ngrok），可用環境變數覆寫
# LM_STUDIO_URLS 用逗號分隔多個網址；LM_STUDIO_URL 單一網址（優先權最高，向下相容）
_default_lm_urls = [
    "http://192.168.1.198:1234/v1/chat/completions",                       # 區網（本地電腦跑時最快）
    "https://tactually-venerable-inez.ngrok-free.dev/v1/chat/completions", # ngrok（雲端部署走這條）
]
if os.getenv("LM_STUDIO_URL"):
    LM_STUDIO_URLS = [os.getenv("LM_STUDIO_URL")]
elif os.getenv("LM_STUDIO_URLS"):
    LM_STUDIO_URLS = [u.strip() for u in os.getenv("LM_STUDIO_URLS").split(",") if u.strip()]
else:
    LM_STUDIO_URLS = _default_lm_urls
LM_STUDIO_MODEL = os.getenv("LM_STUDIO_MODEL", "google/gemma-3-12b")

# 手機錄音儲存目錄（雲端與本地都用同一份程式碼）
RECORDINGS_DIR = Path(os.getenv("RECORDINGS_DIR", str(Path(__file__).parent.parent / "recordings")))
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

# 錄音檔名只允許安全字元，避免路徑穿越
SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.wav$")


def find_fluidsynth() -> Optional[str]:
    """尋找 fluidsynth 執行檔（launchd 環境的 PATH 可能不含 Homebrew）。"""
    p = shutil.which("fluidsynth")
    if p:
        return p
    for c in ("/opt/homebrew/bin/fluidsynth", "/usr/local/bin/fluidsynth", "/usr/bin/fluidsynth"):
        if os.path.exists(c):
            return c
    return None


def find_soundfont() -> Optional[str]:
    """尋找可用的 SoundFont 音色庫（.sf2）。"""
    candidates = []
    if os.getenv("SOUNDFONT_PATH"):
        candidates.append(Path(os.getenv("SOUNDFONT_PATH")))
    project_sf_dir = Path(__file__).parent.parent / "soundfonts"
    if project_sf_dir.exists():
        candidates.extend(sorted(project_sf_dir.glob("*.sf2")))
    # Linux（Docker / apt fluid-soundfont-gm）常見路徑
    candidates.append(Path("/usr/share/sounds/sf2/FluidR3_GM.sf2"))
    candidates.append(Path("/usr/share/sounds/sf2/default-GM.sf2"))
    for p in candidates:
        if p.exists():
            return str(p)
    return None


# Request/Response models
class LyricsRequest(BaseModel):
    keywords: List[str]
    emotion: str = "溫暖"


class LyricsResponse(BaseModel):
    verse: str
    chorus: str


class Note(BaseModel):
    start: float
    end: float
    midi: int
    velocity: int


class AnalyzeResponse(BaseModel):
    notes: List[Note]
    bpm: float
    key: str


class RenderRequest(BaseModel):
    notes: List[Note]
    bpm: float
    key: str
    lyrics: LyricsResponse
    chord_overrides: Optional[List[str]] = None
    seed: Optional[int] = None  # 指定 seed 可重現同一套伴奏；不給則每次隨機變化
    include_recording_filename: Optional[str] = None  # 混入 recordings/ 內的原始錄音（僅 /render-audio）


class AIComposeRequest(BaseModel):
    notes: List[Note]
    bpm: float
    key: str
    num_bars: int = 4


class AIComposeResponse(BaseModel):
    chords: List[str]
    source: str = "lm_studio"  # lm_studio（本地 AI）或 rules（規則式備援）


class MelodyRequest(BaseModel):
    root: str = "C"                     # 根音，例如 C、F#、Bb
    scale_type: str = "major"           # major / minor / major_pentatonic / minor_pentatonic / custom
    custom_notes: Optional[List[str]] = None  # scale_type=custom 時的音名列表，例如 ["C","D","E","G","A"]
    bpm: float = 90.0
    num_bars: int = 4
    seed: Optional[int] = None          # 相同 seed 會生成相同旋律


class MelodyResponse(BaseModel):
    notes: List[Note]
    bpm: float
    key: str


@app.get("/")
async def root():
    # 直接導向前台，手機/電腦打開網址就能用
    return RedirectResponse(url="/web/")


@app.get("/api")
async def api_info():
    return {"message": "Music Education MVP API", "version": "1.0.0"}


# ---------- 手機錄音：上傳 / 列表 / 下載 / 同步 ----------

class RecordingInfo(BaseModel):
    filename: str
    size: int
    created: str


class SyncRequest(BaseModel):
    remote_url: str  # 例如 https://automusic.zeabur.app


@app.post("/recordings/upload")
async def upload_recording(file: UploadFile = File(...)):
    """手機錄音上傳：儲存 WAV 到 recordings 目錄，回傳檔名。"""
    if not file.filename.lower().endswith(".wav"):
        raise HTTPException(status_code=400, detail="只支援 WAV 格式")

    content = await file.read()
    if len(content) < 1000:
        raise HTTPException(status_code=400, detail="錄音檔太小，可能是空的")
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="錄音檔太大（上限 50MB）")

    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    name = datetime.now().strftime("recording-%Y%m%d-%H%M%S") + ".wav"
    # 避免同秒重複覆蓋
    path = RECORDINGS_DIR / name
    counter = 1
    while path.exists():
        path = RECORDINGS_DIR / (name[:-4] + f"-{counter}.wav")
        counter += 1

    path.write_bytes(content)
    return {"filename": path.name, "size": len(content)}


@app.get("/recordings", response_model=List[RecordingInfo])
async def list_recordings():
    """列出所有已上傳的錄音（新的在前）。"""
    items = []
    for p in RECORDINGS_DIR.glob("*.wav"):
        stat = p.stat()
        items.append(
            RecordingInfo(
                filename=p.name,
                size=stat.st_size,
                created=datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            )
        )
    items.sort(key=lambda x: x.created, reverse=True)
    return items


@app.get("/recordings/{filename}")
async def download_recording(filename: str):
    """下載單一錄音檔。"""
    if not SAFE_FILENAME_RE.match(filename):
        raise HTTPException(status_code=400, detail="檔名不合法")
    path = RECORDINGS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="找不到錄音檔")
    return FileResponse(path, media_type="audio/wav", filename=filename)


@app.post("/sync-recordings")
def sync_recordings(request: SyncRequest):  # 同步函式：跑在 threadpool，避免阻塞事件迴圈
    """
    在本地電腦執行：從雲端（例如 Zeabur）把手機上傳的錄音抓回本地 recordings 目錄。
    只下載本地還沒有的檔案。
    """
    base = request.remote_url.rstrip("/")
    try:
        resp = requests.get(f"{base}/recordings", timeout=15)
        resp.raise_for_status()
        remote_list = resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"無法連線到雲端：{e}")

    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    downloaded = []
    skipped = 0
    for item in remote_list:
        fname = item.get("filename", "")
        if not SAFE_FILENAME_RE.match(fname):
            continue
        local_path = RECORDINGS_DIR / fname
        if local_path.exists():
            skipped += 1
            continue
        try:
            r = requests.get(f"{base}/recordings/{fname}", timeout=60)
            r.raise_for_status()
            local_path.write_bytes(r.content)
            downloaded.append(fname)
        except Exception:
            continue

    return {"downloaded": downloaded, "skipped": skipped, "total_remote": len(remote_list)}


@app.post("/generate-lyrics", response_model=LyricsResponse)
async def generate_lyrics(request: LyricsRequest):
    """
    根據關鍵字和情緒生成簡單、溫暖的歌詞
    使用模板規則，不使用外部 API
    """
    from app.lyrics.generator import generate_lyrics as gen_lyrics
    
    result = gen_lyrics(request.keywords, request.emotion)
    return LyricsResponse(**result)


@app.post("/generate-melody", response_model=MelodyResponse)
async def generate_melody_endpoint(request: MelodyRequest):
    """
    輸入音階（內建或自訂音名），在地端用演算法生成旋律。
    不需要錄音、不需要任何外部 API。
    生成的 notes 格式與 /analyze-audio 相同，可以直接丟給 /render-music 編曲。
    """
    from app.melody.generator import generate_melody

    if not (1 <= request.num_bars <= 16):
        raise HTTPException(status_code=400, detail="num_bars 需在 1~16 之間")
    if not (40 <= request.bpm <= 220):
        raise HTTPException(status_code=400, detail="bpm 需在 40~220 之間")

    try:
        result = generate_melody(
            root=request.root,
            scale_type=request.scale_type,
            custom_notes=request.custom_notes,
            bpm=request.bpm,
            num_bars=request.num_bars,
            seed=request.seed,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return MelodyResponse(
        notes=[Note(**n) for n in result["notes"]],
        bpm=result["bpm"],
        key=result["key"],
    )


@app.post("/compose-from-audio")
async def compose_from_audio(file: UploadFile = File(...)):
    """
    從素材聲音生成旋律：素材不會直接變成旋律，而是萃取它的
    「元素（動機、音域）與感覺（明暗、能量、走向）」來創作一段新旋律。
    回傳格式與 /analyze-audio 相容，可直接接編曲流程。
    """
    if not file.filename.lower().endswith(".wav"):
        raise HTTPException(status_code=400, detail="只支援 WAV 格式")

    from app.melody.from_audio import generate_melody_from_material

    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
        content = await file.read()
        tmp_file.write(content)
        tmp_path = tmp_file.name

    try:
        result = generate_melody_from_material(tmp_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    return result


@app.post("/analyze-audio", response_model=AnalyzeResponse)
async def analyze_audio(file: UploadFile = File(...)):
    """
    上傳 wav 檔，辨識音符、BPM 和調性
    """
    if not file.filename.endswith('.wav'):
        raise HTTPException(status_code=400, detail="只支援 WAV 格式")
    
    from app.audio.extract_notes import extract_notes_from_audio
    from app.audio.key_detect import detect_key
    
    # 儲存上傳的檔案到臨時位置
    with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as tmp_file:
        content = await file.read()
        tmp_file.write(content)
        tmp_path = tmp_file.name
    
    try:
        # 提取音符
        notes_data = extract_notes_from_audio(tmp_path)
        
        # 檢測調性
        key = detect_key(notes_data['notes'])
        
        # 計算 BPM（簡化版：假設平均節奏）
        bpm = notes_data.get('bpm', 90.0)
        
        return AnalyzeResponse(
            notes=[Note(**note) for note in notes_data['notes']],
            bpm=bpm,
            key=key
        )
    finally:
        # 清理臨時檔案
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


@app.post("/render-music")
async def render_music(request: RenderRequest):
    """
    根據音符、BPM、調性和歌詞生成完整的 MIDI 檔案
    """
    from app.midi.generate_midi import generate_full_midi
    
    # 轉換 notes
    notes_list = [
        {
            'start': note.start,
            'end': note.end,
            'midi': note.midi,
            'velocity': note.velocity
        }
        for note in request.notes
    ]
    
    # 生成 MIDI
    midi_path = generate_full_midi(
        notes=notes_list,
        bpm=request.bpm,
        key=request.key,
        lyrics=request.lyrics.dict(),
        chord_overrides=request.chord_overrides,
        seed=request.seed,
    )
    
    if not os.path.exists(midi_path):
        raise HTTPException(status_code=500, detail="MIDI 生成失敗")
    
    return FileResponse(
        midi_path,
        media_type="application/octet-stream",
        filename="full.mid"
    )


def _rule_based_chords(notes: List[Note], key: str, bpm: float, num_bars: int) -> List[str]:
    """規則式和弦推薦：依旋律評分挑和弦，含 V→I 終止式。"""
    from app.arrange.chords import select_chords_for_melody

    notes_list = [{"start": n.start, "end": n.end, "midi": n.midi, "velocity": n.velocity} for n in notes]
    return select_chords_for_melody(notes_list, key, bpm, num_bars)


def _load_voice_mono_44k(path: Path):
    """讀取錄音檔，轉單聲道並重取樣到 44100Hz。"""
    import numpy as np
    import soundfile as sf

    data, sr = sf.read(str(path))
    if data.ndim > 1:
        data = np.mean(data, axis=1)
    data = data.astype(np.float64)
    if sr != 44100:
        old_t = np.arange(len(data)) / sr
        new_t = np.arange(int(len(data) * 44100 / sr)) / 44100.0
        data = np.interp(new_t, old_t, data)
    return data


@app.post("/render-audio")
def render_audio(request: RenderRequest):
    """
    用 FluidSynth + SoundFont 把編曲轉成真實樂器音色的 WAV 音檔。
    若指定 include_recording_filename，會把原始錄音（人聲）混入成品，
    跟著歌曲結構在每次主旋律段落出現；MIDI 旋律則轉為小聲跟奏。
    """
    from app.midi.generate_midi import generate_full_midi, compute_song_structure

    fluidsynth_bin = find_fluidsynth()
    if not fluidsynth_bin:
        raise HTTPException(status_code=501, detail="此伺服器未安裝 FluidSynth，無法產生高音質音檔")
    soundfont = find_soundfont()
    if not soundfont:
        raise HTTPException(status_code=501, detail="找不到 SoundFont 音色庫（.sf2）")

    # 檢查要混入的錄音檔
    voice_path = None
    if request.include_recording_filename:
        if not SAFE_FILENAME_RE.match(request.include_recording_filename):
            raise HTTPException(status_code=400, detail="錄音檔名不合法")
        voice_path = RECORDINGS_DIR / request.include_recording_filename
        if not voice_path.exists():
            raise HTTPException(status_code=404, detail="找不到要混入的錄音檔")

    notes_list = [
        {"start": n.start, "end": n.end, "midi": n.midi, "velocity": n.velocity}
        for n in request.notes
    ]
    midi_path = generate_full_midi(
        notes=notes_list,
        bpm=request.bpm,
        key=request.key,
        lyrics=request.lyrics.dict(),
        chord_overrides=request.chord_overrides,
        seed=request.seed,
        melody_gain=0.4 if voice_path else 1.0,  # 有人聲時 MIDI 旋律退居小聲跟奏
    )

    wav_path = "/tmp/full_render.wav"
    try:
        subprocess.run(
            [
                fluidsynth_bin, "-ni",
                "-F", wav_path,
                "-r", "44100",
                "-g", "0.7",       # 整體增益，避免破音
                "-R", "1",         # 殘響：空間感
                "-C", "1",         # 合唱效果：音色厚一點
                soundfont, midi_path,
            ],
            check=True,
            capture_output=True,
            timeout=300,
        )
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"FluidSynth 轉檔失敗：{e.stderr.decode()[:300]}")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="FluidSynth 轉檔逾時")

    if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1000:
        raise HTTPException(status_code=500, detail="音檔產生失敗")

    # ---- 混入原始錄音（人聲）----
    if voice_path:
        import numpy as np
        import soundfile as sf

        acc, _ = sf.read(wav_path)          # 伴奏，44100 立體聲
        if acc.ndim == 1:
            acc = np.stack([acc, acc], axis=1)
        voice = _load_voice_mono_44k(voice_path)

        structure = compute_song_structure(notes_list, request.bpm)
        bar_dur = structure["bar_duration"]
        section_len_samples = int(structure["melody_bars"] * bar_dur * 44100)
        voice = voice[:section_len_samples]  # 裁到主旋律段長度，避免蓋到尾奏

        # 人聲 peak 正規化
        peak = float(np.max(np.abs(voice))) if len(voice) else 0.0
        if peak > 0:
            voice = voice * (0.9 / peak)

        mix = acc * 0.7  # 伴奏稍退，留空間給人聲
        for r in range(structure["repeats"]):
            offset = int((structure["intro_bars"] + r * structure["melody_bars"]) * bar_dur * 44100)
            end = min(len(mix), offset + len(voice))
            if end <= offset:
                continue
            seg = voice[: end - offset]
            mix[offset:end, 0] += seg * 0.95
            mix[offset:end, 1] += seg * 0.95

        # 防爆音
        m = float(np.max(np.abs(mix)))
        if m > 0.99:
            mix = mix * (0.99 / m)

        mixed_path = "/tmp/full_render_voice.wav"
        sf.write(mixed_path, mix, 44100)
        return FileResponse(mixed_path, media_type="audio/wav", filename="song.wav")

    return FileResponse(wav_path, media_type="audio/wav", filename="song.wav")


@app.post("/ai-compose", response_model=AIComposeResponse)
def ai_compose(request: AIComposeRequest):  # 同步函式：跑在 threadpool，LM 推論不會卡住其他請求
    """
    使用本地 LM Studio（例如 Gemma 3 12B）根據學生旋律建議和弦進行。
    若 LM Studio 連不上（例如部署在雲端時），自動改用規則式推薦，不會失敗。
    """
    if not request.notes:
        raise HTTPException(status_code=400, detail="notes 不可為空")

    # 簡單摘要旋律（只取前 N 個音，避免 prompt 太長）
    max_notes_for_prompt = 16
    summary_notes = request.notes[:max_notes_for_prompt]
    melody_tokens = []
    for n in summary_notes:
        melody_tokens.append(f"{n.midi}@{round(n.start, 2)}-{round(n.end, 2)}")
    melody_str = ", ".join(melody_tokens)

    # 準備提示詞：請模型只回傳 JSON，方便程式解析
    system_prompt = (
        "你是一位流行音樂作曲老師，專門為兒童與親子課程設計簡單、穩定的和弦進行。\n"
        "規則：\n"
        "- 調性只考慮大調（例如 C, G, F...）。\n"
        "- 只能使用這些和弦級數：I, IV, V, vi（必要時可以用 ii, iii，但盡量少用）。\n"
        "- 每小節一個和弦，4/4 拍。\n"
        "- 結尾要回到 I（主和弦），聽起來有收尾感。\n"
        "- 回覆時，**只回傳 JSON**，格式為：{\"chords\":[\"I\",\"V\",\"vi\",\"IV\"]}，不要加任何註解或多餘文字。\n"
    )
    user_prompt = (
        f"調性（model 推測用）：{request.key}\n"
        f"BPM：約 {request.bpm}\n"
        f"學生旋律（MIDI 音高 @ 起訖秒數）：{melody_str}\n"
        f"請為這段旋律設計 {request.num_bars} 小節的和弦進行。\n"
        "再次提醒：只回傳 JSON，格式為 {\"chords\":[\"I\",\"V\",\"vi\",\"IV\",...]}。"
    )

    def _ask_lm_studio(url: str) -> List[str]:
        resp = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "ngrok-skip-browser-warning": "1",  # 避免 ngrok 免費版的瀏覽器警告頁
            },
            json={
                "model": LM_STUDIO_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.5,
                "max_tokens": 256,
            },
            timeout=(4, 60),  # 連線 4 秒逾時：連不上就快速換下一個網址
        )
        if resp.status_code != 200:
            raise RuntimeError(f"LM Studio 回傳錯誤：{resp.status_code}")

        data = resp.json()
        content = data["choices"][0]["message"]["content"]

        # 嘗試從 content 中擷取 JSON
        json_str = content.strip()
        # 去掉可能的 code fence
        if json_str.startswith("```"):
            json_str = json_str.strip("`")
            # 移除可能的語言標籤
            json_str = "\n".join(line for line in json_str.splitlines() if not line.strip().startswith("json"))

        try:
            parsed = json.loads(json_str)
        except Exception:
            # 嘗試從文字中抓第一個 {...}
            start = json_str.find("{")
            end = json_str.rfind("}")
            if start != -1 and end != -1 and end > start:
                parsed = json.loads(json_str[start : end + 1])
            else:
                raise RuntimeError("無法解析 LM Studio 回傳的 JSON")

        chords = parsed.get("chords")
        if not isinstance(chords, list) or not chords:
            raise RuntimeError("LM Studio 回傳的 chords 無效")

        # 清洗和弦，只保留允許的級數
        allowed = {"I", "IV", "V", "vi", "ii", "iii"}
        cleaned = [c for c in chords if isinstance(c, str) and c in allowed]
        if not cleaned:
            raise RuntimeError("LM Studio 沒有回傳有效的和弦級數")

        # 修剪 / 補足到指定小節數
        while len(cleaned) < request.num_bars:
            cleaned.extend(cleaned)
        return cleaned[: request.num_bars]

    # 依序嘗試每個 LM Studio 網址，第一個成功的就用
    for url in LM_STUDIO_URLS:
        try:
            chords = _ask_lm_studio(url)
            return AIComposeResponse(chords=chords, source="lm_studio")
        except Exception as e:
            print(f"[ai-compose] LM Studio 網址失敗（{url}）：{e}")
            continue

    # 全部連不上：改用規則式推薦
    print("[ai-compose] 所有 LM Studio 網址都不可用，改用規則式推薦")
    fallback = _rule_based_chords(request.notes, request.key, request.bpm, request.num_bars)
    return AIComposeResponse(chords=fallback, source="rules")
