"""F1.3 音频识别:WAV 解码 + YIN 单音跟踪。

仅依赖标准库 + numpy。MP3/FLAC/M4A 在系统存在 ffmpeg 时自动转 WAV。
"""

from __future__ import annotations

import io
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import List, Tuple

import numpy as np

from ..models import NoteEvent


class AudioDecodeError(Exception):
    pass


def _decode_wav_bytes(data: bytes) -> Tuple[np.ndarray, int]:
    """解码 WAV(PCM 8/16/24/32 / float32),返回 (float32 单声道 [-1,1], 采样率)。"""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            n_ch, sampwidth, sr, n_frames, _, _ = w.getparams()
            raw = w.readframes(n_frames)
    except Exception as e:
        raise AudioDecodeError(f"WAV 文件无法解析:{e}") from e

    if sampwidth not in (1, 2, 3, 4):
        raise AudioDecodeError(f"不支持的采样位深:{sampwidth * 8} bit")

    if sampwidth == 4:
        # 可能是 float32 或 PCM32
        if len(raw) >= 4:
            probe = struct.unpack("<f", raw[:4])[0]
            is_float = 0.0 <= abs(probe) <= 4.0
            if is_float:
                samples = np.frombuffer(raw, dtype="<f4").astype(np.float32)
                samples = np.clip(samples, -1.0, 1.0)
                return _to_mono(samples, n_ch), sr
    if sampwidth == 1:
        samples = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
        samples = (samples - 128.0) / 128.0
    elif sampwidth == 2:
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sampwidth == 3:
        samples = np.frombuffer(raw, dtype=np.uint8)
        samples = samples.reshape(-1, 3).astype(np.int32)
        ints = (samples[:, 0] | (samples[:, 1] << 8) | (samples[:, 2] << 16))
        ints = np.where(ints & 0x800000, ints - 0x1000000, ints)
        samples = ints.astype(np.float32) / 8388608.0
    else:
        samples = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    return _to_mono(samples, n_ch), sr


def _to_mono(samples: np.ndarray, n_ch: int) -> np.ndarray:
    if n_ch == 1:
        return samples
    return samples.reshape(-1, n_ch).mean(axis=1).astype(np.float32)


