"""
AI 作詞：把關鍵字＋風格組成給本地 LM（gemma）的提示詞，並解析回傳的歌詞 JSON。
連不上 LM 時由 main.py 退回模板式歌詞（app/lyrics/generator.py）。
"""
from typing import Optional

from app.lyrics.lm_json import extract_json_objects, message_text

# 各風格的作詞語氣指引（與 theory_db.json 的 styles 對應）
STYLE_FLAVORS = {
    "pop": "明亮直接、有記憶點，副歌要朗朗上口、適合重複哼唱",
    "ballad": "抒情細膩、慢板，帶一點想念或感動的情緒，用字溫柔",
    "folk": "樸實溫暖、有生活感，像在說一個小故事，用字簡單",
    "rock": "有力量、有吶喊感，句子短促有衝勁，帶一點不服輸",
    "jazz": "慵懶搖擺、都會感，帶一點俏皮與慵懶的浪漫",
    "lullaby": "溫柔安眠，多用疊字與柔軟的意象（星星、月亮、懷抱），節奏緩慢",
}

STYLE_LABELS = {
    "pop": "流行 Pop",
    "ballad": "抒情 Ballad",
    "folk": "民謠",
    "rock": "搖滾 Rock",
    "jazz": "爵士 Jazz",
    "lullaby": "搖籃曲",
}


def build_lyrics_prompts(keywords: list, style: Optional[str]) -> tuple:
    """回傳 (system_prompt, user_prompt)。"""
    flavor = STYLE_FLAVORS.get(style or "", "溫暖、正面、有畫面感")
    label = STYLE_LABELS.get(style or "", "自由發揮")

    system_prompt = (
        "你是一位資深的中文流行歌曲作詞人，擅長把日常關鍵字寫成有畫面感、適合唱出來的歌詞。\n\n"
        "寫作規則：\n"
        "- 使用繁體中文，口語自然，避免生硬的書面語。\n"
        "- 主歌（verse）4 句：鋪陳場景與心情，每句 6-10 個字。\n"
        "- 副歌（chorus）4 句：情緒的高點，每句 5-9 個字，第一句和最後一句可以呼應，好記好唱。\n"
        "- 關鍵字要自然融入歌詞，不要硬塞、不要全部堆在同一句。\n"
        "- 取一個 2-6 個字的歌名。\n"
        "- 不要解釋、不要分析、不要輸出思考過程，直接給出成品。\n\n"
        "回覆格式：只回傳一個 JSON 物件（不要用 markdown 程式碼區塊），句與句之間用 \\n 分隔：\n"
        '{"title":"歌名","verse":"第一句\\n第二句\\n第三句\\n第四句","chorus":"第一句\\n第二句\\n第三句\\n第四句"}'
    )
    user_prompt = (
        f"關鍵字：{'、'.join(keywords)}\n"
        f"風格：{label}（{flavor}）\n"
        "請寫出主歌 4 句＋副歌 4 句，並取歌名。只回傳 JSON 一行或一個物件，不要任何其他文字。"
    )
    return system_prompt, user_prompt


def parse_lyrics_json(text: str) -> Optional[dict]:
    """
    從 LM 回覆中撈出 {"title","verse","chorus"} JSON。
    推理模型可能把最終答案放在思考文字的最後，所以由後往前找候選。
    """
    for parsed in extract_json_objects(text):
        verse = parsed.get("verse")
        chorus = parsed.get("chorus")
        # 少數模型用陣列回傳句子
        if isinstance(verse, list):
            verse = "\n".join(str(x).strip() for x in verse if str(x).strip())
        if isinstance(chorus, list):
            chorus = "\n".join(str(x).strip() for x in chorus if str(x).strip())
        if isinstance(verse, str) and isinstance(chorus, str) and verse.strip() and chorus.strip():
            return {
                "title": str(parsed.get("title") or "我們的歌").strip(),
                "verse": verse.strip(),
                "chorus": chorus.strip(),
            }
    return None


def parse_lyrics_from_message(message: dict) -> Optional[dict]:
    """直接吃 LM choices[0].message。"""
    return parse_lyrics_json(message_text(message))
