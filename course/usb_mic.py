"""Capture the real Sense microphone via usb_mic_check; no cloud calls."""
import argparse
import array
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
import wave

import serial


def make_listening_copy(source):
    """Remove constant DC and apply bounded gain; preserve the raw evidence."""
    source = Path(source)
    target = source.with_name(source.stem + '-listen.wav')
    if target.exists():
        raise ValueError(f'Refusing to overwrite {target}')
    with wave.open(str(source), 'rb') as wav:
        params = wav.getparams()
        if params.sampwidth != 2 or params.nchannels != 1:
            raise ValueError('Expected mono signed 16-bit PCM')
        values = array.array('h', wav.readframes(wav.getnframes()))
    if sys.byteorder != 'little':
        values.byteswap()
    dc = sum(values) / len(values)
    peak = max(abs(x - dc) for x in values)
    gain = min(16.0, 0.85 * 32767 / max(peak, 1.0))
    processed = array.array('h', (round((x - dc) * gain) for x in values))
    if sys.byteorder != 'little':
        processed.byteswap()
    with wave.open(str(target), 'wb') as wav:
        wav.setparams(params)
        wav.writeframes(processed.tobytes())
    target.with_suffix('.json').write_text(json.dumps(dict(
        raw_source=source.name, dc_removed=dc, gain=gain,
        processing='constant DC subtraction and bounded linear gain; no denoising'), indent=2), encoding='utf-8')
    return target


def capture(device, output):
    output = Path(output)
    if output.exists():
        raise ValueError(f"Refusing to overwrite {output}")
    with serial.Serial(device, 115200, timeout=0.5, write_timeout=2) as port:
        time.sleep(2)
        port.reset_input_buffer()
        port.write(b'I')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            line = port.readline()
            if line.startswith(b'[MIC] ready=1'):
                break
        else:
            raise RuntimeError('No ready response; verify port and usb_mic_check firmware')
        port.write(b'R')
        deadline = time.monotonic() + 12
        header = None
        while time.monotonic() < deadline:
            line = port.readline().strip()
            if line.startswith(b'[ERROR]'):
                raise RuntimeError(line.decode())
            if line.startswith(b'[RECORDING]'):
                print('Recording real ESP32 microphone for 5 seconds...', flush=True)
            header = re.fullmatch(rb'\[AUDIO\] bytes=(\d+) rate=16000 bits=16 channels=1 capture_ms=(\d+)', line)
            if header:
                break
        if not header or int(header[1]) != 160000:
            raise RuntimeError('Missing or invalid audio header')
        data = bytearray()
        deadline = time.monotonic() + 20
        while len(data) < 160000 and time.monotonic() < deadline:
            data.extend(port.read(min(4096, 160000 - len(data))))
        if len(data) != 160000 or port.read_until(b'[AUDIO_END]\r\n') != b'\n[AUDIO_END]\r\n':
            raise RuntimeError('Truncated PCM or missing end marker; no WAV saved')
    samples = array.array('h', data)
    if sys.byteorder != 'little':
        samples.byteswap()
    mean = sum(samples) / len(samples)
    rms = math.sqrt(sum((x - mean) ** 2 for x in samples) / len(samples))
    stats = dict(source='XIAO ESP32S3 Sense PDM microphone', transport='usb',
                 rate=16000, bits=16, channels=1, seconds=5, bytes=len(data),
                 capture_ms=int(header[2]), peak=max(abs(x) for x in samples),
                 ac_rms=round(rms, 2), dc_mean=round(mean, 2),
                 rms_dbfs=round(20 * math.log10(max(rms, 1e-9) / 32768), 2),
                 clipped_samples=sum(abs(x) >= 32767 for x in samples),
                 nonzero_samples=sum(x != 0 for x in samples),
                 pcm_sha256=hashlib.sha256(data).hexdigest())
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(data)
    with wave.open(str(output), 'rb') as wav:
        assert wav.getnframes() == 80000 and wav.readframes(80000) == data
    output.with_suffix('.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f'Listening copy: {make_listening_copy(output)}')
    return stats


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    capture(args.device, args.output)
