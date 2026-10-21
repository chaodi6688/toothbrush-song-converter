"""音频识别测试:YIN 应能从合成正弦波旋律中恢复音高。"""

from __future__ import annotations

import io
import sys
import unittest
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.recognition.audio import recognize_audio


def make_wav(notes_hz_dur, sr=22050, rest_gap=0.0):
    """合成单声道正弦波 WAV。notes_hz_dur: [(hz, dur), ...]"""
    chunks = []
    for hz, dur in notes_hz_dur:
        n = int(dur * sr)
        t = np.arange(n) / sr
        if hz > 0:
            fade = min(int(0.02 * sr), n // 2)
            env = np.ones(n)
            if fade > 0:
                env[:fade] *= np.linspace(0, 1, fade)
                env[-fade:] *= np.linspace(1, 0, fade)
            chunks.append(0.4 * env * np.sin(2 * np.pi * hz * t))
        else:
            chunks.append(np.zeros(n))
        if rest_gap > 0:
            chunks.append(np.zeros(int(rest_gap * sr)))
    y = np.concatenate(chunks) if chunks else np.zeros(1)
    y = np.clip(y, -1, 1)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((y * 32767).astype("<i2").tobytes())
    return buf.getvalue()


class TestAudioRecognition(unittest.TestCase):
    def test_melody_detection(self):
        data = make_wav([(262, 0.5), (330, 0.5), (392, 0.5)], sr=22050)
        notes, _ = recognize_audio(data, "test.wav")
        freqs = sorted(round(n.pitch) for n in notes if n.pitch > 0)
        # 应识别出三个主音,允许 ±8Hz(YIN 分辨率)
        for target in (262, 330, 392):
            self.assertTrue(
                any(abs(f - target) <= 8 for f in freqs),
                f"未识别到 {target}Hz,实际: {freqs}",
            )

    def test_rest_detected(self):
        data = make_wav([(262, 0.4), (0, 0.4), (330, 0.4)], sr=22050)
        notes, _ = recognize_audio(data, "test.wav")
        self.assertTrue(any(n.pitch == 0 for n in notes), "未识别到休止")

    def test_corrupt_file(self):
        with self.assertRaises(Exception):
            recognize_audio(b"not a wav file at all........", "bad.wav")

    def test_short_audio_error(self):
        data = make_wav([(262, 0.05)], sr=22050)
        with self.assertRaises(Exception):
            recognize_audio(data, "short.wav")

    def test_bpm_estimation(self):
        # BPM=100:四分音符 0.6s;自动估计应接近 100
        data = make_wav([(262, 0.6), (330, 0.6), (392, 0.6), (440, 0.6), (392, 0.6)], sr=22050)
        notes, bpm = recognize_audio(data, "t.wav")
        self.assertTrue(notes)
        self.assertTrue(85 <= bpm <= 115, f"BPM 估计偏差过大:{bpm}")

    def test_repeated_pitch_segmentation(self):
        # 同音高重复音符必须切分为多个事件(1 1 5 5 6 6 5)
        data = make_wav([(262, 0.25), (262, 0.25), (392, 0.25), (392, 0.25),
                         (440, 0.25), (440, 0.25), (392, 0.25)], sr=22050)
        notes, _ = recognize_audio(data, "t.wav")
        pitches = [round(n.pitch) for n in notes if n.pitch > 0]
        # 期望 7 个音高事件,且包含相邻同音高
        self.assertGreaterEqual(len(pitches), 7, f"重复音被合并:{pitches}")
        self.assertTrue(abs(pitches[0] - 262) <= 8 and abs(pitches[1] - 262) <= 8, f"前两个应为 do/do:{pitches[:2]}")
        self.assertTrue(abs(pitches[2] - 392) <= 8 and abs(pitches[3] - 392) <= 8, f"3-4 应为 sol/sol:{pitches[2:4]}")

    def test_note_gaps_produce_rests(self):
        # 音符间 0.05s 间隔应识别为休止事件,节奏不丢失
        data = make_wav([(262, 0.25), (330, 0.25), (392, 0.25), (440, 0.25)],
                        sr=22050, rest_gap=0.05)
        notes, _ = recognize_audio(data, "t.wav")
        rests = [n for n in notes if n.pitch == 0]
        self.assertGreaterEqual(len(rests), 3, f"音符间间隔未识别为休止:{notes}")
        for r in rests:
            self.assertGreaterEqual(r.duration, 0.03)
        # 连奏(无间隔)不应产生休止
        legato = make_wav([(262, 0.25), (330, 0.25), (392, 0.25)], sr=22050)
        notes2, _ = recognize_audio(legato, "t.wav")
        self.assertFalse(any(n.pitch == 0 for n in notes2), f"连奏不应有休止:{notes2}")

    def test_high_pitch_recognition(self):
        # 中高音(880Hz/1046Hz)应识别正确
        data = make_wav([(880.0, 0.3), (1046.5, 0.3), (659.25, 0.3)], sr=22050, rest_gap=0.05)
        notes, _ = recognize_audio(data, "t.wav")
        pitches = [n.pitch for n in notes if n.pitch > 0]
        self.assertGreaterEqual(len(pitches), 3, f"高音未识别:{notes}")
        for expect, got in zip((880.0, 1046.5, 659.25), pitches):
            semitones = 12 * np.log2(got / expect)
            self.assertTrue(abs(semitones) < 1.0, f"{expect}Hz 识别为 {got}Hz,偏差 {semitones:.1f} 半音")


if __name__ == "__main__":
    unittest.main(verbosity=2)
