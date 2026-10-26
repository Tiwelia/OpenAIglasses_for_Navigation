"""摄像头传图、按需场景描述与本地记录。不导入原导航/语音/深度学习模块。"""
from __future__ import annotations

import asyncio
import base64
from collections import deque
from contextlib import asynccontextmanager, closing, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Optional
import uuid

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGE_PIXELS = 12_000_000
FRESH_SECONDS = 10
DEFAULT_QUESTION = "请简要描述画面中的主要物体、位置关系和可见文字。"
SYSTEM_PROMPT = (
    "你是课程演示中的图像描述助手。只依据本张图片回答，用简体中文，控制在150字以内。"
    "看不清或不确定的内容请明确说明，不编造距离、身份或不可见信息。"
    "描述可见环境，不作安全通行判断，不指挥用户过马路。"
)


@dataclass(frozen=True)
class Settings:
    api_key: str = ""
    ai_enabled: bool = True
    model: str = "qwen3-vl-plus"
    api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    timeout: float = 25.0
    data_dir: Path = HERE / "data"

    @classmethod
    def from_env(cls):
        # 进程环境 > 课程版配置 > 原工程配置；不读取源码中的默认Key。
        load_dotenv(HERE / ".env", override=False)
        load_dotenv(ROOT / ".env", override=False)
        return cls(
            api_key=os.getenv("DASHSCOPE_API_KEY", "").strip(),
            ai_enabled=os.getenv("COURSE_AI_ENABLED", "1").strip().lower() not in {"0", "false", "no"},
            model=os.getenv("COURSE_MODEL", "qwen3-vl-plus").strip(),
            api_base=os.getenv("COURSE_API_BASE", cls.api_base).rstrip("/"),
            timeout=max(5.0, min(60.0, float(os.getenv("COURSE_AI_TIMEOUT", "25")))),
            data_dir=Path(os.getenv("COURSE_DATA_DIR", str(HERE / "data"))).resolve(),
        )


def prepare_image(data: bytes):
    """校验真实图像，统一为小尺寸JPEG，限制像素与上传大小。"""
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("请选择不超过2MB的JPEG或PNG图片。")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {"JPEG", "PNG"}:
                raise ValueError("只支持JPEG或PNG图片。")
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise ValueError("图片像素过大，请缩小后重试。")
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((960, 960))
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=78)
            return output.getvalue(), image.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("图片无法解码，请换一张图片。") from exc


@dataclass(frozen=True)
class Frame:
    jpeg: bytes
    number: int
    source: str
    captured_at: str
    received_at: float
    size: tuple[int, int]


class FrameHub:
    def __init__(self):
        self.frame: Optional[Frame] = None
        self.camera: Optional[WebSocket] = None
        self.viewers: set[asyncio.Queue] = set()
        self.times = deque(maxlen=60)
        self.sequence = 0
        self.invalid_frames = 0
        self.transport = None

    async def put(self, data: bytes, source: str):
        jpeg, size = await asyncio.to_thread(prepare_image, data)
        self.sequence += 1
        frame = Frame(jpeg, self.sequence, source, datetime.now(timezone.utc).isoformat(), time.monotonic(), size)
        self.frame = frame
        if source == "camera":
            self.times.append(frame.received_at)
        # 每个观看者只留最新一帧，慢客户端不会阻塞摄像头或积累视频延迟。
        for queue in tuple(self.viewers):
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(frame.jpeg)
        return frame

    def status(self):
        age = time.monotonic() - self.frame.received_at if self.frame else None
        fps = 0.0
        if self.camera is not None and self.times and time.monotonic() - self.times[-1] < 2 and len(self.times) > 1:
            fps = (len(self.times) - 1) / max(.01, self.times[-1] - self.times[0])
        return {
            "camera_connected": self.camera is not None,
            "has_frame": self.frame is not None,
            "source": self.frame.source if self.frame else None,
            "frame_id": self.frame.number if self.frame else None,
            "frame_age_seconds": round(age, 1) if age is not None else None,
            "stale": self.frame is not None and self.frame.source == "camera" and age > FRESH_SECONDS,
            "image_size": list(self.frame.size) if self.frame else None,
            "receive_fps": round(fps, 1),
            "invalid_frames": self.invalid_frames,
            "transport": self.transport,
        }


class RecordStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.database = directory / "records.sqlite3"
        self.images = directory / "images"

    def initialize(self):
        self.images.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)")

    def save(self, record: dict, jpeg: bytes):
        image = self.images / (record["id"] + ".jpg")
        image.write_bytes(jpeg)
        try:
            with closing(sqlite3.connect(self.database)) as connection, connection:
                connection.execute("INSERT INTO records VALUES (?, ?, ?)", (record["id"], record["created_at"], json.dumps(record, ensure_ascii=False)))
        except Exception:
            image.unlink(missing_ok=True)
            raise

    def list(self, limit: Optional[int] = 50):
        sql = "SELECT payload FROM records ORDER BY created_at DESC"
        with closing(sqlite3.connect(self.database)) as connection:
            rows = connection.execute(sql + (" LIMIT ?" if limit is not None else ""), (limit,) if limit is not None else ()).fetchall()
        return [json.loads(row[0]) for row in rows]

    def image_path(self, record_id: str):
        try:
            record_id = str(uuid.UUID(record_id))
        except ValueError:
            return None
        with closing(sqlite3.connect(self.database)) as connection:
            row = connection.execute("SELECT id FROM records WHERE id = ?", (record_id,)).fetchone()
        path = self.images / (record_id + ".jpg")
        return path if row and path.is_file() else None


class AnalysisRequest(BaseModel):
    question: str = Field(default=DEFAULT_QUESTION, max_length=240)


async def describe(client: httpx.AsyncClient, settings: Settings, frame: Frame, question: str):
    payload = {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(frame.jpeg).decode("ascii")}},
                {"type": "text", "text": question},
            ]},
        ],
        "stream": False,
        "max_tokens": 384,
    }
    try:
        response = await asyncio.wait_for(
            client.post(settings.api_base + "/chat/completions", headers={"Authorization": "Bearer " + settings.api_key}, json=payload),
            timeout=settings.timeout,
        )
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        raise HTTPException(504, "分析超时。画面传输仍可使用，请稍后重试。") from exc
    except httpx.RequestError as exc:
        raise HTTPException(502, "暂时连接不到AI服务，请检查网络后重试。") from exc
    if response.status_code in (401, 403):
        raise HTTPException(502, "AI密钥无效或没有访问权限，请检查服务端配置。")
    if response.status_code == 429:
        raise HTTPException(503, "AI服务限流或额度受限，请稍后重试并检查账户。")
    if response.is_error:
        raise HTTPException(502, f"AI服务暂时不可用（HTTP {response.status_code}），请检查模型配置或稍后重试。")
    try:
        answer = response.json()["choices"][0]["message"]["content"]
        if isinstance(answer, list):
            answer = "\n".join(item.get("text", "") for item in answer if isinstance(item, dict))
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("empty answer")
        return answer.strip()
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(502, "AI没有返回有效描述，请重试。") from exc


