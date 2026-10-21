"""转换规则单元测试 + 验收用例(需求第 8 节 1-5)。"""

from __future__ import annotations

import math
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.converter.pipeline import convert
from app.converter.mapper import map_frequencies
from app.converter.quantizer import merge_rests_into_previous, quantize
from app.exporter import build_txt
from app.models import ConvertParams, NoteEvent


def note(pitch, start, dur):
    return NoteEvent(pitch=pitch, start=start, duration=dur)


def melody(hz_list, dur=0.5, start=0.0):
    events = []
    t = start
    for h in hz_list:
        events.append(note(h, t, dur))
        t += dur
    return events


class TestMapper(unittest.TestCase):
    def test_already_in_window_unchanged(self):
        notes = melody([200, 262, 330, 392])
        out, msgs = map_frequencies(notes)
        self.assertEqual([n.pitch for n in out], [200, 262, 330, 392])
        self.assertEqual(msgs, [])

    def test_equal_ratio_scale(self):
        # 150-300:最低音 150 < 180,跨度 2.0 ≤ 2.22,走整体等比缩放
        notes = melody([150, 300])
        out, msgs = map_frequencies(notes)
        k = 400 / 300
        self.assertAlmostEqual(out[0].pitch, round(150 * k))
        self.assertAlmostEqual(out[1].pitch, 400)
        self.assertTrue(any("整体变调" in m for m in msgs))

    def test_log_compression(self):
        # 100-500:跨度 5 > 2.22,走对数压缩,端点映射到 180/400
        notes = melody([100, 500])
        out, msgs = map_frequencies(notes)
        self.assertAlmostEqual(out[0].pitch, 180, delta=1)
        self.assertAlmostEqual(out[1].pitch, 400, delta=1)
        self.assertTrue(any("对数压缩" in m for m in msgs))

    def test_rest_not_scaled(self):
        notes = [note(220, 0, 1.0), note(0, 1.0, 0.5), note(440, 1.5, 1.0)]
        out, _ = map_frequencies(notes)
        self.assertEqual(out[1].pitch, 0)

    def test_all_rests_error(self):
        with self.assertRaises(ValueError):
            map_frequencies([note(0, 0, 1.0), note(0, 1.0, 1.0)])


class TestQuantizer(unittest.TestCase):
    def test_grid_quantization(self):
        params = ConvertParams()
        events = quantize([note(262, 0, 0.1), note(330, 0.1, 0.4)], params, bpm=120)
        # 1/16 音符 = 0.125s;0.1 → 0.125,0.4 → 0.375(0.4/0.125=3.2 → 3)
        self.assertAlmostEqual(events[0].duration, 0.125)
        self.assertAlmostEqual(events[1].duration, 0.375)

    def test_min_duration_merge(self):
        params = ConvertParams()
        events = quantize(
            [note(262, 0, 0.01), note(330, 0.01, 0.5), note(0, 0.51, 0.01)], params, bpm=60
        )
        self.assertTrue(all(e.duration >= params.min_event_dur for e in events))

    def test_merge_rests_into_previous(self):
        events = merge_rests_into_previous(
            [note(262, 0, 1.0), note(0, 1.0, 0.5), note(330, 1.5, 1.0)]
        )
        self.assertEqual([e.pitch for e in events], [262, 330])
        self.assertAlmostEqual(events[0].duration, 1.5)  # 休止时长并入前一音符
        self.assertAlmostEqual(events[1].duration, 1.0)

    def test_merge_rests_leading_and_consecutive(self):
        # 开头休止无处并入,直接丢弃;连续休止累计并入前一音符
        events = merge_rests_into_previous(
            [
                note(0, 0, 0.5),
                note(262, 0.5, 1.0),
                note(0, 1.5, 0.2),
                note(0, 1.7, 0.3),
                note(330, 2.0, 1.0),
            ]
        )
        self.assertEqual([e.pitch for e in events], [262, 330])
        self.assertAlmostEqual(events[0].duration, 1.5)
        self.assertTrue(all(not e.is_rest for e in events))

    def test_merge_rests_all_rests_empty(self):
        self.assertEqual(merge_rests_into_previous([note(0, 0, 1.0), note(0, 1.0, 1.0)]), [])


