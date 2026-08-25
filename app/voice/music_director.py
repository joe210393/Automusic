"""
音樂導演層（Sprint 1）：把旅程 meta 編成 ACE 用的 production caption。

目標不是「標籤堆疊」，而是告訴模型：這首歌要怎麼編、怎麼演奏、怎麼混。
"""
from __future__ import annotations

from typing import Dict, List, Optional

# 歌手 → 人聲企劃（英文，給 ACE caption）
VOCAL_BRIEFS: Dict[str, str] = {
    "female_bright": (
        "warm bright natural female lead vocal, clear Mandarin diction, "
        "intimate yet youthful tone, present in the front of the mix"
    ),
    "female_warm": (
        "warm natural female lead vocal, soft but clearly audible Mandarin lyrics, "
        "intimate midrange tone, emotional but not oversung"
    ),
    "female_soft": (
        "gentle intimate soft female lead vocal, breathy but intelligible Mandarin, "
        "close-mic feel, never buried by the band"
    ),
    "male_deep": (
        "deep rich male lead vocal, clear Mandarin lyrics, grounded mid-low tone, "
        "steady delivery with natural presence"
    ),
    "male_warm": (
        "warm mid-range male lead vocal, heartfelt Mandarin phrasing, "
        "clear diction, front-and-center but not shouted"
    ),
    "male_clear": (
        "clear strong male lead vocal, uplifting Mandarin pop delivery, "
        "memorable melodic presence, full-band compatible"
    ),
}

# engine_style / style_id → 編曲企劃
STYLE_BRIEFS: Dict[str, Dict[str, str]] = {
    "pop": {
        "genre": "Taiwanese indie travel pop, sunny coastal Mandarin pop",
        "instruments": (
            "organic acoustic guitar strumming, realistic finger-picked acoustic guitar, "
            "warm electric bass, soft live drum kit, natural kick and snare, "
            "light shaker and tambourine, subtle piano layers"
        ),
        "arc": (
            "verse starts intimate and sparse, gradually adding bass and percussion; "
            "chorus opens wider with stronger drums, brighter harmony and memorable melodic lift"
        ),
        "melody": (
            "catchy pentatonic-inspired melodic hook, simple memorable vocal melody, "
            "smooth melodic contour with small interval jumps"
        ),
    },
    "ambient": {
        "genre": "seaside chill Mandarin ambient-pop, soft travel mood",
        "instruments": (
            "soft pads with gentle movement, sparse finger-picked acoustic guitar, "
            "warm sub-bass, soft brushed drums, light ocean-air texture without raw noise bed"
        ),
        "arc": (
            "verse stays sparse and floating; chorus gently widens with soft drums "
            "and clearer vocal lift, never harsh"
        ),
        "melody": (
            "slow floating vocal melody, small stepwise motion, "
            "long notes on key words, calm contour"
        ),
    },
    "ballad": {
        "genre": "warm Mandarin travel ballad, nostalgic coastal memory",
        "instruments": (
            "soft piano with natural dynamics, sparse voicing, warm electric bass, "
            "gentle acoustic guitar, soft live brushes or light kit, subtle strings"
        ),
        "arc": (
            "verse intimate and sparse; pre-chorus builds tension softly; "
            "chorus opens with fuller harmony while vocals stay clear"
        ),
        "melody": (
            "singable ballad melody, mostly stepwise, emotional lift into chorus, "
            "resolve back toward tonic"
        ),
    },
    "latin": {
        "genre": "lively harbor Mandarin pop with light Latin groove",
        "instruments": (
            "bright acoustic guitar, warm electric bass with finger attack, "
            "live drum kit with lively snare, light percussion shakers, "
            "occasional soft brass color"
        ),
        "arc": (
            "verse rhythmic and playful; chorus more open and danceable "
            "with stronger drums and hook repetition"
        ),
        "melody": (
            "catchy rhythmic vocal hook, moderate interval jumps, "
            "memorable short motif repeated"
        ),
    },
    "cinematic": {
        "genre": "cinematic Mandarin travel song, film-trailer warmth",
        "instruments": (
            "piano foundation, soft strings, warm bass, restrained drums, "
            "subtle guitar layers, wide but clean stereo image"
        ),
        "arc": (
            "verse restrained and story-like; chorus swells with strings "
            "and stronger drums for emotional release"
        ),
        "melody": (
            "expressive ascending melodic contour into chorus, "
            "clear hook motif, memorable long tones"
        ),
    },
    "rnb": {
        "genre": "romantic Mandarin R&B-tinged travel pop",
        "instruments": (
            "warm electric piano, smooth electric bass, soft kit with ghost notes, "
            "subtle guitar chords, gentle pad"
        ),
        "arc": (
            "verse intimate and close; chorus smoother and wider with "
            "stronger groove while vocals stay sensual and clear"
        ),
        "melody": (
            "smooth vocal melody with tasteful small bends, "
            "hook sits comfortably in midrange"
        ),
    },
    "folk": {
        "genre": "Taiwanese fishing-village folk travel song, Mandarin acoustic",
        "instruments": (
            "finger-picked acoustic guitar with soft fret noise and natural string resonance, "
            "warm upright or fingered electric bass, soft live drum kit or light percussion, "
            "sparse piano or harmonica color"
        ),
        "arc": (
            "verse intimate and sparse like a harbor storyteller; "
            "chorus slightly wider with soft drums and stronger melodic lift"
        ),
        "melody": (
            "folk-like pentatonic-leaning melody, mostly stepwise, "
            "singable and memorable, lower-to-mid vocal range in verse"
        ),
    },
    "jazz": {
        "genre": "harbor-night Mandarin jazz-pop, relaxed evening mood",
        "instruments": (
            "warm electric piano or soft acoustic piano, walking or lyrical bass, "
            "brushed drums, subtle guitar chords, soft saxophone color optional"
        ),
        "arc": (
            "verse cool and intimate; chorus gently opens without becoming loud EDM"
        ),
        "melody": (
            "relaxed melodic contour, tasteful chromatic color sparingly, "
            "clear singable hook"
        ),
    },
    "bossa": {
        "genre": "island bossa-inspired Mandarin travel pop",
        "instruments": (
            "nylon-string or soft acoustic guitar, warm bass, light soft drums, "
            "shaker, gentle piano"
        ),
        "arc": (
            "verse swaying and light; chorus brighter with clearer hook and soft percussion lift"
        ),
        "melody": (
            "gentle swaying vocal melody, small intervals, sunny major feeling"
        ),
    },
    "country": {
        "genre": "coastal road-trip Mandarin country-pop",
        "instruments": (
            "strummed acoustic guitar, warm electric bass, live drums, "
            "light electric guitar fills, soft piano"
        ),
        "arc": (
            "verse story-driven and mid-tempo; chorus opens with stronger strumming and hook"
        ),
        "melody": (
            "straightforward catchy melody, ascending hook into chorus, "
            "memorable short motif"
        ),
    },
    "funk": {
        "genre": "night-market Mandarin funk-pop groove",
        "instruments": (
            "tight electric bass with finger attack, funky guitar chops, "
            "live drum kit with punchy snare, light keys"
        ),
        "arc": (
            "verse groovy and playful; chorus bigger with stronger drums and hook repeats"
        ),
        "melody": (
            "rhythmic vocal hook, syncopated but singable, short catchy motif"
        ),
    },
}

