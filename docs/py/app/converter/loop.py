"""4.5 两分钟控制 + 智能补全/裁剪 + 循环衔接。"""

from __future__ import annotations

import math
from typing import List, Tuple

from ..models import NoteEvent, ConvertParams, PlayEvent


def total_duration(notes: List[NoteEvent]) -> float:
    return sum(n.duration for n in notes)


def _to_play_events(notes: List[NoteEvent]) -> List[PlayEvent]:
    return [PlayEvent(freq=int(round(n.pitch)) if not n.is_rest else 0, duration=n.duration) for n in notes]


def _speed_scale(notes: List[NoteEvent], factor: float) -> List[NoteEvent]:
    """factor > 1 加速(时长缩短)。"""
    return [
        NoteEvent(n.pitch, n.start / factor, n.duration / factor, n.confidence) for n in notes
    ]


def _phrase_boundaries(notes: List[NoteEvent], min_gap: float = 0.20) -> List[int]:
    """用长休止切分乐句,返回每个乐句结束事件的下标(含)。"""
    bounds: List[int] = []
    for i, n in enumerate(notes):
        if i > 0 and notes[i - 1].is_rest and notes[i - 1].duration >= min_gap:
            bounds.append(i - 1)
    if not bounds:
        bounds.append(len(notes) - 1)
    return bounds


def _sequence_similarity(a: List[NoteEvent], b: List[NoteEvent]) -> float:
    """基于量化 token 的编辑距离相似度(0~1)。"""
    tokens_a = [f"{int(round(n.pitch))}:{n.duration:.3f}" for n in a]
    tokens_b = [f"{int(round(n.pitch))}:{n.duration:.3f}" for n in b]
    if not tokens_a or not tokens_b:
        return 0.0

    def edit_distance(x: List[str], y: List[str]) -> int:
        prev = list(range(len(y) + 1))
        for i, cx in enumerate(x, 1):
            cur = [i]
            for j, cy in enumerate(y, 1):
                cur.append(
                    min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (0 if cx == cy else 1))
                )
            prev = cur
        return prev[-1]

    dist = edit_distance(tokens_a, tokens_b)
    return 1.0 - dist / max(len(tokens_a), len(tokens_b))


def _remove_repeated_phrases(notes: List[NoteEvent], messages: List[str]) -> List[NoteEvent]:
    """检测相邻相似乐句并删除重复段(保留一次)。"""
    bounds = _phrase_boundaries(notes)
    if len(bounds) < 2:
        return notes

    segments = []
    prev = 0
    for b in bounds:
        segments.append(notes[prev : b + 1])
        prev = b + 1
    if prev < len(notes):
        segments.append(notes[prev:])

    kept = [segments[0]]
    removed = False
    for seg in segments[1:]:
        if len(kept) >= 1 and _sequence_similarity(kept[-1], seg) >= 0.8:
            removed = True
            continue
        kept.append(seg)

    if removed:
        messages.append("已删除检测到的重复乐段(保留一次)")
    return [n for seg in kept for n in seg]


def _final_tune(
    notes: List[NoteEvent], target: float, messages: List[str], merge_rests: bool = False
) -> List[NoteEvent]:
    """收尾休止微调:让总时长恰好等于 target。"""
    diff = target - total_duration(notes)
    if abs(diff) < 1e-6:
        return notes
    if notes and notes[-1].is_rest:
        last = notes[-1]
        notes[-1] = NoteEvent(last.pitch, last.start, last.duration + diff, last.confidence)
    else:
        if diff > 0:
            if merge_rests:
                # 无休止模式:余量并入最后一个音符
                last = notes[-1]
                notes[-1] = NoteEvent(last.pitch, last.start, last.duration + diff, last.confidence)
                messages.append(f"收尾微调:补 {diff:+.3f}s 至目标时长(并入末音符)")
            else:
                # 补一个呼吸休止,并精确微调
                notes.append(NoteEvent(0.0, total_duration(notes), diff))
                messages.append(f"收尾休止微调:补/调 {diff:+.3f}s 至目标时长")
        else:
            # 超出:把最后一个音符时长收紧
            last = notes[-1]
            notes[-1] = NoteEvent(last.pitch, last.start, last.duration + diff, last.confidence)
            messages.append(f"收尾微调:调 {diff:+.3f}s 至目标时长")
    return notes


