import { spawn } from 'node:child_process';
import { createReadStream } from 'node:fs';
import { mkdtemp, rm, stat } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { pipeline } from 'node:stream/promises';
import { fileURLToPath } from 'node:url';

const maxBytes = 512 * 1024 ** 2;
const provider = 'https://9xbuddy.in';
let busy = false;

class DownloadError extends Error {}

async function mergeHd(dir, tracks, signal) {
  const work = spawn('python3', [fileURLToPath(new URL('./merge_hd.py', import.meta.url)), dir], {
    stdio: ['pipe', 'ignore', 'ignore'],
    signal: AbortSignal.any([signal, AbortSignal.timeout(85_000)]),
  });
  work.stdin.on('error', () => {});
  const done = new Promise(resolve => {
    work.once('error', () => {});
    work.once('close', code => resolve(code === 0));
  });
  work.stdin.end(JSON.stringify({ ...tracks, proxy: process.env.NINEBUDDY_PROXY || '' }));
  if (!await done) {
    await Promise.all(['hd-video.mp4', 'hd-audio.m4a', 'hd.mp4'].map(name =>
      rm(join(dir, name), { force: true })));
    return null;
  }
  const file = join(dir, 'hd.mp4');
  return { file, size: (await stat(file)).size };
}

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
  let pageStatus = 0;
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
      if (message.method === 'Network.responseReceived' && message.params.type === 'Document') {
        pageStatus = message.params.response.status;
      }
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
    await call('Network.enable', {}, sessionId);
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
      console.warn('9xbuddy page wait expired:', failure, 'HTTP', pageStatus || 'unknown');
      throw new DownloadError(failure);
    }

    await call('Browser.setDownloadBehavior', {
      behavior: 'allowAndName', downloadPath: dir, eventsEnabled: true,
    });
    async function navigate(url) {
      pageStatus = 0;
      const result = await call('Page.navigate', { url }, sessionId);
      if (result.errorText) throw new DownloadError('network');
    }
    const url = provider + '/zh/process?url=' + encodeURIComponent('https://www.youtube.com/watch?v=' + id);
    const readFormats = () => {
      if (location.origin !== 'https://9xbuddy.in' || !/^\/(?:[a-z-]+\/)?process$/.test(location.pathname)) return;
      const title = document.querySelector('h1')?.textContent?.trim();
      const links = [...document.querySelectorAll('a[href]')].filter(link =>
        link.origin === location.origin && /^\/(?:[a-z-]+\/)?download\//.test(link.pathname));
      const choices = [1080, 720, 480, 360, 240, 144].map(height => links.find(link => {
        const row = link.closest('article')?.innerText || '';
        return /\bMP4\b/i.test(row) && new RegExp('\\b' + height + 'p(?:\\d+)?\\b','i').test(row)
          || new URL(link.href).searchParams.get('customName')?.endsWith('_' + height + 'p');
      })).filter(Boolean).map(link => link.href);
      const media = [...document.querySelectorAll('article a[href]')].filter(link =>
        link.protocol === 'https:' && (link.hostname.endsWith('.googlevideo.com') && link.pathname === '/videoplayback'
          || link.hostname.endsWith('.9xbud.com') && link.pathname.startsWith('/down/')));
      const hd = media.find(link => /\bMP4\b/i.test(link.closest('article').innerText)
        && /\b1080p(?:\d+)?\b/i.test(link.closest('article').innerText));
      const audio = media.filter(link => /\bM4A\b/i.test(link.closest('article').innerText))
        .sort((a, b) => Number(b.hostname.endsWith('.9xbud.com')) - Number(a.hostname.endsWith('.9xbud.com')))
        .map(link => link.href);
      const tracks = hd && audio.length ? { video: hd.href, audio } : null;
      if ((!choices.length && !tracks) || !title) return;
      const uploader = document.body.innerText.match(/(?:Uploader|Uploaded by|上传者)\s*:?\s*([^\n/]+)/i)?.[1]?.trim();
      return { choices, tracks, title, uploader };
    };
    await navigate(url);
    let result;
    try {
      result = await waitFor(readFormats, 45, 'formats');
    } catch (error) {
      if (!(error instanceof DownloadError) || error.message !== 'formats' || pageStatus === 403 || pageStatus === 429) throw error;
      await navigate(url);
      result = await waitFor(readFormats, 45, 'formats');
    }
    const choices = result.choices.map(link => new URL(link)).filter(link =>
      link.pathname.split('/download/')[1]?.startsWith(id + '-'));
    const alreadyCombined1080 = choices[0]?.searchParams.get('customName')?.endsWith('_1080p');
    if (result.tracks && !alreadyCombined1080) {
      const hd = await mergeHd(dir, result.tracks, signal);
      if (hd) return { ...hd, title: result.title, uploader: result.uploader };
      console.warn('9xbuddy 1080p tracks unavailable; trying a prepared MP4');
    }
    if (!choices.length) throw new DownloadError('formats');
    const prepare = choices[0];
    await navigate(prepare.href);
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
    const result = await download(id, dir, AbortSignal.any([closed.signal, AbortSignal.timeout(310000)]));
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
