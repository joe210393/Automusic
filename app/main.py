from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
from pathlib import Path
import os
import tempfile
import json
import requests

app = FastAPI(title="Music Education MVP", version="1.0.0")

# 提供簡單前台（錄音＋編曲 demo）
frontend_dir = Path(__file__).parent / "frontend"
if frontend_dir.exists():
    app.mount("/web", StaticFiles(directory=str(frontend_dir), html=True), name="web")

# LM Studio（本地）設定：可用環境變數覆寫
LM_STUDIO_URL = os.getenv("LM_STUDIO_URL", "http://192.168.1.199:1234/v1/chat/completions")
LM_STUDIO_MODEL = os.getenv("LM_STUDIO_MODEL", "google/gemma-3-12b")


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


class AIComposeRequest(BaseModel):
    notes: List[Note]
    bpm: float
    key: str
    num_bars: int = 4


class AIComposeResponse(BaseModel):
    chords: List[str]


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
    return {"message": "Music Education MVP API", "version": "1.0.0"}


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
    )
    
    if not os.path.exists(midi_path):
        raise HTTPException(status_code=500, detail="MIDI 生成失敗")
    
    return FileResponse(
        midi_path,
        media_type="application/octet-stream",
        filename="full.mid"
    )


@app.post("/ai-compose", response_model=AIComposeResponse)
async def ai_compose(request: AIComposeRequest):
    """
    使用本地 LM Studio（例如 Gemma 3 12B）根據學生旋律建議和弦進行。
    不呼叫任何雲端付費 API。
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

    try:
        resp = requests.post(
            LM_STUDIO_URL,
            headers={"Content-Type": "application/json"},
            json={
                "model": LM_STUDIO_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.5,
                "max_tokens": 256,
            },
            timeout=30,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"無法連線到 LM Studio: {e}")

    if resp.status_code != 200:
        raise HTTPException(status_code=500, detail=f"LM Studio 回傳錯誤：{resp.text}")

    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise HTTPException(status_code=500, detail=f"LM Studio 回傳格式錯誤：{e}")

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
            snippet = json_str[start : end + 1]
            parsed = json.loads(snippet)
        else:
            raise HTTPException(status_code=500, detail=f"無法解析 LM Studio 回傳的 JSON：{json_str}")

    chords = parsed.get("chords")
    if not isinstance(chords, list) or not chords:
        raise HTTPException(status_code=500, detail="LM Studio 回傳的 chords 無效")

    # 清洗和弦，只保留允許的級數
    allowed = {"I", "IV", "V", "vi", "ii", "iii"}
    cleaned = [c for c in chords if isinstance(c, str) and c in allowed]
    if not cleaned:
        # 退而求其次，用預設 I-V-vi-IV
        cleaned = ["I", "V", "vi", "IV"]

    # 修剪 / 補足到指定小節數
    if len(cleaned) < request.num_bars:
        # 重複填滿
        while len(cleaned) < request.num_bars:
            cleaned.extend(cleaned)
    cleaned = cleaned[: request.num_bars]

    return AIComposeResponse(chords=cleaned)