def fit_to_target(
    notes: List[NoteEvent], params: ConvertParams, messages: List[str]
) -> Tuple[List[NoteEvent], str]:
    """将序列调整到目标时长,返回 (事件列表, 处理模式说明)。"""
    target = params.target_duration
    notes = list(notes)
    t = total_duration(notes)

    if abs(t - target) <= 0.5:
        mode = "原长在容差内"
    elif t < target:
        mode = "补全"
        t = total_duration(notes)
        n_repeat = max(1, round(target / t))

        # 尝试重复 + 限幅内变速
        best = None
        for n in (n_repeat, max(1, n_repeat - 1), n_repeat + 1):
            scale_min = 1.0 - params.extend_speed_limit
            scale_max = 1.0 + params.extend_speed_limit
            factor = target / (n * t)
            # factor > 1 表示加速缩短
            if scale_min <= factor <= scale_max:
                best = (n, factor)
                break
        if best is None:
            best = (n_repeat, 1.0)
        n, factor = best

        if n > 1:
            notes = notes * n
            messages.append(f"原曲 {t:.2f}s 不足目标:按乐句重复 {n} 次")
        if abs(factor - 1.0) > 1e-6:
            notes = _speed_scale(notes, factor)
            messages.append(f"微调全局速度 ×{factor:.4f}(限幅 ±{params.extend_speed_limit * 100:.0f}%)")

        notes = _final_tune(notes, target, messages, params.merge_rests)
    else:
        mode = "裁剪"
        t = total_duration(notes)
        fastest = 1.0 + params.trim_speed_limit
        if t / fastest <= target + 0.5:
            notes = _speed_scale(notes, fastest)
            messages.append(f"整体提速 ×{fastest:.2f}(限幅 +{params.trim_speed_limit * 100:.0f}%)")
        else:
            # 提速后仍超时:截取至最近乐句边界
            notes = _speed_scale(notes, fastest)
            cut = 0
            for b in _phrase_boundaries(notes):
                if total_duration(notes[: b + 1]) <= target:
                    cut = b + 1
            if cut > 0:
                notes = notes[:cut]
                messages.append("提速后仍超时:已截取至最近乐句边界(保证可循环)")
            else:
                # 删除重复乐段
                before = total_duration(notes)
                notes = _remove_repeated_phrases(notes, messages)
                if total_duration(notes) > target:
                    # 硬截断至目标 + 收尾休止兜底
                    acc = 0.0
                    cut_notes: List[NoteEvent] = []
                    for n in notes:
                        if acc + n.duration > target:
                            keep = max(target - acc, params.min_event_dur)
                            cut_notes.append(NoteEvent(n.pitch, acc, keep, n.confidence))
                            break
                        cut_notes.append(n)
                        acc += n.duration
                    notes = cut_notes
                    messages.append("已硬截断至目标时长(收尾休止兜底)")
                else:
                    messages.append("提速后仍超时:已删除重复乐段")

        notes = _final_tune(notes, target, messages, params.merge_rests)
        messages.append("原曲超过目标时长:已裁剪")

    return notes, mode


def build_play_sequence(
    notes: List[NoteEvent], params: ConvertParams, messages: List[str]
) -> List[PlayEvent]:
    """构建最终播放序列:修剪首尾休止 + 两分钟控制。"""
    from .quantizer import trim_leading_trailing_rests

    notes = trim_leading_trailing_rests(notes, params.trim_leading_rests, params.trim_trailing_rests)
    if not notes:
        raise ValueError("修剪首尾休止后序列为空,请检查输入音符")
    notes, mode = fit_to_target(notes, params, messages)
    return _to_play_events(notes), mode
