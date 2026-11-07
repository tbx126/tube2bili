const base = document.querySelector('#base');
const token = document.querySelector('#token');
const status = document.querySelector('#status');
chrome.storage.local.get(['base', 'token', 'last']).then(value => {
  if (value.base) base.value = value.base;
  if (value.token) token.value = value.token;
  if (value.last) status.textContent = value.last;
});
document.querySelector('#save').addEventListener('click', async () => {
  const origin = base.value.trim().replace(/\/$/, '');
  const secret = token.value.trim();
  if (!/^http:\/\/192\.168\.5\.6(?::18080)?$/.test(origin) || secret.length < 32) {
    status.textContent = '请填写 NAS 地址和完整配对码。';
    return;
  }
  await chrome.storage.local.set({base: origin, token: secret});
  status.textContent = '正在同步并验证 YouTube 登录状态…';
  const result = await chrome.runtime.sendMessage({type: 'sync'});
  status.textContent = result?.message || '同步未完成，请检查 NAS 连接。';
});
