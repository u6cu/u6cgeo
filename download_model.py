#!/usr/bin/env python3
"""
Download optional YOLO ONNX models used by u6c_pc_webcam.py.

The scanner runs without these files. With a model, captured targets get a
one-shot label such as PERSON, FACE, AIRPLANE, BIRD, or UNKNOWN.
"""

from __future__ import annotations

import argparse
import urllib.error
import urllib.request
from pathlib import Path


MODEL_SPECS = {
    "people": {
        "filename": "yolov8n.onnx",
        "min_bytes": 5 * 1024 * 1024,
        "urls": [
            "https://huggingface.co/Kalray/yolov8/resolve/main/yolov8n.onnx?download=true",
            "https://huggingface.co/SpotLab/YOLOv8Detection/resolve/main/yolov8n.onnx?download=true",
            "https://github.com/yoobright/yolo-onnx/raw/master/yolov8n.onnx",
        ],
        "note": "COCO model. Detects full bodies as PERSON plus other common objects.",
    },
    "general-s": {
        "filename": "yolov8s.onnx",
        "min_bytes": 20 * 1024 * 1024,
        "urls": [
            "https://huggingface.co/cabelo/yolov8/resolve/main/yolov8s.onnx?download=true",
        ],
        "note": "YOLOv8s COCO model. Larger and more accurate than nano.",
    },
    "general-m": {
        "filename": "yolov8m.onnx",
        "min_bytes": 70 * 1024 * 1024,
        "urls": [
            "https://huggingface.co/cabelo/yolov8/resolve/main/yolov8m.onnx?download=true",
            "https://huggingface.co/amd/yolov8m/resolve/main/yolov8m.onnx?download=true",
        ],
        "note": "YOLOv8m COCO model. Medium-size broad object detector.",
    },
    "general-l": {
        "filename": "yolov8l.onnx",
        "min_bytes": 150 * 1024 * 1024,
        "urls": [
            "https://huggingface.co/cabelo/yolov8/resolve/main/yolov8l.onnx?download=true",
        ],
        "note": "YOLOv8l COCO model. Large, slower, stronger broad object detector.",
    },
    "general-x": {
        "filename": "yolov8x.onnx",
        "min_bytes": 240 * 1024 * 1024,
        "urls": [
            "https://huggingface.co/cabelo/yolov8/resolve/main/yolov8x.onnx?download=true",
        ],
        "note": "YOLOv8x COCO model. Extra-large and very slow on CPU.",
    },
    "face": {
        "filename": "yolov8n-face-lindevs.onnx",
        "min_bytes": 5 * 1024 * 1024,
        "urls": [
            "https://github.com/lindevs/yolov8-face/releases/latest/download/yolov8n-face-lindevs.onnx",
            "https://huggingface.co/deepghs/yolo-face/resolve/main/yolov8n-face/model.onnx?download=true",
        ],
        "note": "Small face-specific YOLOv8 model. Run with --model-label FACE.",
    },
    "face-s": {
        "filename": "yolov8s-face-lindevs.onnx",
        "min_bytes": 20 * 1024 * 1024,
        "urls": [
            "https://github.com/lindevs/yolov8-face/releases/latest/download/yolov8s-face-lindevs.onnx",
        ],
        "note": "Larger face-specific YOLOv8 model. Slower, usually stronger.",
    },
}

YOLO_PT_FALLBACK_URLS = [
    "https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n.pt",
]


def download_file(url: str, dst: Path, min_bytes: int) -> bool:
    part = dst.with_suffix(dst.suffix + ".part")
    if part.exists():
        part.unlink()

    print(f"\nDownloading:\n  {url}\nTo:\n  {dst}")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 U6C-PC-Webcam",
            "Accept": "*/*",
        },
    )

    with urllib.request.urlopen(request, timeout=90) as response:
        total_header = response.headers.get("Content-Length")
        total = int(total_header) if total_header and total_header.isdigit() else None
        downloaded = 0
        chunk_size = 256 * 1024
        with open(part, "wb") as fh:
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                fh.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100.0
                    print(
                        f"\r  {downloaded / (1024 * 1024):.2f} MB / "
                        f"{total / (1024 * 1024):.2f} MB  {pct:.1f}%",
                        end="",
                    )
                else:
                    print(f"\r  {downloaded / (1024 * 1024):.2f} MB", end="")
        print()

    size = part.stat().st_size if part.exists() else 0
    if size < min_bytes:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"downloaded file was too small: {size} bytes")

    if dst.exists():
        dst.unlink()
    part.rename(dst)
    print(f"Saved: {dst} ({dst.stat().st_size / (1024 * 1024):.2f} MB)")
    return True


def try_download(urls: list[str], dst: Path, min_bytes: int) -> bool:
    errors: list[str] = []
    if dst.exists() and dst.stat().st_size >= min_bytes:
        print(f"Already exists: {dst}")
        return True

    for url in urls:
        try:
            return download_file(url, dst, min_bytes)
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            print(f"Download failed: {type(exc).__name__}: {exc}")

    print("\nAll attempts failed:")
    for error in errors:
        print(f"  - {error}")
    return False


def parse_args() -> argparse.Namespace:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Download optional YOLO models for U6C PC Webcam",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--models-dir", type=Path, default=here / "models")
    parser.add_argument(
        "--model",
        choices=sorted(MODEL_SPECS),
        default="people",
        help="model family to download",
    )
    parser.add_argument("--all", action="store_true", help="download every built-in model profile")
    parser.add_argument("--general", action="store_true", help="download all general COCO YOLO profiles")
    parser.add_argument("--url", action="append", help="custom ONNX URL to try first")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.models_dir.mkdir(parents=True, exist_ok=True)

    if args.all:
        wanted = list(MODEL_SPECS)
    elif args.general:
        wanted = ["people", "general-s", "general-m", "general-l", "general-x"]
    else:
        wanted = [args.model]
    failures = 0
    for model_name in wanted:
        spec = MODEL_SPECS[model_name]
        dst = args.models_dir / spec["filename"]
        urls = (list(args.url or []) if len(wanted) == 1 else []) + spec["urls"]
        print(f"\nModel: {model_name}")
        print(spec["note"])

        if try_download(urls, dst, min_bytes=spec["min_bytes"]):
            continue

        failures += 1
        if model_name == "people":
            print("\nONNX download failed. Trying official YOLOv8n .pt fallback...")
            pt_dst = args.models_dir / "yolov8n.pt"
            if try_download(YOLO_PT_FALLBACK_URLS, pt_dst, min_bytes=5 * 1024 * 1024):
                print(
                    "\nDownloaded yolov8n.pt. OpenCV needs ONNX, so export this file "
                    "to ONNX before using YOLO tagging."
                )

    if failures:
        print("\nSome downloads did not complete. The scanner still works with --no-yolo.")
        return 1

    print("\nDONE. Requested model downloads are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
