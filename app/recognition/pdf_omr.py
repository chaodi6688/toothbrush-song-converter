"""乐谱 PDF 识别:渲染每页为 PNG,复用简谱/五线谱 OMR。

需要 Poppler 的 pdftoppm;优先使用系统 PATH,其次 Codex 捆绑运行时。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List

from ..models import NoteEvent
from .image_omr import OMRException, recognize_score_image


def find_pdftoppm() -> str:
    """定位 pdftoppm:环境变量 PDFTOPPM → PATH → 常见路径。"""
    env = os.environ.get("PDFTOPPM")
    if env and Path(env).exists():
        return env
    which = shutil.which("pdftoppm")
    if which:
        return which
    candidates = [
        r"C:\Users\wuweili\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\poppler\Library\bin\pdftoppm.exe",
        r"C:\Program Files\poppler\Library\bin\pdftoppm.exe",
        r"C:\Program Files\Poppler\bin\pdftoppm.exe",
        "/usr/bin/pdftoppm",
        "/opt/homebrew/bin/pdftoppm",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return ""


def render_pdf_pages(data: bytes, dpi: int = 200) -> List[bytes]:
    """将 PDF 渲染为 PNG 页面字节列表。"""
    exe = find_pdftoppm()
    if not exe:
        raise OMRException(
            "识别 PDF 需要 Poppler 渲染工具(pdftoppm)。"
            "请安装 Poppler 并加入 PATH,或将 PDF 转成 PNG/JPG 图片上传。"
        )
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "score.pdf"
        src.write_bytes(data)
        out_prefix = Path(td) / "page"
        try:
            proc = subprocess.run(
                [exe, "-png", "-r", str(dpi), str(src), str(out_prefix)],
                capture_output=True,
                timeout=90,
            )
        except Exception as e:
            raise OMRException(f"PDF 渲染失败:{e}") from e
        if proc.returncode != 0:
            raise OMRException("PDF 渲染失败,文件可能损坏或已加密。")
        pages = sorted(Path(td).glob("page-*.png"))
        if not pages:
            raise OMRException("PDF 渲染失败:未生成任何页面。")
        return [p.read_bytes() for p in pages]


def recognize_pdf(data: bytes, bpm: float = 120.0) -> List[NoteEvent]:
    """PDF 乐谱识别:逐页渲染 + 逐页 OMR + 按页顺序拼接。"""
    pages = render_pdf_pages(data)
    all_notes: List[NoteEvent] = []
    t = 0.0
    for i, png in enumerate(pages):
        notes = recognize_score_image(png, f"score_page{i + 1}.png", bpm=bpm)
        for n in notes:
            all_notes.append(NoteEvent(pitch=n.pitch, start=t, duration=n.duration, confidence=n.confidence))
            t += n.duration
    if not all_notes:
        raise OMRException("PDF 各页均未识别出音符,请检查乐谱清晰度。")
    return all_notes
