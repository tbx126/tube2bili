let debounce;

chrome.cookies.onChanged.addListener(change => {
  if (change.cookie.domain.replace(/^\./, '').endsWith('youtube.com')) {
    clearTimeout(debounce);
    debounce = setTimeout(sync, 5000);
  }
});
chrome.runtime.onMessage.addListener((message, _sender, respond) => {
  if (message?.type === 'sync') sync().then(respond);
  return message?.type === 'sync';
});

function exportNetscape(cookies) {
  const lines = ['# Netscape HTTP Cookie File'];
  for (const c of cookies) {
    const domain = (c.httpOnly ? '#HttpOnly_' : '') + c.domain;
    const includeSubdomains = c.domain.startsWith('.') ? 'TRUE' : 'FALSE';
    const expiry = c.session || !c.expirationDate ? '0' : String(Math.floor(c.expirationDate));
    const columns = [domain, includeSubdomains, c.path || '/', c.secure ? 'TRUE' : 'FALSE', expiry, c.name, c.value];
    lines.push(columns.join('\t'));
  }
  return lines.join('\n') + '\n';
}

async function sync() {
  const settings = await chrome.storage.local.get(['base', 'token']);
  if (!settings.base || !settings.token) return {ok: false, message: '请先在扩展弹窗中填写 NAS 地址和配对码。'};
  try {
    const cookies = await chrome.cookies.getAll({domain: 'youtube.com'});
    if (!cookies.some(c => ['SID', '__Secure-1PSID', '__Secure-3PSID'].includes(c.name))) {
      throw new Error('Edge 中没有找到 YouTube 登录 Cookie，请先登录 YouTube。');
    }
    const response = await fetch(settings.base + '/api/youtube/extension-sync', {
      method: 'POST',
      headers: {'Content-Type': 'text/plain'},
      body: JSON.stringify({content: exportNetscape(cookies), token: settings.token})
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || 'NAS 拒绝 Cookie 同步。');
    const message = `已同步并验证 YouTube 登录；恢复 ${body.resumed} 个等待任务。`;
    await chrome.storage.local.set({last: message, lastSync: Date.now()});
    return {ok: true, message};
  } catch (error) {
    const message = error.message || 'Cookie 同步失败，请检查 NAS 网络。';
    await chrome.storage.local.set({last: message});
    return {ok: false, message};
  }
}
