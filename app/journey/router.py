"""遊客旅程 API：/api/journey/* 、/api/destinations 、/s/{slug}"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Body, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.content.loader import list_destinations, load_destination, resolve_engine_style, split_keywords
from app.journey import store
from app.journey import service
from app.ops import accounts as ops_accounts

router = APIRouter(tags=["journey"])

SAFE_SLOT = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# 避免同一旅程重複開工；Zeabur 閘道對長連線常 60s 左右 502，故 finalize 改非同步
_finalize_lock = threading.Lock()
_finalize_inflight: set[str] = set()


def _finalize_key(journey_id: str, kind: str) -> str:
    return f"{kind}:{journey_id}"


def _spawn_finalize(journey_id: str, kind: str, account_id: Optional[str], worker) -> None:
    key = _finalize_key(journey_id, kind)
    with _finalize_lock:
        if key in _finalize_inflight:
            return
        _finalize_inflight.add(key)

    def _run() -> None:
        try:
            worker()
        finally:
            with _finalize_lock:
                _finalize_inflight.discard(key)

    threading.Thread(target=_run, name=f"finalize-{kind}-{journey_id}", daemon=True).start()


class StoryBody(BaseModel):
    nickname: str = ""
    keywords: List[str] = Field(default_factory=list)
    """使用者提供的作詞關鍵字（非系統預設）。"""
    companions: str = ""
    feeling: str = ""
    memory: str = ""
    place: str = ""
    route_id: Optional[str] = None


class MoodBody(BaseModel):
    mood_id: str


class SingerBody(BaseModel):
    singer_id: str


class ConsentBody(BaseModel):
    accepted: bool = True


class CreateBody(BaseModel):
    destination: str = "suao"


class AccountEmailBody(BaseModel):
    email: str
    display_name: str = ""


class PayStubBody(BaseModel):
    """開發用付費旗標（之後接金流 webhook）。"""
    paid: bool = True


class TitleBody(BaseModel):
    title: str


def _meta_or_404(journey_id: str) -> dict:
    try:
        return store.load_meta(journey_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="找不到這趟旅程")


def _account_from_token(token: Optional[str]):
    if not token:
        return None
    return ops_accounts.get_by_token(token)


def _require_journey_owner(meta: dict, token: Optional[str]):
    acc = _account_from_token(token)
    if not acc:
        raise HTTPException(status_code=401, detail="尚未登入")
    if meta.get("account_id") and meta.get("account_id") != acc["id"]:
        raise HTTPException(status_code=403, detail="這不是你的旅程")
    if not meta.get("account_id"):
        meta["account_id"] = acc["id"]
        store.save_meta(meta["id"], meta)
    return acc


def _friendly(err: Exception) -> str:
    msg = str(err) or "製作暫時無法完成"
    low = msg.lower()
    if any(
        k in low
        for k in (
            "ngrok",
            "timeout",
            "connection",
            "fluidsynth",
            "soundfont",
            "bad gateway",
            "502",
            "gateway",
        )
    ):
        return "製作服務暫時繁忙，請稍後再試"
    return msg


def _finalize_result_payload(meta: dict) -> dict:
    jid = meta["id"]
    return {
        "ok": True,
        "status": meta.get("status"),
        "final_url": f"/api/journey/{jid}/audio/final" if meta.get("final_file") else None,
        "final_voice_url": (
            f"/api/journey/{jid}/audio/final-voice"
            if meta.get("final_voice_file")
            else None
        ),
        "share_path": f"/s/{meta['slug']}" if meta.get("slug") else None,
        "slug": meta.get("slug"),
        "lyrics": meta.get("lyrics"),
        "ai_singer_id": meta.get("ai_singer_id"),
        "ai_singer_label": meta.get("ai_singer_label"),
        "error": meta.get("error"),
    }


# ---------- 內容包 ----------

@router.get("/api/destinations")
def api_destinations():
    return {"destinations": list_destinations()}


@router.get("/api/destinations/{dest_id}")
def api_destination(dest_id: str):
    data = load_destination(dest_id)
    if not data or not data.get("enabled", True):
        raise HTTPException(status_code=404, detail="目的地尚未開放")
    return data


# ---------- 旅程 ----------

@router.post("/api/journey")
def create_journey(
    body: CreateBody = Body(default_factory=CreateBody),
    x_account_token: Optional[str] = Header(None),
):
    payload = body
    dest = load_destination(payload.destination)
    if not dest or not dest.get("enabled", True):
        raise HTTPException(status_code=400, detail="目的地尚未開放")
    account_id = None
    if x_account_token:
        acc = ops_accounts.get_by_token(x_account_token)
        if acc:
            account_id = acc["id"]
    meta = store.create_journey(destination=payload.destination, account_id=account_id)
    return meta


@router.get("/api/journey/{journey_id}")
def get_journey(journey_id: str):
    return _meta_or_404(journey_id)


@router.get("/api/journey/{journey_id}/library")
def journey_library_detail(
    journey_id: str,
    x_account_token: Optional[str] = Header(None),
):
    """使用者後台：單趟旅程詳情（錄音／歌詞／成品／封面）。"""
    meta = _meta_or_404(journey_id)
    _require_journey_owner(meta, x_account_token)
    return store.account_journey_detail(meta)


@router.patch("/api/journey/{journey_id}/title")
def rename_journey(
    journey_id: str,
    body: TitleBody,
    x_account_token: Optional[str] = Header(None),
):
    meta = _meta_or_404(journey_id)
    _require_journey_owner(meta, x_account_token)
    title = (body.title or "").strip()[:40]
    if not title:
        raise HTTPException(status_code=400, detail="請填寫旅程名稱")
    meta["title"] = title
    store.save_meta(journey_id, meta)
    return {"ok": True, "title": title, "journey": store.account_journey_detail(meta)}


@router.post("/api/journey/{journey_id}/cover")
async def upload_cover(
    journey_id: str,
    file: UploadFile = File(...),
    x_account_token: Optional[str] = Header(None),
):
    meta = _meta_or_404(journey_id)
    _require_journey_owner(meta, x_account_token)
    raw = await file.read()
    if len(raw) < 64:
        raise HTTPException(status_code=400, detail="圖片太小")
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="圖片請小於 5MB")
    name = (file.filename or "cover.jpg").lower()
    ext = Path(name).suffix
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        # sniff
        if raw[:3] == b"\xff\xd8\xff":
            ext = ".jpg"
        elif raw[:8] == b"\x89PNG\r\n\x1a\n":
            ext = ".png"
        elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            ext = ".webp"
        else:
            raise HTTPException(status_code=400, detail="僅支援 JPG／PNG／WebP")
    filename = f"cover{ext}"
    path = store.cover_dir(journey_id) / filename
    # 清掉舊封面
    for old in store.cover_dir(journey_id).glob("cover.*"):
        if old.name != filename:
            old.unlink(missing_ok=True)
    path.write_bytes(raw)
    meta["cover_file"] = filename  # 使用者自訂，覆蓋預設庫
    store.save_meta(journey_id, meta)
    detail = store.account_journey_detail(meta)
    return {
        "ok": True,
        "cover_url": detail.get("cover_url"),
        "cover_custom": True,
        "journey": detail,
    }


@router.get("/api/journey/{journey_id}/cover")
def get_cover(
    journey_id: str,
    x_account_token: Optional[str] = Header(None),
):
    meta = _meta_or_404(journey_id)
    ref = store.resolve_cover_ref(meta)
    # 預設庫封面可公開；自訂封面需擁有者或已公開分享
    if ref.startswith(store.STOCK_COVER_PREFIX):
        name = ref[len(store.STOCK_COVER_PREFIX):]
        path = store.stock_covers_dir() / name
        if not path.exists():
            raise HTTPException(status_code=404, detail="預設封面不存在")
        media = "image/webp" if path.suffix.lower() == ".webp" else "image/png"
        return FileResponse(path, media_type=media, filename=name)

    if not meta.get("share_public"):
        _require_journey_owner(meta, x_account_token)
    path = store.cover_dir(journey_id) / ref
    if not path.exists():
        raise HTTPException(status_code=404, detail="封面不存在")
    media = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media, filename=ref)


@router.get("/api/journey/{journey_id}/sounds/{filename}")
def get_journey_sound(
    journey_id: str,
    filename: str,
    x_account_token: Optional[str] = Header(None),
):
    meta = _meta_or_404(journey_id)
    _require_journey_owner(meta, x_account_token)
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(status_code=400, detail="檔名無效")
    path = store.sounds_dir(journey_id) / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="找不到錄音")
    media = "audio/mpeg" if path.suffix.lower() == ".mp3" else "audio/wav"
    return FileResponse(path, media_type=media, filename=filename)


@router.post("/api/journey/{journey_id}/route")
def set_route(journey_id: str, route_id: str = Form(...)):
    meta = _meta_or_404(journey_id)
    dest = load_destination(meta.get("destination") or "suao")
    routes = {r["id"]: r for r in (dest or {}).get("routes", [])}
    if route_id not in routes:
        raise HTTPException(status_code=400, detail="路線不存在")
    meta["route_id"] = route_id
    meta["status"] = "route"
    store.save_meta(journey_id, meta)
    return {"ok": True, "route": routes[route_id], "meta": meta}


@router.post("/api/journey/{journey_id}/sounds")
async def upload_sound(
    journey_id: str,
    file: UploadFile = File(...),
    slot: str = Form("sound"),
):
    meta = _meta_or_404(journey_id)
    if not SAFE_SLOT.match(slot):
        raise HTTPException(status_code=400, detail="slot 不合法")
    content = await file.read()
    if len(content) < 2000:
        raise HTTPException(status_code=400, detail="錄音太短，請再試一次")
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="檔案太大")

    filename = f"{slot}.wav"
    path = store.sounds_dir(journey_id) / filename
    path.write_bytes(content)

    sounds = [s for s in meta.get("sounds", []) if s.get("slot") != slot]
    sounds.append({"slot": slot, "filename": filename, "label": slot})
    meta["sounds"] = sounds
    meta["status"] = "collecting"
    store.save_meta(journey_id, meta)
    return {"ok": True, "sounds": sounds}


@router.delete("/api/journey/{journey_id}/sounds/{slot}")
def delete_sound(journey_id: str, slot: str):
    meta = _meta_or_404(journey_id)
    sounds = meta.get("sounds", [])
    keep = []
    for s in sounds:
        if s.get("slot") == slot:
            try:
                (store.sounds_dir(journey_id) / s["filename"]).unlink(missing_ok=True)
            except Exception:
                pass
        else:
            keep.append(s)
    meta["sounds"] = keep
    store.save_meta(journey_id, meta)
    return {"ok": True, "sounds": keep}


@router.post("/api/journey/{journey_id}/story")
def save_story(journey_id: str, body: StoryBody):
    meta = _meta_or_404(journey_id)
    keywords: List[str] = []
    for k in body.keywords or []:
        s = str(k or "").strip()
        if s and s not in keywords:
            keywords.append(s[:24])
    # 也接受把關鍵字打成一個字串欄位的舊客戶端
    if not keywords and body.place:
        keywords = split_keywords(body.place)[:6]
    if not keywords:
        raise HTTPException(status_code=400, detail="請至少填一個歌詞關鍵字")
    meta["nickname"] = (body.nickname or "")[:40]
    meta["keywords"] = keywords[:6]
    meta["companions"] = (body.companions or "")[:40]
    meta["feeling"] = (body.feeling or "")[:40]
    meta["memory"] = (body.memory or "")[:120]
    meta["place"] = (body.place or "")[:40]
    if body.route_id:
        meta["route_id"] = body.route_id
    if not str(meta.get("title") or "").strip():
        nick = meta["nickname"] or "旅人"
        seed = keywords[0]
        meta["title"] = f"{nick}的{seed}"[:40]
    meta["status"] = "story"
    store.save_meta(journey_id, meta)
    return {"ok": True, "meta": meta}


@router.post("/api/journey/{journey_id}/mood")
def save_mood(journey_id: str, body: MoodBody):
    meta = _meta_or_404(journey_id)
    dest = load_destination(meta.get("destination") or "suao")
    if not dest:
        raise HTTPException(status_code=400, detail="目的地無效")
    moods = {m["id"]: m for m in dest.get("moodStyles", [])}
    if body.mood_id not in moods:
        raise HTTPException(status_code=400, detail="請選擇一種感覺")
    meta["mood_id"] = body.mood_id
    meta["engine_style"] = resolve_engine_style(dest, body.mood_id)
    meta["status"] = "style"
    store.save_meta(journey_id, meta)
    return {"ok": True, "engine_style": meta["engine_style"], "meta": meta}


@router.post("/api/journey/{journey_id}/compose")
def compose_journey(journey_id: str):
    _meta_or_404(journey_id)
    try:
        meta = service.run_compose(journey_id)
        return {
            "ok": True,
            "status": meta["status"],
            "steps": meta.get("compose_steps"),
            "lyrics": meta.get("lyrics"),
            "preview_url": f"/api/journey/{journey_id}/audio/preview",
            "meta": meta,
        }
    except Exception as e:
        meta = store.load_meta(journey_id)
        meta["status"] = "error"
        meta["error"] = _friendly(e)
        store.save_meta(journey_id, meta)
        raise HTTPException(status_code=500, detail=_friendly(e))


@router.post("/api/journey/{journey_id}/preview/remake")
def remake_preview(journey_id: str):
    """同一旋律／歌詞，只重抽真實樂器伴奏。"""
    _meta_or_404(journey_id)
    try:
        meta = service.run_remake_preview(journey_id)
        return {
            "ok": True,
            "status": meta["status"],
            "preview_url": f"/api/journey/{journey_id}/audio/preview",
            "meta": meta,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=_friendly(e))


@router.post("/api/journey/{journey_id}/lyrics/regenerate")
def regenerate_lyrics(journey_id: str):
    meta = _meta_or_404(journey_id)
    dest = load_destination(meta.get("destination") or "suao")
    if not dest:
        raise HTTPException(status_code=400, detail="目的地無效")
    from app.content.loader import story_to_keywords
    from app.journey.service import _generate_lyrics

    story = {
        "keywords": meta.get("keywords") or [],
        "place": meta.get("place") or "",
        "companions": meta.get("companions") or "",
        "feeling": meta.get("feeling") or "",
        "memory": meta.get("memory") or "",
    }
    keywords = story_to_keywords(story, dest)
    if not keywords:
        raise HTTPException(status_code=400, detail="請先填寫歌詞關鍵字")
    lyrics = _generate_lyrics(keywords, meta.get("engine_style") or "pop")
    llm_usage = lyrics.pop("llm_usage", None)
    meta["lyrics"] = lyrics
    if lyrics.get("title") and not str(meta.get("title") or "").strip():
        meta["title"] = str(lyrics["title"]).strip()[:40]
    if llm_usage:
        try:
            from app.ops import metering as ops_metering

            ops_metering.record_llm_usage(
                meta,
                journey_id=journey_id,
                kind="lyrics_regen",
                usage=llm_usage,
                model=llm_usage.get("model"),
                save=False,
            )
        except Exception as e:
            print(f"[journey] llm metering skip: {e}")
    store.save_meta(journey_id, meta)
    return {"ok": True, "lyrics": lyrics}


@router.post("/api/journey/{journey_id}/voice-lines")
async def upload_voice_line(
    journey_id: str,
    file: UploadFile = File(...),
    section: str = Form(...),
    index: int = Form(...),
    text: str = Form(...),
):
    _meta_or_404(journey_id)
    if section not in ("verse", "chorus"):
        raise HTTPException(status_code=400, detail="section 無效")
    if not (0 <= index < 20):
        raise HTTPException(status_code=400, detail="index 超出範圍")
    content = await file.read()
    if len(content) < 2000:
        raise HTTPException(status_code=400, detail="錄音太短，請再試一次")
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="檔案太大")

    filename = f"{section}-{index:02d}.wav"
    (store.voiceprint_dir(journey_id) / filename).write_bytes(content)
    manifest = store.load_voiceprint_manifest(journey_id)
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
    store.save_voiceprint_manifest(journey_id, manifest)
    meta = store.load_meta(journey_id)
    meta["status"] = "voicing"
    store.save_meta(journey_id, meta)
    return {"ok": True, "count": len(manifest["lines"]), "lines": manifest["lines"]}


@router.get("/api/journey/{journey_id}/voice-lines")
def voice_status(journey_id: str):
    _meta_or_404(journey_id)
    manifest = store.load_voiceprint_manifest(journey_id)
    lines = sorted(
        manifest.get("lines", []),
        key=lambda l: (0 if l.get("section") == "verse" else 1, l.get("index", 0)),
    )
    return {"count": len(lines), "lines": lines}


@router.get("/api/journey/{journey_id}/voice-lines/{filename}")
def get_voice_line_file(
    journey_id: str,
    filename: str,
    x_account_token: Optional[str] = Header(None),
):
    meta = _meta_or_404(journey_id)
    if meta.get("account_id"):
        _require_journey_owner(meta, x_account_token)
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(status_code=400, detail="檔名無效")
    if not filename.endswith(".wav"):
        raise HTTPException(status_code=400, detail="檔名無效")
    path = store.voiceprint_dir(journey_id) / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="找不到這句錄音")
    return FileResponse(path, media_type="audio/wav", filename=filename)


@router.get("/api/singers")
def list_singers(gender: Optional[str] = None):
    from app.voice.singer_templates import list_templates

    if gender and gender not in ("female", "male"):
        raise HTTPException(status_code=400, detail="gender 無效")
    return {"singers": list_templates(gender)}


@router.post("/api/journey/{journey_id}/singer")
def set_singer(journey_id: str, body: SingerBody):
    from app.voice.singer_templates import get_template, is_valid_singer_id

    meta = _meta_or_404(journey_id)
    if not is_valid_singer_id(body.singer_id):
        raise HTTPException(status_code=400, detail="請選擇有效的 AI 歌手")
    tpl = get_template(body.singer_id)
    meta["ai_singer_id"] = body.singer_id
    meta["ai_singer_label"] = tpl.get("label")
    meta["status"] = "style"
    meta["finalize_progress"] = None
    store.save_meta(journey_id, meta)
    return {"ok": True, "singer": tpl, "meta": meta}


@router.post("/api/journey/{journey_id}/voiceprint/consent")
def voiceprint_consent(journey_id: str, body: ConsentBody):
    from app.util.timeutil import now_iso

    meta = _meta_or_404(journey_id)
    if not body.accepted:
        raise HTTPException(status_code=400, detail="需同意個資說明才能使用自己的聲音")
    if not meta.get("final_file"):
        raise HTTPException(status_code=400, detail="請先完成 AI 歌手版本")
    meta["voiceprint_consent"] = {
        "accepted": True,
        "at": now_iso(),
    }
    meta["status"] = "voicing"
    store.save_meta(journey_id, meta)
    return {"ok": True, "voiceprint_consent": meta["voiceprint_consent"]}


@router.post("/api/journey/{journey_id}/finalize")
def finalize_journey(
    journey_id: str,
    x_account_token: Optional[str] = Header(None),
):
    """
    非同步開工 AI 成品。立刻回 accepted，避免 Zeabur／ngrok 長連線 Bad Gateway。
    前端輪詢 GET /api/journey/{id}，直到 status=done 或 error。
    """
    meta = _meta_or_404(journey_id)
    account_id = meta.get("account_id")
    if x_account_token:
        acc = ops_accounts.get_by_token(x_account_token)
        if acc:
            account_id = acc["id"]
            meta["account_id"] = account_id
            store.save_meta(journey_id, meta)

    if meta.get("status") == "done" and meta.get("final_file"):
        payload = _finalize_result_payload(meta)
        payload["accepted"] = True
        payload["quota"] = ops_accounts.check_finalize_quota(account_id)
        return payload

    quota = ops_accounts.check_finalize_quota(account_id)
    if not quota.get("allowed"):
        raise HTTPException(
            status_code=402,
            detail="本月免費次數已用完，升級後可繼續完成歌曲",
        )

    if _finalize_key(journey_id, "ai") in _finalize_inflight:
        return {
            "ok": True,
            "accepted": True,
            "status": "finalizing",
            "finalize_progress": meta.get("finalize_progress"),
        }

    meta["status"] = "finalizing"
    meta["error"] = None
    meta["finalize_progress"] = {"pct": 0, "label": "準備製作"}
    store.save_meta(journey_id, meta)

    def _worker() -> None:
        try:
            done = service.run_finalize_ai(journey_id)
            ops_accounts.consume_finalize(
                account_id,
                journey_id=journey_id,
                meta=done,
                kind="ai_finalize",
            )
        except Exception as e:
            print(f"[journey] async finalize failed {journey_id}: {e}")
            try:
                m = store.load_meta(journey_id)
                m["status"] = "error"
                m["error"] = _friendly(e)
                store.save_meta(journey_id, m)
            except Exception as save_err:
                print(f"[journey] async finalize error save failed: {save_err}")

    _spawn_finalize(journey_id, "ai", account_id, _worker)
    return {
        "ok": True,
        "accepted": True,
        "status": "finalizing",
        "finalize_progress": meta.get("finalize_progress"),
    }


@router.post("/api/journey/{journey_id}/finalize-voice")
def finalize_voice_journey(journey_id: str):
    """同旅程加值：非同步製作聲紋版（避免長連線 502）。"""
    meta = _meta_or_404(journey_id)
    account_id = meta.get("account_id")

    if meta.get("final_voice_file") and meta.get("status") in ("done", "finalized"):
        payload = _finalize_result_payload(meta)
        payload["accepted"] = True
        return payload

    if _finalize_key(journey_id, "voice") in _finalize_inflight:
        return {
            "ok": True,
            "accepted": True,
            "status": "finalizing",
            "finalize_progress": meta.get("finalize_progress"),
        }

    meta["status"] = "finalizing"
    meta["error"] = None
    meta["finalize_progress"] = {"pct": 0, "label": "準備製作"}
    store.save_meta(journey_id, meta)

    def _worker() -> None:
        try:
            done = service.run_finalize_voice(journey_id)
            ops_accounts.consume_finalize(
                account_id,
                journey_id=journey_id,
                meta=done,
                kind="voice_finalize",
            )
        except Exception as e:
            print(f"[journey] async voice finalize failed {journey_id}: {e}")
            try:
                m = store.load_meta(journey_id)
                m["status"] = "error"
                m["error"] = _friendly(e)
                store.save_meta(journey_id, m)
            except Exception as save_err:
                print(f"[journey] async voice finalize error save failed: {save_err}")

    _spawn_finalize(journey_id, "voice", account_id, _worker)
    return {
        "ok": True,
        "accepted": True,
        "status": "finalizing",
        "finalize_progress": meta.get("finalize_progress"),
    }


@router.get("/api/journey/{journey_id}/audio/{kind}")
def get_audio(journey_id: str, kind: str):
    meta = _meta_or_404(journey_id)
    if kind == "preview":
        name = meta.get("preview_file")
    elif kind == "final":
        name = meta.get("final_file")
    elif kind in ("final-voice", "final_voice"):
        name = meta.get("final_voice_file")
    else:
        raise HTTPException(status_code=404, detail="無此音檔")
    if not name:
        raise HTTPException(status_code=404, detail="音檔尚未準備好")
    path = store.output_dir(journey_id) / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="音檔不存在")
    media = "audio/mpeg" if path.suffix.lower() == ".mp3" else "audio/wav"
    return FileResponse(path, media_type=media, filename=f"{meta.get('slug', journey_id)}-{kind}{path.suffix}")


# ---------- 公開分享 ----------

@router.get("/api/share/{slug}")
def share_payload(slug: str):
    meta = store.find_by_slug(slug)
    if not meta:
        raise HTTPException(status_code=404, detail="找不到這首歌")
    dest = load_destination(meta.get("destination") or "suao") or {}
    brand = dest.get("brand", {})
    lyrics = meta.get("lyrics") or {}
    has_voice = bool(meta.get("final_voice_file"))
    return {
        "slug": slug,
        "title": lyrics.get("title") or meta.get("title") or "我的旅行歌曲",
        "nickname": meta.get("nickname") or "旅人",
        "place": brand.get("place") or meta.get("destination"),
        "destination": meta.get("destination"),
        "feeling": meta.get("feeling"),
        "memory": meta.get("memory"),
        "companions": meta.get("companions"),
        "route_id": meta.get("route_id"),
        "created": meta.get("created"),
        "verse": lyrics.get("verse"),
        "chorus": lyrics.get("chorus"),
        "audio_url": f"/api/share/{slug}/audio",
        "audio_voice_url": f"/api/share/{slug}/audio-voice" if has_voice else None,
        "has_voice_final": has_voice,
        "ai_singer_label": meta.get("ai_singer_label"),
        "core_line": brand.get("coreLine"),
        "cta_path": "/",
    }


@router.get("/api/share/{slug}/audio")
def share_audio(slug: str):
    meta = store.find_by_slug(slug)
    if not meta:
        raise HTTPException(status_code=404, detail="找不到這首歌")
    name = meta.get("final_file") or meta.get("preview_file")
    if not name:
        raise HTTPException(status_code=404, detail="音檔尚未準備好")
    path = store.output_dir(meta["id"]) / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="音檔不存在")
    media = "audio/mpeg" if path.suffix.lower() == ".mp3" else "audio/wav"
    return FileResponse(path, media_type=media, filename=f"{slug}{path.suffix}")


@router.get("/api/share/{slug}/audio-voice")
def share_audio_voice(slug: str):
    meta = store.find_by_slug(slug)
    if not meta:
        raise HTTPException(status_code=404, detail="找不到這首歌")
    name = meta.get("final_voice_file")
    if not name:
        raise HTTPException(status_code=404, detail="聲紋版尚未準備好")
    path = store.output_dir(meta["id"]) / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="音檔不存在")
    media = "audio/mpeg" if path.suffix.lower() == ".mp3" else "audio/wav"
    return FileResponse(path, media_type=media, filename=f"{slug}-voice{path.suffix}")


# ---------- 帳號／額度（Phase 3） ----------

@router.post("/api/account/register")
def account_register(body: AccountEmailBody):
    """新帳號註冊。"""
    try:
        acc = ops_accounts.register_account(body.email, body.display_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "token": acc["token"],
        "account": ops_accounts.public_account_view(acc),
    }


@router.post("/api/account/login")
def account_login(body: AccountEmailBody):
    """以已註冊 email 登入並回傳 token。"""
    try:
        acc = ops_accounts.login_by_email(body.email)
        if body.display_name:
            acc["display_name"] = body.display_name[:40]
            ops_accounts.save_account(acc)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "token": acc["token"],
        "account": ops_accounts.public_account_view(acc),
    }


@router.get("/api/account/me")
def account_me(x_account_token: Optional[str] = Header(None)):
    if not x_account_token:
        raise HTTPException(status_code=401, detail="尚未登入")
    acc = ops_accounts.get_by_token(x_account_token)
    if not acc:
        raise HTTPException(status_code=401, detail="登入已失效")
    return {
        "account": ops_accounts.public_account_view(acc),
        "journeys": store.list_account_journeys(acc["id"]),
    }


@router.post("/api/account/pay-stub")
def account_pay_stub(
    body: PayStubBody,
    x_account_token: Optional[str] = Header(None),
):
    """開發用：標記付費。之後以金流 webhook 取代。"""
    if not x_account_token:
        raise HTTPException(status_code=401, detail="尚未登入")
    acc = ops_accounts.get_by_token(x_account_token)
    if not acc:
        raise HTTPException(status_code=401, detail="登入已失效")
    updated = ops_accounts.set_paid(acc["id"], body.paid)
    return {"account": ops_accounts.public_account_view(updated)}
