"""Scan staged blobs (or fetched history) without printing secret values.

This is a focused pre-publication check, not a guarantee that all secrets are found.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


def git(*args):
    return subprocess.check_output(['git', *args])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', action='store_true')
    args = parser.parse_args()
    patterns = {
        'api-token': rb'(?:sk-[A-Za-z0-9_.-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})',
        'private-key': rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
        'personal-windows-path': rb'[A-Za-z]:[\\/]Users[\\/][A-Za-z0-9_-]+',
        'workspace-endpoint': rb'https://llm-[a-z0-9]+\.[a-z0-9-]+\.maas\.',
        'literal-wifi-password': rb'(?i)(?:#define\s+\w*PASSWORD\w*\s+|(?:const\s+char\s*\*\s*)?(?:password|wifi_password)\s*=\s*)"[^"\r\n]{1,}"',
    }
    # Match current local credentials exactly too; never emit their values.
    local_values = set()
    for path in [Path('.env'), Path('course/.env'), Path('course/firmware/wifi_camera/secrets.h')]:
        if not path.exists():
            continue
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if re.search(r'(?i)(api_key|token|password|wifi_ssid)', line):
                value = line.split('=', 1)[-1].strip() if '=' in line else line.split(maxsplit=2)[-1].strip()
                value = value.strip('"\' ;')
                if len(value) >= 6:
                    local_values.add(value.encode())
    if args.history:
        objects = []
        for line in git('rev-list', '--objects', 'origin/main').decode().splitlines():
            sha, _, path = line.partition(' ')
            if git('cat-file', '-t', sha).strip() == b'blob':
                objects.append((sha, path))
    else:
        objects = []
        for record in git('ls-files', '--stage', '-z').split(b'\0'):
            if record:
                metadata, path = record.split(b'\t', 1)
                objects.append((metadata.split()[1].decode(), path.decode()))
    findings = []
    total = 0
    for sha, path in objects:
        data = git('cat-file', 'blob', sha)
        total += len(data)
        categories = [name for name, pattern in patterns.items() if re.search(pattern, data)]
        if any(value in data for value in local_values if len(value) >= 16 or b'\0' not in data):
            categories.append('matches-local-credential')
        if not args.history and (path.endswith(('.wav', '.mp3', '.jpg', '.png', '.sqlite3', '.bin'))
                or path.endswith('/secrets.h') or Path(path).name == '.env'
                or path.startswith(('legacy/', 'course/data/', 'docs/history/'))):
            categories.append('private-or-generated-file')
        if categories:
            findings.append({'path': path, 'categories': categories, 'blob': sha[:12]})
    print(json.dumps(dict(scope='origin/main history' if args.history else 'index',
                         files=len(objects), bytes=total, findings=findings), ensure_ascii=False, indent=2))
    return bool(findings)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
