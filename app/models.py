"""数据模型:中间识别结果与最终播放序列."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class NoteEvent:
    """标准化中间数据模型.

    pitch: 音高,单位 Hz(休止为 0.0)。
    start: 起始时刻(秒)。
    duration: 时长(秒)。
    confidence: 识别置信度 0~1,手动输入为 1.0。
    """

    pitch: float
    start: float
    duration: float
    confidence: float = 1.0

    @property
    def is_rest(self) -> bool:
        return self.pitch <= 0


@dataclass
class PlayEvent:
    """播放序列中的单个事件 (频率 Hz, 时长 s)。"""

    freq: int
    duration: float


@dataclass
class ConvertParams:
    """可配置参数表 (见需求 4.6)。"""

    target_duration: float = 120.0
    freq_min: float = 180.0
    freq_max: float = 400.0
    min_event_dur: float = 0.03
    quant_grid_denom: int = 4  # 1/16 音符 = 60/BPM/4
    bpm_override: Optional[float] = None
    extend_speed_limit: float = 0.10  # 补全速度限幅 ±10%
    trim_speed_limit: float = 0.20  # 裁剪速度限幅 +20%
    trim_leading_rests: bool = True
    trim_trailing_rests: bool = True
    loop_enabled: bool = True
    preserve_pitch: bool = False  # 保持原音调:跳过 180-400Hz 频率映射
    merge_rests: bool = False  # 去除休止符:休止时长并入前一音符


@dataclass
class ConvertResult:
    """转换结果,含播放序列与校验/提示信息。"""

    sequence: List[PlayEvent]
    song_name: Optional[str] = None
    total_duration: float = 0.0
    duration_ok: bool = False
    freq_range_ok: bool = False
    min_freq: int = 0
    max_freq: int = 0
    notes: List[NoteEvent] = field(default_factory=list)
    bpm: float = 120.0
    pitch_min_hz: float = 0.0
    pitch_max_hz: float = 0.0
    messages: List[str] = field(default_factory=list)
    loop_point: Optional[str] = None
    metadata: dict = field(default_factory=dict)