def _resample(y: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """线性插值重采样。"""
    if sr_in == sr_out:
        return y
    n_out = int(round(len(y) * sr_out / sr_in))
    x_old = np.linspace(0.0, 1.0, len(y), endpoint=False)
    x_new = np.linspace(0.0, 1.0, n_out, endpoint=False)
    return np.interp(x_new, x_old, y).astype(np.float32)


def decode_audio(data: bytes, filename: str) -> Tuple[np.ndarray, int]:
    """按扩展名解码音频;非 WAV 且存在 ffmpeg 时自动转换。"""
    ext = Path(filename).suffix.lower()
    if ext == ".wav":
        try:
            return _decode_wav_bytes(data)
        except AudioDecodeError:
            raise
    else:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise AudioDecodeError(
                f"{ext.upper()} 格式需要 ffmpeg 解码,当前环境未找到 ffmpeg。"
                "请安装 ffmpeg 并加入 PATH,或直接上传 WAV 文件。"
            )
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / ("in" + ext)
            dst = Path(td) / "out.wav"
            src.write_bytes(data)
            try:
                proc = subprocess.run(
                    [ffmpeg, "-y", "-i", str(src), "-ac", "1", "-ar", "22050", str(dst)],
                    capture_output=True,
                    timeout=120,
                )
            except Exception as e:
                raise AudioDecodeError(f"ffmpeg 转换失败:{e}") from e
            if proc.returncode != 0:
                raise AudioDecodeError(
                    f"ffmpeg 无法解码 {ext.upper()},文件可能已损坏。"
                )
            return _decode_wav_bytes(dst.read_bytes())


# ---------------------------------------------------------------- YIN 音高跟踪


def _autocorr_batch(frames: np.ndarray, fft_len: int) -> np.ndarray:
    spec = np.fft.rfft(frames, n=fft_len, axis=1)
    autoc = np.fft.irfft(spec * np.conj(spec), n=fft_len, axis=1)
    return autoc


def yin_track(
    y: np.ndarray,
    sr: int,
    frame_len: int = 2048,
    hop: int = 512,
    fmin: float = 55.0,
    fmax: float = 1200.0,
    threshold: float = 0.12,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """YIN 基频跟踪。

    返回 (times, f0, voiced, confidence):f0 为 Hz,voiced 为布尔帧掩码。
    """
    if len(y) < frame_len:
        raise AudioDecodeError("音频过短,无法分析(至少需要 0.1 秒)")

    tau_min = max(2, int(sr / fmax))
    tau_max = min(frame_len // 2 - 1, int(sr / fmin))
    taus = np.arange(tau_min, tau_max + 1)

    # 2 倍帧长的 2 次幂,避免循环卷绕
    fft_len = 1 << ((frame_len).bit_length() + 1)

    n_frames = 1 + (len(y) - frame_len) // hop
    f0 = np.zeros(n_frames, dtype=np.float32)
    voiced = np.zeros(n_frames, dtype=bool)
    confidence = np.zeros(n_frames, dtype=np.float32)

    batch = 256
    for start in range(0, n_frames, batch):
        # 分块构建帧视图:整段滑动窗口视图在 32 位环境(Pyodide)会超出索引上限
        count = min(batch, n_frames - start)
        base = start * hop
        seg = y[base : base + (count - 1) * hop + frame_len]
        fb = np.ascontiguousarray(
            np.lib.stride_tricks.sliding_window_view(seg, frame_len)[::hop][:count],
            dtype=np.float32,
        )
        autoc = _autocorr_batch(fb, fft_len)[:, : tau_max + 1]
        r0 = autoc[:, :1]
        # 差分函数 d(tau) ≈ 2*(r0 - R(tau));r0==0 表示静音
        d = np.maximum(2.0 * (r0 - autoc[:, tau_min : tau_max + 1]), 0.0)
        energy = r0[:, 0]

        # CMND(标准 YIN):d'(tau) = d(tau) * tau / sum_{1..tau} d
        cum = np.cumsum(d, axis=1)
        taus_idx = np.arange(1, d.shape[1] + 1, dtype=np.float32)
        cmnd = d * taus_idx[None, :] / np.maximum(cum, 1e-12)
        cmnd = np.where(cum < 1e-12, 1.0, cmnd)
        cmnd[:, 0] = 1.0

        # 第一个低于阈值的局部最小(向量化:避免逐点 Python 循环,
        # 在浏览器 Pyodide 环境下可把音频识别提速一个数量级以上)
        below = cmnd < threshold
        local_min = np.zeros_like(below)
        local_min[:, 1:-1] = (cmnd[:, 1:-1] <= cmnd[:, :-2]) & (
            cmnd[:, 1:-1] <= cmnd[:, 2:]
        )
        cand = below & local_min
        cand[:, 0] = False  # 与原实现一致:滞后从 1 开始
        has_cand = cand.any(axis=1)
        first = np.argmax(cand, axis=1)
        tau_idx = np.where(has_cand, first, -1).astype(np.int64)

        # 能量低于峰值 2% 判为静音(避免量化噪声/静音被误判为有声)
        energy_thresh = max(0.02 * float(np.max(energy)), 1e-4)
        valid = has_cand & (energy > energy_thresh)
        idx = np.where(valid)[0]
        if len(idx):
            f0[start + idx] = sr / taus[tau_idx[idx]]
            voiced[start + idx] = True
            confidence[start + idx] = np.maximum(0.0, 1.0 - cmnd[idx, tau_idx[idx]])

    times = (np.arange(n_frames) * hop) / sr
    return times, f0, voiced, confidence


def _median_smooth(values: np.ndarray, window: int = 5) -> np.ndarray:
    if len(values) < window:
        return values
    out = np.copy(values)
    half = window // 2
    for i in range(len(values)):
        lo = max(0, i - half)
        hi = min(len(values), i + half + 1)
        seg = values[lo:hi]
        seg = seg[seg > 0]
        if len(seg):
            out[i] = np.median(seg)
    return out


def frame_energy(y: np.ndarray, frame_len: int = 2048, hop: int = 512) -> np.ndarray:
    """逐帧 RMS 能量(分块构建视图,兼容 32 位环境)。"""
    n = 1 + (len(y) - frame_len) // hop
    out = np.empty(n, dtype=np.float64)
    block = 512
    for start in range(0, n, block):
        count = min(block, n - start)
        base = start * hop
        seg = y[base : base + (count - 1) * hop + frame_len]
        win = np.lib.stride_tricks.sliding_window_view(seg, frame_len)[::hop][:count]
        out[start : start + count] = np.sqrt(
            np.mean(win.astype(np.float64) ** 2, axis=1)
        )
    return out


def detect_onsets(
    energy: np.ndarray,
    hop: int,
    sr: int,
    min_gap_s: float = 0.06,
) -> List[int]:
    """能量包络突变检测音符起始帧(onset)。"""
    if len(energy) < 4:
        return []
    e = energy / (np.max(energy) + 1e-9)
    e = np.maximum(e, 1e-9)
    d = np.diff(e)
    d = np.maximum(d, 0.0)
    d = np.convolve(d, np.ones(3) / 3.0, mode="same")
    pos = d[d > 0]
    base = float(np.median(pos)) if len(pos) else 0.0
    thresh = max(base * 3.0, 0.015)
    peaks = []
    for i in range(1, len(d) - 1):
        if d[i] > thresh and d[i] >= d[i - 1] and d[i] >= d[i + 1]:
            peaks.append(i + 1)
    min_gap = max(1, int(min_gap_s * sr / hop))
    merged: List[int] = []
    for p in peaks:
        if merged and p - merged[-1] < min_gap:
            continue
        merged.append(p)
    return merged


def short_window_energy(y: np.ndarray, win: int = 256, hop: int = 128) -> np.ndarray:
    """短窗 RMS 能量,用于捕捉音符间细微能量凹陷(分块处理)。"""
    if len(y) < win:
        return np.array([float(np.sqrt(np.mean(y.astype(np.float64) ** 2)))])
    n = 1 + (len(y) - win) // hop
    out = np.empty(n, dtype=np.float64)
    block = 4096
    for start in range(0, n, block):
        count = min(block, n - start)
        base = start * hop
        seg = y[base : base + (count - 1) * hop + win]
        frames = np.lib.stride_tricks.sliding_window_view(seg, win)[::hop][:count]
        out[start : start + count] = np.sqrt(
            np.mean(frames.astype(np.float64) ** 2, axis=1)
        )
    return out


def detect_valleys(
    energy: np.ndarray,
    hop: int,
    sr: int,
    depth_rel: float = 0.40,
    min_gap_s: float = 0.05,
) -> List[int]:
    """短窗能量谷底检测:局部最小值显著低于两侧窗口峰值 → 音符边界。"""
    n = len(energy)
    if n < 6:
        return []
    med = float(np.median(energy))
    win = max(2, int(0.06 * sr / hop))
    floor = max(0.30 * med, 1e-4)
    bounds: List[int] = []
    i = win
    min_gap = max(1, int(min_gap_s * sr / hop))
    while i < n - win:
        left = float(np.max(energy[i - win : i]))
        right = float(np.max(energy[i + 1 : i + 1 + win]))
        base = min(left, right)
        if energy[i] < base * (1.0 - depth_rel) and energy[i] < floor:
            bounds.append(i)
            i += min_gap
        else:
            i += 1
    return bounds


def segment_notes(
    times: np.ndarray,
    f0: np.ndarray,
    voiced: np.ndarray,
    confidence: np.ndarray,
    onsets: List[int],
    energy: np.ndarray,
    short_energy: np.ndarray,
    hop: int,
    sr: int,
    min_note_dur: float = 0.06,
    semitone_tol: float = 0.6,
    min_rest_dur: float = 0.04,
) -> List[NoteEvent]:
    """将帧级音高聚合成 NoteEvent 列表。

    段边界 = 音高变化 ∪ 能量 onset(同音高重复音符在此切分);
    段间静音仅在能量足够低且持续足够长时输出为休止。
    """
    f0 = _median_smooth(f0.astype(np.float64))
    hop_sec = hop / sr
    events: List[NoteEvent] = []

    # 1) 构建连续 voiced 段:onset 或音高变化处切分
    segments: List[List[int]] = []
    cur: List[int] = []
    onset_set = set(onsets)
    for i in range(len(f0)):
        if voiced[i] and f0[i] > 0:
            if cur and (i in onset_set or _pitch_distance(f0[i], f0[cur[-1]]) > semitone_tol):
                segments.append(cur)
                cur = []
            cur.append(i)
        else:
            if cur:
                segments.append(cur)
                cur = []
    if cur:
        segments.append(cur)

    # 1.5) 合并过短段(音符内部碎片):仅并入"连续且音高相近"的邻段,
    #      避免把跨休止间隙的片段并入(会虚增时长)。
    min_seg = 0.06
    merged_segs: List[List[int]] = []
    pending_into_next: List[int] = []
    for i, seg in enumerate(segments):
        dur = (seg[-1] + 1 - seg[0]) * hop_sec
        if dur >= min_seg:
            merged_segs.append(pending_into_next + list(seg))
            pending_into_next = []
        else:
            prev = merged_segs[-1] if merged_segs else None
            nxt = segments[i + 1] if i + 1 < len(segments) else None
            pitch_here = float(np.median(f0[seg]))
            if (
                prev is not None
                and seg[0] == prev[-1] + 1
                and abs(_pitch_distance(pitch_here, float(np.median(f0[prev]))) < 0.7)
            ):
                prev.extend(seg)
            elif (
                nxt is not None
                and seg[-1] + 1 == nxt[0]
                and abs(_pitch_distance(pitch_here, float(np.median(f0[nxt]))) < 0.7)
            ):
                pending_into_next.extend(seg)  # 并入下一段
            elif prev is not None:
                prev.extend(seg)  # 兜底:并入前段(边缘片段)
            else:
                merged_segs.append(list(seg))
    if pending_into_next:
        merged_segs.append(pending_into_next)
    segments = merged_segs

    # 2) 依次输出音符与休止:每个段边界检查短窗静音宽度(不依赖 voiced gap)
    prev_end = -1  # 上一段结束的帧下标
    ratio = hop // 128 if short_energy is not None and len(short_energy) > 0 else 1
    short_hop_sec = 128.0 / sr
    if short_energy is not None and len(short_energy) > 0:
        # 全局参考:活跃帧的高分位能量(弱奏/休止多的曲子中位数会失真)
        active = energy[energy > 1e-6]
        if len(active):
            global_ref = float(np.percentile(active, 85))
        else:
            global_ref = 1.0
    else:
        global_ref = 1.0

    for seg in segments:
        if prev_end >= 0 and short_energy is not None and len(short_energy) > 0:
            # 边界搜索区间:上一段尾 ~ 本段头,向两侧各扩展 3 帧
            lo_frame = max(0, prev_end - 3)
            hi_frame = min(len(f0), seg[0] + 3)
            s0 = min(len(short_energy), lo_frame * ratio)
            s1 = min(len(short_energy), hi_frame * ratio + 1)
            seg_en = short_energy[s0:s1]
            if len(seg_en):
                # 相对参考:上一段与本段在短窗上的能量,取较大者
                prev_short = short_energy[max(0, prev_end * ratio) : (prev_end + 1) * ratio + 1]
                cur_short = short_energy[seg[0] * ratio : (seg[0] + 1) * ratio + 1]
                refs = []
                for part in (prev_short, cur_short):
                    if len(part):
                        refs.append(float(np.median(part)))
                local_ref = max(refs) if refs else global_ref
                ref = max(local_ref, global_ref)
                low = seg_en < max(0.25 * ref, 1e-6)
                rest_dur = int(low.sum()) * short_hop_sec
                if rest_dur >= 0.03:
                    events.append(
                        NoteEvent(
                            pitch=0.0,
                            start=(prev_end + 1) * hop_sec,
                            duration=rest_dur,
                            confidence=1.0,
                        )
                    )
        elif prev_end >= 0:
            gap_start = prev_end + 1
            gap_end = seg[0]
            if gap_end > gap_start:
                gap_dur = (gap_end - gap_start) * hop_sec
                gap_energy = float(np.min(energy[gap_start:gap_end]))
                if gap_dur >= min_rest_dur and gap_energy < max(0.35 * med_energy, 1e-4):
                    events.append(
                        NoteEvent(pitch=0.0, start=gap_start * hop_sec, duration=gap_dur, confidence=1.0)
                    )
        seg_dur = (seg[-1] + 1 - seg[0]) * hop_sec
        if seg_dur >= min_note_dur:
            med = float(np.median(f0[seg]))
            conf = float(np.median(confidence[seg]))
            events.append(
                NoteEvent(pitch=med, start=seg[0] * hop_sec, duration=seg_dur, confidence=conf)
            )
        prev_end = seg[-1]

    # 3) 尾部休止
    tail_start = prev_end + 1
    if tail_start < len(f0):
        tail_dur = (len(f0) - tail_start) * hop_sec
        if short_energy is not None and len(short_energy) > 0:
            s0 = min(len(short_energy), tail_start * ratio)
            seg_en = short_energy[s0:]
            if len(seg_en):
                low = seg_en < max(0.25 * global_ref, 1e-6)
                tail_rest = int(low.sum()) * short_hop_sec
            else:
                tail_rest = 0.0
            if tail_rest >= 0.03:
                events.append(
                    NoteEvent(pitch=0.0, start=tail_start * hop_sec, duration=tail_rest, confidence=1.0)
                )
        else:
            tail_energy = float(np.min(energy[tail_start:])) if tail_start < len(energy) else 0.0
            if tail_dur >= min_rest_dur and tail_energy < max(0.35 * med_energy, 1e-4):
                events.append(
                    NoteEvent(pitch=0.0, start=tail_start * hop_sec, duration=tail_dur, confidence=1.0)
                )

    return events


def estimate_bpm(notes: List[NoteEvent], default: float = 120.0) -> float:
    """从音符起始间隔直方图估计 BPM(主节拍间隔 → 四分音符)。"""
    onsets = sorted(n.start for n in notes if not n.is_rest)
    if len(onsets) < 5:
        return default
    intervals = np.diff(np.array(onsets, dtype=np.float64))
    intervals = intervals[(intervals > 0.08) & (intervals < 2.5)]
    if len(intervals) < 3:
        return default

    # 直方图找主间隔(八分/四分/二分),再取最小可解释倍频
    hist, edges = np.histogram(intervals, bins=240, range=(0.08, 2.48))
    if hist.max() == 0:
        return default
    peak_idx = int(np.argmax(hist))
    # 抛物线插值提高峰值精度
    if 0 < peak_idx < len(hist) - 1 and hist[peak_idx - 1] > 0 and hist[peak_idx + 1] > 0:
        y0, y1, y2 = float(hist[peak_idx - 1]), float(hist[peak_idx]), float(hist[peak_idx + 1])
        delta = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2)
        peak = edges[peak_idx] + (delta + 0.5) * (edges[peak_idx + 1] - edges[peak_idx])
    else:
        peak = (edges[peak_idx] + edges[peak_idx + 1]) / 2.0

    # 主间隔可能是 16 分/8 分/四分/二分音符:
    # bpm = 15 * m / peak(m=1..8 对应 16 分..二分)。
    # 倍频歧义(如 60 vs 120)无法由间隔唯一确定,取最接近常用中位速度 100 的解释。
    candidates = []
    for m in (1, 2, 4, 8):
        bpm = 15.0 * m / peak
        if 50 <= bpm <= 220:
            candidates.append(bpm)
    if not candidates:
        return default
    return float(round(min(candidates, key=lambda b: abs(b - 100.0))))


def _pitch_distance(f1: float, f2: float) -> float:
    return abs(12.0 * np.log2(f1 / f2))


def recognize_audio(
    data: bytes, filename: str, fmin: float = 55.0, fmax: float = 1200.0
) -> Tuple[List[NoteEvent], float]:
    """音频识别入口:解码 → 重采样 → YIN → onset 分段,返回 (音符, 估计 BPM)。"""
    y, sr = decode_audio(data, filename)
    if sr != 22050:
        y = _resample(y, sr, 22050)
        sr = 22050
    frame_len, hop = 2048, 512
    energy = frame_energy(y, frame_len, hop)
    times, f0, voiced, confidence = yin_track(y, sr, fmin=fmin, fmax=fmax)
    onsets = detect_onsets(energy, hop, sr)
    se = short_window_energy(y)
    valleys = detect_valleys(se, hop_short := 128, sr)
    valley_frames = sorted({int(v * 128 / hop) for v in valleys})
    boundaries = sorted(set(onsets + valley_frames))
    # 合并邻近边界(onset 峰值可能比谷底晚 2-3 帧,产生重复切分)
    merged_bounds: List[int] = []
    for b in boundaries:
        if merged_bounds and b - merged_bounds[-1] < 4:
            continue
        merged_bounds.append(b)
    boundaries = merged_bounds
    notes = segment_notes(times, f0, voiced, confidence, boundaries, energy, se, hop=hop, sr=sr)
    if not notes:
        raise AudioDecodeError(
            "未能识别出音符,请上传更清晰的单旋律音频,或检查音频是否损坏/过短。"
        )
    est_bpm = estimate_bpm(notes)
    return notes, est_bpm


def hz_to_midi(freq: float) -> float:
    return 69.0 + 12.0 * np.log2(freq / 440.0)


def midi_to_hz(midi: float) -> float:
    return 440.0 * (2.0 ** ((midi - 69.0) / 12.0))
