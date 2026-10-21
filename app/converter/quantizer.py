"""4.3/4.4 时长量化与休止处理。"""

from __future__ import annotations

from typing import List

from ..models import NoteEvent, ConvertParams


def _grid_seconds(bpm: float, denom: int) -> float:
    """1/denom 音符的秒数:四分音符 = 60/BPM。"""
    return 60.0 / bpm / denom


def quantize(notes: List[NoteEvent], params: ConvertParams, bpm: float) -> List[NoteEvent]:
    """将时长对齐到量化网格,并合并低于电机响应下限的事件。"""
    if not notes:
        return []

    grid = _grid_seconds(bpm, params.quant_grid_denom)
    min_dur = params.min_event_dur

    out: List[NoteEvent] = []
    for n in notes:
        if grid <= 0:
            q = n.duration
        else:
            ticks = max(1, round(n.duration / grid))
            q = ticks * grid
        out.append(NoteEvent(n.pitch, n.start, q, n.confidence))

    # 低于最小事件时长:并入相邻事件(优先前一个,首事件并入后一个)
    merged: List[NoteEvent] = []
    for i, n in enumerate(out):
        if n.duration < min_dur and len(out) > 1:
            if merged:
                merged[-1] = NoteEvent(
                    merged[-1].pitch,
                    merged[-1].start,
                    merged[-1].duration + n.duration,
                    merged[-1].confidence,
                )
            else:
                # 首事件太短且没有前驱:并入后一个(由下一轮循环处理)
                if i + 1 < len(out):
                    out[i + 1] = NoteEvent(
                        out[i + 1].pitch,
                        out[i + 1].start - n.duration,
                        out[i + 1].duration + n.duration,
                        out[i + 1].confidence,
                    )
                else:
                    merged.append(NoteEvent(n.pitch, n.start, min_dur, n.confidence))
                continue
        else:
            merged.append(n)

    # 若只剩一个事件且仍低于下限,强制抬高到下限
    if len(merged) == 1 and merged[0].duration < min_dur:
        merged[0] = NoteEvent(merged[0].pitch, merged[0].start, min_dur, merged[0].confidence)

    # 合并相邻的同类事件(相邻休止 / 相邻同音高音符),保持输出精简
    return _merge_adjacent(merged)


def _merge_adjacent(notes: List[NoteEvent]) -> List[NoteEvent]:
    """仅合并相邻休止。

    相邻同音高音符必须保留:它们可能是重复音符(如"1 1"),合并会破坏节奏。
    """
    if not notes:
        return []
    merged: List[NoteEvent] = [notes[0]]
    for n in notes[1:]:
        last = merged[-1]
        if last.is_rest and n.is_rest:
            merged[-1] = NoteEvent(
                last.pitch, last.start, last.duration + n.duration, max(last.confidence, n.confidence)
            )
        else:
            merged.append(n)
    return merged


def trim_leading_trailing_rests(
    notes: List[NoteEvent], trim_leading: bool, trim_trailing: bool
) -> List[NoteEvent]:
    """修剪序列开头与结尾的休止(保证循环无缝)。"""
    out = list(notes)
    if trim_leading:
        while out and out[0].is_rest:
            out.pop(0)
    if trim_trailing:
        while out and out[-1].is_rest:
            out.pop()
    return out


def merge_rests_into_previous(notes: List[NoteEvent]) -> List[NoteEvent]:
    """去除全部休止:休止时长并入其前一个音符。

    序列开头的休止无处并入,直接丢弃;输出不包含任何 0Hz 事件。
    """
    out: List[NoteEvent] = []
    for n in notes:
        if n.is_rest:
            if out and not out[-1].is_rest:
                last = out[-1]
                out[-1] = NoteEvent(
                    last.pitch,
                    last.start,
                    last.duration + n.duration,
                    last.confidence,
                )
            # 开头或连续的休止:并入前一个已合并的音符(或直接丢弃)
        else:
            out.append(n)
    return out
