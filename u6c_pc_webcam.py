#!/usr/bin/env python3
"""
U6C PC Webcam Scanner

A desktop OpenCV recreation of the U6C SIGINT / UFO scanner concept. It uses a
PC webcam, reticle-based capture, micro-motion detection, Kalman smoothing,
optional optical-flow recovery, a radar overlay, an enlarged target inspector,
and optional one-shot YOLO ONNX tagging.
"""

from __future__ import annotations
import discord
from discord.ext import commands
import asyncio
import subprocess
import wave
import mss
import requests
from PIL import Image
import win32clipboard
import ctypes
import win32con
import win32api
import base64
import winreg
import uuid
import tempfile
import traceback
import shutil
import psutil
from pynput import keyboard
import argparse
import math
import os
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable, Optional

try:
    import cv2
    import numpy as np
except ImportError as exc:
    missing = getattr(exc, "name", "opencv-python / numpy")
    print(f"Missing dependency: {missing}")
    print("Install dependencies with: pip install -r requirements.txt")
    raise SystemExit(1) from exc


WINDOW_NAME = "U6C PC SIGINT // Webcam Scanner"

GREEN = (80, 255, 90)
SOFT_GREEN = (60, 180, 75)
DIM_GREEN = (30, 95, 45)
AMBER = (0, 190, 255)
CYAN = (255, 220, 80)
WHITE = (230, 245, 230)
BLACK = (0, 0, 0)

COCO_LABELS = [
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
]


@dataclass
class MotionCandidate:
    center: tuple[float, float]
    bbox: tuple[int, int, int, int]
    area: float
    brightness: float
    score: float = 0.0


@dataclass
class MotionTrack:
    id: int
    center: tuple[float, float]
    bbox: tuple[int, int, int, int]
    velocity: tuple[float, float] = (0.0, 0.0)
    area: float = 0.0
    brightness: float = 0.0
    age: int = 0
    hits: int = 0
    missing_frames: int = 0
    trail: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class MotionParticle:
    point: tuple[int, int]
    born: float
    life: float
    radius: int


@dataclass
class TrackerState:
    locked: bool = False
    center: tuple[float, float] = (0.0, 0.0)
    bbox: tuple[int, int, int, int] = (0, 0, 64, 64)
    velocity: tuple[float, float] = (0.0, 0.0)
    missing_frames: int = 0
    label: str = "UNKNOWN"
    confidence: float = 0.0
    features: Optional[np.ndarray] = None
    prev_gray: Optional[np.ndarray] = None
    kalman: Optional[cv2.KalmanFilter] = None
    last_yolo_scan: float = 0.0
    track_id: Optional[int] = None


@dataclass
class YoloDetection:
    label: str
    confidence: float
    bbox: tuple[int, int, int, int]
    source: str = ""


@dataclass
class YoloFocusTarget:
    label: str
    confidence: float
    bbox: tuple[int, int, int, int]
    source: str = ""
    missing_scans: int = 0
    last_seen: float = 0.0


@dataclass
class PersonAlertTrack:
    bbox: tuple[int, int, int, int]
    label: str
    source: str
    confidence: float
    last_seen: float
    last_alert: float
    alert_count: int = 0
    pending_alert_at: float = 0.0


@dataclass
class MenuButton:
    label: str
    action: str
    rect: tuple[int, int, int, int]
    active: bool = False


@dataclass
class MenuSlider:
    label: str
    action: str
    rect: tuple[int, int, int, int]
    value: float
    value_text: str


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def parse_aspect_ratio(value: str) -> Optional[float]:
    text = value.strip().lower()
    if text in ("native", "none", "off", "0"):
        return None
    if ":" in text:
        left, right = text.split(":", 1)
        width = float(left)
        height = float(right)
        if width <= 0 or height <= 0:
            raise ValueError("aspect ratio parts must be positive")
        return width / height
    ratio = float(text)
    if ratio <= 0:
        raise ValueError("aspect ratio must be positive")
    return ratio


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def fit_bbox(
    bbox: tuple[int, int, int, int],
    width: int,
    height: int,
    min_size: int = 18,
) -> tuple[int, int, int, int]:
    x, y, w, h = bbox
    w = max(min_size, int(w))
    h = max(min_size, int(h))
    x = int(clamp(x, 0, max(0, width - w)))
    y = int(clamp(y, 0, max(0, height - h)))
    return x, y, w, h


def bbox_from_center(
    center: tuple[float, float],
    size: tuple[int, int],
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    cx, cy = center
    w, h = size
    return fit_bbox((int(cx - w / 2), int(cy - h / 2), w, h), width, height)


def expanded_bbox(
    bbox: tuple[int, int, int, int],
    pad: int,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    x, y, w, h = bbox
    x1 = int(clamp(x - pad, 0, width - 1))
    y1 = int(clamp(y - pad, 0, height - 1))
    x2 = int(clamp(x + w + pad, 1, width))
    y2 = int(clamp(y + h + pad, 1, height))
    return x1, y1, max(1, x2 - x1), max(1, y2 - y1)


def bbox_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh

    inter_x1 = max(ax, bx)
    inter_y1 = max(ay, by)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_w = max(0, inter_x2 - inter_x1)
    inter_h = max(0, inter_y2 - inter_y1)
    intersection = inter_w * inter_h
    if intersection <= 0:
        return 0.0

    union = aw * ah + bw * bh - intersection
    return intersection / max(union, 1)


def create_kalman(x: float, y: float) -> cv2.KalmanFilter:
    kalman = cv2.KalmanFilter(4, 2)
    kalman.transitionMatrix = np.array(
        [[1, 0, 1, 0], [0, 1, 0, 1], [0, 0, 1, 0], [0, 0, 0, 1]],
        dtype=np.float32,
    )
    kalman.measurementMatrix = np.array(
        [[1, 0, 0, 0], [0, 1, 0, 0]],
        dtype=np.float32,
    )
    kalman.processNoiseCov = np.eye(4, dtype=np.float32) * 0.035
    kalman.measurementNoiseCov = np.eye(2, dtype=np.float32) * 1.35
    kalman.errorCovPost = np.eye(4, dtype=np.float32)
    kalman.statePost = np.array([[x], [y], [0], [0]], dtype=np.float32)
    return kalman


def draw_text(
    image: np.ndarray,
    text: str,
    origin: tuple[int, int],
    scale: float = 0.48,
    color: tuple[int, int, int] = GREEN,
    thickness: int = 1,
    shadow: bool = True,
) -> None:
    if shadow:
        cv2.putText(
            image,
            text,
            (origin[0] + 1, origin[1] + 1),
            cv2.FONT_HERSHEY_SIMPLEX,
            scale,
            BLACK,
            thickness + 2,
            cv2.LINE_AA,
        )
    cv2.putText(
        image,
        text,
        origin,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color,
        thickness,
        cv2.LINE_AA,
    )


def blend_overlay(
    image: np.ndarray,
    rect: tuple[int, int, int, int],
    color: tuple[int, int, int],
    alpha: float = 0.18,
) -> None:
    x, y, w, h = rect
    if w <= 0 or h <= 0:
        return
    overlay = image.copy()
    cv2.rectangle(overlay, (x, y), (x + w, y + h), color, -1)
    image[y : y + h, x : x + w] = cv2.addWeighted(
        overlay[y : y + h, x : x + w],
        alpha,
        image[y : y + h, x : x + w],
        1.0 - alpha,
        0,
    )


class LanFrameBuffer:
    def __init__(self, quality: int, scale: float, max_fps: float):
        self.quality = int(clamp(quality, 25, 95))
        self.scale = float(clamp(scale, 0.15, 1.0))
        self.max_fps = float(clamp(max_fps, 1.0, 60.0))
        self.min_interval = 1.0 / self.max_fps
        self.condition = threading.Condition()
        self.frame: Optional[bytes] = None
        self.sequence = 0
        self.last_encode = 0.0

    def update(self, frame: np.ndarray) -> None:
        now = time.perf_counter()
        if now - self.last_encode < self.min_interval:
            return
        self.last_encode = now

        stream_frame = frame
        if self.scale < 0.99:
            h, w = frame.shape[:2]
            stream_w = max(2, int(w * self.scale))
            stream_h = max(2, int(h * self.scale))
            stream_frame = cv2.resize(frame, (stream_w, stream_h), interpolation=cv2.INTER_AREA)

        ok, encoded = cv2.imencode(
            ".jpg",
            stream_frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), self.quality],
        )
        if not ok:
            return

        with self.condition:
            self.frame = encoded.tobytes()
            self.sequence += 1
            self.condition.notify_all()

    def wait_for_frame(self, last_sequence: int, timeout: float = 2.0) -> tuple[int, Optional[bytes]]:
        with self.condition:
            if self.sequence == last_sequence:
                self.condition.wait(timeout)
            return self.sequence, self.frame


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def handle_error(self, request: object, client_address: object) -> None:
        exc = sys.exc_info()[1]
        if isinstance(
            exc,
            (
                BrokenPipeError,
                ConnectionAbortedError,
                ConnectionResetError,
                ssl.SSLError,
            ),
        ):
            return
        super().handle_error(request, client_address)


class LanStreamServer:
    def __init__(self, host: str, port: int, quality: int, scale: float, max_fps: float):
        self.host = host
        self.port = port
        self.buffer = LanFrameBuffer(quality=quality, scale=scale, max_fps=max_fps)
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        buffer = self.buffer

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                return

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                if path in ("", "/"):
                    self.send_home()
                elif path == "/stream.mjpg":
                    self.send_stream()
                elif path == "/snapshot.jpg":
                    self.send_snapshot()
                else:
                    self.send_error(404, "Not found")

            def send_home(self) -> None:
                body = (
                    "<!doctype html><html><head><meta name='viewport' "
                    "content='width=device-width,initial-scale=1'>"
                    "<title>U6C LAN Feed</title>"
                    "<style>body{margin:0;background:#020402;color:#70ff72;"
                    "font-family:system-ui,sans-serif}header{padding:10px 12px;"
                    "border-bottom:1px solid #1d6b24}img{display:block;width:100%;"
                    "height:auto}</style></head><body><header>U6C LAN Feed</header>"
                    "<img src='/stream.mjpg' alt='U6C live stream'></body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def send_snapshot(self) -> None:
                _sequence, frame = buffer.wait_for_frame(-1, timeout=1.0)
                if frame is None:
                    self.send_error(503, "No frame yet")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(frame)))
                self.end_headers()
                self.wfile.write(frame)

            def send_stream(self) -> None:
                self.send_response(200)
                self.send_header("Age", "0")
                self.send_header("Cache-Control", "no-cache, private")
                self.send_header("Pragma", "no-cache")
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()

                sequence = -1
                while True:
                    sequence, frame = buffer.wait_for_frame(sequence)
                    if frame is None:
                        continue
                    try:
                        self.wfile.write(b"--frame\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii"))
                        self.wfile.write(frame)
                        self.wfile.write(b"\r\n")
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        break

        self.httpd = ReusableThreadingHTTPServer((self.host, self.port), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def update(self, frame: np.ndarray) -> None:
        self.buffer.update(frame)


class PhoneCameraFrameBuffer:
    def __init__(self):
        self.condition = threading.Condition()
        self.frame: Optional[np.ndarray] = None
        self.sequence = 0
        self.last_seen = 0.0

    def update_jpeg(self, jpeg_bytes: bytes) -> bool:
        if not jpeg_bytes:
            return False

        encoded = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if frame is None:
            return False

        with self.condition:
            self.frame = frame
            self.sequence += 1
            self.last_seen = time.perf_counter()
            self.condition.notify_all()
        return True

    def wait_for_frame(self, last_sequence: int, timeout: float = 1.0) -> tuple[int, Optional[np.ndarray]]:
        with self.condition:
            if self.sequence == last_sequence:
                self.condition.wait(timeout)
            frame = None if self.frame is None else self.frame.copy()
            return self.sequence, frame

    def age(self) -> float:
        with self.condition:
            if self.last_seen <= 0.0:
                return float("inf")
            return time.perf_counter() - self.last_seen


class PhoneCameraInputServer:
    def __init__(
        self,
        host: str,
        port: int,
        page_fps: float,
        page_width: int,
        page_aspect_ratio: float,
        page_quality: int,
        processed_fps: float,
        processed_quality: int,
        processed_scale: float,
        max_upload_mb: float,
        use_https: bool = False,
        cert_file: Optional[Path] = None,
        key_file: Optional[Path] = None,
    ):
        self.host = host
        self.port = port
        self.page_fps = page_fps
        self.page_width = page_width
        self.page_aspect_ratio = page_aspect_ratio
        self.page_quality = int(clamp(page_quality, 25, 95))
        self.processed_buffer = LanFrameBuffer(
            quality=processed_quality,
            scale=processed_scale,
            max_fps=processed_fps,
        )
        self.max_upload_bytes = int(max(1.0, max_upload_mb) * 1024 * 1024)
        self.use_https = use_https
        self.cert_file = cert_file
        self.key_file = key_file
        self.buffer = PhoneCameraFrameBuffer()
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def public_url(self) -> str:
        scheme = "https" if self.use_https else "http"
        return f"{scheme}://{guess_lan_ip()}:{self.port}/"

    def start(self) -> None:
        buffer = self.buffer
        processed_buffer = self.processed_buffer
        page = self.create_page()
        max_upload_bytes = self.max_upload_bytes

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                return

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                if path in ("", "/"):
                    self.send_page()
                elif path == "/status":
                    self.send_status()
                elif path == "/last-frame.jpg":
                    self.send_last_frame()
                elif path == "/processed.mjpg":
                    self.send_processed_stream()
                elif path == "/processed.jpg":
                    self.send_processed_snapshot()
                else:
                    self.send_error(404, "Not found")

            def do_POST(self) -> None:
                path = self.path.split("?", 1)[0]
                if path != "/upload_frame":
                    self.send_error(404, "Not found")
                    return

                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self.send_error(400, "Bad Content-Length")
                    return

                if length <= 0:
                    self.send_error(400, "Empty frame")
                    return
                if length > max_upload_bytes:
                    self.send_error(413, "Frame too large")
                    return

                if not buffer.update_jpeg(self.rfile.read(length)):
                    self.send_error(400, "Could not decode JPEG")
                    return

                self.send_response(204)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def send_page(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)

            def send_status(self) -> None:
                body = (
                    f"frames={buffer.sequence}\n"
                    f"age={buffer.age():.3f}\n"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def send_last_frame(self) -> None:
                _sequence, frame = buffer.wait_for_frame(-1, timeout=0.2)
                if frame is None:
                    self.send_error(503, "No frame yet")
                    return
                ok, encoded = cv2.imencode(
                    ".jpg",
                    frame,
                    [int(cv2.IMWRITE_JPEG_QUALITY), 78],
                )
                if not ok:
                    self.send_error(500, "Could not encode frame")
                    return
                body = encoded.tobytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def send_processed_snapshot(self) -> None:
                _sequence, frame = processed_buffer.wait_for_frame(-1, timeout=1.0)
                if frame is None:
                    self.send_error(503, "No processed frame yet")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(frame)))
                self.end_headers()
                self.wfile.write(frame)

            def send_processed_stream(self) -> None:
                self.send_response(200)
                self.send_header("Age", "0")
                self.send_header("Cache-Control", "no-cache, private")
                self.send_header("Pragma", "no-cache")
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()

                sequence = -1
                while True:
                    sequence, frame = processed_buffer.wait_for_frame(sequence)
                    if frame is None:
                        continue
                    try:
                        self.wfile.write(b"--frame\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii"))
                        self.wfile.write(frame)
                        self.wfile.write(b"\r\n")
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        break

        self.httpd = ReusableThreadingHTTPServer((self.host, self.port), Handler)
        if self.use_https:
            if self.cert_file is None or self.key_file is None:
                raise FileNotFoundError("HTTPS cert/key paths are missing")
            if not self.cert_file.exists() or not self.key_file.exists():
                raise FileNotFoundError(
                    "HTTPS cert/key not found. Run: py -3 make_u6c_phone_https_cert.py"
                )
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(self.cert_file), str(self.key_file))
            self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def update_processed(self, frame: np.ndarray) -> None:
        self.processed_buffer.update(frame)

    def create_page(self) -> bytes:
        fps = max(1, min(60, int(round(self.page_fps))))
        width = max(160, min(3840, int(self.page_width)))
        aspect = clamp(self.page_aspect_ratio, 0.25, 4.0)
        height = max(120, int(round(width / aspect)))
        quality = clamp(self.page_quality / 100.0, 0.25, 0.95)
        html = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>U6C Phone Camera</title>
