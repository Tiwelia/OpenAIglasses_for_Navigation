"""用已有录像或电脑摄像头模拟ESP32，默认不打开任何摄像头。"""
import argparse
import asyncio
from pathlib import Path

import cv2
import websockets


async def run(args):
    source = str(Path(args.video).resolve()) if args.video else args.camera
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise RuntimeError("无法打开录像或摄像头，请检查输入参数。")
    uri = f"ws://{args.host}:{args.port}/ws/camera?transport=replay"
    try:
        async with websockets.connect(uri, max_size=2 * 1024 * 1024) as ws:
            print(f"正在推送图像至 {uri}，按 Ctrl+C 停止。")
            while True:
                start = asyncio.get_running_loop().time()
                ok, frame = await asyncio.to_thread(cap.read)
                if not ok:
                    if args.video and args.loop:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        ok, frame = await asyncio.to_thread(cap.read)
                    if not ok:
                        break
                height, width = frame.shape[:2]
                if max(height, width) > 960:
                    factor = 960 / max(height, width)
                    frame = cv2.resize(frame, (round(width * factor), round(height * factor)))
                ok, image = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 78])
                if ok:
                    await ws.send(image.tobytes())
                await asyncio.sleep(max(0, 1 / args.fps - (asyncio.get_running_loop().time() - start)))
    finally:
        cap.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--video", help="本地录像路径")
    sources.add_argument("--camera", type=int, help="明确指定电脑摄像头编号，例如0")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--fps", type=float, default=5)
    parser.add_argument("--loop", action="store_true", help="循环录像")
    args = parser.parse_args()
    if not 1 <= args.fps <= 20:
        parser.error("fps须在1到20之间")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n图像推送已停止。")


if __name__ == "__main__":
    main()
