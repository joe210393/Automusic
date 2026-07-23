"""
檔案處理工具 - 暫存檔管理
"""

import os
import tempfile
from typing import Optional


def get_temp_dir() -> str:
    """
    取得暫存目錄路徑
    
    Returns:
        暫存目錄路徑
    """
    temp_dir = "/tmp"
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir


def cleanup_temp_file(file_path: str) -> None:
    """
    清理暫存檔案
    
    Args:
        file_path: 檔案路徑
    """
    if os.path.exists(file_path):
        try:
            os.unlink(file_path)
        except Exception:
            pass  # 忽略清理錯誤
