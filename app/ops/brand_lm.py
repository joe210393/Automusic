"""後台品牌文案：用 LM 自動生成，失敗時退回模板。"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Tuple

import requests

from app.lyrics.lm_json import extract_json_objects


def _lm_urls() -> List[str]:
    if os.getenv("LM_STUDIO_URL"):
        return [os.getenv("LM_STUDIO_URL")]
    if os.getenv("LM_STUDIO_URLS"):
        return [u.strip() for u in os.getenv("LM_STUDIO_URLS").split(",") if u.strip()]
    return [
        "http://127.0.0.1:1234/v1/chat/completions",
        "http://192.168.1.198:1234/v1/chat/completions",
    ]


def _model() -> str:
    return os.getenv("LM_STUDIO_MODEL", "google/gemma-4-31b-qat")


def _fallback_brand(place: str) -> Dict[str, str]:
    place = (place or "這裡").strip() or "這裡"
    return {
        "place": place,
        "headline": f"把今天的{place}，變成一首屬於你的歌",
        "subhead": f"在{place}收集聲音與故事，帶走一段旅行旋律。",
        "cta": "開始旅程",
        "coreLine": "不是來做一首歌，是把這趟旅行帶回家",
    }


def _parse_brand(message: dict, place: str) -> Dict[str, str] | None:
    blobs = []
    for key in ("content", "reasoning_content"):
        text = (message or {}).get(key) or ""
        if text:
            blobs.append(text)
    joined = "\n".join(blobs)
    for obj in extract_json_objects(joined):
        if not isinstance(obj, dict):
            continue
        headline = str(obj.get("headline") or "").strip()
        if not headline:
            continue
        return {
            "place": str(obj.get("place") or place or "").strip()[:40],
            "headline": headline[:80],
            "subhead": str(obj.get("subhead") or "").strip()[:200],
            "cta": str(obj.get("cta") or "開始旅程").strip()[:24] or "開始旅程",
            "coreLine": str(obj.get("coreLine") or obj.get("core_line") or "").strip()[:80],
        }
    # 寬鬆：找 headline 行
    m = re.search(r'"headline"\s*:\s*"([^"]+)"', joined)
    if m:
        fb = _fallback_brand(place)
        fb["headline"] = m.group(1)[:80]
        return fb
    return None


def generate_brand_copy(place: str, hints: str = "") -> Tuple[Dict[str, str], str]:
    """
    回傳 (brand_fields, source)。
    source: lm_studio | template
    """
    place = (place or "").strip() or "蘇澳"
    hints = (hints or "").strip()[:200]
    system = (
        "你是台灣旅遊體驗品牌文案寫手。輸出繁體中文，語氣溫暖、具體、有地方感，"
        "避免空洞行銷腔與誇飾。只輸出一個 JSON 物件，不要 markdown。"
    )
    user = (
        f"為旅遊音樂體驗「聲之旅」撰寫目的地品牌首頁文案。\n"
        f"地名：{place}\n"
        f"{'補充：' + hints if hints else ''}\n"
        "請輸出 JSON：\n"
        "{\n"
        '  "place": "地名",\n'
        '  "headline": "主標（一句，有畫面）",\n'
        '  "subhead": "副標（兩句內）",\n'
        '  "cta": "按鈕文字",\n'
        '  "coreLine": "核心句（簡短有記憶點）"\n'
        "}"
    )
    errors: List[str] = []
    for url in _lm_urls():
        try:
            resp = requests.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "ngrok-skip-browser-warning": "1",
                },
                json={
                    "model": _model(),
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.8,
                    "max_tokens": 1024,
                },
                timeout=(4, 120),
            )
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {(resp.text or '')[:160]}")
            message = resp.json()["choices"][0]["message"]
            parsed = _parse_brand(message, place)
            if not parsed:
                raise RuntimeError("無法解析品牌文案 JSON")
            if not parsed.get("place"):
                parsed["place"] = place
            return parsed, "lm_studio"
        except Exception as e:
            errors.append(f"{url} → {e}")
            print(f"[brand-lm] 失敗：{e}")
            continue
    brand = _fallback_brand(place)
    detail = "；".join(errors[-2:]) if errors else "無可用 LM"
    print(f"[brand-lm] 改用模板：{detail}")
    return brand, "template"
