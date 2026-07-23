"""
歌詞生成器 - 使用模板規則生成簡單、溫暖的歌詞
不使用外部 API
"""

# 模板庫
EMOTION_TEMPLATES = {
    "溫暖": {
        "verse_start": ["輕輕地", "慢慢地", "靜靜地"],
        "verse_middle": ["我們一起", "手牽著手", "心連著心"],
        "verse_end": ["唱出心中的歌", "分享這份美好", "留下美好回憶"],
        "chorus_start": ["這是", "我們", "一起"],
        "chorus_middle": ["創造的", "演奏的", "完成的"],
        "chorus_end": ["屬於我們的旋律", "最特別的音樂", "獨一無二的歌"]
    },
    "開心": {
        "verse_start": ["快樂地", "興奮地", "開心地"],
        "verse_middle": ["我們一起", "手舞足蹈", "大聲歌唱"],
        "verse_end": ["唱出歡樂的歌", "分享這份喜悅", "留下快樂回憶"],
        "chorus_start": ["這是", "我們", "一起"],
        "chorus_middle": ["創造的", "演奏的", "完成的"],
        "chorus_end": ["屬於我們的旋律", "最特別的音樂", "獨一無二的歌"]
    },
    "平靜": {
        "verse_start": ["安靜地", "溫柔地", "平靜地"],
        "verse_middle": ["我們一起", "靜靜聆聽", "感受這份"],
        "verse_end": ["唱出心中的歌", "分享這份寧靜", "留下美好回憶"],
        "chorus_start": ["這是", "我們", "一起"],
        "chorus_middle": ["創造的", "演奏的", "完成的"],
        "chorus_end": ["屬於我們的旋律", "最特別的音樂", "獨一無二的歌"]
    }
}

# 關鍵字替換規則
KEYWORD_PLACEHOLDERS = {
    "第一次": "第一次",
    "自己做的樂器": "自己做的樂器",
    "開心": "開心",
    "溫暖": "溫暖",
    "音樂": "音樂",
    "創作": "創作"
}


def generate_lyrics(keywords: list, emotion: str = "溫暖") -> dict:
    """
    根據關鍵字和情緒生成歌詞
    
    Args:
        keywords: 關鍵字列表
        emotion: 情緒（溫暖、開心、平靜等）
    
    Returns:
        dict: {"verse": "...", "chorus": "..."}
    """
    # 取得對應情緒的模板
    template = EMOTION_TEMPLATES.get(emotion, EMOTION_TEMPLATES["溫暖"])
    
    # 從關鍵字中提取可用詞彙
    keyword_text = " ".join(keywords[:3])  # 最多用前3個關鍵字
    
    # 生成 verse（主歌）
    import random
    verse_parts = [
        random.choice(template["verse_start"]),
        random.choice(template["verse_middle"]),
        keyword_text if keyword_text else "一起",
        random.choice(template["verse_end"])
    ]
    verse = "，".join(verse_parts) + "。"
    
    # 生成 chorus（副歌）
    chorus_parts = [
        random.choice(template["chorus_start"]),
        random.choice(template["chorus_middle"]),
        keyword_text if keyword_text else "音樂",
        random.choice(template["chorus_end"])
    ]
    chorus = "，".join(chorus_parts) + "。"
    
    return {
        "verse": verse,
        "chorus": chorus
    }
