"""F1.4 图片乐谱识别 MVP:简谱(数字+下划线+高/低音点)与高音谱五线谱。

仅依赖 Pillow + numpy,模板匹配采用归一化互相关。
"""

from __future__ import annotations

import io
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..models import NoteEvent


class OMRException(Exception):
    pass


SOLFEGE_TO_DEGREE = {"1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6}
C_MAJOR_SEMITONES = [0, 2, 4, 5, 7, 9, 11]  # do re mi fa sol la si 相对 C 的半音
NOTE_NAMES_STAFF = ["E4", "F4", "G4", "A4", "B4", "C5", "D5", "E5", "F5"]


def _load_image(data: bytes) -> np.ndarray:
    try:
        img = Image.open(io.BytesIO(data)).convert("L")
    except Exception as e:
        raise OMRException(f"图片无法解析,请上传 PNG/JPG 图片:{e}") from e
    arr = np.asarray(img, dtype=np.float32)
    return arr


def _binarize(arr: np.ndarray, threshold: int = 160) -> np.ndarray:
    return (arr < threshold).astype(np.uint8)


def _find_fonts() -> List[str]:
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\DejaVuSans.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    import os

    found = [p for p in candidates if os.path.exists(p)]
    return found or None


def _render_digit_templates(size: int = 40) -> List[Tuple[str, np.ndarray]]:
    """渲染数字 0-7 模板,返回 (字符, 二值数组)。"""
    fonts = _find_fonts()
    templates = []
    for ch in "01234567":
        best = None
        for path in fonts:
            try:
                font = ImageFont.truetype(path, size)
            except Exception:
                continue
            img = Image.new("L", (size + 8, size + 8), 255)
            d = ImageDraw.Draw(img)
            bbox = d.textbbox((0, 0), ch, font=font)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            d.text(((size + 8 - w) // 2 - bbox[0], (size + 8 - h) // 2 - bbox[1]), ch, font=font, fill=0)
            arr = _binarize(np.asarray(img, dtype=np.float32))
            # 只保留数字核心区域
            ys, xs = np.where(arr)
            if len(ys) == 0:
                continue
            arr = arr[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
            if arr.mean() > 0.02:
                best = arr
                break
        if best is not None:
            templates.append((ch, best))
    if not templates:
        raise OMRException("系统缺少可用字体,无法渲染识别模板")
    return templates


def _connected_components(bin_img: np.ndarray, min_area: int = 3) -> List[dict]:
    """4-邻接连通域分析,返回 bbox/面积列表。"""
    h, w = bin_img.shape
    visited = np.zeros_like(bin_img, dtype=bool)
    comps = []
    for y0 in range(h):
        for x0 in range(w):
            if bin_img[y0, x0] == 0 or visited[y0, x0]:
                continue
            stack = [(y0, x0)]
            visited[y0, x0] = True
            pts_y, pts_x = [], []
            while stack:
                y, x = stack.pop()
                pts_y.append(y)
                pts_x.append(x)
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx] and bin_img[ny, nx]:
                        visited[ny, nx] = True
                        stack.append((ny, nx))
            if len(pts_y) >= min_area:
                comps.append(
                    {
                        "y0": min(pts_y),
                        "y1": max(pts_y),
                        "x0": min(pts_x),
                        "x1": max(pts_x),
                        "area": len(pts_y),
                    }
                )
    return comps


def _normalized_correlation(patch: np.ndarray, templ: np.ndarray) -> float:
    """将 patch 缩放到模板大小后计算 NCC。"""
    if patch.size == 0 or templ.size == 0:
        return 0.0
    th, tw = templ.shape
    # patch 为二值(1=笔画),转 255 后笔画为高值
    src = np.ascontiguousarray((patch * 255).astype(np.uint8))
    p = np.asarray(Image.fromarray(src).resize((tw, th), Image.LANCZOS))
    p = (p > 128).astype(np.float32)
    t = templ.astype(np.float32)
    n = t.size
    mp, mt = p.mean(), t.mean()
    vp = np.sqrt(np.sum((p - mp) ** 2) + 1e-9)
    vt = np.sqrt(np.sum((t - mt) ** 2) + 1e-9)
    if vp == 0 or vt == 0:
        return 0.0
    return float(np.sum((p - mp) * (t - mt)) / (vp * vt))


def _identify_digit(patch: np.ndarray, templates: List[Tuple[str, np.ndarray]]) -> Tuple[str, float]:
    best_ch, best_score = "?", -1.0
    for ch, templ in templates:
        score = _normalized_correlation(patch, templ)
        if score > best_score:
            best_ch, best_score = ch, score
    return best_ch, best_score


def _note_duration_symbol(comp: dict, comps: List[dict], digit_w: int) -> Tuple[float, int]:
    """根据下划线(单/双)与时值线判断音符时值。

    返回 (音符值, 延长线数量[-1 表示无延长线])。
    """
    # 检测数字正下方的水平线(下划线)
    lines_below = []
    for c in comps:
        if c is comp:
            continue
        ch = c["x1"] - c["x0"]
        hh = c["y1"] - c["y0"]
        if (
            hh <= max(3, digit_w // 6)
            and ch >= digit_w * 0.7
            and abs(c["x0"] + c["x1"] - comp["x0"] - comp["x1"]) <= digit_w * 0.6
            and comp["y1"] < c["y0"] < comp["y1"] + digit_w * 0.8
        ):
            lines_below.append(c)
    lines_below.sort(key=lambda c: c["y0"])
    if lines_below:
        # 两条线且间距 > 线高 → 十六分
        if len(lines_below) >= 2 and (lines_below[1]["y0"] - lines_below[0]["y1"]) > max(2, hh):
            return 0.25, 0
        return 0.5, 0
    return 1.0, 0


def _count_dots(comp: dict, comps: List[dict], side: str, digit_w: int) -> int:
    """统计数字上/下方的高/低音点数量。side: 'above' / 'below'"""
    dots = 0
    for c in comps:
        if c is comp:
            continue
        cw = c["x1"] - c["x0"]
        chh = c["y1"] - c["y0"]
        if cw > digit_w * 0.5 or chh > digit_w * 0.6:
            continue
        if abs((c["x0"] + c["x1"]) - (comp["x0"] + comp["x1"])) > digit_w * 0.8:
            continue
        if side == "above" and comp["y0"] - digit_w * 0.7 <= c["y1"] < comp["y0"]:
            dots += 1
        elif side == "below" and comp["y1"] < c["y0"] <= comp["y1"] + digit_w * 0.7:
            # 排除下划线(线已单独识别)
            if chh <= max(2, digit_w // 8) and cw >= digit_w * 0.7:
                continue
            dots += 1
    return dots


def _degree_to_freq(degree: int, octave: int) -> float:
    """简谱音级转频率(C 大调,do=0)。degree 0-6,octave 相对中音区的偏移。"""
    midi = 60 + C_MAJOR_SEMITONES[degree] + 12 * octave  # 中音 do = C4 = midi 60
    return 440.0 * (2.0 ** ((midi - 69.0) / 12.0))


def recognize_simple_notation(bin_img: np.ndarray, bpm: float) -> List[NoteEvent]:
    """简谱识别:数字 1-7 为音符,0 为休止;下划线决定时值;高/低音点决定八度。"""
    templates = _render_digit_templates()
    comps = _connected_components(bin_img)
    if not comps:
        raise OMRException("图片中未检测到任何图形,请上传清晰的简谱图片")

    # 数字候选:面积较大、宽高比合理
    areas = sorted((c["area"] for c in comps), reverse=True)
    median_area = areas[len(areas) // 2] if areas else 1
    digit_comps = []
    for c in comps:
        cw = c["x1"] - c["x0"]
        chh = c["y1"] - c["y0"]
        if chh <= 0:
            continue
        aspect = cw / chh
        # 宽松几何过滤(细"1" 也可能很扁),最终由模板匹配得分把关
        if 0.15 <= aspect <= 2.5 and cw >= 6 and chh >= 10 and c["area"] >= max(8, median_area * 0.25):
            digit_comps.append(c)

    if not digit_comps:
        raise OMRException(
            "未能识别出简谱音符数字。请确保图片包含清晰的 1-7 数字,且分辨率足够(每字至少约 20 像素)。"
        )

    # 按 x 聚类:同一列的多个数字视为一组(取置信度最高)
    digit_comps.sort(key=lambda c: c["x0"])
    groups: List[List[dict]] = []
    for c in digit_comps:
        if groups and c["x0"] - groups[-1][-1]["x1"] <= max(6, (c["x1"] - c["x0"]) // 2):
            groups[-1].append(c)
        else:
            groups.append([c])

    events: List[NoteEvent] = []
    for g in groups:
        best_comp, best_ch, best_score = None, "?", -1.0
        for c in g:
            patch = bin_img[c["y0"] : c["y1"] + 1, c["x0"] : c["x1"] + 1]
            ch, score = _identify_digit(patch, templates)
            if score > best_score:
                best_comp, best_ch, best_score = c, ch, score
        if best_comp is None or best_ch == "?" or best_score < 0.30:
            continue
        cw = best_comp["x1"] - best_comp["x0"]
        note_value, _ = _note_duration_symbol(best_comp, comps, cw)
        octave = _count_dots(best_comp, comps, "above", cw) - _count_dots(best_comp, comps, "below", cw)

        if best_ch == "0":
            freq = 0.0
        else:
            freq = _degree_to_freq(SOLFEGE_TO_DEGREE[best_ch], octave)
        dur = 60.0 / bpm * note_value
        conf = max(0.0, min(1.0, best_score))
        cy = (best_comp["y0"] + best_comp["y1"]) / 2.0
        events.append(NoteEvent(pitch=freq, start=cy, duration=dur, confidence=conf))

    if not events:
        raise OMRException("未能识别出有效音符,请换一张更清晰的谱图")
    return _order_and_assign_starts(events, bpm)


def _order_and_assign_starts(
    events_with_y: List[NoteEvent], bpm: float, row_tolerance: float = 0.5
) -> List[NoteEvent]:
    """按图片行序(先 y 后 x)为事件分配顺序起始时刻。

    events_with_y 的 start 字段暂存 y 坐标,返回时替换为真实起始时刻。
    """
    if not events_with_y:
        return []
    items = sorted(events_with_y, key=lambda e: e.start)  # 按 y 暂存值排序
    # 行聚类:相邻 y 差 > 平均数字高度 1.2 倍视为换行
    rows: List[List[NoteEvent]] = [[items[0]]]
    prev_y = items[0].start
    for e in items[1:]:
        if e.start - prev_y > 12:
            rows.append([])
        rows[-1].append(e)
        prev_y = e.start
    # 行内按 x 排序需要 x 信息,暂用 confidence 存 x 不可靠;改为在识别处记录。
    # 这里按行顺序平铺,每行内部保持原有顺序(识别处已按列排序)。
    ordered: List[NoteEvent] = []
    t = 0.0
    for row in rows:
        for e in row:
            ordered.append(NoteEvent(e.pitch, t, e.duration, e.confidence))
            t += e.duration
    return ordered


# ---------------------------------------------------------------- 五线谱


def _detect_staff_lines(bin_img: np.ndarray) -> Optional[List[int]]:
    """检测五线谱 5 条水平线(按水平投影峰值聚类)。"""
    h, w = bin_img.shape
    row_density = bin_img.sum(axis=1)
    # 谱线:黑像素占比高且连续的行
    lines = []
    y = 0
    while y < h:
        if row_density[y] > w * 0.25:
            start = y
            while y < h and row_density[y] > w * 0.25:
                y += 1
            center = (start + y - 1) // 2
            lines.append(center)
        else:
            y += 1
    # 聚类为 5 条近似等距线
    if len(lines) < 5:
        return None
    groups = []
    for l in lines:
        if groups and l - groups[-1][-1] <= 4:
            groups[-1].append(l)
        else:
            groups.append([l])
    staff = [int(np.mean(g)) for g in groups]
    if len(staff) >= 5:
        return staff[:5]
    return None


def _staff_pitch(cy: float, staff: List[int], step_px: float) -> Tuple[int, float]:
    """音符头中心 y → (midi, 半音偏差)。高音谱:最下线 = E4(64)。"""
    # 高音谱自下而上:E4 F4 G4 A4 B4 C5 D5 E5 F5
    staff_midi = [64, 65, 67, 69, 71, 72, 74, 76, 77]
    rel = (staff[-1] - cy) / step_px  # 相对最低线的步数(线=0,间=0.5)
    idx = int(round(rel * 2))
    if not (0 <= idx < len(staff_midi)):
        # 超出谱表:按半音外推
        step = 2 if idx % 2 == 0 else 1
        ref = staff_midi[0] if idx < 0 else staff_midi[-1]
        midi = ref + (idx - (0 if idx < 0 else len(staff_midi) - 1)) * step
        return midi, 0.0
    midi = staff_midi[idx]
    return midi, rel * 2 - round(rel * 2)


def recognize_staff_notation(bin_img: np.ndarray, bpm: float) -> List[NoteEvent]:
    """高音谱五线谱识别(基础版)。"""
    staff = _detect_staff_lines(bin_img)
    if not staff:
        raise OMRException("未检测到五线谱,请上传含五线谱的图片")

    step_px = (staff[-1] - staff[0]) / 4.0
    if step_px < 2:
        raise OMRException("谱线间距过小,请提高图片分辨率")

    # 擦除谱线(含邻域),避免音符头与谱线融合
    img_wo_lines = bin_img.copy()
    h = img_wo_lines.shape[0]
    for ly in staff:
        for dy in (-1, 0, 1):
            if 0 <= ly + dy < h:
                img_wo_lines[ly + dy, :] = 0

    comps = _connected_components(img_wo_lines, min_area=3)
    # 音符头:位于谱线区域内、尺寸接近 step_px、宽高比接近椭圆
    head_comps = []
    top, bottom = staff[0] - step_px * 2, staff[-1] + step_px * 3
    for c in comps:
        ch = c["y1"] - c["y0"]
        cw = c["x1"] - c["x0"]
        cy = (c["y0"] + c["y1"]) / 2
        if not (top <= cy <= bottom):
            continue
        if not (step_px * 0.3 <= ch <= step_px * 2.5):
            continue
        if not (0.4 <= cw / max(ch, 1) <= 5.5):
            continue
        head_comps.append(c)

    if not head_comps:
        raise OMRException("未识别到音符头,请上传清晰的五线谱图片")

    # 合并被谱线切断的上下块(同一音符头)
    head_comps.sort(key=lambda c: c["x0"])
    merged: List[dict] = []
    for c in head_comps:
        if merged:
            last = merged[-1]
            x_overlap = min(c["x1"], last["x1"]) - max(c["x0"], last["x0"])
            x_gap = max(0, c["x0"] - last["x1"])
            y_gap = c["y0"] - last["y1"]
            # 仅合并被谱线切断的同一音符头:y 间隙很小(≈线宽),x 重叠
            if (x_overlap > 0 or x_gap < step_px * 0.8) and 0 < y_gap <= max(6, step_px * 0.35):
                last["y1"] = max(last["y1"], c["y1"])
                last["x0"] = min(last["x0"], c["x0"])
                last["x1"] = max(last["x1"], c["x1"])
                last["area"] += c["area"]
                continue
        merged.append(dict(c))
    head_comps = merged

    # 按 x 排序
    events: List[NoteEvent] = []
    for c in head_comps:
        cy = (c["y0"] + c["y1"]) / 2
        midi, dev = _staff_pitch(cy, staff, step_px)
        if abs(dev) > 0.9:
            continue
        # 时值:检测符干/符尾(简化:默认四分音符)
        dur = 60.0 / bpm
        freq = 440.0 * (2.0 ** ((midi - 69.0) / 12.0))
        conf = 0.7  # 基础版固定置信度
        events.append(NoteEvent(pitch=freq, start=cy, duration=dur, confidence=conf))

    if not events:
        raise OMRException("未能识别出有效音符,请换一张更清晰的谱图")
    return _order_and_assign_starts(events, bpm)


def recognize_score_image(data: bytes, filename: str, bpm: float = 120.0) -> List[NoteEvent]:
    """图片识别入口:自动判断简谱/五线谱。"""
    arr = _load_image(data)
    bin_img = _binarize(arr)
    # 白边裁剪
    ys, xs = np.where(bin_img)
    if len(ys) == 0:
        raise OMRException("图片内容为空(全白),请上传清晰乐谱")
    bin_img = bin_img[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]

    # 尝试简谱:检测是否存在谱线(没有 → 简谱;有 → 五线谱)
    staff = _detect_staff_lines(bin_img)
    if staff:
        return recognize_staff_notation(bin_img, bpm)
    return recognize_simple_notation(bin_img, bpm)
