"""
音符量化 - 將音符時間對齊到格點
"""


def quantize_notes(notes: list, bpm: float, grid: str = "1/8") -> list:
    """
    將音符的開始和結束時間對齊到最近的格點
    
    Args:
        notes: 音符列表 [{"start": float, "end": float, ...}]
        bpm: 節拍速度
        grid: 格點大小（"1/4", "1/8", "1/16"）
    
    Returns:
        量化後的音符列表
    """
    # 計算格點間隔（秒）
    beats_per_second = bpm / 60.0
    
    if grid == "1/4":
        grid_interval = 1.0 / 4.0 / beats_per_second
    elif grid == "1/8":
        grid_interval = 1.0 / 8.0 / beats_per_second
    elif grid == "1/16":
        grid_interval = 1.0 / 16.0 / beats_per_second
    else:
        grid_interval = 1.0 / 8.0 / beats_per_second  # 預設 1/8
    
    quantized_notes = []
    
    for note in notes:
        # 量化開始時間
        quantized_start = round(note["start"] / grid_interval) * grid_interval
        
        # 量化結束時間
        quantized_end = round(note["end"] / grid_interval) * grid_interval
        
        # 確保最小長度至少是一個格點
        if quantized_end - quantized_start < grid_interval:
            quantized_end = quantized_start + grid_interval
        
        quantized_note = note.copy()
        quantized_note["start"] = quantized_start
        quantized_note["end"] = quantized_end
        
        quantized_notes.append(quantized_note)
    
    return quantized_notes
