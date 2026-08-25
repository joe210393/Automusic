"""
AI 作詞：把關鍵字＋風格組成給本地 LM（gemma）的提示詞，並解析回傳的歌詞 JSON。
連不上 LM 時由 main.py 退回模板式歌詞（app/lyrics/generator.py）。
"""
from typing import Optional

from app.lyrics.lm_json import extract_json_objects, message_text
from app.lyrics.prosody import optimize_lyrics

# 各風格的作詞語氣指引（與 theory_db.json 的 styles 對應；未列者走預設）
STYLE_FLAVORS = {
    "pop": "明亮直接、有記憶點，副歌要朗朗上口、適合重複哼唱",
    "ballad": "抒情細膩、慢板，帶一點想念或感動的情緒，用字溫柔",
    "folk": "樸實溫暖、有生活感，像在說一個小故事，用字簡單",
    "rock": "有力量、有吶喊感，句子短促有衝勁，帶一點不服輸",
    "jazz": "慵懶搖擺、都會感，帶一點俏皮與慵懶的浪漫",
    "lullaby": "溫柔安眠，多用疊字與柔軟的意象（星星、月亮、懷抱），節奏緩慢",
    "reggae": "慵懶陽光、島嶼節奏感，用字輕鬆有一點俏皮",
    "symphony": "莊重優美、有畫面感，像電影或音樂會的開場",
    "edm": "節奏鮮明、夜色霓虹，句子短、有反覆 hook",
    "hiphop": "口語節奏感、自信直接，可有一點街頭感但不粗俗",
    "rnb": "甜蜜柔軟、情感細膩，像深夜電台情歌",
    "country": "公路與家鄉感，樸實敘事、溫暖收尾",
    "latin": "熱情節奏、陽光色彩，句子有擺動感",
    "blues": "帶一點滄桑與釋放，情緒真實、用字簡單有力",
    "funk": "俏皮律動、身體感，短句、重節奏感",
    "disco": "華麗舞池、歡快閃亮，副歌要好喊",
    "ambient": "空靈緩慢、意象多於敘事，像風景與呼吸",
    "cinematic": "史詩或柔情的電影感，畫面強烈",
    "bossa": "慵懶咖啡廳、輕柔搖擺，浪漫但不造作",
    "gospel": "希望與力量、溫暖群唱感，正向收束",
}

STYLE_LABELS = {
    "pop": "流行 Pop",
    "ballad": "抒情 Ballad",
    "folk": "民謠",
    "rock": "搖滾 Rock",
    "jazz": "爵士 Jazz",
    "lullaby": "搖籃曲",
    "reggae": "雷鬼 Reggae",
    "symphony": "交響樂／古典",
    "edm": "電子 EDM",
    "hiphop": "嘻哈 Hip-Hop",
    "rnb": "R&B／靈魂",
    "country": "鄉村 Country",
    "latin": "拉丁 Latin",
    "blues": "藍調 Blues",
    "funk": "放克 Funk",
    "disco": "迪斯可 Disco",
    "ambient": "氛圍 Ambient",
    "cinematic": "電影配樂",
    "bossa": "巴薩諾瓦 Bossa",
    "gospel": "福音 Gospel",
}


def build_lyrics_prompts(keywords: list, style: Optional[str]) -> tuple:
    """回傳 (system_prompt, user_prompt)。"""
    flavor = STYLE_FLAVORS.get(style or "", "溫暖、正面、有畫面感")
    label = STYLE_LABELS.get(style or "", "自由發揮")

    system_prompt = (
        "你是一位資深的中文流行歌曲作詞人，擅長把日常關鍵字寫成好唱、有畫面的短句歌詞。\n\n"
        "寫作規則：\n"
        "- 使用繁體中文，口語自然，避免生硬書面語與超長句子。\n"
        "- 主歌（verse）剛好 4 句：鋪陳場景與心情，每句 5-9 個字。\n"
        "- 預副歌（prechorus）剛好 2 句：情緒往上推、製造張力，每句 5-8 個字。\n"
        "- 副歌（chorus）剛好 4 句：情緒高點與 hook，每句 5-8 個字；第一句要好記，可與最後一句呼應。\n"
        "- 禁止把很多資訊塞進同一句；寧可短句也不要長句。\n"
        "- 關鍵字要自然融入，不要硬塞、不要全部堆在同一句。\n"
        "- 取一個 2-6 個字的歌名。\n"
        "- 不要解釋、不要分析、不要輸出思考過程，直接給出成品。\n\n"
        "回覆格式：只回傳一個 JSON 物件（不要用 markdown 程式碼區塊），句與句之間用 \\n 分隔：\n"
        '{"title":"歌名","verse":"一\\n二\\n三\\n四","prechorus":"一\\n二","chorus":"一\\n二\\n三\\n四"}'
    )
    user_prompt = (
        f"關鍵字：{'、'.join(keywords)}\n"
        f"風格：{label}（{flavor}）\n"
        "請寫出主歌 4 句＋預副歌 2 句＋副歌 4 句，並取歌名。只回傳 JSON，不要其他文字。"
    )
    return system_prompt, user_prompt


def parse_lyrics_json(text: str) -> Optional[dict]:
    """
    從 LM 回覆中撈出 {"title","verse","chorus","prechorus?"} JSON。
    推理模型可能把最終答案放在思考文字的最後，所以由後往前找候選。
    """
    for parsed in extract_json_objects(text):
        verse = parsed.get("verse")
        chorus = parsed.get("chorus")
        pre = parsed.get("prechorus") or parsed.get("pre_chorus") or parsed.get("bridge")
        if isinstance(verse, list):
            verse = "\n".join(str(x).strip() for x in verse if str(x).strip())
        if isinstance(chorus, list):
            chorus = "\n".join(str(x).strip() for x in chorus if str(x).strip())
        if isinstance(pre, list):
            pre = "\n".join(str(x).strip() for x in pre if str(x).strip())
        if isinstance(verse, str) and isinstance(chorus, str) and verse.strip() and chorus.strip():
            raw = {
                "title": str(parsed.get("title") or "我們的歌").strip(),
                "verse": verse.strip(),
                "chorus": chorus.strip(),
            }
            if isinstance(pre, str) and pre.strip():
                raw["prechorus"] = pre.strip()
            return optimize_lyrics(raw)
    return None


def parse_lyrics_from_message(message: dict) -> Optional[dict]:
    """直接吃 LM choices[0].message。"""
    return parse_lyrics_json(message_text(message))
