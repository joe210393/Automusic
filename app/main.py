from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.concurrency import run_in_threadpool
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

# 遊客旅程 API（薄適配層）
from app.journey.router import router as journey_router
from app.ops.admin_router import router as admin_router

app.include_router(journey_router)
app.include_router(admin_router)

# 工程實驗室（原六步驟 Demo）— 路徑維持 /web，勿改壞
frontend_dir = Path(__file__).parent / "frontend"
if frontend_dir.exists():
    app.mount("/web", StaticFiles(directory=str(frontend_dir), html=True), name="web")

# 遊客體驗站靜態資源
tourist_dir = Path(__file__).parent / "tourist"
if tourist_dir.exists():
    app.mount("/trip", StaticFiles(directory=str(tourist_dir)), name="tourist_assets")

# 內容後台（旅程／目的地 CMS）
admin_dir = Path(__file__).parent / "admin"
if admin_dir.exists():
    @app.get("/admin")
    @app.get("/admin/")
    async def admin_home():
        return FileResponse(admin_dir / "index.html")

    app.mount("/admin-assets", StaticFiles(directory=str(admin_dir)), name="admin_assets")

# LM Studio 設定：依序嘗試多個網址（區網優先、再走 ngrok），可用環境變數覆寫
# LM_STUDIO_URLS 用逗號分隔多個網址；LM_STUDIO_URL 單一網址（優先權最高，向下相容）
# ngrok 免費版只有一個固定網域，指向本地 8080 的 FastAPI；
# 雲端存取 LM Studio 走 FastAPI 的 /lm/ 代理（見 lm_proxy）。
_default_lm_urls = [
    "http://127.0.0.1:1234/v1/chat/completions",                              # 本機（LaunchAgent / 本機開網頁最快）
    "http://192.168.1.198:1234/v1/chat/completions",                          # 區網（手機同 Wi‑Fi）
    "https://tactually-venerable-inez.ngrok-free.dev/lm/v1/chat/completions", # 雲端 Zeabur → ngrok → /lm → LM Studio
]
# /lm 代理的轉發目標（本地 LM Studio）
LM_PROXY_TARGET = os.getenv("LM_PROXY_TARGET", "http://127.0.0.1:1234")
if os.getenv("LM_STUDIO_URL"):
    LM_STUDIO_URLS = [os.getenv("LM_STUDIO_URL")]
elif os.getenv("LM_STUDIO_URLS"):
    LM_STUDIO_URLS = [u.strip() for u in os.getenv("LM_STUDIO_URLS").split(",") if u.strip()]
else:
    LM_STUDIO_URLS = _default_lm_urls
LM_STUDIO_MODEL = os.getenv("LM_STUDIO_MODEL", "google/gemma-4-31b-qat")

# 手機錄音儲存目錄（雲端與本地都用同一份程式碼）
# Zeabur 掛載持久化硬碟在 /voice：存在就用它，重新部署後錄音不會消失；
# 本地電腦沒有 /voice，退回專案內的 recordings/。也可用環境變數 RECORDINGS_DIR 覆寫。
_persistent_root = Path("/voice")
_default_recordings = (
    _persistent_root / "recordings" if _persistent_root.is_dir()
    else Path(__file__).parent.parent / "recordings"
)
RECORDINGS_DIR = Path(os.getenv("RECORDINGS_DIR", str(_default_recordings)))
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
print(f"[startup] 錄音儲存目錄：{RECORDINGS_DIR}")

# 聲紋目錄（步驟 5：使用者逐句唸歌詞的錄音＋manifest.json）
_default_voiceprint = (
    _persistent_root / "voiceprint" if _persistent_root.is_dir()
    else Path(__file__).parent.parent / "voiceprint"
)
VOICEPRINT_DIR = Path(os.getenv("VOICEPRINT_DIR", str(_default_voiceprint)))
VOICEPRINT_DIR.mkdir(parents=True, exist_ok=True)
VOICEPRINT_MANIFEST = VOICEPRINT_DIR / "manifest.json"


def _load_voiceprint_manifest() -> dict:
    if VOICEPRINT_MANIFEST.exists():
        try:
            return json.loads(VOICEPRINT_MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"lines": []}


