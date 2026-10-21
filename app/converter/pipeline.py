"""转换流水线:中间数据模型 → PlaySequence。"""

from __future__ import annotations

import math
from typing import List, Optional

from ..models import NoteEvent, ConvertParams, ConvertResult, PlayEvent
from .mapper import map_frequencies
from .quantizer import quantize, merge_rests_into_previous, trim_leading_trailing_rests
from .loop import total_duration, build_play_sequence


def convert(
    notes: List[NoteEvent],
    params: ConvertParams,
    song_name: Optional[str] = None,
) -> ConvertResult:
    """完整转换流程:
    频率映射 → 时长量化 → 休止处理 → 2 分钟控制 → 循环衔接。
    """
    if not notes:
        raise ValueError("输入为空,未识别到任何音符")

    bpm = params.bpm_override or 120.0
    messages: List[str] = []

    # 1) 频率映射(可跳过:保持原音调,可能超出电机窗口)
    if params.preserve_pitch:
        messages.append("已保持原音调:未执行频率映射(输出频率可能超出电机窗口)")
        rounded = [
            NoteEvent(round(n.pitch), n.start, n.duration, n.confidence)
            if not n.is_rest
            else n
            for n in notes
        ]
        notes = rounded
    else:
        notes, freq_msgs = map_frequencies(notes, params.freq_min, params.freq_max)
        messages.extend(freq_msgs)

    # 2) 时长量化
    notes = quantize(notes, params, bpm)
    if not notes:
        raise ValueError("量化后序列为空")

    # 2.5) 去除休止:休止时长并入前一音符
    if params.merge_rests:
        notes = merge_rests_into_previous(notes)
        messages.append("已去除休止符:休止时长并入前一音符")

    # 3) 两分钟控制 + 循环衔接
    seq_notes, mode = build_play_sequence(notes, params, messages)
    sequence = seq_notes

    total = sum(e.duration for e in sequence)
    non_rest = [e.freq for e in sequence if e.freq > 0]
    min_f = min(non_rest) if non_rest else 0
    max_f = max(non_rest) if non_rest else 0

    # 循环衔接检查:首尾音高是否衔接
    loop_point = None
    if params.loop_enabled and sequence:
        first = sequence[0].freq
        last = sequence[-1].freq
        if first != last:
            loop_point = "循环衔接点:序列末尾(首尾音高不衔接,已按乐句循环)"

    non_rest_pitches = [n.pitch for n in notes if not n.is_rest]
    result = ConvertResult(
        sequence=sequence,
        song_name=song_name,
        total_duration=total,
        duration_ok=abs(total - params.target_duration) <= 0.5,
        freq_range_ok=bool(non_rest) and all(params.freq_min <= f <= params.freq_max for f in non_rest),
        min_freq=min_f,
        max_freq=max_f,
        notes=notes,
        bpm=bpm,
        pitch_min_hz=min(non_rest_pitches) if non_rest_pitches else 0.0,
        pitch_max_hz=max(non_rest_pitches) if non_rest_pitches else 0.0,
        messages=messages,
        loop_point=loop_point,
        metadata={
            "mode": mode,
            "freq_window": [params.freq_min, params.freq_max],
            "quant_grid": f"1/{params.quant_grid_denom * 4}",
            "loop_enabled": params.loop_enabled,
            "merge_rests": params.merge_rests,
        },
    )
    return result