DEFAULT_STYLE = STYLE_BRIEFS["pop"]

HUMANIZATION = (
    "natural human timing, slight performance imperfections, "
    "realistic instrument dynamics, soft fret noise where guitar is present, "
    "natural kick and snare, soft ghost notes on drums"
)

MIX = (
    "clean modern production, warm stereo image, clear vocal in front, "
    "controlled low end, radio-ready but not over-compressed"
)

NEGATIVES = (
    "not instrumental-only, not a karaoke backing track without singer, "
    "no synthetic EDM textures, no harsh digital supersaw walls, "
    "melody and groove inspired by on-site travel field recordings "
    "(not a raw nature bed under the mix)"
)


def _style_key(engine_style: Optional[str], material: Optional[dict]) -> str:
    mat = material if isinstance(material, dict) else {}
    raw = (mat.get("style_id") or engine_style or "pop")
    key = str(raw).strip().lower()
    if key in ("auto", ""):
        return "pop"
    return key if key in STYLE_BRIEFS else "pop"


def _bpm_phrase(bpm: Optional[float], material: Optional[dict]) -> str:
    mat = material if isinstance(material, dict) else {}
    val = bpm if bpm else mat.get("bpm")
    try:
        n = int(round(float(val)))
    except (TypeError, ValueError):
        return ""
    if n < 30 or n > 300:
        return ""
    return f"{n} BPM"


