"""
簡易模板作詞（LM 連不上時的備援）。
輸出短句結構，再交給 prosody.optimize_lyrics 對齊句數。
"""
from __future__ import annotations

import random


TEMPLATES = {
    "溫暖": {
        "verse": [
            "海風輕輕吹來",
            "我們走在港邊",
            "把今天的故事",
            "放進口袋裡面",
        ],
        "prechorus": [
            "再靠近一點看",
            "心也慢慢變亮",
        ],
        "chorus": [
            "這是我們的歌",
            "唱過蘇澳的夜",
            "把溫柔都留下",
            "帶回家慢慢想",
        ],
    },
    "快樂": {
        "verse": [
            "陽光灑在路上",
            "腳步輕快向前",
            "笑聲跟著波浪",
            "一路都是晴天",
        ],
        "prechorus": [
            "再唱大聲一點",
            "把快樂都聽見",
        ],
        "chorus": [
            "這是我們的歌",
            "跳過南方澳岸",
            "把今天的精彩",
            "唱成永遠紀念",
        ],
    },
    "平靜": {
        "verse": [
            "冷泉悄悄流過",
            "山谷把風送來",
            "我們靜靜坐著",
            "把心事慢慢放",
        ],
        "prechorus": [
            "再深呼吸一次",
            "世界變得好輕",
        ],
        "chorus": [
            "這是我們的歌",
            "留在蘇澳心上",
            "把平靜都帶走",
            "回家還能回望",
        ],
    },
}


def generate_lyrics(keywords: list, emotion: str = "溫暖") -> dict:
    """回傳 {verse, chorus, prechorus} 換行短句。"""
    template = TEMPLATES.get(emotion) or TEMPLATES["溫暖"]
    kw = [k for k in (keywords or []) if str(k).strip()]
    verse = list(template["verse"])
    chorus = list(template["chorus"])
    pre = list(template["prechorus"])
    if kw:
        # 把第一個關鍵字自然嵌進主歌末句
        verse[-1] = f"想起{kw[0]}"
        if len(kw) > 1:
            chorus[-1] = f"留下{kw[1]}"
    return {
        "verse": "\n".join(verse),
        "prechorus": "\n".join(pre),
        "chorus": "\n".join(chorus),
    }
