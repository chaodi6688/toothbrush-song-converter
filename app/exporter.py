"""F3 导出文本文件:纯数据模式 / 可选元数据头。"""

from __future__ import annotations

import time
from typing import List, Optional

from .models import ConvertResult


def build_txt(result: ConvertResult, include_header: bool = False) -> str:
    """生成 UTF-8 文本内容。"""
    lines: List[str] = []
    if include_header:
        song = result.song_name or "未命名"
        lines.append(f"# 歌名: {song}")
        lines.append(f"# 总时长: {result.total_duration:.3f} s")
        lines.append(f"# 频率范围: {result.min_freq}-{result.max_freq} Hz")
        lines.append(f"# BPM: {result.bpm:.1f}")
        lines.append(f"# 元组数量: {len(result.sequence)}")
        if result.messages:
            lines.append("# 转换提示: " + "; ".join(result.messages))
        lines.append("#")

    for e in result.sequence:
        lines.append(f"({e.freq}, {e.duration:.3f})")
    return "\n".join(lines) + "\n"


def filename_for(song_name: Optional[str]) -> str:
    """文件名规则:{歌曲名}_牙刷版.txt,无歌曲名用时间戳兜底。"""
    if song_name and song_name.strip():
        safe = "".join(c for c in song_name.strip() if c not in '\\/:*?"<>|')
        return f"{safe}_牙刷版.txt"
    return f"歌曲_{int(time.time())}_牙刷版.txt"
