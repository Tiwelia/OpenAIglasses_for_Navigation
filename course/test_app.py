"""关键业务回归；只使用临时目录与模拟云端，不调用付费API。"""
import asyncio
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import time
import unittest

import httpx
from PIL import Image

from course.app import MAX_IMAGE_BYTES, Settings, create_app, prepare_image


def jpeg(color="red", size=(64, 48)):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, "JPEG")
    return output.getvalue()


class CourseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="course-test-")
        self.directory = Path(self.temp.name)
        self.calls = []
        self.provider = None

        async def transport(request):
            self.calls.append(json.loads(request.content))
            if self.provider:
                return await self.provider(request)
            return httpx.Response(200, json={"choices": [{"message": {"content": "画面中是一块红色区域。"}}]})

        self.settings = Settings(api_key="test-key-never-sent", data_dir=self.directory, timeout=.5)
        self.app = create_app(self.settings, transport=httpx.MockTransport(transport))
        self.context = self.app.router.lifespan_context(self.app)
        await self.context.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.context.__aexit__(None, None, None)
        self.temp.cleanup()

    async def upload(self, color="red"):
        response = await self.client.post("/api/image", content=jpeg(color))
        self.assertEqual(response.status_code, 200)
        return response.json()

    async def test_no_picture_does_not_call_ai(self):
        response = await self.client.post("/api/analyze", json={})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.calls, [])

    async def test_preview_only_blocks_ai_even_with_key_and_valid_picture(self):
        app = create_app(replace(self.settings, ai_enabled=False), transport=httpx.MockTransport(lambda request: self.fail("Preview must never call AI")))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                self.assertEqual((await client.get("/preview")).status_code, 200)
                self.assertEqual((await client.post("/api/image", content=jpeg())).status_code, 200)
                self.assertFalse((await client.get("/api/status")).json()["ai_enabled"])
                self.assertEqual((await client.post("/api/analyze", json={})).status_code, 503)
                self.assertEqual((await client.get("/api/history")).json(), [])

    async def test_invalid_upload_preserves_previous_picture(self):
        previous = await self.upload()
        response = await self.client.post("/api/image", content=b"not-a-picture")
        self.assertEqual(response.status_code, 400)
        status = (await self.client.get("/api/status")).json()
        self.assertEqual(status["frame_id"], previous["frame_id"])

    async def test_oversized_upload_is_rejected(self):
        response = await self.client.post("/api/image", content=b"x" * (MAX_IMAGE_BYTES + 1))
        self.assertEqual(response.status_code, 413)
        self.assertFalse((await self.client.get("/api/status")).json()["has_frame"])

    async def test_resize_and_format_validation(self):
        content, size = prepare_image(jpeg(size=(1920, 1080)))
        self.assertEqual(size, (960, 540))
        self.assertEqual(Image.open(io.BytesIO(content)).format, "JPEG")
        output = io.BytesIO()
        Image.new("RGB", (20, 20)).save(output, "GIF")
        with self.assertRaises(ValueError):
            prepare_image(output.getvalue())

    async def test_saved_image_matches_analyzed_frame_even_when_stream_changes(self):
        await self.upload("red")
        original = (await self.client.get("/api/frame")).content
        started, release = asyncio.Event(), asyncio.Event()

        async def provider(request):
            started.set()
            await release.wait()
            return httpx.Response(200, json={"choices": [{"message": {"content": "测试描述"}}]})

        self.provider = provider
        task = asyncio.create_task(self.client.post("/api/analyze", json={"question": "描述颜色"}))
        await started.wait()
        await self.upload("blue")
        status = await self.client.get("/api/status")
        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.json()["analyzing"])
        release.set()
        response = await task
        self.assertEqual(response.status_code, 200)
        record = response.json()
        self.assertEqual(record["frame_id"], 1)
        saved = await self.client.get(record["image_url"])
        self.assertEqual(saved.content, original)
        self.assertNotEqual(saved.content, (await self.client.get("/api/frame")).content)
        self.assertEqual(len((await self.client.get("/api/history")).json()), 1)
        exported = (await self.client.get("/api/history/export")).json()
        self.assertEqual(exported["records"][0]["id"], record["id"])
        self.assertNotIn(self.settings.api_key, json.dumps(exported))

    async def test_parallel_analysis_is_rejected_without_second_cloud_call(self):
        await self.upload()
        started, release = asyncio.Event(), asyncio.Event()

        async def provider(request):
            started.set()
            await release.wait()
            return httpx.Response(200, json={"choices": [{"message": {"content": "测试描述"}}]})

        self.provider = provider
        first = asyncio.create_task(self.client.post("/api/analyze", json={}))
        await started.wait()
        second = await self.client.post("/api/analyze", json={})
        self.assertEqual(second.status_code, 409)
        self.assertEqual(len(self.calls), 1)
        release.set()
        self.assertEqual((await first).status_code, 200)

    async def test_timeout_releases_lock_and_does_not_save_fake_result(self):
        await self.upload()

        async def provider(request):
            await asyncio.sleep(5)

        self.provider = provider
        response = await self.client.post("/api/analyze", json={})
        self.assertEqual(response.status_code, 504)
        self.assertFalse((await self.client.get("/api/status")).json()["analyzing"])
        self.assertEqual((await self.client.get("/api/history")).json(), [])

    async def test_provider_failure_does_not_expose_upstream_body_or_key(self):
        await self.upload()

        async def provider(request):
            return httpx.Response(401, text="secret: " + self.settings.api_key)

        self.provider = provider
        response = await self.client.post("/api/analyze", json={})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(self.settings.api_key, response.text)

    async def test_network_error_and_empty_answer_are_handled(self):
        await self.upload()

        async def disconnected(request):
            raise httpx.ConnectError("network down", request=request)

        self.provider = disconnected
        self.assertEqual((await self.client.post("/api/analyze", json={})).status_code, 502)

        async def empty(request):
            return httpx.Response(200, json={"choices": []})

        self.provider = empty
        self.assertEqual((await self.client.post("/api/analyze", json={})).status_code, 502)
        self.assertEqual((await self.client.get("/api/history")).json(), [])

    async def test_stale_camera_frame_is_not_sent_to_ai(self):
        await self.upload()
        self.app.state.hub.frame = replace(self.app.state.hub.frame, source="camera", received_at=time.monotonic() - 11)
        response = await self.client.post("/api/analyze", json={})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.calls, [])

    async def test_history_remains_after_app_recreation(self):
        await self.upload()
        self.assertEqual((await self.client.post("/api/analyze", json={})).status_code, 200)
        other = create_app(self.settings)
        async with other.router.lifespan_context(other):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=other), base_url="http://test") as client:
                self.assertEqual(len((await client.get("/api/history")).json()), 1)
                self.assertEqual((await client.get("/api/records/not-a-uuid/image")).status_code, 404)

    async def test_missing_key_allows_preview_but_blocks_cloud_calls(self):
        app = create_app(replace(self.settings, api_key=""))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                self.assertEqual((await client.post("/api/image", content=jpeg())).status_code, 200)
                self.assertFalse((await client.get("/api/status")).json()["ai_configured"])
                self.assertEqual((await client.post("/api/analyze", json={})).status_code, 503)

    async def test_slow_viewer_queue_keeps_only_latest_frame(self):
        queue = asyncio.Queue(maxsize=1)
        self.app.state.hub.viewers.add(queue)
        await self.app.state.hub.put(jpeg("red"), "camera")
        await self.app.state.hub.put(jpeg("blue"), "camera")
        self.assertEqual(queue.qsize(), 1)
        self.assertEqual(queue.get_nowait(), self.app.state.hub.frame.jpeg)


if __name__ == "__main__":
    unittest.main()