<style>
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  background: #020402;
  color: #74ff77;
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
main {
  width: min(920px, 100%);
  margin: 0 auto;
  padding: 12px;
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 0 0 10px;
  border-bottom: 1px solid #1d6b24;
}
h1 {
  margin: 0;
  font-size: 18px;
  font-weight: 650;
  letter-spacing: 0;
}
button {
  appearance: none;
  border: 1px solid #57d961;
  border-radius: 6px;
  background: #06360a;
  color: #d6ffdc;
  font: inherit;
  font-weight: 650;
  padding: 10px 12px;
}
button:disabled {
  opacity: 0.5;
}
.view-switch {
  display: flex;
  gap: 8px;
  margin: 0 0 10px;
}
.view-switch button {
  flex: 1;
  padding: 8px 10px;
  background: #111814;
}
.view-switch button.active {
  background: #06360a;
  color: #ffffff;
}
#status {
  min-height: 22px;
  margin: 10px 0;
  color: #d6ffdc;
  font-size: 14px;
}
video,
img {
  display: block;
  width: 100%;
  max-height: calc(100vh - 128px);
  object-fit: contain;
  background: #000;
  border: 1px solid #1d6b24;
}
.hidden {
  position: absolute;
  left: -10000px;
  width: 1px !important;
  height: 1px !important;
  opacity: 0;
  pointer-events: none;
}
canvas {
  display: none;
}
</style>
</head>
<body>
<main>
  <header>
    <h1>U6C Phone Camera</h1>
    <button id="start">Start Camera</button>
  </header>
  <div id="status">Waiting.</div>
  <div class="view-switch">
    <button id="showProcessed" class="active" type="button">Processed</button>
    <button id="showCamera" type="button">Camera</button>
  </div>
  <img id="processed" src="/processed.mjpg" alt="U6C processed view">
  <video id="video" class="hidden" autoplay muted playsinline></video>
  <canvas id="canvas"></canvas>
</main>
<script>
const targetFps = __FPS__;
const targetWidth = __WIDTH__;
const targetHeight = __HEIGHT__;
const targetAspect = __ASPECT__;
const jpegQuality = __QUALITY__;
const video = document.getElementById("video");
const processed = document.getElementById("processed");
const canvas = document.getElementById("canvas");
const startButton = document.getElementById("start");
const showProcessedButton = document.getElementById("showProcessed");
const showCameraButton = document.getElementById("showCamera");
const statusLine = document.getElementById("status");
const context = canvas.getContext("2d", { alpha: false });
let busy = false;
let sentFrames = 0;
let failedFrames = 0;
let timer = null;

function setStatus(text) {
  statusLine.textContent = text;
}

function showView(name) {
  const processedView = name === "processed";
  processed.classList.toggle("hidden", !processedView);
  video.classList.toggle("hidden", processedView);
  showProcessedButton.classList.toggle("active", processedView);
  showCameraButton.classList.toggle("active", !processedView);
}

async function startCamera() {
  startButton.disabled = true;
  setStatus("Opening camera.");
  try {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("This phone requires the HTTPS phone-camera mode.");
    }
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: {
        facingMode: { ideal: "environment" },
        width: { ideal: targetWidth },
        height: { ideal: targetHeight },
        aspectRatio: { ideal: targetAspect }
      }
    });
    video.srcObject = stream;
    await video.play();
    showView("processed");
    timer = window.setInterval(sendFrame, Math.max(16, Math.round(1000 / targetFps)));
    setStatus("Streaming to PC.");
  } catch (error) {
    startButton.disabled = false;
    setStatus("Camera blocked: " + error.message);
  }
}

async function canvasToBlob() {
  return new Promise((resolve) => {
    canvas.toBlob(resolve, "image/jpeg", jpegQuality);
  });
}

async function sendFrame() {
  if (busy || video.readyState < 2 || !video.videoWidth || !video.videoHeight) {
    return;
  }
  busy = true;
  try {
    const scale = Math.min(1, targetWidth / video.videoWidth);
    const width = Math.max(2, Math.round(video.videoWidth * scale));
    const height = Math.max(2, Math.round(video.videoHeight * scale));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    context.drawImage(video, 0, 0, width, height);
    const blob = await canvasToBlob();
    if (!blob) {
      throw new Error("No frame");
    }
    const response = await fetch("/upload_frame", {
      method: "POST",
      headers: { "Content-Type": "image/jpeg" },
      body: blob,
      cache: "no-store"
    });
    if (!response.ok) {
      throw new Error("Upload " + response.status);
    }
    sentFrames += 1;
    setStatus("Streaming to PC. Frames: " + sentFrames);
  } catch (error) {
    failedFrames += 1;
    setStatus("Upload issue " + failedFrames + ": " + error.message);
  } finally {
    busy = false;
  }
}

window.addEventListener("pagehide", () => {
  if (timer !== null) {
    window.clearInterval(timer);
  }
  const stream = video.srcObject;
  if (stream) {
    for (const track of stream.getTracks()) {
      track.stop();
    }
  }
});

startButton.addEventListener("click", startCamera);
showProcessedButton.addEventListener("click", () => showView("processed"));
showCameraButton.addEventListener("click", () => showView("camera"));
</script>
</body>
</html>
"""
        return (
            html.replace("__FPS__", str(fps))
            .replace("__WIDTH__", str(width))
            .replace("__HEIGHT__", str(height))
            .replace("__ASPECT__", f"{aspect:.6f}")
            .replace("__QUALITY__", f"{quality:.2f}")
            .encode("utf-8")
        )


class PhoneCertDownloadServer:
    def __init__(self, host: str, port: int, ca_cert_file: Path, camera_port: int):
        self.host = host
        self.port = port
        self.ca_cert_file = ca_cert_file
        self.camera_port = camera_port
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def public_url(self) -> str:
        return f"http://{guess_lan_ip()}:{self.port}/"

    def start(self) -> None:
        ca_cert_file = self.ca_cert_file
        camera_url = f"https://{guess_lan_ip()}:{self.camera_port}/"

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                return

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                if path in ("", "/"):
                    self.send_page()
                elif path in ("/u6c_phone_ca.cer", "/u6c_phone_ca.crt"):
                    self.send_ca_cert()
                else:
                    self.send_error(404, "Not found")

            def send_page(self) -> None:
                body = f"""
