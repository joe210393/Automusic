"""
蘇澳拾音 — 路線旋律／編曲 DNA（產品層文字規則，餵給 ACE caption）。
"""
from __future__ import annotations

from typing import Dict, Optional

# route_id → DNA brief（英文，給 production caption）
ROUTE_DNA: Dict[str, Dict[str, str]] = {
    "nanfangao": {
        "label": "南方澳漁港",
        "genre_tint": "Taiwanese fishing-harbor folk-pop, slower nostalgic coastal pace",
        "melody": (
            "folk-like lower-to-mid vocal range in verse, mostly stepwise motion within a 7th, "
            "chorus lifts gently with a short ascending hook that resolves to tonic"
        ),
        "instruments": (
            "finger-picked acoustic guitar with soft fret noise, warm fingered bass, "
            "soft live drum kit, sparse piano, natural room ambience"
        ),
        "feel": "intimate harbor storytelling, unhurried, humanized timing",
    },
    "coldspring": {
        "label": "蘇澳冷泉",
        "genre_tint": "fresh Mandarin travel pop with light major-pentatonic sparkle",
        "melody": (
            "bright major pentatonic-leaning melody, light playful interval hops, "
            "clear ascending hook into chorus, airy open vowels on long notes"
        ),
        "instruments": (
            "clean acoustic guitar, crisp light percussion and shaker, warm bass, "
            "subtle piano sparkle, refreshing but not cold digital textures"
        ),
        "feel": "cool spring clarity, youthful bounce, sparkling but natural",
    },
    "coast": {
        "label": "海岸",
        "genre_tint": "open coastal Mandarin pop, bright major travel anthem",
        "melody": (
            "pentatonic-inspired ascending hook, verse stepwise within about a 7th, "
            "chorus enters higher with a 2-4 note memorable motif repeated once, ending on tonic"
        ),
        "instruments": (
            "organic acoustic guitar strumming, warm electric bass, soft live drums, "
            "light shaker, subtle piano layers, wide but clean stereo"
        ),
        "feel": "open-sky brightness, wind-in-hair energy without EDM harshness",
    },
    "market": {
        "label": "市場",
        "genre_tint": "lively night-market Mandarin pop with playful groove",
        "melody": (
            "rhythmic catchy vocal hook, moderate syncopation still singable, "
            "short motif repeated, chorus brighter and more forward"
        ),
        "instruments": (
            "tight fingered bass, funky soft guitar chops, punchy but natural snare, "
            "light keys, lively but not cluttered arrangement"
        ),
        "feel": "warm crowd energy translated into groove, not raw street noise",
    },
    "oldstreet": {
        "label": "老街",
        "genre_tint": "nostalgic Mandarin alleyway pop with gentle folk color",
        "melody": (
            "simple memorable stepwise melody, intimate midrange verse, "
            "soft lift into chorus hook, resolve to tonic"
        ),
        "instruments": (
            "nylon or soft steel acoustic guitar, warm bass, soft brushes or light kit, "
            "sparse piano, gentle room tone"
        ),
        "feel": "walking-lane nostalgia, close and human",
    },
    "family": {
        "label": "親子",
        "genre_tint": "warm family Mandarin travel pop, bright and kind",
        "melody": (
            "easy sing-along melody with small intervals, bright major feeling, "
            "chorus hook short and repeatable for all ages"
        ),
        "instruments": (
            "bright acoustic guitar, warm bass, soft live drums, light shaker, "
            "gentle piano, clear vocal always in front"
        ),
        "feel": "sunny togetherness, clear diction, never heavy or dark",
    },
}

DEFAULT_DNA: Dict[str, str] = {
    "label": "蘇澳",
    "genre_tint": "Taiwanese indie travel pop, bright major coastal souvenir song",
    "melody": (
        "catchy pentatonic-inspired melodic hook, verse mostly stepwise within a 7th, "
        "pre-chorus rising tension, chorus enters higher with a short 2-4 note hook "
        "repeated once, long tones on key words, resolve to tonic"
    ),
    "instruments": (
        "organic acoustic guitar, warm electric bass, soft live drum kit, "
        "light shaker and tambourine, subtle piano layers"
    ),
    "feel": "souvenir-song clarity: memorable, bright, radio-ready but natural",
}


def resolve_route_dna(route_id: Optional[str]) -> Dict[str, str]:
    key = str(route_id or "").strip().lower()
    if key in ROUTE_DNA:
        return dict(ROUTE_DNA[key])
    return dict(DEFAULT_DNA)


def build_melody_brief(
    *,
    material: Optional[dict] = None,
    route_id: Optional[str] = None,
    key: Optional[str] = None,
) -> str:
    """文字層作曲邏輯（非 MIDI conditioning）：音域、級進、hook、段落輪廓。"""
    dna = resolve_route_dna(route_id)
    mat = material if isinstance(material, dict) else {}
    bits = [
        dna["melody"],
        "verse vocal range stays comfortable with mostly stepwise motion",
        "pre-chorus gradually rises with harmonic tension",
        "chorus first phrase enters a higher register with a memorable hook motif",
        "hook motif of 2 to 4 notes repeats once",
        "avoid consecutive large leaps; keep jumps rare and purposeful",
        "end phrases resolving toward the tonic",
    ]
    contour = str(mat.get("contour") or "")
    if "上行" in contour:
        bits.append("field-recording contour bias: ascending phrases")
    elif "下行" in contour:
        bits.append("field-recording contour bias: gentle descending release phrases")
    elif "平穩" in contour:
        bits.append("field-recording contour bias: stable small-motion phrases")
    root = str(mat.get("root") or key or "").strip()
    if root:
        bits.append(f"tonal center hint around {root}")
    return ", ".join(bits)
