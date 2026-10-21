"""乐谱 PDF 识别测试(渲染 → OMR)。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.recognition.pdf_omr import recognize_pdf, render_pdf_pages

from test_omr import make_simple_notation_png


class TestPdfRecognition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from reportlab.pdfgen import canvas

            png = Path("work/jianpu.png")
            png.write_bytes(make_simple_notation_png())
            c = canvas.Canvas("work/test_score.pdf", pagesize=(595, 300))
            for _ in range(2):
                c.drawImage(str(png), 30, 40, width=530, height=200)
                c.showPage()
            c.save()
            cls.pdf = Path("work/test_score.pdf").read_bytes()
        except Exception as e:  # noqa: BLE001
            raise unittest.SkipTest(f"reportlab 不可用,跳过 PDF 测试:{e}")

    def test_render_pages(self):
        pages = render_pdf_pages(self.pdf)
        self.assertEqual(len(pages), 2)
        self.assertTrue(all(p.startswith(b"\x89PNG") for p in pages))

    def test_recognize_pdf(self):
        notes = recognize_pdf(self.pdf, bpm=120)
        self.assertTrue(notes, "PDF 未识别出音符")
        freqs = sorted(round(n.pitch) for n in notes if n.pitch > 0)
        # 2 页 × (1 2 3 5 0),非休止 8 个;检查包含 do/re/mi/sol
        for target in (262, 294, 330, 392):
            self.assertTrue(any(abs(f - target) <= 5 for f in freqs), f"缺 {target},实际 {freqs}")
        self.assertGreaterEqual(len(freqs), 6)

    def test_corrupt_pdf_error(self):
        with self.assertRaises(Exception):
            recognize_pdf(b"%PDF-1.4 broken garbage not a real pdf", bpm=120)


if __name__ == "__main__":
    unittest.main(verbosity=2)
