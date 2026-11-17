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

class DownloadError extends Error {}

async function download(id, dir, signal) {
  const browser = spawn('chromium-headless-shell', [
    '--headless', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
    '--disable-breakpad', '--disable-crash-reporter',
    '--remote-debugging-pipe', '--user-data-dir=' + join(dir, 'profile'),
    ...(process.env.NINEBUDDY_PROXY ? ['--proxy-server=' + process.env.NINEBUDDY_PROXY] : []),
    'about:blank',
  ], { stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'], detached: true, signal });
  const closed = new Promise(resolve => browser.once('close', resolve));
  let sequence = 0;
  let chunk = '';
  let completed;
  let rejected = false;
  const pending = new Map();
  const abort = () => {
    for (const callback of pending.values()) callback.reject(new DownloadError('browser'));
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
      reject(new DownloadError('browser'));
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
    async function waitFor(fn, seconds, failure) {
      const end = Date.now() + seconds * 1000;
      while (Date.now() < end) {
        const value = await evaluate(fn);
        if (value) return value;
        await delay(750, undefined, { signal });
      }
      throw new DownloadError(failure);
    }

    await call('Browser.setDownloadBehavior', {
      behavior: 'allowAndName', downloadPath: dir, eventsEnabled: true,
    });
    const url = provider + '/process?url=' + encodeURIComponent('https://www.youtube.com/watch?v=' + id);
    await call('Page.navigate', { url }, sessionId);
    const result = await waitFor(() => {
      if (location.origin !== 'https://9xbuddy.in' || !/^\/(?:[a-z-]+\/)?process$/.test(location.pathname)) return;
      const title = document.querySelector('h1')?.textContent?.trim();
      const links = [...document.querySelectorAll('a[href]')].filter(link =>
        link.origin === location.origin && /^\/(?:[a-z-]+\/)?download\//.test(link.pathname));
      const link = [480, 360, 240, 144].map(height => links.find(a =>
        new URL(a.href).searchParams.get('customName')?.endsWith('_' + height + 'p'))).find(Boolean);
      if (!link || !title) return;
      const uploader = document.body.innerText.match(/(?:Uploader|Uploaded by|上传者)\s*:?\s*([^\n/]+)/i)?.[1]?.trim();
      return { url: link.href, title, uploader };
    }, 35, 'formats');
    const prepare = new URL(result.url);
    if (!prepare.pathname.split('/download/')[1]?.startsWith(id + '-')) {
      throw new DownloadError('formats');
    }
    await call('Page.navigate', { url: prepare.href }, sessionId);
    await waitFor(() => {
      if (location.origin !== 'https://9xbuddy.in' || !/^\/(?:[a-z-]+\/)?download\//.test(location.pathname)) return;
      const link = [...document.querySelectorAll('a[href]')].find(a =>
        a.protocol === 'https:' && a.hostname.endsWith('.9xbud.com') && a.pathname.startsWith('/down/'));
      if (!link) return;
      link.click();
      return true;
    }, 105, 'prepare');
    while (!completed && !rejected) await delay(500, undefined, { signal });
    if (rejected || !/^[a-f0-9-]{36}$/i.test(completed)) throw new DownloadError('transfer');
    const file = join(dir, completed);
    const size = (await stat(file)).size;
    if (!size || size > maxBytes) throw new DownloadError('transfer');
    return { file, size, title: result.title, uploader: result.uploader };
  } finally {
    // Chromium forks renderers and zygotes. Killing only the launcher leaves
    // its descendants alive and exhausts the container's PID budget.
    if (browser.pid) {
      try {
        process.kill(-browser.pid, 'SIGKILL');
      } catch (error) {
        if (error.code !== 'ESRCH') throw error;
      }
    }
    await closed;
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
  } catch (error) {
    const reason = error instanceof DownloadError ? error.message
      : error.name === 'AbortError' || error.name === 'TimeoutError' ? 'timeout' : 'service';
    console.warn('9xbuddy failure:', reason);
    if (!res.headersSent && !res.destroyed) res.writeHead(502, {'x-ninebuddy-error': reason}).end();
    else if (!res.destroyed) res.destroy();
  } finally {
    try {
      if (dir) await rm(dir, { recursive: true, force: true });
    } finally {
      busy = false;
    }
  }
}).listen(8061);
