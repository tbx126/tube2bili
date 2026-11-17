"""Download the two media links offered by 9xbuddy and mux without re-encoding."""
import json
from pathlib import Path
import re
import subprocess
import sys
import urllib.request
from urllib.parse import urlsplit

MAX_BYTES = 512 * 1024**2


def validate(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or parsed.username or parsed.password
            or parsed.port not in (None, 443)
            or not (host.endswith('.googlevideo.com') and parsed.path == '/videoplayback'
                    or host.endswith('.9xbud.com') and parsed.path.startswith('/down/'))):
        raise ValueError('Unexpected media location')


class MediaRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def save(client, url, target, limit):
    validate(url)
    request = urllib.request.Request(url, headers={
        'Referer': 'https://www.youtube.com/', 'Origin': 'https://www.youtube.com',
    })
    count = 0
    with client.open(request, timeout=12) as response, target.open('wb') as output:
        expected = int(response.headers.get('Content-Length', '0'))
        whole_range = re.fullmatch(r'bytes 0-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
        if response.status == 206 and whole_range and int(whole_range[1]) + 1 == int(whole_range[2]):
            expected = int(whole_range[2])
        elif response.status != 200:
            raise ValueError('Incomplete media response')
        if expected > limit:
            raise ValueError('Incomplete or oversized media response')
        while chunk := response.read(128 * 1024):
            count += len(chunk)
            if count > limit:
                raise ValueError('Media size limit reached')
            output.write(chunk)
    if not count or (expected and count != expected):
        raise ValueError('Incomplete media')
    return count


def main():
    job = json.load(sys.stdin)
    folder = Path(sys.argv[1])
    proxy = {'https': job['proxy']} if job['proxy'] else {}
    client = urllib.request.build_opener(urllib.request.ProxyHandler(proxy), MediaRedirect())
    video, audio = folder / 'hd-video.mp4', folder / 'hd-audio.m4a'
    output = folder / 'hd.mp4'
    try:
        reserved = save(client, job['video'], video, MAX_BYTES)
        remaining = MAX_BYTES - reserved
        for url in job['audio']:
            try:
                save(client, url, audio, remaining)
                break
            except (ValueError, OSError):
                audio.unlink(missing_ok=True)
        if not audio.exists():
            raise ValueError('No audio stream available')
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-protocol_whitelist', 'file',
                        '-i', str(video), '-protocol_whitelist', 'file', '-i', str(audio),
                        '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy', str(output)],
                       check=True, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not 0 < output.stat().st_size <= MAX_BYTES:
            raise ValueError('Invalid output size')
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    finally:
        video.unlink(missing_ok=True)
        audio.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError):
        sys.exit(1)