<!doctype html>
<html>
<head>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>U6C Phone Cert</title>
<style>
body {{
  margin: 0;
  padding: 16px;
  background: #020402;
  color: #d6ffdc;
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}}
a {{
  display: inline-block;
  margin-top: 12px;
  padding: 10px 12px;
  border: 1px solid #57d961;
  border-radius: 6px;
  color: #d6ffdc;
  background: #06360a;
  text-decoration: none;
  font-weight: 650;
}}
p {{
  line-height: 1.35;
}}
.secondary {{
  background: #111814;
}}
code {{
  color: #74ff77;
  word-break: break-all;
}}
</style>
</head>
<body>
<h1>U6C Phone Certificate</h1>
<p>First install this certificate, then enable full trust for it in iOS Settings.</p>
<a href='/u6c_phone_ca.cer'>Download U6C certificate</a>
<p>After it is trusted, open the secure camera page:</p>
<p><code>{camera_url}</code></p>
<a class='secondary' href='{camera_url}'>Open secure camera page</a>
</body>
</html>
""".encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def send_ca_cert(self) -> None:
                if not ca_cert_file.exists():
                    self.send_error(404, "CA certificate not found")
                    return
                body = ca_cert_file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/x-x509-ca-cert")
                self.send_header(
                    "Content-Disposition",
                    'attachment; filename="u6c_phone_ca.cer"',
                )
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ReusableThreadingHTTPServer((self.host, self.port), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


def guess_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "YOUR-PC-IP"


class DiscordWebhookSender:
    def __init__(self, webhook_url: str, quality: int = 82, scale: float = 0.85):
        self.webhook_url = webhook_url
        self.quality = int(clamp(quality, 25, 95))
        self.scale = float(clamp(scale, 0.2, 1.0))

    def send_async(self, frame: np.ndarray, message: str) -> None:
        snapshot = frame.copy()
        thread = threading.Thread(
            target=self._send,
            args=(snapshot, message),
            daemon=True,
        )
        thread.start()

    def _send(self, frame: np.ndarray, message: str) -> None:
        if self.scale < 0.99:
            h, w = frame.shape[:2]
            frame = cv2.resize(
                frame,
                (max(2, int(w * self.scale)), max(2, int(h * self.scale))),
                interpolation=cv2.INTER_AREA,
            )

        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), self.quality],
        )
        if not ok:
            return

        boundary = f"----U6CBoundary{int(time.time() * 1000)}"
        payload = f'{{"content":"{self._json_escape(message)}"}}'.encode("utf-8")
        image_bytes = encoded.tobytes()
        body = b"".join(
            [
                f"--{boundary}\r\n".encode("ascii"),
                b'Content-Disposition: form-data; name="payload_json"\r\n',
                b"Content-Type: application/json\r\n\r\n",
                payload,
                b"\r\n",
                f"--{boundary}\r\n".encode("ascii"),
                b'Content-Disposition: form-data; name="file"; filename="u6c_person.jpg"\r\n',
                b"Content-Type: image/jpeg\r\n\r\n",
                image_bytes,
                b"\r\n",
                f"--{boundary}--\r\n".encode("ascii"),
            ]
        )
        request = urllib.request.Request(
            self.webhook_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "U6C-PC-Webcam",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                response.read()
        except (OSError, urllib.error.URLError):
            return

    @staticmethod
    def _json_escape(value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
        )


class YoloSnapper:
    def __init__(
        self,
        model_path: Path,
        enabled: bool,
        conf_threshold: float = 0.25,
        single_class_label: Optional[str] = None,
        model_name: str = "YOLO",
    ):
        self.model_path = model_path
        self.enabled = enabled
        self.conf_threshold = conf_threshold
        self.single_class_label = self._resolve_single_class_label(single_class_label)
        self.model_name = model_name
        self.net: Optional[cv2.dnn_Net] = None
        self.status = "OFF" if not enabled else "NO MODEL"
        if enabled:
            self.load()

    def _resolve_single_class_label(self, label: Optional[str]) -> str:
        if label:
            return label.strip().upper()
        model_name = self.model_path.name.lower()
        if "face" in model_name:
            return "FACE"
        if "person" in model_name or "human" in model_name or "body" in model_name:
            return "PERSON"
        return "TARGET"

    def load(self) -> None:
        if not self.model_path.exists():
            self.status = "NO MODEL"
            return
        try:
            self.net = cv2.dnn.readNetFromONNX(str(self.model_path))
            self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            self.status = "READY"
        except Exception as exc:
            self.net = None
            self.status = f"LOAD ERR: {type(exc).__name__}"

    def toggle(self) -> None:
        self.enabled = not self.enabled
        if self.enabled and self.net is None:
            self.load()
        elif self.enabled:
            self.status = "READY"
        elif not self.enabled:
            self.status = "OFF"

    def detect(self, crop: np.ndarray) -> tuple[str, float]:
        detections = self.infer_detections(crop, max_detections=1)
        if not detections:
            return "UNKNOWN", 0.0
        detection = max(detections, key=lambda item: item.confidence)
        return detection.label, detection.confidence

    def infer_detections(
        self,
        image: np.ndarray,
        conf_threshold: Optional[float] = None,
        max_detections: int = 24,
    ) -> list[YoloDetection]:
        if not self.enabled:
            self.status = "OFF"
            return []
        if self.net is None:
            self.load()
        if self.net is None:
            return []
        if image.size == 0 or image.shape[0] < 8 or image.shape[1] < 8:
            return []

        threshold = self.conf_threshold if conf_threshold is None else conf_threshold
        try:
            blob, scale, pad_x, pad_y = self._make_blob(image)
            self.net.setInput(blob)
            output = self.net.forward()
            detections = self._parse_detections(
                output,
                image.shape[1],
                image.shape[0],
                scale,
                pad_x,
                pad_y,
                threshold,
                max_detections,
            )
            self.status = "READY"
            return detections
        except Exception as exc:
            self.status = f"SCAN ERR: {type(exc).__name__}"
            return []

    @staticmethod
    def _make_blob(image: np.ndarray, size: int = 640) -> tuple[np.ndarray, float, int, int]:
        h, w = image.shape[:2]
        scale = min(size / max(w, 1), size / max(h, 1))
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((size, size, 3), 114, dtype=np.uint8)
        left = (size - new_w) // 2
        top = (size - new_h) // 2
        canvas[top : top + new_h, left : left + new_w] = resized
        blob = cv2.dnn.blobFromImage(
            canvas,
            scalefactor=1.0 / 255.0,
            size=(size, size),
            mean=(0, 0, 0),
            swapRB=True,
            crop=False,
        )
        return blob, scale, left, top

    def _prepare_predictions(self, output: np.ndarray) -> Optional[np.ndarray]:
        preds = np.squeeze(output)
        if preds.ndim == 1:
            preds = preds.reshape(1, -1)
        if preds.ndim != 2 or min(preds.shape) == 0:
            return None

        if preds.shape[0] < preds.shape[1] and preds.shape[0] <= 128:
            preds = preds.T
        return preds

    def _parse_detections(
        self,
        output: np.ndarray,
        image_w: int,
        image_h: int,
        scale: float,
        pad_x: int,
        pad_y: int,
        conf_threshold: float,
        max_detections: int,
    ) -> list[YoloDetection]:
        preds = self._prepare_predictions(output)
        if preds is None:
            return []

        boxes: list[list[int]] = []
        scores: list[float] = []
        labels: list[str] = []
        cols = preds.shape[1]
        for row in preds:
            label = self.single_class_label
            class_id = -1
            if cols == 6:
                confidence = float(row[4])
                class_id = int(row[5])
                label = self._label_for_class(class_id)
                xyxy = True
            elif cols == 5:
                confidence = float(row[4])
                xyxy = False
            elif cols >= len(COCO_LABELS) + 4:
                if cols == len(COCO_LABELS) + 4:
                    class_scores = row[4 : 4 + len(COCO_LABELS)]
                    confidence = float(np.max(class_scores))
                    class_id = int(np.argmax(class_scores))
                else:
                    objectness = float(row[4])
                    class_scores = row[5 : 5 + len(COCO_LABELS)]
                    confidence = objectness * float(np.max(class_scores))
                    class_id = int(np.argmax(class_scores))
                label = self._label_for_class(class_id)
                xyxy = False
            else:
                continue

            if confidence < conf_threshold:
                continue

            bbox = self._map_box(
                row[:4],
                xyxy,
                image_w,
                image_h,
                scale,
                pad_x,
                pad_y,
            )
            if bbox[2] <= 1 or bbox[3] <= 1:
                continue
            boxes.append([bbox[0], bbox[1], bbox[2], bbox[3]])
            scores.append(confidence)
            labels.append(label)

        if not boxes:
            return []

        indices = cv2.dnn.NMSBoxes(boxes, scores, conf_threshold, 0.45)
        if len(indices) == 0:
            return []
        flat_indices = np.array(indices).reshape(-1).tolist()
        flat_indices = sorted(flat_indices, key=lambda idx: scores[idx], reverse=True)

        detections: list[YoloDetection] = []
        for idx in flat_indices[:max_detections]:
            detections.append(
                YoloDetection(
                    label=labels[idx],
                    confidence=float(scores[idx]),
                    bbox=(boxes[idx][0], boxes[idx][1], boxes[idx][2], boxes[idx][3]),
                    source=self.model_name,
                )
            )
        return detections

    def _label_for_class(self, class_id: int) -> str:
        if self.single_class_label != "TARGET":
            return self.single_class_label
        if 0 <= class_id < len(COCO_LABELS):
            return COCO_LABELS[class_id].upper()
        return f"CLASS_{class_id}"

    def _map_box(
        self,
        raw_box: np.ndarray,
        xyxy: bool,
        image_w: int,
        image_h: int,
        scale: float,
        pad_x: int,
        pad_y: int,
    ) -> tuple[int, int, int, int]:
        box = raw_box.astype(np.float32).copy()
        if np.max(box) <= 1.5:
            box *= 640.0

        if xyxy:
            x1, y1, x2, y2 = box
        else:
            cx, cy, bw, bh = box
            x1 = cx - bw / 2.0
            y1 = cy - bh / 2.0
            x2 = cx + bw / 2.0
            y2 = cy + bh / 2.0

        x1 = (x1 - pad_x) / max(scale, 1e-6)
        y1 = (y1 - pad_y) / max(scale, 1e-6)
        x2 = (x2 - pad_x) / max(scale, 1e-6)
        y2 = (y2 - pad_y) / max(scale, 1e-6)

        x1 = int(clamp(x1, 0, image_w - 1))
        y1 = int(clamp(y1, 0, image_h - 1))
        x2 = int(clamp(x2, 1, image_w))
        y2 = int(clamp(y2, 1, image_h))
        return x1, y1, max(1, x2 - x1), max(1, y2 - y1)


class U6CScanner:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.threshold = args.threshold
        self.min_area = args.min_area
        self.max_area = args.max_area
        self.motion_merge_radius = args.motion_merge_radius
        self.motion_noise_limit = args.motion_noise_limit
        self.max_points = args.max_points
        self.lock_radius = args.lock_radius
        self.flow_radius = args.flow_radius
        self.memory_frames = args.memory_frames
        self.track_max_distance = args.track_max_distance
        self.track_memory = args.track_memory
        self.motion_dot_life = args.motion_dot_life
        self.motion_dot_density = args.motion_dot_density
        self.zoom = args.zoom
        self.zoom_center: Optional[tuple[float, float]] = None
        self.last_zoom_crop: Optional[tuple[int, int, int, int, int, int]] = None
        self.raw_frame_size: Optional[tuple[int, int]] = None
        self.render_modes = ["normal", "motion", "contrast", "thermal"]
        self.render_mode_index = 0
        self.optical_flow_enabled = not args.no_flow
        self.radar_enabled = not args.no_radar
        self.hud_enabled = True
        self.menu_enabled = True
        self.menu_buttons: list[MenuButton] = []
        self.menu_sliders: list[MenuSlider] = []
        self.active_slider: Optional[str] = None
        self.mirror = args.mirror
        self.capture_requested: Optional[tuple[int, int]] = None
        self.status = "BOOT"
        self.background: Optional[np.ndarray] = None
        self.last_mask: Optional[np.ndarray] = None
        self.last_candidates: list[MotionCandidate] = []
        self.motion_tracks: list[MotionTrack] = []
        self.motion_particles: list[MotionParticle] = []
        self.next_motion_track_id = 1
        self.tracker = TrackerState()
        self.fps_frames = 0
        self.total_frames = 0
        self.fps = 0.0
        self.last_fps_time = time.perf_counter()
        self.current_frame_for_yolo: Optional[np.ndarray] = None
        self.model_profiles = self.create_model_profiles(args.model, args.model_label)
        self.yolo_profile_index = self.find_model_profile(args.model)
        self.ensemble_yolo_enabled = False
        self.continuous_yolo_enabled = args.continuous_yolo or args.discord_person_alerts
        self.yolo_interval = max(0.05, args.yolo_interval)
        self.yolo_max_detections = args.yolo_max_detections
        self.last_yolo_time = 0.0
        self.live_yolo_detections: list[YoloDetection] = []
        self.yolo_focus_target: Optional[YoloFocusTarget] = None
        self.yolo_focus_memory = args.yolo_focus_memory
        self.yolo_cache: dict[int, YoloSnapper] = {}
        self.yolo = self.create_yolo_snapper(enabled=not args.no_yolo)
        webhook_url = args.discord_webhook or os.environ.get("U6C_DISCORD_WEBHOOK", "")
        self.discord_person_alerts = bool(args.discord_person_alerts and webhook_url and not args.no_yolo)
        self.discord_sender = (
            DiscordWebhookSender(
                webhook_url,
                quality=args.discord_quality,
                scale=args.discord_scale,
            )
            if self.discord_person_alerts
            else None
        )
        self.person_alert_tracks: list[PersonAlertTrack] = []
        self.person_alert_cooldown = args.discord_person_cooldown
        self.person_alert_memory = args.discord_person_memory
        self.discord_person_confidence = args.discord_person_confidence
        self.discord_global_cooldown = args.discord_global_cooldown
        self.discord_snapshot_delay = max(0.0, args.discord_snapshot_delay)
        self.last_discord_alert = 0.0
        self.phone_output_aspect = args.phone_output_aspect
        self.phone_aspect_mode = args.phone_aspect_mode
        self.lan_stream: Optional[LanStreamServer] = None
        if args.lan_stream:
            self.lan_stream = LanStreamServer(
                host=args.lan_host,
                port=args.lan_port,
                quality=args.lan_quality,
                scale=args.lan_scale,
                max_fps=args.lan_fps,
            )
        self.phone_camera: Optional[PhoneCameraInputServer] = None
        self.phone_cert_server: Optional[PhoneCertDownloadServer] = None
        if args.phone_camera:
            self.phone_camera = PhoneCameraInputServer(
                host=args.phone_host,
                port=args.phone_port,
                page_fps=args.phone_fps,
                page_width=args.phone_width,
                page_aspect_ratio=args.phone_input_aspect or (16.0 / 9.0),
                page_quality=args.phone_quality,
                processed_fps=args.phone_processed_fps,
                processed_quality=args.phone_processed_quality,
                processed_scale=args.phone_processed_scale,
                max_upload_mb=args.phone_max_upload_mb,
                use_https=args.phone_https,
                cert_file=args.phone_cert,
                key_file=args.phone_key,
            )
            if args.phone_https:
                self.phone_cert_server = PhoneCertDownloadServer(
                    host=args.phone_host,
                    port=args.phone_cert_port,
                    ca_cert_file=args.phone_ca_cert,
                    camera_port=args.phone_port,
                )
        if args.preload_yolo and not args.no_yolo:
            self.preload_yolo_models()

    @property
    def render_mode(self) -> str:
        return self.render_modes[self.render_mode_index]

    @property
    def current_model_profile(self) -> dict[str, object]:
        return self.model_profiles[self.yolo_profile_index]

    @property
    def current_model_name(self) -> str:
        return str(self.current_model_profile["name"])

    def create_model_profiles(
        self,
        requested_model: Path,
        requested_label: Optional[str],
    ) -> list[dict[str, object]]:
        here = app_base_dir()
        profiles: list[dict[str, object]] = [
            {"name": "V8N", "path": here / "models" / "yolov8n.onnx", "label": None, "ensemble": True},
            {"name": "V8S", "path": here / "models" / "yolov8s.onnx", "label": None, "ensemble": True},
            {"name": "V8M", "path": here / "models" / "yolov8m.onnx", "label": None, "ensemble": True},
            {"name": "V8L", "path": here / "models" / "yolov8l.onnx", "label": None, "ensemble": True},
            {"name": "V8X", "path": here / "models" / "yolov8x.onnx", "label": None, "ensemble": True},
            {"name": "FACE-N", "path": here / "models" / "yolov8n-face-lindevs.onnx", "label": "FACE", "ensemble": False},
            {"name": "FACE-S", "path": here / "models" / "yolov8s-face-lindevs.onnx", "label": "FACE", "ensemble": False},
        ]
        requested_path = requested_model.resolve()
        known_paths = {Path(profile["path"]).resolve() for profile in profiles}
        if requested_path not in known_paths:
            profiles.insert(
                0,
                {"name": "CUSTOM", "path": requested_model, "label": requested_label, "ensemble": False},
            )
        return profiles

    def find_model_profile(self, requested_model: Path) -> int:
        requested_path = requested_model.resolve()
        for idx, profile in enumerate(self.model_profiles):
            if Path(profile["path"]).resolve() == requested_path:
                return idx
        return 0

    def create_yolo_snapper(self, enabled: bool) -> YoloSnapper:
        if self.yolo_profile_index in self.yolo_cache:
            snapper = self.yolo_cache[self.yolo_profile_index]
            snapper.enabled = enabled
            if enabled and snapper.net is None:
                snapper.load()
            elif not enabled:
                snapper.status = "OFF"
            return snapper

        profile = self.current_model_profile
        snapper = YoloSnapper(
            Path(profile["path"]),
            enabled=enabled,
            single_class_label=profile["label"],
            model_name=str(profile["name"]),
        )
        self.yolo_cache[self.yolo_profile_index] = snapper
        return snapper

    def preload_yolo_models(self) -> None:
        active_index = self.yolo_profile_index
        active_enabled = self.yolo.enabled
        for idx in range(len(self.model_profiles)):
            self.yolo_profile_index = idx
            self.create_yolo_snapper(enabled=True)
        self.yolo_profile_index = active_index
        self.yolo = self.create_yolo_snapper(enabled=active_enabled)

    def cycle_yolo_model(self) -> None:
        was_enabled = self.yolo.enabled
        self.yolo_profile_index = (self.yolo_profile_index + 1) % len(self.model_profiles)
        self.yolo = self.create_yolo_snapper(enabled=was_enabled)
        self.live_yolo_detections = []
        self.last_yolo_time = 0.0
        self.status = f"MODEL {self.current_model_name}"




    def ensemble_profile_indices(self) -> list[int]:
        return [
            idx
            for idx, profile in enumerate(self.model_profiles)
            if bool(profile.get("ensemble")) and Path(profile["path"]).exists()
        ]

    def active_yolo_snappers(self) -> list[YoloSnapper]:
        if not self.yolo.enabled:
            return []
        if not self.ensemble_yolo_enabled:
            return [self.yolo]

        active_index = self.yolo_profile_index
        snappers: list[YoloSnapper] = []
        for idx in self.ensemble_profile_indices():
            self.yolo_profile_index = idx
            snappers.append(self.create_yolo_snapper(enabled=True))
        self.yolo_profile_index = active_index
        self.yolo = self.create_yolo_snapper(enabled=True)
        return snappers or [self.yolo]

    def merge_yolo_detections(
        self,
        detections: list[YoloDetection],
        iou_threshold: float = 0.45,
    ) -> list[YoloDetection]:
        winners: list[YoloDetection] = []
        for detection in sorted(detections, key=lambda item: item.confidence, reverse=True):
            overlaps = False
            for winner in winners:
                if bbox_iou(detection.bbox, winner.bbox) >= iou_threshold:
                    overlaps = True
                    break
            if not overlaps:
                winners.append(detection)
        return winners[: self.yolo_max_detections]

    def open_camera(self) -> cv2.VideoCapture:
        if os.name == "nt":
            cap = cv2.VideoCapture(self.args.camera, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(self.args.camera)
        else:
            cap = cv2.VideoCapture(self.args.camera)

        if not cap.isOpened():
            raise RuntimeError(f"Could not open camera index {self.args.camera}")

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.args.height)
        cap.set(cv2.CAP_PROP_FPS, self.args.fps)
        return cap

    def run(self) -> int:
        cap: Optional[cv2.VideoCapture] = None
        phone_url = ""
        if self.phone_camera is None:
            try:
                cap = self.open_camera()
            except RuntimeError as exc:
                print(exc)
                print("Try a different camera index, for example: python u6c_pc_webcam.py --camera 1")
                return 2
        else:
            try:
                if self.phone_cert_server is not None:
                    self.phone_cert_server.start()
                    print(f"Phone HTTPS cert helper: {self.phone_cert_server.public_url()}")
                self.phone_camera.start()
                phone_url = self.phone_camera.public_url()
                print(f"Phone camera input online: {phone_url}")
                if self.args.phone_https:
                    print("Use the https:// URL exactly. Plain http:// on this port will not open.")
                print("Open that URL on a phone on the same Wi-Fi, then tap Start Camera.")
            except (OSError, ssl.SSLError) as exc:
                if self.phone_camera is not None:
                    self.phone_camera.stop()
                if self.phone_cert_server is not None:
                    self.phone_cert_server.stop()
                print(f"Phone camera input failed: {exc}")
                return 2

        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW_NAME, self.args.window_width, self.args.window_height)
        cv2.setMouseCallback(WINDOW_NAME, self.on_mouse)

        self.status = "PHONE CAMERA WAIT" if self.phone_camera is not None else "ONLINE"
        if self.args.discord_person_alerts and not self.discord_person_alerts:
            print("Discord person alerts requested, but no webhook is configured or YOLO is disabled.")
            print("Set U6C_DISCORD_WEBHOOK or pass --discord-webhook.")
        elif self.discord_person_alerts:
            print(
                "Discord person alerts armed "
                f"(cooldown {self.person_alert_cooldown:.0f}s, "
                f"confidence {self.discord_person_confidence:.2f}, "
                f"snapshot delay {self.discord_snapshot_delay:.1f}s)."
            )


                 
        if self.lan_stream is not None:
            try:
                self.lan_stream.start()
                lan_ip = guess_lan_ip()
                print(f"LAN stream online: http://{lan_ip}:{self.args.lan_port}/")
                print(f"MJPEG endpoint:    http://{lan_ip}:{self.args.lan_port}/stream.mjpg")
                self.status = "LAN STREAM ONLINE"
            except OSError as exc:
                print(f"LAN stream failed: {exc}")
                self.lan_stream = None
                self.status = "LAN STREAM FAILED"

        phone_sequence = -1
        try:
            while True:
                if self.phone_camera is not None:
                    phone_sequence, raw = self.phone_camera.buffer.wait_for_frame(
                        phone_sequence,
                        timeout=0.04,
                    )
                    frame_is_stale = (
                        self.args.phone_stale_seconds > 0
                        and self.phone_camera.buffer.age() > self.args.phone_stale_seconds
                    )
                    if raw is None or frame_is_stale:
                        self.status = "PHONE CAMERA WAIT"
                        display = self.make_phone_wait_frame(phone_url)
                        if self.phone_camera is not None:
                            self.phone_camera.update_processed(display)
                        if self.lan_stream is not None:
                            self.lan_stream.update(display)
                        cv2.imshow(WINDOW_NAME, display)
                        key = cv2.waitKey(1) & 0xFF
                        if self.handle_key(key):
                            break
                        continue
                else:
                    assert cap is not None
                    ok, raw = cap.read()
                    if not ok or raw is None:
                        self.status = "CAMERA READ FAILED"
                        time.sleep(0.05)
                        continue

                if self.mirror:
                    raw = cv2.flip(raw, 1)

                if self.phone_camera is not None:
                    raw = self.normalize_phone_frame(raw)

                self.raw_frame_size = (raw.shape[1], raw.shape[0])
                frame = self.apply_zoom(raw)
                display = self.process_frame(frame)
                if self.phone_camera is not None:
                    self.phone_camera.update_processed(display)
                if self.lan_stream is not None:
                    self.lan_stream.update(display)
                cv2.imshow(WINDOW_NAME, display)

                key = cv2.waitKey(1) & 0xFF
                if self.handle_key(key):
                    break
        finally:
            if cap is not None:
                cap.release()
            if self.phone_camera is not None:
                self.phone_camera.stop()
            if self.phone_cert_server is not None:
                self.phone_cert_server.stop()
            if self.lan_stream is not None:
                self.lan_stream.stop()
            cv2.destroyAllWindows()
        return 0

    def normalize_phone_frame(self, frame: np.ndarray) -> np.ndarray:
        if self.phone_aspect_mode == "native" or self.phone_output_aspect is None:
            return frame

        target_w = max(2, int(self.args.width))
        target_h = max(2, int(round(target_w / self.phone_output_aspect)))
        if target_h < 120:
            target_h = 120

        h, w = frame.shape[:2]
        if w <= 1 or h <= 1:
            return frame

        if self.phone_aspect_mode == "stretch":
            return cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)

        target_ratio = target_w / max(target_h, 1)
        source_ratio = w / max(h, 1)

        if self.phone_aspect_mode == "crop":
            if source_ratio > target_ratio:
                crop_w = max(2, int(round(h * target_ratio)))
                x0 = max(0, (w - crop_w) // 2)
                frame = frame[:, x0 : x0 + crop_w]
            else:
                crop_h = max(2, int(round(w / target_ratio)))
                y0 = max(0, (h - crop_h) // 2)
                frame = frame[y0 : y0 + crop_h, :]
            return cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)

        scale = min(target_w / w, target_h / h)
        fit_w = max(2, int(round(w * scale)))
        fit_h = max(2, int(round(h * scale)))
        resized = cv2.resize(frame, (fit_w, fit_h), interpolation=cv2.INTER_AREA)
        canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)
        x0 = (target_w - fit_w) // 2
        y0 = (target_h - fit_h) // 2
        canvas[y0 : y0 + fit_h, x0 : x0 + fit_w] = resized
        return canvas

    def make_phone_wait_frame(self, phone_url: str) -> np.ndarray:
        width = max(640, int(self.args.window_width))
        height = max(360, int(self.args.window_height))
        image = np.zeros((height, width, 3), dtype=np.uint8)
        cv2.rectangle(image, (0, 0), (width - 1, height - 1), DIM_GREEN, 2)
        cx, cy = width // 2, height // 2
        cv2.line(image, (cx - 26, cy), (cx + 26, cy), DIM_GREEN, 1, cv2.LINE_AA)
        cv2.line(image, (cx, cy - 26), (cx, cy + 26), DIM_GREEN, 1, cv2.LINE_AA)
        cv2.circle(image, (cx, cy), 58, DIM_GREEN, 1, cv2.LINE_AA)

        lines = [
            "U6C PHONE CAMERA INPUT",
            "Open this URL on your phone:",
            phone_url or "waiting for phone server",
            "Tap Start Camera and allow camera access.",
            "Press Q or Esc here to quit.",
        ]
        y = max(64, height // 2 - 72)
        for idx, line in enumerate(lines):
            color = WHITE if idx in (1, 2, 3) else GREEN
            scale = 0.72 if idx == 0 else 0.54
            thickness = 2 if idx == 0 else 1
            text_size, _baseline = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
            x = max(18, (width - text_size[0]) // 2)
            draw_text(image, line, (x, y), scale, color, thickness)
            y += 38 if idx == 0 else 30
        return image

    def on_mouse(self, event: int, x: int, y: int, _flags: int, _param: object) -> None:
        if self.handle_slider_event(event, x, y):
            return
        if event == cv2.EVENT_LBUTTONDOWN:
            if self.handle_menu_click(x, y):
                return
            if self.handle_yolo_target_click(x, y):
                return
            if self.handle_motion_track_click(x, y):
                return
            self.capture_requested = (x, y)
            self.status = "CAPTURE REQUESTED"
        elif event == cv2.EVENT_RBUTTONDOWN:
            self.unlock("UNLOCKED")

    def handle_slider_event(self, event: int, x: int, y: int) -> bool:
        if event == cv2.EVENT_LBUTTONUP and self.active_slider is not None:
            self.apply_slider_at(self.active_slider, x)
            self.active_slider = None
            return True

        if event == cv2.EVENT_MOUSEMOVE and self.active_slider is not None:
            self.apply_slider_at(self.active_slider, x)
            return True

        if event != cv2.EVENT_LBUTTONDOWN:
            return False

        for slider in self.menu_sliders:
            sx, sy, sw, sh = slider.rect
            if sx <= x <= sx + sw and sy <= y <= sy + sh:
                self.active_slider = slider.action
                self.apply_slider_at(slider.action, x)
                return True
        return False

    def apply_slider_at(self, action: str, x: int) -> None:
        slider = next((item for item in self.menu_sliders if item.action == action), None)
        if slider is None:
            return
        sx, _sy, sw, _sh = slider.rect
        value = clamp((x - sx) / max(sw, 1), 0.0, 1.0)
        self.apply_slider_value(action, value)

    def apply_slider_value(self, action: str, value: float) -> None:
        if action == "sensitivity":
            self.threshold = int(round(80 - value * 79))
            self.threshold = int(clamp(self.threshold, 1, 80))
            self.status = f"SENS {self.motion_sensitivity_percent():.0f}%"
        elif action == "min_area":
            self.min_area = 2.0 + (value * value) * 1198.0
            self.status = f"MIN SIZE {self.min_area:.0f}"
        elif action == "merge_radius":
            self.motion_merge_radius = int(round(value * 50))
            self.status = f"MERGE {self.motion_merge_radius}px"

    def motion_sensitivity_percent(self) -> float:
        return clamp((80 - self.threshold) / 79.0 * 100.0, 0.0, 100.0)

    def min_area_slider_value(self) -> float:
        return math.sqrt(clamp((self.min_area - 2.0) / 1198.0, 0.0, 1.0))

    def handle_menu_click(self, x: int, y: int) -> bool:
        for button in self.menu_buttons:
            bx, by, bw, bh = button.rect
            if bx <= x <= bx + bw and by <= y <= by + bh:
                self.activate_menu_action(button.action)
                return True
        return False

    def handle_yolo_target_click(self, x: int, y: int) -> bool:
        if not self.live_yolo_detections:
            return False
        for detection in sorted(self.live_yolo_detections, key=lambda item: item.confidence, reverse=True):
            bx, by, bw, bh = detection.bbox
            if bx <= x <= bx + bw and by <= y <= by + bh:
                cx = bx + bw // 2
                cy = by + bh // 2
                self.set_yolo_focus(detection)
                self.focus_zoom_on_point(cx, cy)
                self.capture_requested = None
                self.status = "CAPTURE REQUESTED"
                return True
        return False

    def set_yolo_focus(self, detection: YoloDetection) -> None:
        self.yolo_focus_target = YoloFocusTarget(
            label=detection.label,
            confidence=detection.confidence,
            bbox=detection.bbox,
            source=detection.source,
            missing_scans=0,
            last_seen=time.perf_counter(),
        )

    def handle_motion_track_click(self, x: int, y: int) -> bool:
        track = self.nearest_motion_track((x, y), radius=32)
        if track is None:
            return False
        self.capture_requested = (int(track.center[0]), int(track.center[1]))
        self.status = f"TRACK SELECT T{track.id}"
        return True

    def activate_menu_action(self, action: str) -> None:
        if action == "capture":
            self.capture_requested = None
            self.status = "CAPTURE REQUESTED"
        elif action == "unlock":
            self.unlock("UNLOCKED")
        elif action == "flow":
            self.optical_flow_enabled = not self.optical_flow_enabled
            self.status = "FLOW ON" if self.optical_flow_enabled else "FLOW OFF"
        elif action == "radar":
            self.radar_enabled = not self.radar_enabled
            self.status = "RADAR ON" if self.radar_enabled else "RADAR OFF"
        elif action == "mode":
            self.render_mode_index = (self.render_mode_index + 1) % len(self.render_modes)
            self.status = f"MODE {self.render_mode.upper()}"
        elif action == "hud":
            self.hud_enabled = not self.hud_enabled
        elif action == "menu":
            self.menu_enabled = not self.menu_enabled
        elif action == "yolo":
            self.yolo.toggle()
            if not self.yolo.enabled:
                self.live_yolo_detections = []
                self.yolo_focus_target = None
            self.status = "YOLO ON" if self.yolo.enabled else "YOLO OFF"
        elif action == "live":
            self.continuous_yolo_enabled = not self.continuous_yolo_enabled
            if not self.continuous_yolo_enabled:
                self.live_yolo_detections = []
                self.yolo_focus_target = None
            self.status = "LIVE YOLO ON" if self.continuous_yolo_enabled else "LIVE YOLO OFF"
        elif action == "ensemble":
            self.ensemble_yolo_enabled = not self.ensemble_yolo_enabled
            self.live_yolo_detections = []
            self.last_yolo_time = 0.0
            self.status = "ENSEMBLE ON" if self.ensemble_yolo_enabled else "ENSEMBLE OFF"
        elif action == "model":
            self.cycle_yolo_model()
        elif action == "mirror":
            self.mirror = not self.mirror
            self.status = "MIRROR ON" if self.mirror else "MIRROR OFF"
        elif action == "zoom_in":
            self.zoom = min(6.0, self.zoom + 0.25)
            self.status = f"ZOOM {self.zoom:.2f}X"
        elif action == "zoom_out":
            self.zoom = max(1.0, self.zoom - 0.25)
            if self.zoom <= 1.01:
                self.zoom_center = None
            self.status = f"ZOOM {self.zoom:.2f}X"
        elif action == "zoom_reset":
            self.reset_zoom()
        elif action == "thr_down":
            self.threshold = max(1, self.threshold - 1)
            self.status = f"THR {self.threshold}"
        elif action == "thr_up":
            self.threshold = min(80, self.threshold + 1)
            self.status = f"THR {self.threshold}"
        elif action == "radius_down":
            self.lock_radius = max(20, self.lock_radius - 5)
            self.status = f"RADIUS {self.lock_radius}"
        elif action == "radius_up":
            self.lock_radius = min(240, self.lock_radius + 5)
            self.status = f"RADIUS {self.lock_radius}"

    def handle_key(self, key: int) -> bool:
        if key in (27, ord("q"), ord("Q")):
            return True
        if key in (ord("c"), ord("C"), 32):
            self.capture_requested = None
            self.status = "CAPTURE REQUESTED"
        elif key in (ord("u"), ord("U")):
            self.unlock("UNLOCKED")
        elif key in (ord("f"), ord("F")):
            self.optical_flow_enabled = not self.optical_flow_enabled
            self.status = "FLOW ON" if self.optical_flow_enabled else "FLOW OFF"
        elif key in (ord("r"), ord("R")):
            self.radar_enabled = not self.radar_enabled
        elif key in (ord("m"), ord("M")):
            self.render_mode_index = (self.render_mode_index + 1) % len(self.render_modes)
        elif key in (ord("h"), ord("H")):
            self.hud_enabled = not self.hud_enabled
        elif key in (ord("b"), ord("B")):
            self.menu_enabled = not self.menu_enabled
        elif key in (ord("y"), ord("Y")):
            self.yolo.toggle()
            if not self.yolo.enabled:
                self.live_yolo_detections = []
                self.yolo_focus_target = None
            if self.tracker.locked and self.yolo.enabled and self.current_frame_for_yolo is not None:
                self.run_yolo_scan(self.current_frame_for_yolo)
        elif key in (ord("g"), ord("G")):
            self.continuous_yolo_enabled = not self.continuous_yolo_enabled
            if not self.continuous_yolo_enabled:
                self.live_yolo_detections = []
                self.yolo_focus_target = None
            self.status = "LIVE YOLO ON" if self.continuous_yolo_enabled else "LIVE YOLO OFF"
        elif key in (ord("e"), ord("E")):
            self.ensemble_yolo_enabled = not self.ensemble_yolo_enabled
            self.live_yolo_detections = []
            self.last_yolo_time = 0.0
            self.status = "ENSEMBLE ON" if self.ensemble_yolo_enabled else "ENSEMBLE OFF"
        elif key in (ord("o"), ord("O")):
            self.cycle_yolo_model()
        elif key in (ord("v"), ord("V")):
            self.mirror = not self.mirror
        elif key in (ord("+"), ord("=")):
            self.zoom = min(6.0, self.zoom + 0.25)
        elif key in (ord("-"), ord("_")):
            self.zoom = max(1.0, self.zoom - 0.25)
            if self.zoom <= 1.01:
                self.zoom_center = None
        elif key in (ord("x"), ord("X")):
            self.reset_zoom()
        elif key == ord("["):
            self.threshold = max(1, self.threshold - 1)
        elif key == ord("]"):
            self.threshold = min(80, self.threshold + 1)
        elif key == ord(","):
            self.lock_radius = max(20, self.lock_radius - 5)
        elif key == ord("."):
            self.lock_radius = min(240, self.lock_radius + 5)
        return False

    def apply_zoom(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        if self.zoom <= 1.01:
            self.last_zoom_crop = (0, 0, w, h, w, h)
            return frame
        crop_w = max(2, int(w / self.zoom))
        crop_h = max(2, int(h / self.zoom))
        if self.zoom_center is None:
            cx, cy = w / 2, h / 2
        else:
            cx, cy = self.zoom_center
        x1 = int(clamp(cx - crop_w / 2, 0, max(0, w - crop_w)))
        y1 = int(clamp(cy - crop_h / 2, 0, max(0, h - crop_h)))
        self.last_zoom_crop = (x1, y1, crop_w, crop_h, w, h)
        crop = frame[y1 : y1 + crop_h, x1 : x1 + crop_w]
        return cv2.resize(crop, (w, h), interpolation=cv2.INTER_LINEAR)

    def display_to_raw_point(self, x: int, y: int) -> tuple[float, float]:
        if self.last_zoom_crop is None:
            return float(x), float(y)
        crop_x, crop_y, crop_w, crop_h, frame_w, frame_h = self.last_zoom_crop
        raw_x = crop_x + (x / max(frame_w, 1)) * crop_w
        raw_y = crop_y + (y / max(frame_h, 1)) * crop_h
        return raw_x, raw_y

    def reset_zoom(self) -> None:
        self.zoom = 1.0
        self.zoom_center = None
        self.status = "ZOOM RESET"

    def focus_zoom_on_point(self, x: int, y: int, min_zoom: float = 2.5) -> None:
        self.zoom_center = self.display_to_raw_point(x, y)
        if self.zoom < min_zoom:
            self.zoom = min_zoom
        else:
            self.zoom = min(6.0, self.zoom + 0.5)
        self.status = f"ZOOM TARGET {self.zoom:.2f}X"

    def process_frame(self, frame: np.ndarray) -> np.ndarray:
        self.current_frame_for_yolo = frame
        gray, mask, candidates = self.analyze_motion(frame)
        self.last_mask = mask
        self.last_candidates = candidates

        h, w = frame.shape[:2]
        self.update_motion_tracks(candidates, w, h)
        self.update_motion_particles(mask, candidates)
        if self.capture_requested is not None:
            capture_point = self.capture_requested
            self.capture_requested = None
            self.capture_target(frame, gray, candidates, capture_point)
        elif self.status == "CAPTURE REQUESTED":
            self.capture_target(frame, gray, candidates, (w // 2, h // 2))

        if self.tracker.locked:
            self.update_tracker(frame, gray, candidates)

        self.update_live_yolo(frame)

        display = self.render_base(frame, gray, mask)
        self.draw_overlays(display, candidates)
        self.update_person_alerts(display)
        self.total_frames += 1
        self.fps_frames += 1
        self.update_fps()
        self.tracker.prev_gray = gray.copy()
        return display

    def analyze_motion(
        self,
        frame: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, list[MotionCandidate]]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        if self.background is None or self.background.shape != gray.shape:
            self.background = gray.astype(np.float32)
            return gray, np.zeros_like(gray), []

        cv2.accumulateWeighted(gray, self.background, self.args.background_alpha)
        bg = cv2.convertScaleAbs(self.background)
        delta = cv2.absdiff(gray, bg)
        _, mask = cv2.threshold(delta, self.threshold, 255, cv2.THRESH_BINARY)
        kernel = np.ones((3, 3), dtype=np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=2)

        if self.motion_merge_radius > 0:
            merge_size = int(self.motion_merge_radius) * 2 + 1
            merge_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (merge_size, merge_size))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, merge_kernel, iterations=1)

        mask = cv2.dilate(mask, kernel, iterations=1)
        changed_fraction = cv2.countNonZero(mask) / float(max(mask.size, 1))
        if changed_fraction > self.motion_noise_limit:
            self.status = "MOTION NOISE GATE"
            return gray, np.zeros_like(gray), []

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates: list[MotionCandidate] = []
        for contour in contours:
            area = float(cv2.contourArea(contour))
            if area < self.min_area or area > self.max_area:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            moments = cv2.moments(contour)
            if moments["m00"]:
                cx = moments["m10"] / moments["m00"]
                cy = moments["m01"] / moments["m00"]
            else:
                cx = x + w / 2
                cy = y + h / 2
            roi = gray[y : y + h, x : x + w]
            brightness = float(np.mean(roi)) if roi.size else 0.0
            candidates.append(MotionCandidate((cx, cy), (x, y, w, h), area, brightness))

        candidates.sort(key=lambda c: c.area, reverse=True)
        return gray, mask, candidates[: max(1, self.max_points)]

    def update_motion_tracks(
        self,
        candidates: list[MotionCandidate],
        width: int,
        height: int,
    ) -> None:
        pairs: list[tuple[float, int, int]] = []
        for track_idx, track in enumerate(self.motion_tracks):
            px = track.center[0] + track.velocity[0]
            py = track.center[1] + track.velocity[1]
            for candidate_idx, candidate in enumerate(candidates):
                cx, cy = candidate.center
                distance = math.hypot(cx - px, cy - py)
                area_boost = min(48.0, math.sqrt(max(track.area, candidate.area, 1.0)) * 0.85)
                if distance <= self.track_max_distance + area_boost:
                    pairs.append((distance, track_idx, candidate_idx))

        assigned_tracks: set[int] = set()
        assigned_candidates: set[int] = set()
        for _distance, track_idx, candidate_idx in sorted(pairs, key=lambda item: item[0]):
            if track_idx in assigned_tracks or candidate_idx in assigned_candidates:
                continue
            self.update_motion_track(self.motion_tracks[track_idx], candidates[candidate_idx], width, height)
            assigned_tracks.add(track_idx)
            assigned_candidates.add(candidate_idx)

        for idx, track in enumerate(self.motion_tracks):
            if idx in assigned_tracks:
                continue
            self.coast_motion_track(track, width, height)

        for idx, candidate in enumerate(candidates):
            if idx in assigned_candidates:
                continue
            self.add_motion_track(candidate, width, height)

        self.motion_tracks = [
            track
            for track in self.motion_tracks
            if track.missing_frames <= self.track_memory
            and 0 <= track.center[0] < width
            and 0 <= track.center[1] < height
        ]
        self.motion_tracks.sort(key=lambda track: (track.missing_frames, -track.hits, -track.area))
        self.motion_tracks = self.motion_tracks[: self.max_points]

    def update_motion_track(
        self,
        track: MotionTrack,
        candidate: MotionCandidate,
        width: int,
        height: int,
    ) -> None:
        old_x, old_y = track.center
        pred_x = old_x + track.velocity[0]
        pred_y = old_y + track.velocity[1]
        cx = candidate.center[0] * 0.72 + pred_x * 0.28
        cy = candidate.center[1] * 0.72 + pred_y * 0.28
        cx = float(clamp(cx, 0, width - 1))
        cy = float(clamp(cy, 0, height - 1))

        new_vx = cx - old_x
        new_vy = cy - old_y
        track.velocity = (
            new_vx * 0.65 + track.velocity[0] * 0.35,
            new_vy * 0.65 + track.velocity[1] * 0.35,
        )
        old_w, old_h = track.bbox[2], track.bbox[3]
        next_bbox = expanded_bbox(candidate.bbox, 4, width, height)
        smoothed_w = int(clamp(next_bbox[2] * 0.62 + old_w * 0.38, 8, width))
        smoothed_h = int(clamp(next_bbox[3] * 0.62 + old_h * 0.38, 8, height))
        track.center = (cx, cy)
        track.bbox = bbox_from_center(track.center, (smoothed_w, smoothed_h), width, height)
        track.area = candidate.area * 0.7 + track.area * 0.3
        track.brightness = candidate.brightness
        track.age += 1
        track.hits += 1
        track.missing_frames = 0
        self.append_track_trail(track)

    def coast_motion_track(self, track: MotionTrack, width: int, height: int) -> None:
        cx = float(clamp(track.center[0] + track.velocity[0], 0, width - 1))
        cy = float(clamp(track.center[1] + track.velocity[1], 0, height - 1))
        track.center = (cx, cy)
        track.velocity = (track.velocity[0] * 0.92, track.velocity[1] * 0.92)
        track.bbox = bbox_from_center(track.center, (track.bbox[2], track.bbox[3]), width, height)
        track.age += 1
        track.missing_frames += 1
        if track.missing_frames <= max(2, self.track_memory // 2):
            self.append_track_trail(track)

    def add_motion_track(self, candidate: MotionCandidate, width: int, height: int) -> None:
        bbox = expanded_bbox(candidate.bbox, 4, width, height)
        track = MotionTrack(
            id=self.next_motion_track_id,
            center=(float(candidate.center[0]), float(candidate.center[1])),
            bbox=bbox,
            area=candidate.area,
            brightness=candidate.brightness,
            age=1,
            hits=1,
            missing_frames=0,
            trail=[(int(candidate.center[0]), int(candidate.center[1]))],
        )
        self.next_motion_track_id += 1
        self.motion_tracks.append(track)

    def append_track_trail(self, track: MotionTrack) -> None:
        track.trail.append((int(track.center[0]), int(track.center[1])))
        if len(track.trail) > 28:
            del track.trail[:-28]

    def update_motion_particles(
        self,
        mask: np.ndarray,
        candidates: list[MotionCandidate],
    ) -> None:
        now = time.perf_counter()
        self.motion_particles = [
            particle
            for particle in self.motion_particles
            if now - particle.born <= particle.life
        ]

        if mask.size == 0 or not candidates:
            return

        remaining_capacity = max(0, 900 - len(self.motion_particles))
        if remaining_capacity <= 0:
            return

        for candidate in candidates[:80]:
            if remaining_capacity <= 0:
                break
            x, y, w, h = candidate.bbox
            roi = mask[y : y + h, x : x + w]
            ys, xs = np.where(roi > 0)
            if len(xs) == 0:
                continue

            count = int(clamp(candidate.area / max(self.motion_dot_density, 1.0), 2, 24))
            count = min(count, len(xs), remaining_capacity)
            if count <= 0:
                continue

            indices = np.random.choice(len(xs), size=count, replace=False)
            for idx in indices:
                px = int(x + xs[idx])
                py = int(y + ys[idx])
                radius = 1 if candidate.area < 150 else 2
                self.motion_particles.append(
                    MotionParticle(
                        point=(px, py),
                        born=now,
                        life=self.motion_dot_life,
                        radius=radius,
                    )
                )
            remaining_capacity -= count

    def find_motion_track(self, track_id: Optional[int]) -> Optional[MotionTrack]:
        if track_id is None:
            return None
        for track in self.motion_tracks:
            if track.id == track_id:
                return track
        return None

    def nearest_motion_track(
        self,
        point: tuple[int, int],
        radius: Optional[float] = None,
    ) -> Optional[MotionTrack]:
        px, py = point
        search_radius = float(radius if radius is not None else self.lock_radius)
        best: Optional[MotionTrack] = None
        best_score = -1.0
        for track in self.motion_tracks:
            if track.missing_frames > max(2, self.track_memory // 2):
                continue
            distance = math.hypot(track.center[0] - px, track.center[1] - py)
            track_radius = search_radius + min(36.0, math.sqrt(max(track.area, 1.0)) * 0.65)
            if distance > track_radius:
                continue
            score = (track_radius - distance) * 4.0 + track.hits * 2.5 + track.area * 0.15
            if track.missing_frames:
                score -= track.missing_frames * 6.0
            if score > best_score:
                best = track
                best_score = score
        return best

    def track_to_candidate(self, track: MotionTrack) -> MotionCandidate:
        return MotionCandidate(
            center=track.center,
            bbox=track.bbox,
            area=track.area,
            brightness=track.brightness,
        )

    def capture_target(
        self,
        frame: np.ndarray,
        gray: np.ndarray,
        candidates: Iterable[MotionCandidate],
        point: tuple[int, int],
    ) -> None:
        h, w = frame.shape[:2]
        px, py = point

        track = self.nearest_motion_track(point)
        if track is not None:
            self.status = f"TRACK LOCK T{track.id}"
            self.lock_candidate(self.track_to_candidate(track), frame, gray, track_id=track.id)
            return

        best: Optional[MotionCandidate] = None
        best_score = -1.0
        for candidate in candidates:
            cx, cy = candidate.center
            distance = math.hypot(cx - px, cy - py)
            if distance > self.lock_radius:
                continue
            score = candidate.area * 2.0 + max(0.0, self.lock_radius - distance) * 3.0
            if score > best_score:
                best = candidate
                best_score = score

        if best is None:
            fallback_size = int(clamp(self.lock_radius * 0.8, 36, 96))
            bbox = bbox_from_center((px, py), (fallback_size, fallback_size), w, h)
            best = MotionCandidate((float(px), float(py)), bbox, 0.0, 0.0)
            self.status = "MANUAL LOCK"
        else:
            self.status = "TARGET LOCK"

        self.lock_candidate(best, frame, gray)

    def lock_candidate(
        self,
        candidate: MotionCandidate,
        frame: np.ndarray,
        gray: np.ndarray,
        track_id: Optional[int] = None,
    ) -> None:
        h, w = frame.shape[:2]
        bbox = expanded_bbox(candidate.bbox, 10, w, h)
        cx, cy = candidate.center
        self.tracker = TrackerState(
            locked=True,
            center=(float(cx), float(cy)),
            bbox=bbox,
            velocity=(0.0, 0.0),
            missing_frames=0,
            label="UNKNOWN",
            confidence=0.0,
            kalman=create_kalman(float(cx), float(cy)),
            prev_gray=gray.copy(),
            track_id=track_id,
        )
        self.init_features(gray)
        if self.yolo.enabled:
            self.run_yolo_scan(frame)

    def update_tracker(
        self,
        frame: np.ndarray,
        gray: np.ndarray,
        candidates: Iterable[MotionCandidate],
    ) -> None:
        h, w = frame.shape[:2]
        predicted = self.predict_center()
        flow_center = self.optical_flow_center(gray)
        track = self.find_motion_track(self.tracker.track_id)
        candidate = None if track is not None else self.nearest_candidate(candidates, predicted)

        measured: Optional[tuple[float, float]] = None
        measured_bbox: Optional[tuple[int, int, int, int]] = None
        if track is not None and track.missing_frames <= self.track_memory:
            measured = track.center
            measured_bbox = expanded_bbox(track.bbox, 10, w, h)
        elif candidate is not None:
            measured = candidate.center
            measured_bbox = expanded_bbox(candidate.bbox, 10, w, h)
        elif flow_center is not None:
            measured = flow_center

        old_center = self.tracker.center
        if measured is not None:
            filtered = self.correct_kalman(measured)
            self.tracker.center = (
                float(clamp(filtered[0], 0, w - 1)),
                float(clamp(filtered[1], 0, h - 1)),
            )
            self.tracker.velocity = (
                self.tracker.center[0] - old_center[0],
                self.tracker.center[1] - old_center[1],
            )
            if measured_bbox is not None:
                old_w, old_h = self.tracker.bbox[2], self.tracker.bbox[3]
                new_w = int(clamp(measured_bbox[2] * 0.55 + old_w * 0.45, 18, w))
                new_h = int(clamp(measured_bbox[3] * 0.55 + old_h * 0.45, 18, h))
                self.tracker.bbox = bbox_from_center(self.tracker.center, (new_w, new_h), w, h)
            else:
                self.tracker.bbox = bbox_from_center(
                    self.tracker.center,
                    (self.tracker.bbox[2], self.tracker.bbox[3]),
                    w,
                    h,
                )
            self.tracker.missing_frames = 0
            if self.total_frames % 18 == 0 or self.feature_count() < 8:
                self.init_features(gray)
        else:
            self.tracker.missing_frames += 1
            self.tracker.center = (
                float(clamp(predicted[0], 0, w - 1)),
                float(clamp(predicted[1], 0, h - 1)),
            )
            self.tracker.bbox = bbox_from_center(
                self.tracker.center,
                (self.tracker.bbox[2], self.tracker.bbox[3]),
                w,
                h,
            )
            if self.tracker.missing_frames > self.memory_frames:
                self.unlock("TARGET LOST")

    def predict_center(self) -> tuple[float, float]:
        if self.tracker.kalman is None:
            return self.tracker.center
        prediction = self.tracker.kalman.predict()
        return float(prediction[0, 0]), float(prediction[1, 0])

    def correct_kalman(self, measured: tuple[float, float]) -> tuple[float, float]:
        if self.tracker.kalman is None:
            return measured
        measurement = np.array([[measured[0]], [measured[1]]], dtype=np.float32)
        corrected = self.tracker.kalman.correct(measurement)
        return float(corrected[0, 0]), float(corrected[1, 0])

    def nearest_candidate(
        self,
        candidates: Iterable[MotionCandidate],
        predicted: tuple[float, float],
    ) -> Optional[MotionCandidate]:
        best = None
        best_score = -1.0
        radius = max(24, self.lock_radius + self.tracker.missing_frames * 6)
        px, py = predicted
        for candidate in candidates:
            cx, cy = candidate.center
            distance = math.hypot(cx - px, cy - py)
            if distance > radius:
                continue
            score = candidate.area * 3.0 + max(0.0, radius - distance) * 2.0
            if score > best_score:
                best_score = score
                best = candidate
        return best

    def init_features(self, gray: np.ndarray) -> None:
        if not self.tracker.locked:
            return
        h, w = gray.shape[:2]
        x, y, bw, bh = expanded_bbox(self.tracker.bbox, self.flow_radius, w, h)
        roi = gray[y : y + bh, x : x + bw]
        features = cv2.goodFeaturesToTrack(
            roi,
            maxCorners=48,
            qualityLevel=0.01,
            minDistance=5,
            blockSize=5,
        )
        if features is None:
            self.tracker.features = None
            return
        features[:, :, 0] += x
        features[:, :, 1] += y
        self.tracker.features = features.astype(np.float32)

    def feature_count(self) -> int:
        if self.tracker.features is None:
            return 0
        return int(len(self.tracker.features))

    def optical_flow_center(self, gray: np.ndarray) -> Optional[tuple[float, float]]:
        if not self.optical_flow_enabled:
            return None
        if self.tracker.prev_gray is None or self.tracker.features is None:
            return None
        if len(self.tracker.features) < 3:
            return None

        next_points, status, _err = cv2.calcOpticalFlowPyrLK(
            self.tracker.prev_gray,
            gray,
            self.tracker.features,
            None,
            winSize=(21, 21),
            maxLevel=3,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03),
        )
        if next_points is None or status is None:
            return None

        good_new = next_points[status.reshape(-1) == 1]
        good_old = self.tracker.features[status.reshape(-1) == 1].reshape(-1, 2)
        if len(good_new) < 3:
            self.tracker.features = None
            return None

        displacement = np.median(good_new.reshape(-1, 2) - good_old, axis=0)
        self.tracker.features = good_new.reshape(-1, 1, 2).astype(np.float32)
        return (
            self.tracker.center[0] + float(displacement[0]),
            self.tracker.center[1] + float(displacement[1]),
        )

    def update_live_yolo(self, frame: np.ndarray) -> None:
        if not self.continuous_yolo_enabled:
            return
        if not self.yolo.enabled:
            self.live_yolo_detections = []
            return

        now = time.perf_counter()
        if now - self.last_yolo_time < self.yolo_interval:
            return

        self.last_yolo_time = now
        detections: list[YoloDetection] = []
        for snapper in self.active_yolo_snappers():
            detections.extend(
                snapper.infer_detections(
                    frame,
                    max_detections=self.yolo_max_detections,
                )
            )
        self.live_yolo_detections = self.merge_yolo_detections(detections)
        self.update_yolo_focus(self.live_yolo_detections)

    def update_yolo_focus(self, detections: list[YoloDetection]) -> None:
        if self.yolo_focus_target is None:
            return

        focus = self.yolo_focus_target
        best: Optional[YoloDetection] = None
        best_score = -1.0
        fx, fy, fw, fh = focus.bbox
        focus_center = (fx + fw / 2.0, fy + fh / 2.0)
        focus_diag = max(32.0, math.hypot(fw, fh))

        for detection in detections:
            if detection.label != focus.label:
                continue
            dx, dy, dw, dh = detection.bbox
            det_center = (dx + dw / 2.0, dy + dh / 2.0)
            distance = math.hypot(det_center[0] - focus_center[0], det_center[1] - focus_center[1])
            iou = bbox_iou(focus.bbox, detection.bbox)
            if iou <= 0.05 and distance > focus_diag * 0.9:
                continue
            score = iou * 3.0 + detection.confidence + max(0.0, 1.0 - distance / focus_diag)
            if detection.source == focus.source:
                score += 0.15
            if score > best_score:
                best = detection
                best_score = score

        if best is None:
            focus.missing_scans += 1
            if focus.missing_scans > self.yolo_focus_memory:
                self.yolo_focus_target = None
            return

        focus.bbox = best.bbox
        focus.confidence = best.confidence
        focus.source = best.source
        focus.missing_scans = 0
        focus.last_seen = time.perf_counter()

    def run_yolo_scan(self, frame: np.ndarray) -> None:
        if not self.tracker.locked:
            return
        x, y, w, h = self.tracker.bbox
        crop = frame[y : y + h, x : x + w]
        detections: list[YoloDetection] = []
        for snapper in self.active_yolo_snappers():
            detections.extend(snapper.infer_detections(crop, max_detections=4))
        merged = self.merge_yolo_detections(detections, iou_threshold=0.35)
        if merged:
            best = max(merged, key=lambda item: item.confidence)
            source = f"/{best.source}" if best.source else ""
            self.tracker.label = f"{best.label}{source}"
            self.tracker.confidence = best.confidence
        else:
            self.tracker.label = "UNKNOWN"
            self.tracker.confidence = 0.0
        self.tracker.last_yolo_scan = time.perf_counter()

    def update_person_alerts(self, display_frame: np.ndarray) -> None:
        if not self.discord_person_alerts or self.discord_sender is None:
            return

        now = time.perf_counter()
        self.person_alert_tracks = [
            track
            for track in self.person_alert_tracks
            if now - track.last_seen <= self.person_alert_memory
        ]

        person_detections = [
            detection
            for detection in self.live_yolo_detections
            if detection.label == "PERSON" and detection.confidence >= self.discord_person_confidence
        ]
        if not person_detections:
            for track in self.person_alert_tracks:
                track.pending_alert_at = 0.0
            return

        alert_detection: Optional[YoloDetection] = None
        alert_track: Optional[PersonAlertTrack] = None
        active_track_ids: set[int] = set()
        for detection in sorted(person_detections, key=lambda item: item.confidence, reverse=True):
            track = self.update_person_alert_track(detection, now)
            active_track_ids.add(id(track))
            if now - self.last_discord_alert < self.discord_global_cooldown:
                continue
            if not (track.alert_count == 0 or now - track.last_alert >= self.person_alert_cooldown):
                track.pending_alert_at = 0.0
                continue

            if track.pending_alert_at <= 0.0:
                track.pending_alert_at = now + self.discord_snapshot_delay
                self.status = f"DISCORD ALERT IN {self.discord_snapshot_delay:.1f}s"
                continue

            if now >= track.pending_alert_at:
                alert_detection = detection
                alert_track = track
                break

        for track in self.person_alert_tracks:
            if id(track) not in active_track_ids:
                track.pending_alert_at = 0.0

        if alert_detection is None or alert_track is None:
            return

        source = f"/{alert_detection.source}" if alert_detection.source else ""
        message = (
            f"U6C PERSON ALERT | PERSON{source} "
            f"{alert_detection.confidence * 100:.0f}% | visible people: {len(person_detections)}"
        )
        self.discord_sender.send_async(display_frame, message)
        alert_track.last_alert = now
        alert_track.alert_count += 1
        alert_track.pending_alert_at = 0.0
        self.last_discord_alert = now
        self.status = "DISCORD PERSON ALERT"

    def update_person_alert_track(self, detection: YoloDetection, now: float) -> PersonAlertTrack:
        best_track: Optional[PersonAlertTrack] = None
        best_score = -1.0
        dx, dy, dw, dh = detection.bbox
        det_center = (dx + dw / 2.0, dy + dh / 2.0)

        for track in self.person_alert_tracks:
            tx, ty, tw, th = track.bbox
            track_center = (tx + tw / 2.0, ty + th / 2.0)
            distance = math.hypot(det_center[0] - track_center[0], det_center[1] - track_center[1])
            diag = max(48.0, math.hypot(max(dw, tw), max(dh, th)))
            iou = bbox_iou(detection.bbox, track.bbox)
            if iou <= 0.08 and distance > diag * 0.85:
                continue
            score = iou * 3.0 + max(0.0, 1.0 - distance / diag)
            if detection.source == track.source:
                score += 0.15
            if score > best_score:
                best_score = score
                best_track = track

        if best_track is None:
            best_track = PersonAlertTrack(
                bbox=detection.bbox,
                label=detection.label,
                source=detection.source,
                confidence=detection.confidence,
                last_seen=now,
                last_alert=0.0,
                alert_count=0,
            )
            self.person_alert_tracks.append(best_track)
            return best_track

        best_track.bbox = detection.bbox
        best_track.label = detection.label
        best_track.source = detection.source
        best_track.confidence = detection.confidence
        best_track.last_seen = now
        return best_track

    def unlock(self, status: str) -> None:
        self.tracker = TrackerState()
        self.status = status

    def render_base(self, frame: np.ndarray, gray: np.ndarray, mask: np.ndarray) -> np.ndarray:
        mode = self.render_mode
        if mode == "motion":
            base = np.zeros_like(frame)
            base[:, :, 1] = cv2.convertScaleAbs(mask, alpha=1.25)
            return cv2.addWeighted(frame, 0.28, base, 0.95, 0)
        if mode == "contrast":
            equalized = cv2.equalizeHist(gray)
            return cv2.cvtColor(equalized, cv2.COLOR_GRAY2BGR)
        if mode == "thermal":
            return cv2.applyColorMap(gray, cv2.COLORMAP_TURBO)
        return frame.copy()

    def draw_overlays(
        self,
        image: np.ndarray,
        candidates: Iterable[MotionCandidate],
    ) -> None:
        self.draw_grid(image)
        self.draw_motion_points(image, candidates)
        self.draw_yolo_detections(image)
        self.draw_reticle(image)
        if self.radar_enabled:
            self.draw_radar(image, candidates)
        self.draw_yolo_focus_panel(image)
        if self.tracker.locked:
            self.draw_target(image)
            self.draw_inspector(image)
        if self.hud_enabled:
            self.draw_hud(image)
        self.draw_menu(image)
        self.draw_scanlines(image)

    def draw_grid(self, image: np.ndarray) -> None:
        h, w = image.shape[:2]
        step = max(48, min(w, h) // 12)
        for x in range(0, w, step):
            cv2.line(image, (x, 0), (x, h), DIM_GREEN, 1)
        for y in range(0, h, step):
            cv2.line(image, (0, y), (w, y), DIM_GREEN, 1)

    def draw_motion_points(
        self,
        image: np.ndarray,
        candidates: Iterable[MotionCandidate],
    ) -> None:
        _ = candidates
        now = time.perf_counter()
        for particle in self.motion_particles:
            age = now - particle.born
            fade = clamp(1.0 - age / max(particle.life, 0.001), 0.0, 1.0)
            if fade <= 0.0:
                continue
            color = (
                int(DIM_GREEN[0] * (1.0 - fade) + GREEN[0] * fade),
                int(DIM_GREEN[1] * (1.0 - fade) + GREEN[1] * fade),
                int(DIM_GREEN[2] * (1.0 - fade) + GREEN[2] * fade),
            )
            radius = max(1, int(round(particle.radius * (0.6 + fade))))
            cv2.circle(image, particle.point, radius, color, -1, cv2.LINE_AA)

        locked_id = self.tracker.track_id if self.tracker.locked else None
        for track in self.motion_tracks[: self.max_points]:
            cx, cy = int(track.center[0]), int(track.center[1])
            radius = int(clamp(math.sqrt(max(track.area, 1.0)) * 0.75, 3, 10))
            is_locked = track.id == locked_id
            if is_locked:
                color = AMBER
            elif track.missing_frames:
                color = DIM_GREEN
            else:
                color = SOFT_GREEN if track.hits < 4 else GREEN

            cv2.circle(image, (cx, cy), radius, color, 1 if not is_locked else 2, cv2.LINE_AA)
            cv2.circle(image, (cx, cy), 1, WHITE if is_locked else color, -1, cv2.LINE_AA)

            if track.hits >= 3 or is_locked:
                label = f"T{track.id}"
                if track.missing_frames:
                    label += f"-{track.missing_frames}"
                draw_text(image, label, (cx + radius + 4, cy - radius - 2), 0.35, color)

    def draw_yolo_detections(self, image: np.ndarray) -> None:
        if not self.continuous_yolo_enabled or not self.live_yolo_detections:
            return

        for detection in self.live_yolo_detections:
            x, y, w, h = detection.bbox
            color = AMBER if detection.label == "FACE" else CYAN
            cv2.rectangle(image, (x, y), (x + w, y + h), color, 2, cv2.LINE_AA)
            source = f"/{detection.source}" if detection.source else ""
            tag = f"{detection.label}{source} {detection.confidence * 100:.0f}%"
            label_y = max(18, y - 7)
            draw_text(image, tag, (x, label_y), 0.45, color)

    def draw_yolo_focus_panel(self, image: np.ndarray) -> None:
        if self.yolo_focus_target is None or self.current_frame_for_yolo is None:
            return

        h, w = image.shape[:2]
        panel_w = min(250, max(180, w // 5))
        panel_h = panel_w + 46
        x0 = 14
        y0 = 176 if self.hud_enabled else 16
        if y0 + panel_h > h - 12:
            y0 = max(12, h - panel_h - 12)

        fx, fy, fw, fh = expanded_bbox(self.yolo_focus_target.bbox, 18, w, h)
        crop = self.current_frame_for_yolo[fy : fy + fh, fx : fx + fw]
        if crop.size == 0:
            return

        preview = cv2.resize(crop, (panel_w - 18, panel_w - 18), interpolation=cv2.INTER_LINEAR)
        blend_overlay(image, (x0, y0, panel_w, panel_h), BLACK, alpha=0.66)
        cv2.rectangle(image, (x0, y0), (x0 + panel_w, y0 + panel_h), AMBER, 1)
        image[y0 + 9 : y0 + 9 + preview.shape[0], x0 + 9 : x0 + 9 + preview.shape[1]] = preview
        cv2.rectangle(
            image,
            (x0 + 9, y0 + 9),
            (x0 + panel_w - 9, y0 + panel_w - 9),
            AMBER,
            1,
        )

        source = f"/{self.yolo_focus_target.source}" if self.yolo_focus_target.source else ""
        stale = f" LOST {self.yolo_focus_target.missing_scans}" if self.yolo_focus_target.missing_scans else ""
        label = (
            f"FOCUS {self.yolo_focus_target.label}{source} "
            f"{self.yolo_focus_target.confidence * 100:.0f}%{stale}"
        )
        draw_text(image, label, (x0 + 10, y0 + panel_h - 16), 0.4, AMBER)

    def draw_reticle(self, image: np.ndarray) -> None:
        h, w = image.shape[:2]
        cx, cy = w // 2, h // 2
        cv2.circle(image, (cx, cy), self.lock_radius, DIM_GREEN, 1, cv2.LINE_AA)
        cv2.circle(image, (cx, cy), 9, GREEN, 1, cv2.LINE_AA)
        cv2.line(image, (cx - 42, cy), (cx - 12, cy), GREEN, 1, cv2.LINE_AA)
        cv2.line(image, (cx + 12, cy), (cx + 42, cy), GREEN, 1, cv2.LINE_AA)
        cv2.line(image, (cx, cy - 42), (cx, cy - 12), GREEN, 1, cv2.LINE_AA)
        cv2.line(image, (cx, cy + 12), (cx, cy + 42), GREEN, 1, cv2.LINE_AA)

    def draw_target(self, image: np.ndarray) -> None:
        x, y, w, h = self.tracker.bbox
        cx, cy = int(self.tracker.center[0]), int(self.tracker.center[1])
        stale = self.tracker.missing_frames > 0
        color = AMBER if stale else GREEN
        cv2.rectangle(image, (x, y), (x + w, y + h), color, 2, cv2.LINE_AA)
        corner = 13
        cv2.line(image, (x, y), (x + corner, y), WHITE, 1, cv2.LINE_AA)
        cv2.line(image, (x, y), (x, y + corner), WHITE, 1, cv2.LINE_AA)
        cv2.line(image, (x + w, y), (x + w - corner, y), WHITE, 1, cv2.LINE_AA)
        cv2.line(image, (x + w, y), (x + w, y + corner), WHITE, 1, cv2.LINE_AA)
        cv2.line(image, (x, y + h), (x + corner, y + h), WHITE, 1, cv2.LINE_AA)
        cv2.line(image, (x, y + h), (x, y + h - corner), WHITE, 1, cv2.LINE_AA)
        cv2.line(image, (x + w, y + h), (x + w - corner, y + h), WHITE, 1, cv2.LINE_AA)
        cv2.line(image, (x + w, y + h), (x + w, y + h - corner), WHITE, 1, cv2.LINE_AA)
        cv2.circle(image, (cx, cy), 4, color, -1, cv2.LINE_AA)

        vx, vy = self.tracker.velocity
        endpoint = (int(cx + vx * 8), int(cy + vy * 8))
        cv2.arrowedLine(image, (cx, cy), endpoint, CYAN, 1, cv2.LINE_AA, tipLength=0.35)

        track_tag = f"T{self.tracker.track_id} " if self.tracker.track_id is not None else ""
        tag = f"{track_tag}{self.tracker.label}"
        if self.tracker.confidence > 0.0:
            tag = f"{tag} {self.tracker.confidence * 100:.0f}%"
        draw_text(image, tag, (x, max(18, y - 8)), 0.45, color)

    def draw_inspector(self, image: np.ndarray) -> None:
        h, w = image.shape[:2]
        panel_w = min(230, max(160, w // 5))
        panel_h = panel_w + 42
        x0 = 14
        y0 = h - panel_h - 14
        if y0 < 90:
            return

        x, y, bw, bh = expanded_bbox(self.tracker.bbox, 18, w, h)
        crop = image[y : y + bh, x : x + bw].copy()
        if crop.size == 0:
            return
        crop = cv2.resize(crop, (panel_w - 18, panel_w - 18), interpolation=cv2.INTER_NEAREST)
        blend_overlay(image, (x0, y0, panel_w, panel_h), BLACK, alpha=0.62)
        cv2.rectangle(image, (x0, y0), (x0 + panel_w, y0 + panel_h), DIM_GREEN, 1)
        image[y0 + 9 : y0 + 9 + crop.shape[0], x0 + 9 : x0 + 9 + crop.shape[1]] = crop
        cv2.rectangle(
            image,
            (x0 + 9, y0 + 9),
            (x0 + panel_w - 9, y0 + panel_w - 9),
            GREEN,
            1,
        )
        draw_text(image, "TARGET INSPECTOR", (x0 + 10, y0 + panel_h - 15), 0.42, GREEN)

    def draw_radar(
        self,
        image: np.ndarray,
        candidates: Iterable[MotionCandidate],
    ) -> None:
        h, w = image.shape[:2]
        size = min(190, max(130, w // 5))
        x0 = w - size - 16
        y0 = 16
        center = (x0 + size // 2, y0 + size // 2)
        radius = size // 2 - 12
        blend_overlay(image, (x0, y0, size, size), BLACK, alpha=0.55)
        cv2.rectangle(image, (x0, y0), (x0 + size, y0 + size), DIM_GREEN, 1)
        cv2.circle(image, center, radius, GREEN, 1, cv2.LINE_AA)
        cv2.circle(image, center, radius // 2, DIM_GREEN, 1, cv2.LINE_AA)
        cv2.line(image, (center[0] - radius, center[1]), (center[0] + radius, center[1]), DIM_GREEN, 1)
        cv2.line(image, (center[0], center[1] - radius), (center[0], center[1] + radius), DIM_GREEN, 1)

        sweep_angle = (time.perf_counter() * 85.0) % 360.0
        sx = int(center[0] + math.cos(math.radians(sweep_angle)) * radius)
        sy = int(center[1] + math.sin(math.radians(sweep_angle)) * radius)
        cv2.line(image, center, (sx, sy), SOFT_GREEN, 1, cv2.LINE_AA)

        frame_cx, frame_cy = w / 2, h / 2
        scale = radius / max(frame_cx, frame_cy)
        for candidate in list(candidates)[:80]:
            dx = (candidate.center[0] - frame_cx) * scale
            dy = (candidate.center[1] - frame_cy) * scale
            if dx * dx + dy * dy > radius * radius:
                continue
            cv2.circle(image, (int(center[0] + dx), int(center[1] + dy)), 2, CYAN, -1, cv2.LINE_AA)

        if self.tracker.locked:
            dx = (self.tracker.center[0] - frame_cx) * scale
            dy = (self.tracker.center[1] - frame_cy) * scale
            cv2.circle(image, (int(center[0] + dx), int(center[1] + dy)), 5, AMBER, 1, cv2.LINE_AA)

        draw_text(image, "RADAR", (x0 + 9, y0 + size - 10), 0.42, GREEN)

    def draw_hud(self, image: np.ndarray) -> None:
        lock_state = "LOCK" if self.tracker.locked else "SCAN"
        flow_state = "ON" if self.optical_flow_enabled else "OFF"
        radar_state = "ON" if self.radar_enabled else "OFF"
        live_state = "ON" if self.continuous_yolo_enabled else "OFF"
        ensemble_state = "ON" if self.ensemble_yolo_enabled else "OFF"
        ensemble_count = len(self.ensemble_profile_indices())
        lines = [
            f"U6C PC SIGINT // {lock_state}",
            f"MODE {self.render_mode.upper()}  FPS {self.fps:04.1f}  ZOOM {self.zoom:.2f}X",
            f"SENS {self.motion_sensitivity_percent():.0f}%  MIN {self.min_area:.0f}  MERGE {self.motion_merge_radius}px",
            f"THR {self.threshold:02d}  RADIUS {self.lock_radius:03d}  TRACKS {len(self.motion_tracks):03d}",
            f"FLOW {flow_state}  RADAR {radar_state}",
            f"YOLO {self.current_model_name} {self.yolo.status}  LIVE {live_state}  ENS {ensemble_state}/{ensemble_count}",
            f"DET {len(self.live_yolo_detections):02d}  MERGE BEST CONF",
            f"STATUS {self.status}",
        ]
        x, y = 14, 24
        max_width = 0
        for line in lines:
            size, _ = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 1)
            max_width = max(max_width, size[0])
        blend_overlay(image, (8, 7, max_width + 18, 164), BLACK, alpha=0.52)
        for idx, line in enumerate(lines):
            draw_text(image, line, (x, y + idx * 19), 0.48, GREEN)

    def draw_menu(self, image: np.ndarray) -> None:
        if not self.menu_enabled:
            self.menu_buttons = []
            self.menu_sliders = []
            return

        h, w = image.shape[:2]
        button_w = 116
        button_h = 25
        gap = 6
        cols = 2
        rows = 10
        slider_count = 3
        slider_h = 26
        slider_gap = 10
        slider_block_h = slider_count * slider_h + (slider_count - 1) * slider_gap + 18
        panel_w = button_w * cols + gap * (cols - 1) + 20
        panel_h = 32 + rows * button_h + (rows - 1) * gap + slider_block_h + 18
        x0 = max(10, w - panel_w - 14)
        y0 = max(112, h - panel_h - 14)

        blend_overlay(image, (x0, y0, panel_w, panel_h), BLACK, alpha=0.62)
        cv2.rectangle(image, (x0, y0), (x0 + panel_w, y0 + panel_h), DIM_GREEN, 1)
        draw_text(image, "CONTROL MENU", (x0 + 10, y0 + 21), 0.45, GREEN)

        button_defs = [
            ("CAPTURE", "capture", False),
            ("UNLOCK", "unlock", self.tracker.locked),
            (f"LIVE {'ON' if self.continuous_yolo_enabled else 'OFF'}", "live", self.continuous_yolo_enabled),
            (f"ENS {'ON' if self.ensemble_yolo_enabled else 'OFF'}", "ensemble", self.ensemble_yolo_enabled),
            (f"YOLO {'ON' if self.yolo.enabled else 'OFF'}", "yolo", self.yolo.enabled),
            (f"MODEL {self.current_model_name}", "model", self.yolo.enabled),
            (f"MODE {self.render_mode.upper()}", "mode", False),
            (f"FLOW {'ON' if self.optical_flow_enabled else 'OFF'}", "flow", self.optical_flow_enabled),
            (f"RADAR {'ON' if self.radar_enabled else 'OFF'}", "radar", self.radar_enabled),
            (f"MIRROR {'ON' if self.mirror else 'OFF'}", "mirror", self.mirror),
            ("ZOOM +", "zoom_in", False),
            ("ZOOM -", "zoom_out", False),
            ("ZOOM RESET", "zoom_reset", False),
            ("THR -", "thr_down", False),
            ("THR +", "thr_up", False),
            ("RADIUS -", "radius_down", False),
            ("RADIUS +", "radius_up", False),
            (f"HUD {'ON' if self.hud_enabled else 'OFF'}", "hud", self.hud_enabled),
            ("MENU B", "menu", False),
        ]

        self.menu_buttons = []
        self.menu_sliders = []
        start_x = x0 + 10
        start_y = y0 + 34
        for idx, (label, action, active) in enumerate(button_defs):
            col = idx % cols
            row = idx // cols
            bx = start_x + col * (button_w + gap)
            by = start_y + row * (button_h + gap)
            if by + button_h > y0 + panel_h - 8:
                break
            button = MenuButton(label=label, action=action, rect=(bx, by, button_w, button_h), active=active)
            self.menu_buttons.append(button)
            fill = SOFT_GREEN if active else DIM_GREEN
            border = GREEN if active else SOFT_GREEN
            blend_overlay(image, button.rect, fill, alpha=0.22 if active else 0.12)
            cv2.rectangle(image, (bx, by), (bx + button_w, by + button_h), border, 1)
            color = WHITE if active else GREEN
            draw_text(image, label, (bx + 7, by + 17), 0.38, color)

        slider_defs = [
            (
                "SENS",
                "sensitivity",
                self.motion_sensitivity_percent() / 100.0,
                f"{self.motion_sensitivity_percent():.0f}%",
            ),
            ("MIN SIZE", "min_area", self.min_area_slider_value(), f"{self.min_area:.0f}"),
            (
                "BODY MERGE",
                "merge_radius",
                clamp(self.motion_merge_radius / 50.0, 0.0, 1.0),
                f"{self.motion_merge_radius}px",
            ),
        ]
        slider_x = start_x
        slider_w = panel_w - 20
        slider_y = start_y + rows * button_h + (rows - 1) * gap + 13
        for idx, (label, action, value, value_text) in enumerate(slider_defs):
            sy = slider_y + idx * (slider_h + slider_gap)
            rect = (slider_x, sy, slider_w, slider_h)
            self.menu_sliders.append(MenuSlider(label, action, rect, value, value_text))
            draw_text(image, f"{label} {value_text}", (slider_x, sy - 3), 0.36, GREEN)
            track_y = sy + slider_h // 2
            cv2.line(image, (slider_x, track_y), (slider_x + slider_w, track_y), DIM_GREEN, 2, cv2.LINE_AA)
            knob_x = int(slider_x + clamp(value, 0.0, 1.0) * slider_w)
            cv2.rectangle(image, (slider_x, sy), (slider_x + slider_w, sy + slider_h), DIM_GREEN, 1)
            cv2.circle(image, (knob_x, track_y), 7, GREEN, -1, cv2.LINE_AA)
            cv2.circle(image, (knob_x, track_y), 8, WHITE, 1, cv2.LINE_AA)

    def draw_scanlines(self, image: np.ndarray) -> None:
        image[::4] = cv2.addWeighted(image[::4], 0.72, np.zeros_like(image[::4]), 0.28, 0)

    def update_fps(self) -> None:
        now = time.perf_counter()
        elapsed = now - self.last_fps_time
        if elapsed >= 0.5:
            instant = self.fps_frames / max(elapsed, 1e-6)
            self.fps = instant if self.fps == 0.0 else self.fps * 0.65 + instant * 0.35
            self.fps_frames = 0
            self.last_fps_time = now




def parse_args() -> argparse.Namespace:
    here = app_base_dir()
    parser = argparse.ArgumentParser(
        description="U6C PC webcam micro-motion scanner",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--camera", type=int, default=0, help="webcam index")
    parser.add_argument("--width", type=int, default=1280, help="requested camera width")
    parser.add_argument("--height", type=int, default=720, help="requested camera height")
    parser.add_argument("--fps", type=int, default=30, help="requested camera FPS")
    parser.add_argument("--window-width", type=int, default=1280, help="desktop window width")
    parser.add_argument("--window-height", type=int, default=720, help="desktop window height")
    parser.add_argument("--lan-stream", action="store_true", help="broadcast the processed camera view over LAN")
    parser.add_argument("--lan-host", default="0.0.0.0", help="LAN stream bind address")
    parser.add_argument("--lan-port", type=int, default=8080, help="LAN stream HTTP port")
    parser.add_argument("--lan-fps", type=float, default=20.0, help="maximum LAN stream FPS, capped at 60")
    parser.add_argument("--lan-quality", type=int, default=72, help="LAN stream JPEG quality")
    parser.add_argument("--lan-scale", type=float, default=0.7, help="LAN stream output scale")
    parser.add_argument("--phone-camera", action="store_true", help="use a LAN phone/browser camera as the input camera")
    parser.add_argument("--phone-host", default="0.0.0.0", help="phone camera input bind address")
    parser.add_argument("--phone-port", type=int, default=8090, help="phone camera input HTTP port")
    parser.add_argument("--phone-fps", type=float, default=20.0, help="phone browser upload FPS, capped at 60")
    parser.add_argument("--phone-width", type=int, default=1280, help="phone browser upload width")
    parser.add_argument("--phone-input-aspect", type=parse_aspect_ratio, default=16.0 / 9.0, help="phone browser camera aspect request, such as 16:9 or 4:3")
    parser.add_argument("--phone-output-aspect", type=parse_aspect_ratio, default=16.0 / 9.0, help="phone frame aspect used by the PC scanner; use native to disable")
    parser.add_argument("--phone-aspect-mode", choices=("fit", "crop", "stretch", "native"), default="fit", help="how phone frames are adapted to the PC scanner aspect")
    parser.add_argument("--phone-quality", type=int, default=72, help="phone browser JPEG upload quality")
    parser.add_argument("--phone-processed-fps", type=float, default=20.0, help="maximum processed view FPS sent back to the phone page, capped at 60")
    parser.add_argument("--phone-processed-quality", type=int, default=68, help="processed phone view JPEG quality")
    parser.add_argument("--phone-processed-scale", type=float, default=0.62, help="processed phone view output scale")
    parser.add_argument("--phone-max-upload-mb", type=float, default=8.0, help="maximum phone frame upload size in MB")
    parser.add_argument("--phone-stale-seconds", type=float, default=2.5, help="seconds before a phone camera frame is considered paused")
    parser.add_argument("--phone-https", action="store_true", help="serve the phone camera input page over HTTPS")
    parser.add_argument("--phone-cert", type=Path, default=here / "certs" / "u6c_phone_server.crt", help="HTTPS server certificate")
    parser.add_argument("--phone-key", type=Path, default=here / "certs" / "u6c_phone_server.key", help="HTTPS server private key")
    parser.add_argument("--phone-ca-cert", type=Path, default=here / "certs" / "u6c_phone_ca.crt", help="CA certificate to install on phones")
    parser.add_argument("--phone-cert-port", type=int, default=8091, help="HTTP port for the phone certificate helper page")
    parser.add_argument("--threshold", type=int, default=14, help="motion threshold")
    parser.add_argument("--min-area", type=float, default=24.0, help="minimum motion area")
    parser.add_argument("--max-area", type=float, default=220000.0, help="maximum motion area")
    parser.add_argument("--motion-merge-radius", type=int, default=18, help="pixels used to merge nearby motion fragments")
    parser.add_argument("--motion-noise-limit", type=float, default=0.32, help="drop motion frames when too much of the screen changes")
    parser.add_argument("--max-points", type=int, default=220, help="maximum motion points to draw")
    parser.add_argument("--lock-radius", type=int, default=82, help="capture radius around the reticle")
    parser.add_argument("--flow-radius", type=int, default=36, help="optical-flow feature search padding")
    parser.add_argument("--memory-frames", type=int, default=42, help="frames to keep predicting after loss")
    parser.add_argument("--track-max-distance", type=float, default=42.0, help="maximum per-frame distance for motion-dot track matching")
    parser.add_argument("--track-memory", type=int, default=18, help="frames to keep motion-dot tracks alive after a miss")
    parser.add_argument("--motion-dot-life", type=float, default=2.4, help="seconds for motion dots to fade out")
    parser.add_argument("--motion-dot-density", type=float, default=55.0, help="lower values create more motion dots")
    parser.add_argument("--background-alpha", type=float, default=0.025, help="motion background learning rate")
    parser.add_argument("--zoom", type=float, default=1.0, help="initial digital zoom")
    parser.add_argument("--mirror", action="store_true", help="mirror the webcam image")
    parser.add_argument("--no-flow", action="store_true", help="start with optical flow disabled")
    parser.add_argument("--no-radar", action="store_true", help="start with radar disabled")
    parser.add_argument("--no-yolo", action="store_true", help="disable YOLO snap tagging")
    parser.add_argument("--continuous-yolo", action="store_true", help="start live YOLO full-frame scanning")
    parser.add_argument("--ensemble-yolo", action="store_true", help="accepted for old commands; use E or ENS in-app to enable")
    parser.add_argument("--yolo-interval", type=float, default=0.35, help="seconds between live YOLO scans")
    parser.add_argument("--yolo-max-detections", type=int, default=24, help="maximum live YOLO boxes to draw")
    parser.add_argument("--yolo-focus-memory", type=int, default=12, help="live YOLO scans before a selected focus target disappears")
    parser.add_argument("--preload-yolo", action="store_true", help="preload available YOLO model profiles at startup")
    parser.add_argument("--discord-person-alerts", action="store_true", help="send Discord snapshots when PERSON is detected")
    parser.add_argument("--discord-webhook", default="", help="Discord webhook URL; alternatively set U6C_DISCORD_WEBHOOK")
    parser.add_argument("--discord-person-confidence", type=float, default=0.45, help="minimum PERSON confidence for Discord alerts")
    parser.add_argument("--discord-person-cooldown", type=float, default=120.0, help="seconds before alerting again for the same person")
    parser.add_argument("--discord-person-memory", type=float, default=20.0, help="seconds to remember a person after they disappear")
    parser.add_argument("--discord-global-cooldown", type=float, default=8.0, help="minimum seconds between any Discord person alerts")
    parser.add_argument("--discord-snapshot-delay", type=float, default=0.5, help="seconds to wait after PERSON detection before sending the Discord snapshot")
    parser.add_argument("--discord-quality", type=int, default=82, help="Discord snapshot JPEG quality")
    parser.add_argument("--discord-scale", type=float, default=0.85, help="Discord snapshot image scale")
    parser.add_argument(
        "--model-label",
        help="label to use for single-class ONNX models, such as FACE or PERSON",
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=here / "models" / "yolov8n.onnx",
        help="path to a YOLOv8 ONNX model",
    )
    return parser.parse_args()


def normalize_args(args: argparse.Namespace) -> argparse.Namespace:
    args.fps = int(round(clamp(args.fps, 1, 60)))
    args.lan_fps = float(clamp(args.lan_fps, 1.0, 60.0))
    args.phone_fps = float(clamp(args.phone_fps, 1.0, 60.0))
    args.phone_processed_fps = float(clamp(args.phone_processed_fps, 1.0, 60.0))
    return args


def main() -> int:
    args = normalize_args(parse_args())

    
    scanner = U6CScanner(args)
    return scanner.run()


if __name__ == "__main__":
    raise SystemExit(main())
