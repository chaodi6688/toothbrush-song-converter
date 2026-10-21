"""本地 Web 服务(纯标准库,零依赖)。

启动:python app/server.py [端口]
接口:
  GET  /                    前端页面
  GET  /static/...          静态资源
  GET  /api/sample.wav      生成示例旋律 WAV(便于演示)
  POST /api/convert         上传音频/图片 → 识别 → 转换 → 返回 JSON
"""

from __future__ import annotations

import io
import json
import re
import sys
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import numpy as np

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.converter.pipeline import convert  # noqa: E402
from app.exporter import filename_for  # noqa: E402
from app.models import ConvertParams  # noqa: E402
from app.recognition.audio import AudioDecodeError, recognize_audio  # noqa: E402
from app.recognition.image_omr import OMRException, recognize_score_image  # noqa: E402
from app.recognition.pdf_omr import recognize_pdf  # noqa: E402

UI_DIR = ROOT / "ui"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".pdf"}
AUDIO_EXTS = {".wav", ".mp3", ".flac", ".m4a"}
IMAGE_MAX = 20 * 1024 * 1024
AUDIO_MAX = 50 * 1024 * 1024


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def parse_multipart(body: bytes, content_type: str) -> Tuple[Dict[str, str], Optional[Tuple[str, bytes]]]:
    """解析 multipart/form-data,返回 (字段 dict, 文件 (文件名, 内容))。"""
    m = re.search(r"boundary=(?:\"([^\"]+)\"|([^;]+))", content_type)
    if not m:
        raise ApiError("请求不是有效的 multipart/form-data")
    boundary = (m.group(1) or m.group(2)).encode()

    fields: Dict[str, str] = {}
    uploaded: Optional[Tuple[str, bytes]] = None
    for part in body.split(b"--" + boundary):
        part = part.strip(b"\r\n")
        if not part or part in (b"--", b""):
            continue
        header_blob, sep, content = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        header = header_blob.decode("utf-8", "replace")
        name_m = re.search(r'name="([^"]+)"', header)
        if not name_m:
            continue
        name = name_m.group(1)
        filename_m = re.search(r'filename="([^"]*)"', header)
        if filename_m:
            uploaded = (filename_m.group(1) or "file", content)
        else:
            fields[name] = content.decode("utf-8", "replace")
    return fields, uploaded


def _param_float(fields: Dict[str, str], key: str, default: float) -> float:
    try:
        return float(fields.get(key, str(default)))
    except ValueError:
        return default


def _param_bool(fields: Dict[str, str], key: str, default: bool) -> bool:
    v = fields.get(key)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "on")


def handle_convert(fields: Dict[str, str], filename: str, data: bytes) -> dict:
    ext = Path(filename).suffix.lower()
    input_type = fields.get("input_type", "audio")

    # F1.2 格式与大小限制
    if input_type == "audio":
        if ext not in AUDIO_EXTS:
            raise ApiError(f"音频格式不支持:{ext or '未知'}。支持 WAV / MP3 / FLAC / M4A。")
        if len(data) > AUDIO_MAX:
            raise ApiError("音频文件超过 50 MB 上限。")
    elif input_type == "image":
        if ext not in IMAGE_EXTS:
            raise ApiError(f"图片格式不支持:{ext or '未知'}。支持 PNG / JPG / JPEG / PDF。")
        if len(data) > IMAGE_MAX:
            raise ApiError("图片文件超过 20 MB 上限。")
    else:
        raise ApiError("input_type 必须是 audio 或 image")

    params = ConvertParams(
        target_duration=_param_float(fields, "target_duration", 120.0),
        freq_min=_param_float(fields, "freq_min", 180.0),
        freq_max=_param_float(fields, "freq_max", 400.0),
        min_event_dur=_param_float(fields, "min_event_dur", 0.03),
        bpm_override=_param_float(fields, "bpm", 0.0) or None,
        loop_enabled=_param_bool(fields, "loop", True),
        preserve_pitch=_param_bool(fields, "preserve_pitch", False),
        merge_rests=_param_bool(fields, "merge_rests", True),
        trim_leading_rests=_param_bool(fields, "trim_rests", True),
        trim_trailing_rests=_param_bool(fields, "trim_rests", True),
    )
    if params.freq_max <= params.freq_min:
        raise ApiError("频率窗口无效:上限必须大于下限。")
    if params.target_duration <= 0:
        raise ApiError("目标时长必须为正数。")

    song_name = Path(filename).stem
    est_bpm = None
    try:
        if input_type == "audio":
            notes, est_bpm = recognize_audio(data, filename)
        elif ext == ".pdf":
            bpm = params.bpm_override or 120.0
            notes = recognize_pdf(data, bpm=bpm)
        else:
            bpm = params.bpm_override or 120.0
            notes = recognize_score_image(data, filename, bpm=bpm)
    except (AudioDecodeError, OMRException) as e:
        raise ApiError(str(e)) from e

    # BPM 优先级:用户覆盖 > 自动估计 > 默认 120
    if params.bpm_override:
        bpm = params.bpm_override
        bpm_source = "user"
    elif est_bpm:
        bpm = est_bpm
        bpm_source = "auto"
    else:
        bpm = 120.0
        bpm_source = "default"
    try:
        params.bpm_override = bpm
        result = convert(notes, params, song_name=song_name)
    except ValueError as e:
        raise ApiError(str(e)) from e

    pitch_notes = [n for n in notes if not n.is_rest]
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
        "avg_confidence": round(
            float(np.mean([n.confidence for n in notes])) if notes else 0.0, 3
        ),
        "recognized_notes": [
            {
                "pitch": round(n.pitch, 1),
                "start": round(n.start, 3),
                "duration": round(n.duration, 3),
                "confidence": round(n.confidence, 3),
            }
            for n in notes[:200]
        ],
        "filename": filename_for(song_name),
        "input_type": input_type,
    }


