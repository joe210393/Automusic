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
        "你是一位資深的華語流行歌曲作詞人，擅長把旅行關鍵字寫成好唱、有畫面、讓人想跟著哼的歌詞。\n\n"
        "這是一首約三分鐘的完整歌曲，結構：主歌一 → 預副歌 → 副歌 → 主歌二 → 預副歌 → 副歌 → 橋段 → 副歌。\n"
        "寫作規則：\n"
        "- 使用繁體中文，口語自然，避免生硬書面語。\n"
        "- 主歌一（verse）4 句：鋪陳場景，像在說故事，每句 6-11 個字，長短句交錯。\n"
        "- 預副歌（prechorus）2 句：情緒往上推，每句 5-8 個字。\n"
        "- 副歌（chorus）4 句：整首歌的情緒高點，每句 5-9 個字；第一句是最好記的 hook，"
        "第四句呼應第一句；副歌四句句尾一定押同一個韻。\n"
        "- 主歌二（verse2）4 句：延續故事、換新的細節或時間點，不要重複主歌一的句子。\n"
        "- 橋段（bridge）2-4 句：情緒轉折，換一個角度（回望、對話或心裡話）。\n"
        "- 每一段句尾盡量押韻；同一段不要每句都同樣字數。\n"
        "- 關鍵字要自然融入，分散在不同段落，不要全部堆在同一句。\n"
        "- 取一個 2-6 個字的歌名，最好就是副歌 hook 裡的詞。\n"
        "- 不要解釋、不要分析、不要輸出思考過程，直接給出成品。\n\n"
        "回覆格式：只回傳一個 JSON 物件（不要用 markdown 程式碼區塊），句與句之間用 \\n 分隔：\n"
        '{"title":"歌名","verse":"一\\n二\\n三\\n四","prechorus":"一\\n二",'
        '"chorus":"一\\n二\\n三\\n四","verse2":"一\\n二\\n三\\n四","bridge":"一\\n二"}'
    )
    user_prompt = (
        f"關鍵字：{'、'.join(keywords)}\n"
        f"風格：{label}（{flavor}）\n"
        "請寫出主歌一、預副歌、副歌、主歌二、橋段，並取歌名。只回傳 JSON，不要其他文字。"
    )
    return system_prompt, user_prompt


def parse_lyrics_json(text: str) -> Optional[dict]:
    """
    從 LM 回覆中撈出 {"title","verse","chorus","prechorus?","verse2?","bridge?"} JSON。
    推理模型可能把最終答案放在思考文字的最後，所以由後往前找候選。
    """

    def _lines(val) -> str:
        if isinstance(val, list):
            return "\n".join(str(x).strip() for x in val if str(x).strip())
        return val.strip() if isinstance(val, str) else ""

    for parsed in extract_json_objects(text):
        verse = _lines(parsed.get("verse") or parsed.get("verse1") or parsed.get("verse_1"))
        chorus = _lines(parsed.get("chorus"))
        if not (verse and chorus):
            continue
        raw = {
            "title": str(parsed.get("title") or "我們的歌").strip(),
            "verse": verse,
            "chorus": chorus,
        }
        pre = _lines(parsed.get("prechorus") or parsed.get("pre_chorus"))
        verse2 = _lines(parsed.get("verse2") or parsed.get("verse_2") or parsed.get("second_verse"))
        bridge = _lines(parsed.get("bridge"))
        if pre:
            raw["prechorus"] = pre
        if verse2:
            raw["verse2"] = verse2
        if bridge:
            raw["bridge"] = bridge
        return optimize_lyrics(raw)
    return None


def parse_lyrics_from_message(message: dict) -> Optional[dict]:
    """直接吃 LM choices[0].message。"""
    return parse_lyrics_json(message_text(message))