class TestFullPipeline(unittest.TestCase):
    def _assert_validation(self, res, params=None):
        params = params or ConvertParams()
        self.assertTrue(res.duration_ok, f"时长校验失败: {res.total_duration:.3f}")
        self.assertTrue(res.freq_range_ok, "频率越界")
        self.assertTrue(abs(res.total_duration - params.target_duration) <= 0.5)
        for e in res.sequence:
            self.assertTrue(e.freq == 0 or params.freq_min <= e.freq <= params.freq_max)
            self.assertGreaterEqual(e.duration, params.min_event_dur - 1e-6)

    def test_2min_single_melody(self):
        # 验收 1:2 分钟单旋律 → 120s ±0.5
        events = melody([262, 294, 330, 349, 392, 440, 392, 349], dur=1.0)
        res = convert(events, ConvertParams())
        self._assert_validation(res)
        self.assertAlmostEqual(res.total_duration, 120.0, delta=0.5)

    def test_short_song_extends(self):
        # 验收 2:30 秒歌曲自动补全至 120s
        events = melody([262, 330, 392, 330], dur=0.25)  # 1s
        res = convert(events, ConvertParams())
        self._assert_validation(res)
        self.assertEqual(res.metadata["mode"], "补全")

    def test_long_song_trims(self):
        # 验收 3:5 分钟歌曲自动裁剪至 120s
        events = melody([262, 294, 330, 349, 392, 440, 523, 440], dur=4.0) * 4  # 128s
        res = convert(events, ConvertParams())
        self._assert_validation(res)
        self.assertEqual(res.metadata["mode"], "裁剪")
        self.assertTrue(any("已裁剪" in m for m in res.messages))

    def test_rests_preserved(self):
        # 验收 4:休止以 (0, d) 完整保留
        events = [
            note(262, 0, 0.25),
            note(0, 0.25, 0.25),
            note(330, 0.5, 0.25),
            note(0, 0.75, 0.25),
        ]
        res = convert(events, ConvertParams())
        freqs = [e.freq for e in res.sequence]
        self.assertIn(0, freqs)
        rests = [e for e in res.sequence if e.freq == 0]
        self.assertTrue(rests)
        self.assertTrue(all(e.duration >= 0.03 for e in rests))

    def test_merge_rests_removes_rests(self):
        # 开启去除休止:输出无 0Hz 事件,休止时长并入前一音符
        events = [
            note(262, 0, 0.25),
            note(0, 0.25, 0.25),
            note(330, 0.5, 0.25),
            note(0, 0.75, 0.25),
        ]
        res = convert(events, ConvertParams(merge_rests=True))
        self._assert_validation(res, ConvertParams(merge_rests=True))
        self.assertTrue(res.metadata["merge_rests"])
        self.assertTrue(any("已去除休止符" in m for m in res.messages))
        self.assertFalse(any(e.freq == 0 for e in res.sequence), "输出不应包含休止")
        # 首两个事件:262(0.25)+休止(0.25)=0.5s,量化网格 0.125 → 4 格
        self.assertAlmostEqual(res.sequence[0].freq, 262)
        self.assertAlmostEqual(res.sequence[0].duration, 0.5, delta=0.001)
        self.assertAlmostEqual(res.sequence[1].freq, 330)

    def test_output_format(self):
        # 验收 5:纯数据模式,每行匹配 (整数, 小数),半角符号,无注释
        events = melody([262, 330, 392], dur=0.5)
        res = convert(events, ConvertParams())
        txt = build_txt(res)
        lines = [l for l in txt.strip().splitlines() if l.strip()]
        pattern = re.compile(r"^\(\d+, \d+\.\d{3}\)$")
        for line in lines:
            self.assertRegex(line, pattern)
        self.assertFalse(any(l.startswith("#") for l in lines))

    def test_header_mode(self):
        events = melody([262, 330], dur=0.5)
        res = convert(events, ConvertParams(), song_name="小星星")
        txt = build_txt(res, include_header=True)
        self.assertTrue(txt.startswith("# 歌名: 小星星"))

    def test_empty_input_errors(self):
        with self.assertRaises(ValueError):
            convert([], ConvertParams())

    def test_determinism(self):
        events = melody([262, 294, 330, 349, 392, 440], dur=0.5)
        r1 = convert(events, ConvertParams())
        r2 = convert(events, ConvertParams())
        self.assertEqual([(e.freq, e.duration) for e in r1.sequence],
                         [(e.freq, e.duration) for e in r2.sequence])


if __name__ == "__main__":
    unittest.main(verbosity=2)