def sample_wav() -> bytes:
    """小星星前两句示例旋律(1 1 5 5 6 6 5 - 4 4 3 3 2 2 1 -)。"""
    semitones = [0, 0, 7, 7, 9, 9, 7, -1, 5, 5, 4, 4, 2, 2, 0, -1]
    sr = 22050
    chunks = []
    for st in semitones:
        dur = 0.5 if st == -1 else 0.25
        n = int(dur * sr)
        t = np.arange(n) / sr
        if st >= 0:
            freq = 440.0 * (2.0 ** ((60 - 69 + st) / 12.0))
            fade = int(0.015 * sr)
            env = np.ones(n)
            env[:fade] *= np.linspace(0, 1, fade)
            env[-fade:] *= np.linspace(1, 0, fade)
            chunks.append(0.35 * env * np.sin(2 * np.pi * freq * t).astype(np.float32))
        else:
            chunks.append(np.zeros(n, dtype=np.float32))
    y = np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    server_version = "ToothbrushPlayer/1.1"

    def log_message(self, fmt, *args):
        sys.stdout.write(f"[server] {fmt % args}\n")

    def _send(self, status: int, body: bytes, ctype: str = "application/octet-stream"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, obj: dict):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self._send_file(UI_DIR / "index.html", "text/html; charset=utf-8")
        elif path == "/favicon.ico":
            self._send(204, b"")
        elif path.startswith("/static/"):
            rel = path[len("/static/") :]
            target = (UI_DIR / rel).resolve()
            if not str(target).startswith(str(UI_DIR.resolve())):
                self._send_json(403, {"error": "禁止访问"})
                return
            ctype = {
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".svg": "image/svg+xml",
                ".png": "image/png",
            }.get(target.suffix.lower(), "application/octet-stream")
            self._send_file(target, ctype)
        elif path == "/api/sample.wav":
            self._send(200, sample_wav(), "audio/wav")
        elif path == "/api/health":
            self._send_json(200, {"ok": True})
        else:
            self._send_json(404, {"error": "接口不存在"})

    def _send_file(self, path: Path, ctype: str):
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            self._send_json(404, {"error": "文件不存在"})
            return
        self._send(200, data, ctype)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/convert":
            self._send_json(404, {"error": "接口不存在"})
            return
        length = int(self.headers.get("Content-Length", 0))
        if length > 60 * 1024 * 1024:
            self._send_json(413, {"error": "请求体过大"})
            return
        body = self.rfile.read(length)
        ctype = self.headers.get("Content-Type", "")
        try:
            fields, uploaded = parse_multipart(body, ctype)
            if not uploaded:
                raise ApiError("未收到文件,请选择音频或图片后重试。")
            filename, data = uploaded
            if not data:
                raise ApiError("上传文件为空。")
            result = handle_convert(fields, filename, data)
            self._send_json(200, result)
        except ApiError as e:
            self._send_json(e.status, {"error": e.message})
        except Exception as e:  # noqa: BLE001
            self._send_json(500, {"error": f"服务器处理失败:{e}"})


def main():
    import os

    env_port = os.environ.get("PORT")
    port = 8765
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    elif env_port:
        try:
            port = int(env_port)
        except ValueError:
            port = 8765

    # 云端平台会注入 PORT,此时需监听 0.0.0.0;本地默认只监听回环地址
    host = os.environ.get("HOST") or ("0.0.0.0" if env_port else "127.0.0.1")

    server = ThreadingHTTPServer((host, port), Handler)
    shown_host = "127.0.0.1" if host == "0.0.0.0" else host
    print("=" * 56)
    print("  歌曲 → 声波电动牙刷播放格式 转化程序")
    print(f"  监听地址: {host}:{port}")
    print(f"  本机访问: http://{shown_host}:{port}")
    print("  按 Ctrl+C 停止")
    print("=" * 56)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
