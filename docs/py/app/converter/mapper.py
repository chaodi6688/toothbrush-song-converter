"""4.2 频率映射规则:等比转化 / 对数压缩,保证落在 [180, 400] Hz。"""

from __future__ import annotations

import math
from typing import List, Tuple

from ..models import NoteEvent


def _non_rest_pitches(notes: List[NoteEvent]) -> List[float]:
    return [n.pitch for n in notes if not n.is_rest]


def map_frequencies(
    notes: List[NoteEvent],
    freq_min: float = 180.0,
    freq_max: float = 400.0,
) -> Tuple[List[NoteEvent], List[str]]:
    """将非休止音符映射进 [freq_min, freq_max]。

    返回 (新 NoteEvent 列表, 提示消息)。
    """
    pitches = _non_rest_pitches(notes)
    if not pitches:
        raise ValueError("未识别到有效音符,无法执行频率映射")

    f_min = min(pitches)
    f_max = max(pitches)
    messages: List[str] = []

    if f_min >= freq_min and f_max <= freq_max:
        # 已完全落窗,保持原频率
        pass
    else:
        span_ratio = f_max / f_min
        window_ratio = freq_max / freq_min
        if span_ratio <= window_ratio + 1e-9:
            # 整体等比缩放:最高音压到 freq_max
            k = freq_max / f_max
            messages.append(f"已整体变调 k=×{k:.2f}(等比缩放,最高音压至 {freq_max:.0f}Hz)")
        else:
            # 对数压缩映射
            p = math.log(freq_max / freq_min) / math.log(f_max / f_min)
            messages.append(f"已对数压缩(跨度 {span_ratio:.2f}× > 窗口 {window_ratio:.2f}×),映射至 [{freq_min:.0f}, {freq_max:.0f}]Hz")
            k = None

        mapped: List[NoteEvent] = []
        for n in notes:
            if n.is_rest:
                mapped.append(n)
            else:
                if span_ratio <= window_ratio + 1e-9:
                    f = n.pitch * k
                else:
                    f = freq_min * ((n.pitch / f_min) ** p)
                mapped.append(
                    NoteEvent(
                        pitch=f,
                        start=n.start,
                        duration=n.duration,
                        confidence=n.confidence,
                    )
                )
        notes = mapped

    # 输出频率四舍五入为整数 Hz(保留到映射后,识别阶段仍用浮点)
    rounded: List[NoteEvent] = []
    for n in notes:
        if n.is_rest:
            rounded.append(n)
        else:
            rounded.append(
                NoteEvent(
                    pitch=round(n.pitch),
                    start=n.start,
                    duration=n.duration,
                    confidence=n.confidence,
                )
            )
    return rounded, messages