def _save_voiceprint_manifest(manifest: dict):
    VOICEPRINT_DIR.mkdir(parents=True, exist_ok=True)
    VOICEPRINT_MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

# 錄音檔名只允許安全字元，避免路徑穿越
SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.wav$")

# 雲端（Zeabur）沒有原聲 SF2 時，把 MIDI→WAV 委託回本機 Mac（同一條 ngrok）
_default_render_urls = "https://tactually-venerable-inez.ngrok-free.dev"
RENDER_REMOTE_URLS = [
    u.strip() for u in os.getenv("RENDER_REMOTE_URLS", _default_render_urls).split(",") if u.strip()
]


def find_fluidsynth() -> Optional[str]:
    """尋找 fluidsynth 執行檔（launchd 環境的 PATH 可能不含 Homebrew）。"""
    p = shutil.which("fluidsynth")
    if p:
        return p
    for c in ("/opt/homebrew/bin/fluidsynth", "/usr/local/bin/fluidsynth", "/usr/bin/fluidsynth"):
        if os.path.exists(c):
            return c
    return None


def find_ffmpeg() -> Optional[str]:
    """尋找 ffmpeg 執行檔（用來把成品 WAV 壓成 MP3，下載快很多）。"""
    p = shutil.which("ffmpeg")
    if p:
        return p
    for c in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
        if os.path.exists(c):
            return c
    return None