def create_app(settings: Optional[Settings] = None, transport=None):
    settings = settings or Settings.from_env()
    hub = FrameHub()
    store = RecordStore(settings.data_dir)
    analysis_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(store.initialize)
        async with httpx.AsyncClient(timeout=httpx.Timeout(settings.timeout, connect=5), transport=transport) as client:
            app.state.ai_client = client
            yield

    app = FastAPI(title="AI Glass · 综合实践精简版", lifespan=lifespan)
    app.state.hub, app.state.store, app.state.settings = hub, store, settings
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.get("/")
    async def index():
        return FileResponse(HERE / "static" / "index.html")

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": "course-1.0"}

    @app.get("/preview")
    async def preview():
        return FileResponse(HERE / "static" / "preview.html")

    @app.get("/api/status")
    async def status():
        return {**hub.status(), "ai_configured": settings.ai_enabled and bool(settings.api_key), "ai_enabled": settings.ai_enabled, "model": settings.model, "analyzing": analysis_lock.locked()}

    @app.get("/api/frame")
    async def frame():
        if hub.frame is None:
            raise HTTPException(404, "还没有图像。")
        return Response(hub.frame.jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/image")
    async def upload(request: Request):
        # 流式限额，避免先将任意大小的上传读入内存；不依赖multipart。
        chunks, count = [], 0
        async for chunk in request.stream():
            count += len(chunk)
            if count > MAX_IMAGE_BYTES:
                raise HTTPException(413, "图片超过2MB，请缩小后重试。")
            chunks.append(chunk)
        try:
            frame = await hub.put(b"".join(chunks), "upload")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"frame_id": frame.number, "image_size": list(frame.size)}

    @app.post("/api/analyze")
    async def analyze(body: AnalysisRequest):
        if not settings.ai_enabled:
            raise HTTPException(503, "当前为纯预览模式，AI分析已停用。预览和截图均不调用AI。")
        if not settings.api_key:
            raise HTTPException(503, "尚未配置AI密钥。请按课程版说明配置后重启服务。")
        if hub.frame is None:
            raise HTTPException(409, "还没有图像，请先接入摄像头或上传图片。")
        if hub.status()["stale"]:
            raise HTTPException(409, "摄像头画面已超时，请重新连接或上传一张图片。")
        if analysis_lock.locked():
            raise HTTPException(409, "正在分析上一张图片，请稍候。")
        async with analysis_lock:
            # 固定本次图片，后续实时帧不会替换这次请求或保存的证据。
            frame = hub.frame
            question = body.question.strip() or DEFAULT_QUESTION
            start = time.perf_counter()
            answer = await describe(app.state.ai_client, settings, frame, question)
            record_id = str(uuid.uuid4())
            record = {
                "id": record_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "captured_at": frame.captured_at,
                "frame_id": frame.number,
                "source": frame.source,
                "question": question,
                "answer": answer,
                "model": settings.model,
                "elapsed_ms": round((time.perf_counter() - start) * 1000),
                "image_url": f"/api/records/{record_id}/image",
                "image_file": f"images/{record_id}.jpg",
            }
            try:
                await asyncio.to_thread(store.save, record, frame.jpeg)
            except OSError as exc:
                raise HTTPException(507, "分析完成，但记录写入失败，请检查磁盘空间后重试。") from exc
            except sqlite3.Error as exc:
                raise HTTPException(500, "分析完成，但记录保存失败，请检查课程版data目录。") from exc
            return record

    @app.get("/api/history")
    async def history():
        return await asyncio.to_thread(store.list)

    @app.get("/api/history/export")
    async def export():
        records = await asyncio.to_thread(store.list, None)
        payload = {"format": "aiglass-course-1", "exported_at": datetime.now(timezone.utc).isoformat(), "records": records}
        return Response(json.dumps(payload, ensure_ascii=False, indent=2), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="aiglass-records.json"'})

    @app.get("/api/records/{record_id}/image")
    async def record_image(record_id: str):
        path = await asyncio.to_thread(store.image_path, record_id)
        if path is None:
            raise HTTPException(404, "记录图像不存在。")
        return FileResponse(path, media_type="image/jpeg")

    @app.websocket("/ws/camera")
    async def camera(ws: WebSocket):
        if hub.camera is not None:
            await ws.close(code=1013)
            return
        hub.camera = ws
        hub.transport = ws.query_params.get("transport", "wifi")
        if hub.transport not in {"wifi", "usb", "replay"}:
            hub.transport = "unknown"
        hub.times.clear()
        try:
            await ws.accept()
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    try:
                        await hub.put(msg["bytes"], "camera")
                    except ValueError:
                        hub.invalid_frames += 1
                        # 原固件的文本SNAP标记可忽略；无效二进制帧不覆盖有效画面。
        except WebSocketDisconnect:
            pass
        finally:
            if hub.camera is ws:
                hub.camera = None

    @app.websocket("/ws/viewer")
    async def viewer(ws: WebSocket):
        await ws.accept()
        queue = asyncio.Queue(maxsize=1)
        hub.viewers.add(queue)
        if hub.frame:
            queue.put_nowait(hub.frame.jpeg)

        async def send():
            while True:
                image = await queue.get()
                await asyncio.wait_for(ws.send_bytes(image), timeout=3)

        async def receive():
            while True:
                if (await ws.receive())["type"] == "websocket.disconnect":
                    return

        tasks = [asyncio.create_task(send()), asyncio.create_task(receive())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            hub.viewers.discard(queue)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("COURSE_PORT", "8081"))
    print(f"\nAI Glass 综合实践精简版：http://127.0.0.1:{port}")
    print("设备连接同一网络；操作说明见 course/README.md。按 Ctrl+C 停止。\n")
    uvicorn.run(app, host="0.0.0.0", port=port, access_log=False, ws_max_size=MAX_IMAGE_BYTES + 1024)