def _format_key_scale(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if " " in text:
        return text
    if text.endswith("m") and not text.lower().endswith("major"):
        return f"{text[:-1]} Minor"
    return f"{text} Major"


def _key_phrase(key: Optional[str], material: Optional[dict]) -> str:
    mat = material if isinstance(material, dict) else {}
    raw = (key or "").strip() or str(mat.get("root") or "").strip()
    if not raw:
        return ""
    formatted = _format_key_scale(raw)
    mood = str(mat.get("mood") or "")
    if "小調" in mood and "Minor" not in formatted and " " not in raw:
        root = raw[:-1] if raw.endswith("m") else raw
        return f"{root} Minor"
    return formatted


def _melody_from_material(base_melody: str, material: Optional[dict]) -> str:
    mat = material if isinstance(material, dict) else {}
    bits: List[str] = [base_melody]
    contour = str(mat.get("contour") or "")
    if "上行" in contour:
        bits.append("overall ascending melodic tendency from the field recording")
    elif "下行" in contour:
        bits.append("gentle descending phrases inspired by the field recording")
    elif "平穩" in contour:
        bits.append("mostly stable melodic contour with small motion")
    energy = mat.get("energy")
    try:
        e = float(energy)
        if e >= 4.0:
            bits.append("slightly denser rhythmic vocal phrasing")
        elif e <= 1.5:
            bits.append("more spacious phrasing with longer held notes")
    except (TypeError, ValueError):
        pass
    if mat.get("progression"):
        bits.append(f"harmonic color hint: {mat['progression']}")
    return ", ".join(bits)


def build_production_caption(
    *,
    singer_id: Optional[str] = None,
    engine_style: Optional[str] = None,
    title: Optional[str] = None,
    material: Optional[dict] = None,
    bpm: Optional[float] = None,
    key: Optional[str] = None,
) -> str:
    """編成完整 production caption（英文，逗號／短句結構，適合 ACE prompt/caption）。"""
    style = STYLE_BRIEFS.get(_style_key(engine_style, material), DEFAULT_STYLE)
    vocal = VOCAL_BRIEFS.get(
        singer_id or "",
        "natural Mandarin lead vocal singing Chinese lyrics clearly, full-band travel song",
    )

    parts: List[str] = [
        style["genre"],
        vocal,
    ]

    tempo_bits = []
    bpm_p = _bpm_phrase(bpm, material)
    key_p = _key_phrase(key, material)
    if bpm_p:
        tempo_bits.append(bpm_p)
    if key_p:
        tempo_bits.append(key_p)
    if tempo_bits:
        parts.append(", ".join(tempo_bits))

    parts.append(_melody_from_material(style["melody"], material))
    parts.append(style["instruments"])
    parts.append(style["arc"])
    parts.append(HUMANIZATION)
    parts.append(MIX)
    parts.append(NEGATIVES)

    if title:
        parts.append(f"song about: {title}")

    # 去重空白，保持一行可餵 API
    text = ", ".join(p.strip().rstrip(",") for p in parts if p and str(p).strip())
    return text


_LEGACY_SINGER: Dict[str, str] = {
    "female_bright": (
        "A bright Mandarin pop travel song. The lead female vocal is clear, present, and sings "
        "Chinese lyrics throughout the verse and chorus; sunny coastal vibe with full-band backing."
    ),
    "female_warm": (
        "A warm Mandarin pop ballad. The lead female vocal is soft but clearly audible, singing "
        "Chinese lyrics in verse and chorus; nostalgic travel mood with full-band accompaniment."
    ),
    "female_soft": (
        "A gentle Mandarin acoustic pop song. Intimate soft female lead vocals sing Chinese lyrics "
        "prominently; light seaside arrangement that never covers the singer."
    ),
    "male_deep": (
        "A grounded Mandarin pop anthem. Deep rich male lead vocals sing Chinese lyrics clearly "
        "through verse and chorus; full-band travel mood."
    ),
    "male_warm": (
        "A heartfelt Mandarin pop song. Warm mid-range male lead vocals are front and center, "
        "singing Chinese lyrics in verse and chorus; coastal journey arrangement."
    ),
    "male_clear": (
        "An uplifting Mandarin pop souvenir song. Clear strong male lead vocals sing Chinese lyrics "
        "prominently; memorable travel arrangement with full band."
    ),
}


def build_legacy_prompt(
    *,
    singer_id: Optional[str],
    engine_style: Optional[str],
    title: Optional[str] = None,
    material: Optional[dict] = None,
) -> str:
    """Sprint 1 A/B 用：舊短 prompt（與改版前行為對齊）。"""
    base = _LEGACY_SINGER.get(
        singer_id or "",
        "Mandarin pop song with natural lead vocals singing Chinese lyrics, travel memory, full arrangement",
    )
    bits = [
        base,
        "lead vocal must be clearly audible and sing the given Mandarin lyrics",
        "not instrumental-only",
        "not a karaoke backing track without singer",
        "melody and groove inspired by on-site travel field recordings (not a raw nature bed under the mix)",
    ]
    if engine_style:
        bits.append(str(engine_style))
    if title:
        bits.append(f"song about: {title}")
    mat = material if isinstance(material, dict) else {}
    if mat.get("mood"):
        bits.append(f"overall color from recording: {mat['mood']}")
    if mat.get("contour"):
        bits.append(f"melodic contour from recording: {mat['contour']}")
    if mat.get("root"):
        bits.append(f"tonal center hinted by recording root {mat['root']}")
    if mat.get("energy") is not None:
        bits.append(f"activity from recording density about {mat['energy']} events/sec")
    return ", ".join(bits)