def compress_to_mp3(wav_path: str) -> Optional[str]:
    """
    把 WAV 壓成 MP3（160kbps）。成功回傳 MP3 路徑，失敗回傳 None（改傳 WAV）。
    未壓縮 WAV 一首歌 5-10MB，行動網路下載要數分鐘；MP3 只有約十分之一。
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return None
    mp3_path = wav_path.rsplit(".", 1)[0] + ".mp3"
    try:
        subprocess.run(
            [ffmpeg, "-y", "-i", wav_path, "-codec:a", "libmp3lame", "-b:a", "160k", mp3_path],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except Exception as e:
        print(f"[render-audio] MP3 壓縮失敗，改傳 WAV：{e}")
        return None
    if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 1000:
        return mp3_path
    return None


def find_soundfont() -> Optional[str]:
    """尋找可用的 SoundFont（優先 MuseScore_General 原聲向 GM）。"""
    from app.audio.soundfont_render import find_base_soundfont

    return find_base_soundfont()


# Request/Response models
class LyricsRequest(BaseModel):
    keywords: List[str]
    emotion: str = "溫暖"


class LyricsResponse(BaseModel):
    verse: str
    chorus: str


class AILyricsRequest(BaseModel):
    keywords: List[str]           # 關鍵字，例如 ["海邊", "夏天", "朋友"]
    style: Optional[str] = None   # pop/ballad/folk/rock/jazz/lullaby，影響歌詞語氣
    seed: Optional[int] = None    # 換一版時給不同 seed（目前僅影響備援模板）


class AILyricsResponse(BaseModel):
    title: str
    verse: str    # 主歌，多行以換行分隔
    chorus: str   # 副歌，多行以換行分隔
    source: str = "lm_studio"  # lm_studio（本地 AI）或 template（模板備援）
    detail: Optional[str] = None  # 備援時說明原因（逾時／解析失敗等）


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
    style: Optional[str] = None  # 風格（pop/ballad/folk/rock/jazz/lullaby），影響鼓/貝斯/樂器音色
    duration_seconds: int = 30  # 歌曲長度：30（副歌）/ 60 / 90（完整：主歌→副歌）
    use_voiceprint: bool = False  # 步驟 6：用聲紋（步驟 5 的逐句錄音）合成人聲並混入（僅 /render-audio）


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
    """遊客體驗首頁；工程 Demo 請走 /web/。"""
    index = tourist_dir / "index.html"
    if index.exists():
        return FileResponse(index)
    return RedirectResponse(url="/web/")


@app.get("/login")
async def tourist_login_page():
    page = tourist_dir / "login.html"
    if page.exists():
        return FileResponse(page, headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail="登入頁尚未準備好")


@app.get("/register")
async def tourist_register_page():
    page = tourist_dir / "register.html"
    if page.exists():
        return FileResponse(page, headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail="註冊頁尚未準備好")


@app.get("/me")
async def tourist_me_page():
    """使用者後台：帳號與我的旅程。"""
    page = tourist_dir / "me.html"
    if page.exists():
        return FileResponse(page, headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail="使用者後台尚未準備好")


@app.get("/s/{slug}")
async def share_page(slug: str):
    """公開旅行音樂卡（OG／分享頁）。"""
    page = tourist_dir / "share.html"
    if page.exists():
        return FileResponse(page)
    raise HTTPException(status_code=404, detail="分享頁尚未準備好")


@app.get("/api")
async def api_info():
    return {
        "message": "Automusic Travel + Lab API",
        "version": "1.0.0",
        "tourist": "/",
        "login": "/login",
        "register": "/register",
        "me": "/me",
        "lab": "/web/",
        "admin": "/admin",
    }


@app.get("/health")
async def health():
    """開機就緒檢查：DiffSinger / Seed-VC / LM Studio / 原聲渲染。"""
    from app.voice import svs as _svs
    from app.voice import neural_vc as _vc
    from app.audio.soundfont_render import can_render_acoustic_locally, acoustic_lead_programs

    lm_ok = False
    try:
        r = requests.get(f"{LM_PROXY_TARGET.rstrip('/')}/v1/models", timeout=1.5)
        lm_ok = r.status_code == 200
    except Exception:
        pass

    acoustic = can_render_acoustic_locally()
    ready = _svs.is_available() and _vc.is_available() and acoustic
    return {
        "ok": ready,
        "diffsinger": _svs.is_available(),
        "seed_vc": _vc.is_available(),
        "lm_studio": lm_ok,
        "acoustic_render": acoustic,
        "acoustic_programs": sorted(acoustic_lead_programs()),
        "fluidsynth": bool(find_fluidsynth()),
        "recordings_dir": str(RECORDINGS_DIR),
        "voiceprint_dir": str(VOICEPRINT_DIR),
    }


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


# ---------- 步驟 5：聲紋收集（逐句唸歌詞） ----------

@app.post("/voiceprint/upload")
async def upload_voiceprint_line(
    file: UploadFile = File(...),
    section: str = Form(...),   # verse / chorus
    index: int = Form(...),     # 句序（該段落內從 0 起算）
    text: str = Form(...),      # 這句歌詞的文字（用來算音節數）
):
    """步驟 5：上傳使用者唸某一句歌詞的錄音。同一句重錄會直接覆蓋。"""
    if section not in ("verse", "chorus"):
        raise HTTPException(status_code=400, detail="section 必須是 verse 或 chorus")
    if not (0 <= index < 20):
        raise HTTPException(status_code=400, detail="index 超出範圍")
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="錄音檔太大（上限 20MB）")
    if len(content) < 2000:
        raise HTTPException(status_code=400, detail="錄音太短，請重錄")

    VOICEPRINT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{section}-{index:02d}.wav"
    (VOICEPRINT_DIR / filename).write_bytes(content)

    manifest = _load_voiceprint_manifest()
    manifest["lines"] = [
        l for l in manifest.get("lines", [])
        if not (l.get("section") == section and l.get("index") == index)
    ]
    manifest["lines"].append({
        "section": section,
        "index": index,
        "text": text.strip(),
        "filename": filename,
    })
    _save_voiceprint_manifest(manifest)
    return {"filename": filename, "recorded": len(manifest["lines"])}


@app.get("/voiceprint/status")
async def voiceprint_status():
    """查詢已錄好的聲紋句子。"""
    manifest = _load_voiceprint_manifest()
    lines = sorted(
        manifest.get("lines", []),
        key=lambda l: (0 if l.get("section") == "verse" else 1, l.get("index", 0)),
    )
    return {"count": len(lines), "lines": lines}


@app.post("/voiceprint/reset")
async def voiceprint_reset():
    """清空聲紋（重新開始錄）。"""
    manifest = _load_voiceprint_manifest()
    for l in manifest.get("lines", []):
        try:
            (VOICEPRINT_DIR / l.get("filename", "")).unlink(missing_ok=True)
        except Exception:
            pass
    _save_voiceprint_manifest({"lines": []})
    return {"ok": True}


# ---------- LM Studio 代理（一條 ngrok 通道同時服務 LM 與 Seed-VC） ----------

@app.api_route("/lm/{path:path}", methods=["GET", "POST"])
async def lm_proxy(path: str, request: Request):
    """
    把 /lm/* 轉發到本地 LM Studio（例如 /lm/v1/chat/completions → 127.0.0.1:1234/v1/chat/completions）。
    ngrok 免費版只有一個固定網域，讓它指向 FastAPI，雲端就能經這裡使用 LM Studio。
    """
    target = f"{LM_PROXY_TARGET.rstrip('/')}/{path}"
    body = await request.body()

    def _forward():
        return requests.request(
            request.method,
            target,
            data=body if body else None,
            headers={"Content-Type": request.headers.get("content-type", "application/json")},
            timeout=(5, 600),  # LM 推理可能要幾分鐘
        )

    try:
        resp = await run_in_threadpool(_forward)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"無法連線到本地 LM Studio：{e}")
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )


@app.api_route("/v1/{path:path}", methods=["GET", "POST"])
async def lm_proxy_v1(path: str, request: Request):
    """
    /v1/* 直通 LM Studio：讓其他專案沿用舊的 ngrok 網址
    （以前這個網域直接指向 LM Studio，現在指向 FastAPI，補這條保持相容）。
    """
    return await lm_proxy("v1/" + path, request)


# ---------- 神經歌聲轉換服務（給雲端部署委託本地電腦用） ----------

@app.post("/render-midi")
async def render_midi_remote_endpoint(
    midi: UploadFile = File(...),
    use_lead_overlay: bool = Form(True),
):
    """
    本機原聲 FluidSynth 渲染：上傳 MIDI → 回傳 WAV。
    給雲端 Zeabur 委託（與 /vc/convert、/svs/synthesize 同一條 ngrok）。
    """
    from app.audio.soundfont_render import render_midi_to_wav

    fluidsynth_bin = find_fluidsynth()
    if not fluidsynth_bin:
        raise HTTPException(status_code=501, detail="此伺服器未安裝 FluidSynth")
    soundfont = find_soundfont()
    if not soundfont:
        raise HTTPException(status_code=501, detail="找不到 SoundFont 音色庫")

    fd_mid, midi_path = tempfile.mkstemp(suffix=".mid")
    os.close(fd_mid)
    fd_wav, wav_path = tempfile.mkstemp(suffix="_render.wav")
    os.close(fd_wav)
    try:
        with open(midi_path, "wb") as f:
            f.write(await midi.read())
        render_midi_to_wav(
            fluidsynth_bin,
            midi_path,
            wav_path,
            use_lead_overlay=use_lead_overlay,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode(errors="replace")[:300]
        raise HTTPException(status_code=500, detail=f"FluidSynth 轉檔失敗：{err}")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="FluidSynth 轉檔逾時")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"音檔渲染失敗：{e}")
    finally:
        try:
            os.unlink(midi_path)
        except OSError:
            pass

    if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1000:
        raise HTTPException(status_code=500, detail="音檔產生失敗")

    # 遠端委託回傳未壓縮 WAV（避免再壓一次損失）；雲端端再決定是否壓 MP3
    return FileResponse(
        wav_path,
        media_type="audio/wav",
        filename="render.wav",
        headers={"X-Render-Engine": "acoustic-local"},
    )


def _render_midi_via_remote(
    midi_path: str,
    *,
    use_lead_overlay: bool = True,
    timeout: int = 420,
) -> Optional[str]:
    """把 MIDI 交給 Mac（ngrok /render-midi），成功回傳本機暫存 WAV 路徑。"""
    if not RENDER_REMOTE_URLS:
        return None
    for base in RENDER_REMOTE_URLS:
        url = base.rstrip("/") + "/render-midi"
        try:
            with open(midi_path, "rb") as mf:
                resp = requests.post(
                    url,
                    headers={"ngrok-skip-browser-warning": "1"},
                    files={"midi": ("song.mid", mf, "audio/midi")},
                    data={"use_lead_overlay": "true" if use_lead_overlay else "false"},
                    timeout=(10, timeout),
                )
            if resp.status_code != 200 or len(resp.content) < 1000:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:240]}")
            out_path = tempfile.mktemp(prefix="render_remote_", suffix=".wav")
            with open(out_path, "wb") as f:
                f.write(resp.content)
            print(f"[render-audio] 已用遠端原聲渲染：{url}", flush=True)
            return out_path
        except Exception as e:
            print(f"[render-audio] 遠端渲染失敗（{url}）：{e}", flush=True)
            continue
    return None


@app.post("/vc/convert")
def vc_convert(source: UploadFile = File(...), reference: UploadFile = File(...)):
    """
    用本機的 Seed-VC 做歌聲轉換：source 是代唱乾聲，reference 是音色參考。
    雲端（Zeabur）沒有 GPU/模型時，可透過這個端點把轉換交給本地電腦跑。
    """
    from app.voice import neural_vc

    if not neural_vc.is_available():
        raise HTTPException(status_code=501, detail="此伺服器未安裝 Seed-VC")

    import tempfile
    src_path = tempfile.mktemp(prefix="vc_src_", suffix=".wav")
    ref_path = tempfile.mktemp(prefix="vc_ref_", suffix=".wav")
    with open(src_path, "wb") as f:
        f.write(source.file.read())
    with open(ref_path, "wb") as f:
        f.write(reference.file.read())

    out = neural_vc.convert_voice_local(src_path, ref_path)
    if not out:
        raise HTTPException(status_code=500, detail="Seed-VC 轉換失敗")
    return FileResponse(out, media_type="audio/wav", filename="converted.wav")


class SVSJobRequest(BaseModel):
    text: str
    notes: str
    notes_duration: str
    input_type: str = "word"


@app.post("/svs/synthesize")
def svs_synthesize(job: SVSJobRequest):
    """
    用本機 DiffSinger 代唱：word-level job → 乾聲 WAV。
    給雲端部署委託本地 Mac 用。
    """
    from app.voice import svs

    if not svs.is_available():
        raise HTTPException(status_code=501, detail="此伺服器未安裝 DiffSinger")

    payload = job.model_dump() if hasattr(job, "model_dump") else job.dict()
    out = svs.synthesize_job_to_wav(payload)
    if not out:
        raise HTTPException(status_code=500, detail="DiffSinger 代唱失敗")
    return FileResponse(out, media_type="audio/wav", filename="svs.wav")


@app.post("/generate-lyrics", response_model=LyricsResponse)
async def generate_lyrics(request: LyricsRequest):
    """
    根據關鍵字和情緒生成簡單、溫暖的歌詞
    使用模板規則，不使用外部 API
    """
    from app.lyrics.generator import generate_lyrics as gen_lyrics
    
    result = gen_lyrics(request.keywords, request.emotion)
    return LyricsResponse(**result)


@app.post("/generate-lyrics-ai", response_model=AILyricsResponse)
def generate_lyrics_ai(request: AILyricsRequest):  # 同步函式：跑在 threadpool，LM 推論不會卡住其他請求
    """
    步驟 4｜關鍵字填詞：用本地 LM Studio（gemma）依關鍵字＋風格寫出主歌與副歌。
    LM 連不上／解析失敗時退回模板式歌詞，不會失敗。
    """
    keywords = [k.strip() for k in request.keywords if k and k.strip()]
    if not keywords:
        raise HTTPException(status_code=400, detail="請至少輸入一個關鍵字")
    keywords = keywords[:6]  # 太多關鍵字反而寫不好

    from app.lyrics.ai_writer import build_lyrics_prompts, parse_lyrics_from_message

    system_prompt, user_prompt = build_lyrics_prompts(keywords, request.style)
    errors: list[str] = []

    for url in LM_STUDIO_URLS:
        try:
            resp = requests.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "ngrok-skip-browser-warning": "1",
                },
                json={
                    "model": LM_STUDIO_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.9,  # 作詞要有創意，溫度開高一點
                    # gemma-4 是推理模型，會先思考再寫詞，token 要留多一點
                    "max_tokens": 2048,
                },
                timeout=(4, 300),
            )
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {(resp.text or '')[:200]}")

            message = resp.json()["choices"][0]["message"]
            parsed = parse_lyrics_from_message(message)
            if not parsed:
                preview = (
                    (message.get("content") or "")
                    + "\n"
                    + (message.get("reasoning_content") or "")
                ).strip()[:300]
                raise RuntimeError(f"無法解析歌詞 JSON；回傳預覽：{preview!r}")
            return AILyricsResponse(**parsed, source="lm_studio")
        except Exception as e:
            msg = f"{url} → {e}"
            errors.append(msg)
            print(f"[generate-lyrics-ai] LM Studio 網址失敗（{url}）：{e}")
            continue

    # 全部連不上或解析失敗：退回模板式歌詞
    detail = "；".join(errors[-3:]) if errors else "沒有可用的 LM Studio 網址"
    print(f"[generate-lyrics-ai] 改用模板歌詞：{detail}")
    from app.lyrics.generator import generate_lyrics as gen_lyrics

    style_emotion = {
        "pop": "開心", "rock": "開心",
        "ballad": "溫暖", "lullaby": "溫暖",
        "folk": "平靜", "jazz": "平靜",
    }
    result = gen_lyrics(keywords, style_emotion.get(request.style or "", "溫暖"))
    return AILyricsResponse(
        title=keywords[0] + "之歌",
        verse=result["verse"],
        chorus=result["chorus"],
        source="template",
        detail=detail,
    )


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
async def compose_from_audio(
    file: UploadFile = File(...),
    style: Optional[str] = Form(None),
    seed: Optional[int] = Form(None),
):
    """
    從素材聲音生成旋律：素材不會直接變成旋律，而是萃取它的
    「元素（動機、音域）與感覺（明暗、能量、走向）」來創作一段新旋律。
    style 可指定風格；省略則自動依素材感覺隨機挑相容風格。
    seed 可重現同一版；省略則每次隨機。
    """
    if not file.filename.lower().endswith(".wav"):
        raise HTTPException(status_code=400, detail="只支援 WAV 格式")

    from app.melody.from_audio import generate_melody_from_material

    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
        content = await file.read()
        tmp_file.write(content)
        tmp_path = tmp_file.name

    try:
        result = generate_melody_from_material(tmp_path, style=style, seed=seed)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    return result


@app.get("/theory")
def get_theory():
    """回傳樂理資料庫內容（app/theory/theory_db.json），方便查看目前的作曲規則。"""
    from app.theory.knowledge import load_theory

    return load_theory()


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
        style=request.style,
        duration_seconds=request.duration_seconds,
    )
    
    if not os.path.exists(midi_path):
        raise HTTPException(status_code=500, detail="MIDI 生成失敗")
    
    return FileResponse(
        midi_path,
        media_type="application/octet-stream",
        filename="full.mid"
    )


def _rule_based_chords(
    notes: List[Note],
    key: str,
    bpm: float,
    num_bars: int,
    style: Optional[str] = None,
    seed: Optional[int] = None,
) -> List[str]:
    """規則式和弦推薦：依風格進行池＋旋律評分挑選。"""
    from app.arrange.chords import select_chords_for_melody

    notes_list = [{"start": n.start, "end": n.end, "midi": n.midi, "velocity": n.velocity} for n in notes]
    return select_chords_for_melody(
        notes_list, key, bpm, num_bars, style=style, seed=seed,
    )


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
        # 聲紋演唱時關掉 MIDI 主旋律（兩個聲音疊在一起超魔音）；
        # 只有混入原始錄音時才留小聲跟奏
        melody_gain=0.0 if request.use_voiceprint else (0.4 if voice_path else 1.0),
        style=request.style,
        duration_seconds=request.duration_seconds,
    )

    wav_path = "/tmp/full_render.wav"
    use_lead_overlay = not request.use_voiceprint
    render_engine = "local"
    try:
        from app.audio.soundfont_render import (
            can_render_acoustic_locally,
            render_midi_to_wav,
        )

        # 雲端沒有原聲 SF2：先委託 Mac（ngrok /render-midi）；失敗再退回本機 FluidR3
        remote_wav = None
        if not can_render_acoustic_locally() and RENDER_REMOTE_URLS:
            remote_wav = _render_midi_via_remote(
                midi_path, use_lead_overlay=use_lead_overlay
            )
        if remote_wav:
            shutil.copyfile(remote_wav, wav_path)
            try:
                os.unlink(remote_wav)
            except OSError:
                pass
            render_engine = "acoustic-remote"
        else:
            # 聲紋代唱時主旋律 MIDI 已壓低／關掉，不必再套原聲主奏疊層
            info = render_midi_to_wav(
                fluidsynth_bin,
                midi_path,
                wav_path,
                use_lead_overlay=use_lead_overlay,
            )
            mode = (info or {}).get("mode", "base_only")
            render_engine = "acoustic-local" if mode == "layered" else "base-local"
    except FileNotFoundError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode(errors="replace")[:300]
        raise HTTPException(status_code=500, detail=f"FluidSynth 轉檔失敗：{err}")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="FluidSynth 轉檔逾時")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"音檔渲染失敗：{e}")

    if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1000:
        raise HTTPException(status_code=500, detail="音檔產生失敗")

    render_headers = {"X-Render-Engine": render_engine}

    # ---- 混入原始錄音（人聲）----
    if voice_path:
        import numpy as np
        import soundfile as sf

        acc, _ = sf.read(wav_path)          # 伴奏，44100 立體聲
        if acc.ndim == 1:
            acc = np.stack([acc, acc], axis=1)
        voice = _load_voice_mono_44k(voice_path)

        structure = compute_song_structure(notes_list, request.bpm, target_seconds=request.duration_seconds)
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
        mp3 = compress_to_mp3(mixed_path)
        if mp3:
            return FileResponse(
                mp3, media_type="audio/mpeg", filename="song.mp3", headers=render_headers
            )
        return FileResponse(
            mixed_path, media_type="audio/wav", filename="song.wav", headers=render_headers
        )

    # ---- 步驟 6：系統代唱（DiffSinger）→ Seed-VC 換成使用者聲紋 → 混進伴奏 ----
    if request.use_voiceprint:
        import numpy as np
        import soundfile as sf
        from app.voice.sing import build_vocal_track, apply_reverb, load_mono
        from app.voice import neural_vc, svs

        manifest = _load_voiceprint_manifest()
        if not manifest.get("lines"):
            raise HTTPException(status_code=400, detail="還沒有聲紋，請先在步驟 5 逐句錄音")

        acc, _ = sf.read(wav_path)
        if acc.ndim == 1:
            acc = np.stack([acc, acc], axis=1)

        structure = compute_song_structure(notes_list, request.bpm, target_seconds=request.duration_seconds)
        lyrics_dict = request.lyrics.dict() if request.lyrics else None

        # 1) 優先：DiffSinger 代唱乾聲（真正的歌聲合成）
        vocal_engine = "fallback-speech"
        vocal = None
        if svs.is_available() or svs.SVS_REMOTE_URLS:
            vocal = svs.build_svs_vocal_track(
                notes=notes_list,
                bpm=request.bpm,
                structure=structure,
                lyrics=lyrics_dict,
                total_samples=len(acc),
            )
            if vocal is not None:
                vocal_engine = "diffsinger"

        # 2) 代唱失敗才退回舊的「說話拉伸」底稿
        if vocal is None:
            print("[render-audio] DiffSinger 不可用，退回說話拉伸底稿")
            vocal = build_vocal_track(
                notes=notes_list,
                bpm=request.bpm,
                structure=structure,
                voiceprint_dir=VOICEPRINT_DIR,
                manifest=manifest,
                total_samples=len(acc),
                lyrics=lyrics_dict,
            )
        if vocal is None:
            raise HTTPException(status_code=400, detail="無法生成人聲底稿，請確認步驟 4 歌詞與步驟 5 聲紋")

        # 3) Seed-VC：把乾聲換成使用者音色
        ref_path = neural_vc.build_reference_wav(VOICEPRINT_DIR, manifest)
        if ref_path and (neural_vc.is_available() or neural_vc.VC_REMOTE_URLS):
            src_path = "/tmp/vocal_draft.wav"
            sf.write(src_path, vocal, 44100)
            converted = neural_vc.convert_voice(src_path, ref_path)
            if converted:
                v = load_mono(converted, 44100)
                if len(v) < len(vocal):
                    v = np.pad(v, (0, len(vocal) - len(v)))
                vocal = v[: len(vocal)]
                vocal_engine = vocal_engine + "+seed-vc"
            else:
                print("[render-audio] Seed-VC 不可用，使用代唱原音色")

        vocal = apply_reverb(vocal)

        # 人聲響度對齊伴奏（略突出但不搶戲）
        acc_rms = float(np.sqrt(np.mean(acc ** 2)))
        voc_rms = float(np.sqrt(np.mean(vocal[vocal != 0] ** 2))) if np.any(vocal != 0) else 0.0
        if voc_rms > 1e-6:
            vocal = vocal * (acc_rms * 1.25 / voc_rms)

        mix = acc * 0.8
        mix[:, 0] += vocal
        mix[:, 1] += vocal
        m = float(np.max(np.abs(mix)))
        if m > 0.99:
            mix = mix * (0.99 / m)

        sung_path = "/tmp/full_render_sung.wav"
        sf.write(sung_path, mix, 44100)
        headers = {**render_headers, "X-Vocal-Engine": vocal_engine}
        mp3 = compress_to_mp3(sung_path)
        if mp3:
            return FileResponse(mp3, media_type="audio/mpeg", filename="song.mp3", headers=headers)
        return FileResponse(sung_path, media_type="audio/wav", filename="song.wav", headers=headers)

    mp3 = compress_to_mp3(wav_path)
    if mp3:
        return FileResponse(
            mp3, media_type="audio/mpeg", filename="song.mp3", headers=render_headers
        )
    return FileResponse(
        wav_path, media_type="audio/wav", filename="song.wav", headers=render_headers
    )


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

    # 準備提示詞：先讓模型「讀樂理資料庫」，再請它只回傳 JSON
    from app.theory.knowledge import theory_prompt_text

    system_prompt = (
        "你是一位流行音樂作曲老師，專門為兒童與親子課程設計簡單、穩定的和弦進行。\n\n"
        + theory_prompt_text(compact=True)
        + "\n\n格式規則：\n"
        "- 可用和弦級數：I, ii, iii, IV, V, vi, vii, bVII, bVI, bIII, "
        "Imaj7, ii7, iii7, IVmaj7, V7, vi7。每小節一個和弦，4/4 拍。\n"
        "- 不要逐音分析，直接依旋律的整體感覺挑選，越快決定越好。\n"
        "- 回覆時，**只回傳 JSON**，格式為：{\"chords\":[\"I\",\"V\",\"vi\",\"IV\"]}，不要加任何註解或多餘文字。\n"
    )
    user_prompt = (
        f"調性（model 推測用）：{request.key}\n"
        f"BPM：約 {request.bpm}\n"
        f"學生旋律（MIDI 音高 @ 起訖秒數）：{melody_str}\n"
        f"請為這段旋律設計 {request.num_bars} 小節的和弦進行。直接決定，不要分析過程。\n"
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
                # gemma-4 是推理模型，會先思考再作答（解析時也會從思考欄位撈 JSON）。
                # 本機 31B 約 11 token/秒，上限 1024 約等 90 秒內。
                "max_tokens": 1024,
            },
            timeout=(4, 300),  # 連線 4 秒逾時：連不上就快速換下一個網址；31B 推理模型作答需 1-3 分鐘
        )
        if resp.status_code != 200:
            raise RuntimeError(f"LM Studio 回傳錯誤：{resp.status_code}")

        data = resp.json()
        message = data["choices"][0]["message"]
        from app.lyrics.lm_json import extract_json_objects, message_text

        parsed = None
        for obj in extract_json_objects(message_text(message)):
            if isinstance(obj.get("chords"), list) and obj["chords"]:
                parsed = obj
                break
        if parsed is None:
            preview = message_text(message)[:300]
            raise RuntimeError(f"無法解析 LM Studio 回傳的 JSON；預覽：{preview!r}")

        chords = parsed.get("chords")
        if not isinstance(chords, list) or not chords:
            raise RuntimeError("LM Studio 回傳的 chords 無效")

        # 清洗和弦，只保留允許的級數
        from app.arrange.chords import CHORD_TYPES

        allowed = set(CHORD_TYPES.keys())
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
    fallback = _rule_based_chords(
        request.notes, request.key, request.bpm, request.num_bars,
        seed=hash((request.key, request.bpm, request.num_bars)) % (10**9),
    )
    return AIComposeResponse(chords=fallback, source="rules")
