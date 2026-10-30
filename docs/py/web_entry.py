"""浏览器版(Pyodide)入口:复用与本地服务完全相同的识别 / 转换逻辑。

输入输出与 /api/convert 接口保持一致(JSON 字符串),前端无需改动数据结构。
"""

from __future__ import annotations

import json
from typing import List

from app.converter.pipeline import convert
from app.models import ConvertParams, NoteEvent
from app.recognition.audio import recognize_audio
from app.recognition.image_omr import recognize_score_image


def _build_params(raw: dict) -> ConvertParams:
    bpm = raw.get("bpm")
    if isinstance(bpm, str):
        bpm = float(bpm) if bpm.strip() else None
    if bpm in ("", 0):
        bpm = None

    def _f(key: str, default: float) -> float:
        try:
            return float(raw.get(key) or default)
        except (TypeError, ValueError):
            return default

    return ConvertParams(
        target_duration=_f("target_duration", 120.0),
        freq_min=_f("freq_min", 180.0),
        freq_max=_f("freq_max", 400.0),
        min_event_dur=_f("min_event_dur", 0.03),
        bpm_override=bpm,
        loop_enabled=bool(raw.get("loop", True)),
        preserve_pitch=bool(raw.get("preserve_pitch", False)),
        merge_rests=bool(raw.get("merge_rests", True)),
        trim_leading_rests=bool(raw.get("trim_rests", True)),
        trim_trailing_rests=bool(raw.get("trim_rests", True)),
    )


def _safe_filename(song_name: str) -> str:
    bad = '\\/:*?"<>|'
    return "".join(c for c in (song_name or "").strip() if c not in bad) or "未命名"


def _payload(
    song_name: str,
    notes: List[NoteEvent],
    est_bpm: float,
    params: ConvertParams,
    input_type: str,
) -> dict:
    user_bpm = params.bpm_override
    if user_bpm:
        bpm, bpm_source = user_bpm, "user"
    elif est_bpm:
        bpm, bpm_source = est_bpm, "auto"
    else:
        bpm, bpm_source = 120.0, "default"
    params.bpm_override = bpm

    result = convert(notes, params, song_name=song_name)
    pitch_notes = [n for n in notes if not n.is_rest]
    avg_conf = (
        round(sum(n.confidence for n in notes) / len(notes), 3) if notes else 0.0
    )
    return {
        "song_name": song_name,
        "sequence": [[e.freq, round(e.duration, 3)] for e in result.sequence],
        "total_duration": round(result.total_duration, 3),
        "duration_ok": result.duration_ok,
        "freq_range_ok": result.freq_range_ok,
        "min_freq": result.min_freq,
        "max_freq": result.max_freq,
        "bpm": result.bpm,
        "bpm_source": bpm_source,
        "estimated_bpm": est_bpm,
        "preserve_pitch": params.preserve_pitch,
        "pitch_min_hz": round(result.pitch_min_hz, 1),
        "pitch_max_hz": round(result.pitch_max_hz, 1),
        "note_count": len(pitch_notes),
        "event_count": len(result.sequence),
        "rest_count": sum(1 for e in result.sequence if e.freq == 0),
        "messages": result.messages,
        "loop_point": result.loop_point,
        "metadata": result.metadata,
        "avg_confidence": avg_conf,
        "recognized_notes": [
            {
                "pitch": round(n.pitch, 1),
                "start": round(n.start, 3),
                "duration": round(n.duration, 3),
                "confidence": round(n.confidence, 3),
            }
            for n in notes[:200]
        ],
        "filename": f"{_safe_filename(song_name)}_牙刷版.txt",
        "input_type": input_type,
    }


def _stem(filename: str) -> str:
    name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


def convert_audio_bytes(song_name: str, filename: str, data: bytes, params_json: str) -> str:
    """从音频字节识别并转换。filename 决定解码器(wav 直读,其余应由前端转成 wav)。"""
    params = _build_params(json.loads(params_json or "{}"))
    notes, est_bpm = recognize_audio(bytes(data), filename)
    return json.dumps(
        _payload(song_name or _stem(filename), notes, est_bpm, params, "audio"),
        ensure_ascii=False,
    )


def convert_image_bytes(song_name: str, filename: str, data: bytes, params_json: str) -> str:
    """从乐谱图片字节识别并转换。"""
    params = _build_params(json.loads(params_json or "{}"))
    bpm = params.bpm_override or 120.0
    notes = recognize_score_image(bytes(data), filename, bpm=bpm)
    return json.dumps(
        _payload(song_name or _stem(filename), notes, bpm, params, "image"),
        ensure_ascii=False,
    )


def convert_pages_bytes(song_name: str, pages: list, params_json: str) -> str:
    """从 PDF 渲染出的多页 PNG 字节识别并转换(与 pdf_omr.recognize_pdf 同逻辑)。"""
    params = _build_params(json.loads(params_json or "{}"))
    bpm = params.bpm_override or 120.0
    all_notes: List[NoteEvent] = []
    t = 0.0
    for i, png in enumerate(pages):
        page_notes = recognize_score_image(bytes(png), f"score_page{i + 1}.png", bpm=bpm)
        for n in page_notes:
            all_notes.append(NoteEvent(n.pitch, t, n.duration, n.confidence))
            t += n.duration
    if not all_notes:
        raise ValueError("PDF 各页均未识别出音符,请检查乐谱清晰度。")
    return json.dumps(
        _payload(song_name or "score", all_notes, bpm, params, "image"),
        ensure_ascii=False,
    )
