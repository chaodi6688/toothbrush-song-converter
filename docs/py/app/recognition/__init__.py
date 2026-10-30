"""识别模块:音频(YIN)与图片乐谱(OMR MVP)。"""

from .audio import recognize_audio
from .image_omr import recognize_score_image

__all__ = ["recognize_audio", "recognize_score_image"]
