"""內容後台 API：/api/admin/*

以環境變數 ADMIN_TOKEN 保護；請求頭 X-Admin-Token 或 Authorization: Bearer …。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.content import store
from app.journey import store as journey_store
from app.ops import accounts as ops_accounts

router = APIRouter(tags=["admin"])


def _expected_token() -> str:
    return (os.getenv("ADMIN_TOKEN") or "").strip()


def _require_admin(
    x_admin_token: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
) -> None:
    expected = _expected_token()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="尚未設定 ADMIN_TOKEN，請在環境變數加入後再使用後台",
        )
    got = (x_admin_token or "").strip()
    if not got and authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            got = parts[1].strip()
    if not got or got != expected:
        raise HTTPException(status_code=401, detail="後台金鑰無效")


class BrandBody(BaseModel):
    place: str = ""
    headline: str = ""
    subhead: str = ""
    cta: str = ""
    coreLine: str = ""
    demoSongUrl: Optional[str] = None


class SoundTaskBody(BaseModel):
    id: str = ""
    label: str


class RouteBody(BaseModel):
    id: str = ""
    label: str
    blurb: str = ""
    soundTasks: List[SoundTaskBody] = Field(default_factory=list)


class MoodBody(BaseModel):
    id: str
    label: str
    emoji: str = ""
    engineStyle: str = "pop"
    blurb: str = ""


class StoryPromptsBody(BaseModel):
    places: List[str] = Field(default_factory=list)
    companions: List[str] = Field(default_factory=list)
    feelings: List[str] = Field(default_factory=list)
    memoryPlaceholder: str = ""


class DestinationBody(BaseModel):
    enabled: bool = True
    brand: BrandBody = Field(default_factory=BrandBody)
    routes: List[RouteBody] = Field(default_factory=list)
    moodStyles: List[MoodBody] = Field(default_factory=list)
    storyPrompts: StoryPromptsBody = Field(default_factory=StoryPromptsBody)


class CreateDestBody(BaseModel):
    id: str
    place: str
    headline: str = ""
    enabled: bool = True


class CreateRouteBody(BaseModel):
    id: str = ""
    label: str
    blurb: str = ""
    soundTasks: List[SoundTaskBody] = Field(default_factory=list)


class BrandGenerateBody(BaseModel):
    place: str = ""
    hints: str = ""


@router.get("/api/admin/status")
def admin_status():
    return {
        "ok": True,
        "tokenConfigured": bool(_expected_token()),
        "contentDir": str(store.CONTENT_ROOT),
    }


@router.post("/api/admin/login")
def admin_login(
    x_admin_token: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
):
    _require_admin(x_admin_token, authorization)
    return {"ok": True}


@router.get("/api/admin/destinations")
def admin_list_destinations(
    x_admin_token: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
):
    _require_admin(x_admin_token, authorization)
    return {"destinations": store.list_all(), "contentDir": str(store.CONTENT_ROOT)}


@router.get("/api/admin/destinations/{dest_id}")
def admin_get_destination(
    dest_id: str,
    x_admin_token: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
):
    _require_admin(x_admin_token, authorization)
    data = store.get_destination(dest_id)
    if not data:
        raise HTTPException(status_code=404, detail="找不到目的地")
    return data


@router.post("/api/admin/destinations")
def admin_create_destination(
    body: CreateDestBody,
    x_admin_token: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
):
    _require_admin(x_admin_token, authorization)
    try:
        return store.create_destination(
            body.id.strip().lower(),
            place=body.place,
            headline=body.headline,
            enabled=body.enabled,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/api/admin/destinations/{dest_id}")
def admin_put_destination(
    dest_id: str,
    body: DestinationBody,
    x_admin_token: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
):
    _require_admin(x_admin_token, authorization)
    try:
        payload: Dict[str, Any] = body.model_dump()
        return store.save_destination(dest_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/admin/destinations/{dest_id}/brand/generate")
def admin_generate_brand(
    dest_id: str,
    body: BrandGenerateBody = BrandGenerateBody(),
    x_admin_token: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
):
    """用 LM 生成品牌首頁文案（不自動存檔，回填表單後再儲存）。"""
    _require_admin(x_admin_token, authorization)
    data = store.get_destination(dest_id)
    if not data:
        raise HTTPException(status_code=404, detail="找不到目的地")
    place = (body.place or (data.get("brand") or {}).get("place") or dest_id).strip()
    from app.ops.brand_lm import generate_brand_copy

    brand, source = generate_brand_copy(place, body.hints or "")
    return {"ok": True, "brand": brand, "source": source}


@router.post("/api/admin/destinations/{dest_id}/routes")
def admin_upsert_route(
    dest_id: str,
    body: CreateRouteBody,
    x_admin_token: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
):
    _require_admin(x_admin_token, authorization)
    rid = (body.id or "").strip().lower() or store.slugify(body.label)
    tasks = [t.model_dump() for t in body.soundTasks]
    if not tasks:
        # 預設三個空槽，後台可再改
        tasks = [
            {"id": "sound1", "label": "第一個聲音"},
            {"id": "sound2", "label": "第二個聲音"},
            {"id": "sound3", "label": "第三個聲音"},
        ]
    else:
        fixed = []
        for i, t in enumerate(tasks):
            tid = (t.get("id") or "").strip().lower() or f"sound{i + 1}"
            fixed.append({"id": tid, "label": (t.get("label") or tid).strip()})
        tasks = fixed
    route = {
        "id": rid,
        "label": body.label.strip(),
        "blurb": body.blurb.strip(),
        "soundTasks": tasks,
    }
    try:
        data = store.upsert_route(dest_id, route)
        return {"ok": True, "route": route, "destination": data}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="找不到目的地")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/admin/destinations/{dest_id}/routes/{route_id}")
def admin_delete_route(
    dest_id: str,
    route_id: str,
    x_admin_token: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
):
    _require_admin(x_admin_token, authorization)
    try:
        data = store.delete_route(dest_id, route_id)
        return {"ok": True, "destination": data}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="找不到目的地")
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到這條旅程")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/admin/activity")
def admin_activity(
    limit: int = 20,
    page: int = 1,
    q: Optional[str] = None,
    status: Optional[str] = None,
    destination: Optional[str] = None,
    has_final: Optional[str] = None,
    x_admin_token: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
):
    """遊客玩了什麼、錄了什麼、產出了什麼（搜尋／篩選／分頁）。"""
    import json as _json

    _require_admin(x_admin_token, authorization)
    accounts = ops_accounts.account_map()
    has_final_flag = None
    if has_final is not None and str(has_final).strip() != "":
        has_final_flag = str(has_final).strip().lower() in ("1", "true", "yes")
    page_n = max(1, int(page or 1))
    page_size = max(1, min(int(limit or 20), 100))
    offset = (page_n - 1) * page_size
    packed = journey_store.list_all_journeys(
        limit=page_size,
        offset=offset,
        q=q,
        status=status,
        destination=destination,
        has_final=has_final_flag,
    )
    journeys = packed["journeys"]
    for j in journeys:
        acc = accounts.get(j.get("account_id") or "")
        if acc:
            j["account"] = {
                "id": acc["id"],
                "email": acc.get("email"),
                "display_name": acc.get("display_name"),
                "paid": acc.get("paid"),
            }
        else:
            j["account"] = None

    n_sounds = n_final = n_all = 0
    for path in journey_store.JOURNEYS_ROOT.glob("*/meta.json"):
        try:
            meta = _json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        n_all += 1
        if meta.get("sounds"):
            n_sounds += 1
        if meta.get("final_file"):
            n_final += 1
    total_pages = max(1, (int(packed["total"]) + page_size - 1) // page_size)
    return {
        "journeys": journeys,
        "accounts": list(accounts.values()),
        "page": page_n,
        "page_size": page_size,
        "total": packed["total"],
        "total_pages": total_pages,
        "q": q or "",
        "status": status or "",
        "destination": destination or "",
        "has_final": has_final_flag,
        "stats": {
            "journeys": n_all,
            "accounts": len(accounts),
            "with_sounds": n_sounds,
            "with_final": n_final,
            "filtered": packed["total"],
        },
    }


@router.get("/api/admin/journeys/{journey_id}/sounds/{filename}")
def admin_journey_sound(
    journey_id: str,
    filename: str,
    x_admin_token: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
):
    _require_admin(x_admin_token, authorization)
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(status_code=400, detail="檔名無效")
    try:
        path = journey_store.sounds_dir(journey_id) / filename
    except ValueError:
        raise HTTPException(status_code=400, detail="旅程 ID 無效")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="找不到錄音")
    media = "audio/mpeg" if path.suffix.lower() == ".mp3" else "audio/wav"
    return FileResponse(path, media_type=media, filename=filename)
