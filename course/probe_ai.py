"""Explicit, single-request cloud check. Requires --send; never prints credentials."""
import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path
import time
import wave

import httpx
from dotenv import load_dotenv


def run(args):
    root = Path(__file__).resolve().parent
    load_dotenv(root / '.env')
    load_dotenv(root.parent / '.env')
    output = Path(args.output)
    if output.exists():
        raise ValueError('Output already exists; choose a new evidence filename')
    data = Path(args.file).read_bytes()
    if args.kind == 'audio':
        with wave.open(args.file, 'rb') as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
                raise ValueError('Expected 16kHz mono 16-bit WAV')
            if not 0 < wav.getnframes() <= 16000 * 60:
                raise ValueError('Expected at most 60 seconds')
        model = args.model or os.getenv('COURSE_ASR_MODEL', 'qwen3-asr-flash')
        content = [{'type': 'input_audio', 'input_audio': {
            'data': 'data:audio/wav;base64,' + base64.b64encode(data).decode()}}]
        payload = dict(model=model, messages=[dict(role='user', content=content)],
                       stream=False, asr_options={'enable_itn': False})
    else:
        from course.app import prepare_image
        jpeg, _ = prepare_image(data)
        model = args.model or os.getenv('COURSE_MODEL', 'qwen3.8-flash')
        content = [{'type': 'image_url', 'image_url': {
            'url': 'data:image/jpeg;base64,' + base64.b64encode(jpeg).decode()}},
            {'type': 'text', 'text': '请用中文简短描述画面中实际可见的内容。如果有清晰文字，请抄录；看不清就说明，不猜测。'}]
        payload = dict(model=model, messages=[dict(role='user', content=content)],
                       stream=False, max_tokens=256, enable_thinking=False)
    if not args.send:
        print(json.dumps({'dry_run': True, 'model': model, 'bytes': len(data)}))
        return
    key = os.getenv('DASHSCOPE_API_KEY', '').strip()
    base = os.getenv('COURSE_API_BASE', '').rstrip('/')
    if not key or not base.startswith('https://'):
        raise ValueError('Missing key or HTTPS base URL')
    evidence = dict(kind=args.kind, model=model, source=str(Path(args.file).resolve()),
                    sha256=hashlib.sha256(data).hexdigest(), stream=False)
    start = time.monotonic()
    try:
        with httpx.Client(timeout=httpx.Timeout(60, connect=12)) as client:
            response = client.post(base + '/chat/completions', json=payload,
                                   headers={'Authorization': 'Bearer ' + key})
        evidence['elapsed_seconds'] = round(time.monotonic() - start, 3)
        evidence['http_status'] = response.status_code
        result = response.json()
        if response.is_success:
            evidence['answer'] = result['choices'][0]['message']['content']
            evidence['usage'] = result.get('usage')
            evidence['request_id'] = result.get('id')
        else:
            error = result.get('error', result)
            evidence['error'] = {k: str(error.get(k, '')).replace(key, '[REDACTED]')[:500]
                                 for k in ('code', 'message', 'type')}
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
        evidence['elapsed_seconds'] = round(time.monotonic() - start, 3)
        evidence['failure_type'] = type(exc).__name__
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=['audio', 'image'])
    parser.add_argument('--file', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--model')
    parser.add_argument('--send', action='store_true')
    run(parser.parse_args())
