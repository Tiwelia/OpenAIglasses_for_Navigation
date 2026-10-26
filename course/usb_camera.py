"""Forward real ESP32 USB JPEG frames to the local browser preview. No AI calls."""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

import serial
import websockets

HEADER = re.compile(rb"\[FRAME\] bytes=(\d+) width=(\d+) height=(\d+) capture_ms=(\d+)")
MAX_JPEG = 2 * 1024 * 1024


def read_line(port, timeout=15):
    result = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = port.read(1)
        if data == b"\n":
            return bytes(result).rstrip(b"\r")
        if data:
            result.extend(data)
            if len(result) > 4096:
                raise ValueError("Serial header exceeds limit")
    raise TimeoutError("ESP32 USB response timeout")


def read_status(port):
    port.write(b"I")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        line = read_line(port)
        if line.startswith(b"[CAM_ERROR]") or line.startswith(b"[CAM] ready=0"):
            raise RuntimeError(line.decode("ascii", errors="replace"))
        if line.startswith(b"[CAM] ready=1"):
            return line.decode("ascii")
    raise TimeoutError("No camera-ready status")


def capture(port, command=b"V"):
    started = time.perf_counter()
    port.write(command)
    deadline = time.monotonic() + 15
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError("No frame header; upload current camera_check firmware")
        line = read_line(port)
        if line.startswith(b"[CAM_ERROR]"):
            raise RuntimeError(line.decode("ascii", errors="replace"))
        match = HEADER.fullmatch(line)
        if match:
            break
    size, width, height, capture_ms = map(int, match.groups())
    if not 4 <= size <= MAX_JPEG:
        raise ValueError(f"Invalid JPEG byte count: {size}")
    jpeg = bytearray()
    while len(jpeg) < size:
        if time.monotonic() > deadline:
            raise TimeoutError(f"Incomplete JPEG: {len(jpeg)}/{size}")
        jpeg.extend(port.read(size - len(jpeg)))
    footer = read_line(port)
    if not footer:
        footer = read_line(port)
    if footer != b"[FRAME_END]" or not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
        raise ValueError("Invalid JPEG or frame footer")
    return bytes(jpeg), {"width": width, "height": height, "bytes": size, "capture_ms": capture_ms,
                         "usb_request_ms": round((time.perf_counter() - started) * 1000, 1)}


async def run(args):
    uri = f"ws://{args.host}:{args.port}/ws/camera?transport=usb"
    counts = {"frames": 0, "reconnects": 0}
    recent = deque(maxlen=60)
    started = time.monotonic()
    report_time = 0
    stats_path = Path(args.stats).resolve()
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    while args.seconds == 0 or time.monotonic() - started < args.seconds:
        port = serial.Serial(port=None, baudrate=115200, timeout=.3, write_timeout=3)
        port.dtr = True
        port.rts = False
        port.port = args.device
        try:
            await asyncio.to_thread(port.open)
            status = await asyncio.to_thread(read_status, port)
            print(status, flush=True)
            async with websockets.connect(uri, max_size=MAX_JPEG, open_timeout=5, close_timeout=2) as ws:
                print(f"Real ESP32 {args.device} -> {uri}; Ctrl+C stops preview.", flush=True)
                first = True
                while args.seconds == 0 or time.monotonic() - started < args.seconds:
                    frame_start = time.monotonic()
                    jpeg, metadata = await asyncio.to_thread(capture, port, b"C" if first else b"V")
                    first = False
                    await ws.send(jpeg)
                    counts["frames"] += 1
                    recent.append(time.monotonic())
                    if time.monotonic() - report_time >= 5:
                        report_time = time.monotonic()
                        fps = (len(recent) - 1) / (recent[-1] - recent[0]) if len(recent) > 1 else 0
                        stats = {"updated_at": datetime.now(timezone.utc).isoformat(), "source": "ESP32 USB",
                                 "device": args.device, "camera_status": status, "fps": round(fps, 2),
                                 "running_seconds": round(time.monotonic() - started, 1), **counts, **metadata}
                        stats_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
                        print(json.dumps(stats), flush=True)
                    await asyncio.sleep(max(0, 1 / args.fps - (time.monotonic() - frame_start)))
        except (serial.SerialException, OSError, TimeoutError, ValueError, RuntimeError, websockets.exceptions.WebSocketException) as exc:
            counts["reconnects"] += 1
            recent.clear()
            print(f"Preview disconnected: {exc}; retry in 2 seconds.", flush=True)
            await asyncio.sleep(2)
        finally:
            port.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="COM5")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--fps", type=float, default=10)
    parser.add_argument("--seconds", type=float, default=0, help="0 runs until Ctrl+C")
    parser.add_argument("--stats", default="course/data/usb_preview_stats.json")
    args = parser.parse_args()
    if not 1 <= args.fps <= 15 or args.seconds < 0:
        parser.error("fps must be 1..15, seconds must be nonnegative")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nUSB preview stopped.")


if __name__ == "__main__":
    main()
