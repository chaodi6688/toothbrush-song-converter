"""图片乐谱识别 MVP 测试(合成简谱/五线谱图片)。"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.recognition.image_omr import recognize_score_image


def _font(size=64):
    for p in [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\simhei.ttf",
    ]:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def make_simple_notation_png(digits="1 2 3 5 0", bpm=120):
    """渲染简谱图片:数字 + 下划线(八分)。"""
    font = _font(72)
    text = " ".join(digits.split())
    img = Image.new("L", (1400, 300), 255)
    d = ImageDraw.Draw(img)
    d.text((40, 60), text, font=font, fill=0)
    # 下划线:每个数字下方画单线(八分音符)
    x = 40
    for ch in digits.split():
        bbox = d.textbbox((0, 0), ch, font=font)
        w = bbox[2] - bbox[0]
        cx = x + w // 2
        d.line([(cx - 34, 190), (cx + 34, 190)], fill=0, width=6)
        x += w + 52
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def make_staff_png():
    """渲染高音谱五线谱:五条线 + 3 个实心音符头。"""
    img = Image.new("L", (800, 300), 255)
    d = ImageDraw.Draw(img)
    staff_y = [80, 100, 120, 140, 160]
    for y in staff_y:
        d.line([(50, y), (750, y)], fill=0, width=3)
    # 音符头:最低线 E4,中间线 B4,最高线 F5(不同小节位置)
    for x, y in ((150, 160), (260, 120), (370, 80)):
        d.ellipse([(x, y - 12), (x + 40, y + 12)], fill=0, outline=0)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class TestOMR(unittest.TestCase):
    def test_simple_notation_digits(self):
        data = make_simple_notation_png("1 2 3 5 0")
        notes = recognize_score_image(data, "jianpu.png")
        self.assertTrue(notes, "未识别出音符")
        freqs = sorted(round(n.pitch) for n in notes if n.pitch > 0)
        # 简谱 1=262(C4) 2=294 3=330 5=392
        for target in (262, 294, 330, 392):
            self.assertTrue(any(abs(f - target) <= 5 for f in freqs), f"缺 {target},实际 {freqs}")
        self.assertTrue(any(n.pitch == 0 for n in notes), "休止 0 未识别")

    def test_staff_notation(self):
        data = make_staff_png()
        notes = recognize_score_image(data, "staff.png")
        self.assertTrue(notes, "五线谱未识别出音符")
        freqs = sorted(round(n.pitch) for n in notes)
        # E4≈330, B4≈494, F5≈698
        targets = (330, 494, 698)
        for t in targets:
            self.assertTrue(any(abs(f - t) <= 8 for f in freqs), f"缺 {t},实际 {freqs}")

    def test_empty_image_error(self):
        img = Image.new("L", (200, 200), 255)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        with self.assertRaises(Exception):
            recognize_score_image(buf.getvalue(), "blank.png")


if __name__ == "__main__":
    unittest.main(verbosity=2)
