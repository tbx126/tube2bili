import { spawn } from 'node:child_process';
import { createReadStream } from 'node:fs';
import { mkdtemp, rm, stat } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { pipeline } from 'node:stream/promises';

const maxBytes = 512 * 1024 ** 2;
const provider = 'https://9xbuddy.in';
let busy = false;

async function download(id, dir, signal) {
  const browser = spawn('chromium-headless-shell', [
    '--headless', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
    '--remote-debugging-pipe', '--user-data-dir=' + join(dir, 'profile'),
    ...(process.env.NINEBUDDY_PROXY ? ['--proxy-server=' + process.env.NINEBUDDY_PROXY] : []),
    'about:blank',
  ], { stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'], signal });
  let sequence = 0;
  let chunk = '';
  let completed;
  let rejected = false;
  const pending = new Map();
  const abort = () => {
    for (const callback of pending.values()) callback.reject(new Error('Browser stopped'));
    pending.clear();
  };
  browser.on('error', abort);
  browser.on('exit', abort);
  browser.stdio[3].on('error', abort);
  browser.stdio[4].on('error', abort);
  browser.stdio[4].setEncoding('utf8').on('data', data => {
    chunk += data;
    let end;
    while ((end = chunk.indexOf('\0')) !== -1) {
      const message = JSON.parse(chunk.slice(0, end));
      chunk = chunk.slice(end + 1);
      const callback = pending.get(message.id);
      if (callback) {
        pending.delete(message.id);
        if (message.error) callback.reject(new Error(message.error.message));
        else callback.resolve(message.result);
      }
      if (message.method === 'Browser.downloadProgress') {
        const { guid, state, receivedBytes, totalBytes } = message.params;
        if (state === 'canceled' || Math.max(receivedBytes, totalBytes) > maxBytes) {
          rejected = true;
        } else if (state === 'completed') {
          completed = guid;
        }
      }
    }
  });
  const call = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    if (signal.aborted || browser.exitCode !== null || !browser.stdio[3].writable) {
      reject(new Error('Browser unavailable'));
      return;
    }
    const id = ++sequence;
    pending.set(id, { resolve, reject });
    browser.stdio[3].write(JSON.stringify({ id, method, params, sessionId }) + '\0');
  });
  try {
    const { targetId } = await call('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await call('Target.attachToTarget', { targetId, flatten: true });
    const evaluate = async fn => (await call('Runtime.evaluate', {
      expression: '(' + fn.toString() + ')()', returnByValue: true, userGesture: true,
    }, sessionId)).result?.value;
    async function waitFor(fn, seconds) {
      const end = Date.now() + seconds * 1000;
      while (Date.now() < end) {
        const value = await evaluate(fn);
        if (value) return value;
        await delay(750, undefined, { signal });
      }
      throw new Error('Page did not expose the expected download control');
    }

    await call('Browser.setDownloadBehavior', {
      behavior: 'allowAndName', downloadPath: dir, eventsEnabled: true,
    });
    const url = provider + '/process?url=' + encodeURIComponent('https://www.youtube.com/watch?v=' + id);
    await call('Page.navigate', { url }, sessionId);
    const result = await waitFor(() => {
      if (location.origin !== 'https://9xbuddy.in' || location.pathname !== '/process') return;
      const title = document.querySelector('h1')?.textContent?.trim();
      const links = [...document.querySelectorAll('a[href]')].filter(link =>
        link.origin === location.origin && link.pathname.startsWith('/download/'));
      const link = [480, 360, 240, 144].map(height => links.find(a =>
        new URL(a.href).searchParams.get('customName')?.endsWith('_' + height + 'p'))).find(Boolean);
      if (!link || !title) return;
      const uploader = document.body.innerText.match(/(?:Uploader|Uploaded by|上传者)\s*:?\s*([^\n/]+)/i)?.[1]?.trim();
      return { url: link.href, title, uploader };
    }, 35);
    const prepare = new URL(result.url);
    if (!prepare.pathname.startsWith('/download/' + id + '-')) throw new Error('Unexpected video');
    await call('Page.navigate', { url: prepare.href }, sessionId);
    await waitFor(() => {
      if (location.origin !== 'https://9xbuddy.in' || !location.pathname.startsWith('/download/')) return;
      const link = [...document.querySelectorAll('a[href]')].find(a =>
        a.protocol === 'https:' && a.hostname.endsWith('.9xbud.com') && a.pathname.startsWith('/down/'));
      if (!link) return;
      link.click();
      return true;
    }, 105);
    while (!completed && !rejected) await delay(500, undefined, { signal });
    if (rejected || !/^[a-f0-9-]{36}$/i.test(completed)) throw new Error('Download rejected');
    const file = join(dir, completed);
    const size = (await stat(file)).size;
    if (!size || size > maxBytes) throw new Error('Invalid media size');
    return { file, size, title: result.title, uploader: result.uploader };
  } finally {
    browser.kill();
    abort();
  }
}

createServer(async (req, res) => {
  if (req.method === 'GET' && req.url === '/health') {
    res.writeHead(200).end('ok');
    return;
  }
  if (req.method !== 'POST' || req.url !== '/download') {
    res.writeHead(404).end();
    return;
  }
  if (busy) {
    res.writeHead(503).end();
    return;
  }
  busy = true;
  let dir;
  const closed = new AbortController();
  res.on('close', () => closed.abort());
  try {
    let body = '';
    for await (const data of req) {
      body += data;
      if (body.length > 256) throw new Error('Invalid input');
    }
    const { id } = JSON.parse(body);
    if (typeof id !== 'string' || !/^[\w-]{11}$/.test(id)) throw new Error('Invalid input');
    dir = await mkdtemp(join(tmpdir(), 'ninebuddy-'));
    const result = await download(id, dir, AbortSignal.any([closed.signal, AbortSignal.timeout(175000)]));
    res.writeHead(200, {
      'content-type': 'video/mp4', 'content-length': result.size,
      'x-video-title': encodeURIComponent(result.title.slice(0, 1000)),
      'x-video-uploader': encodeURIComponent((result.uploader || '').slice(0, 300)),
    });
    await pipeline(createReadStream(result.file), res);
  } catch {
    if (!res.headersSent && !res.destroyed) res.writeHead(502).end();
    else if (!res.destroyed) res.destroy();
  } finally {
    try {
      if (dir) await rm(dir, { recursive: true, force: true });
    } finally {
      busy = false;
    }
  }
}).listen(8061);
