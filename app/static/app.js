const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pages = {today:'今天', tasks:'任务', channels:'频道', settings:'设置'};
const states = {queued:'排队中', running:'处理中', waiting:'等待配置', paused:'已暂停', cancelled:'已取消', failed:'失败', retrying:'等待重试', reconcile:'需核对投稿', completed:'已完成'};
const stageOrder = ['download', 'translate', 'publish', 'subtitles', 'verify', 'collection'];
const stageNames = {download:'下载', translate:'翻译字幕', publish:'投稿', subtitles:'提交字幕', verify:'确认可见', collection:'归入合集'};
const groups = {all:null, attention:['waiting','failed','reconcile'], active:['running','queued','retrying','paused'], completed:['completed']};
const groupNames = {all:'全部', attention:'需处理', active:'进行中', completed:'已完成'};
const ACTIVE = ['running','queued','retrying'];
let page = 'today', taskId = null, overview, settingsData, channelData = [], detailData = null;
let filter = 'all', query = '', toastTimer, settingsDirty = false;

const icon = {
  plus: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
  play: '<svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor" aria-hidden="true"><path d="M8 5.5v13l11-6.5z"/></svg>',
  alert: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16.5h.01"/></svg>',
  cross: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  check: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12l5 5 9-10"/></svg>',
  search: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>',
  chevron: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true" style="color:var(--faint);flex:none"><path d="M9 6l6 6-6 6"/></svg>',
  out: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9"/></svg>'
};

const when = t => {
  if (!t) return '—';
  const d = new Date(t * 1000), now = new Date();
  const hm = d.toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit', hour12:false});
  return d.toDateString() === now.toDateString() ? hm : `${d.getMonth()+1}/${d.getDate()} ${hm}`;
};
const bytes = n => n >= 1024**4 ? (n/1024**4).toFixed(1)+' TB' : n >= 1024**3 ? (n/1024**3).toFixed(1)+' GB' : (n/1024**2).toFixed(1)+' MB';
const tag = s => `<span class="tag ${esc(s)}">${esc(states[s] || s)}</span>`;
const source = t => t.channel_name || (t.channel_id ? '订阅抓取' : '手动导入');
const inGroup = (g, s) => !groups[g] || groups[g].includes(s);
const canResume = t => !['running','completed','reconcile'].includes(t.status) && !(t.assets_deleted || t.payload?.assets_deleted);

async function api(path, method='GET', data) {
  const response = await fetch('/api'+path, {method, headers:{'Content-Type':'application/json','X-Requested-With':'Tube2Bili'}, body:data===undefined?undefined:JSON.stringify(data)});
  if (response.status === 401 && path !== '/login') { $('#shell').hidden = true; $('#login').hidden = false; }
  const value = await response.json();
  if (!response.ok) throw new Error(value.detail || '请求失败');
  return value;
}
function toast(message) { clearTimeout(toastTimer); $('#toast').textContent = message; $('#toast').hidden = false; toastTimer = setTimeout(() => $('#toast').hidden = true, 5000); }
function modal(title, content) { $('#modal-title').textContent = title; $('#modal-body').innerHTML = content; if (!$('#modal').open) $('#modal').showModal(); }
const field = (label, name, value, type='text', extra='', hint='') => `<label class="field">${label}${hint?`<small>${hint}</small>`:''}<input name="${name}" type="${type}" value="${esc(value)}" ${extra}></label>`;

function stages(t) {
  const idx = t.status === 'completed' ? stageOrder.length : stageOrder.indexOf(t.stage);
  const stalled = !ACTIVE.includes(t.status);
  return `<ol class="stages" aria-label="处理阶段">${stageOrder.map((s, i) => {
    const cls = i < idx ? 'done' : i === idx ? `current${stalled ? ' stalled' : ''}` : '';
    return `<li class="${cls}"${i === idx ? ' aria-current="step"' : ''}>${stageNames[s]}</li>`;
  }).join('')}</ol>`;
}
function thumb(t, hasCover) {
  return `<div class="thumb">${icon.play}${hasCover ? `<img alt="" src="/api/tasks/${esc(t.id)}/files/source.jpg">` : ''}</div>`;
}
function reason(t) {
  if (t.status === 'reconcile') return '投稿已提交但没有收到明确结果。为避免重复投稿，系统不会自动重试。';
  if (t.error) return t.error;
  return t.status === 'waiting' ? '等待配置或登录，修复后点「继续」。' : '处理失败，可以重试。';
}

/* ---------- 今天 ---------- */
function drawToday() {
  const {tasks, usage, disk, daily, notices} = overview;
  const s = settingsData;
  const attention = tasks.filter(t => groups.attention.includes(t.status));
  const running = tasks.filter(t => t.status === 'running');
  const queue = tasks.filter(t => ['queued','retrying'].includes(t.status)).reverse();
  const summary = [attention.length ? `<strong style="color:var(--warn)">${attention.length} 件事需要你处理</strong>` : '没有需要处理的事', `${running.length} 个任务正在处理`, `队列中 ${queue.length} 条`].join(' · ');

  const attnHtml = attention.length ? `<div class="card">${attention.slice(0, 6).map(t => {
    const failed = t.status === 'failed';
    let actions;
    if (t.status === 'reconcile') actions = `<a class="btn btn-dark" href="#tasks/${esc(t.id)}">去核对</a>`;
    else actions = `${canResume(t) ? `<button class="btn btn-dark" data-action="task-action" data-command="resume" data-id="${esc(t.id)}">${failed ? '重试' : '继续'}</button>` : ''}${t.status === 'waiting' ? '<a class="btn" href="#settings">去设置</a>' : ''}<a class="btn btn-quiet" href="#tasks/${esc(t.id)}">详情</a>`;
    return `<div class="attn"><span class="attn-icon ${failed ? 'failed' : ''}">${failed ? icon.cross : icon.alert}</span><div class="attn-body"><div class="row-actions" style="gap:8px;align-items:center"><span class="attn-kind ${failed ? 'failed' : ''}">${esc(states[t.status])}${t.attempts ? ` · 已尝试 ${t.attempts} 次` : ''}</span><span class="mono muted" style="font-size:12px">${esc(t.video_id)}</span></div><div class="attn-title ellipsis">${esc(t.title || t.video_id)}</div><div class="attn-why">${esc(reason(t))}</div></div><div class="row-actions">${actions}</div></div>`;
  }).join('')}${attention.length > 6 ? `<a class="q-row" href="#tasks" data-filter="attention">还有 ${attention.length - 6} 条，查看全部</a>` : ''}</div>`
    : `<div class="card all-clear"><span style="color:var(--ok)">${icon.check}</span>一切正常，没有需要你处理的任务。</div>`;

  const nowHtml = running.length ? running.map(t => `<div class="card pad now"><div class="now-top">${thumb(t, false)}<div style="flex:1 1 300px;min-width:0"><div class="now-title">${esc(t.title || '正在读取视频信息…')}</div><div class="meta">${source(t)} · <span class="mono">${esc(t.video_id)}</span> · ${Math.round(t.progress)}%</div><div class="row-actions" style="margin-top:14px"><a class="btn" href="#tasks/${esc(t.id)}">打开详情</a>${t.stage !== 'publish' ? `<button class="btn btn-quiet" data-action="task-action" data-command="pause" data-id="${esc(t.id)}">暂停</button>` : ''}</div></div></div>${stages(t)}</div>`).join('')
    : `<div class="card empty"><strong>现在没有在处理的任务</strong>${queue.length ? '队列中的任务会自动开始。' : '粘贴一条视频链接，或订阅一个频道。'}${queue.length ? '' : `<div><button class="btn btn-dark" data-action="add">${icon.plus}添加链接</button></div>`}</div>`;

  const queueHtml = queue.length ? `<section><div class="section-title"><h2>接下来</h2><a href="#tasks" data-filter="active">全部任务</a></div><div class="card">${queue.slice(0, 5).map((t, i) => `<a class="q-row" href="#tasks/${esc(t.id)}"><span class="q-n">${String(i+1).padStart(2,'0')}</span><div style="flex:1;min-width:0"><div class="ellipsis" style="font-weight:500">${esc(t.title || t.video_id)}</div><div class="muted" style="font-size:12px">${source(t)}</div></div>${tag(t.status)}</a>`).join('')}</div></section>` : '';

  const health = [['工作线程', overview.worker_enabled, overview.worker_enabled ? '运行中' : '已关闭']];
  if (s) {
    health.push(['ffmpeg / biliup', s.tools.ffmpeg && s.tools.biliup, s.tools.ffmpeg && s.tools.biliup ? '就绪' : '未检测到']);
    health.push(['B 站登录', s.bilibili_configured, s.bilibili_configured ? '已导入' : '未导入']);
    health.push(['翻译 API', !!s.translation.primary.base_url, s.translation.primary.base_url ? '已配置' : '未配置']);
    health.push(['语音识别 API', !!s.transcription.primary.base_url, s.transcription.primary.base_url ? '已配置' : '未配置']);
    health.push(['Telegram 通知', s.telegram_configured && s.telegram_chat_id, s.telegram_configured && s.telegram_chat_id ? '已配置' : '未配置']);
  }
  const bars = Array.from({length:7}, (_, i) => { const d = new Date(Date.now() + 8*3600000 - (6-i)*86400000); const day = d.toISOString().slice(0,10); return {label:'日一二三四五六'[d.getUTCDay()], count:daily.find(x => x.day === day)?.count || 0}; });
  const max = Math.max(1, ...bars.map(b => b.count));
  const used = disk.total ? (1 - disk.free/disk.total) * 100 : 0;

  $('#main').innerHTML = `<div class="page-head"><div><h1>今天</h1><p class="sub">${summary}</p></div>${s ? `<span class="muted" style="font-size:13px">每 <span class="mono">${s.poll_minutes}</span> 分钟检查一次订阅</span>` : ''}</div>
  <div class="today"><div class="today-main">
    <section><div class="section-title"><h2>需要你处理 ${attention.length ? `<span class="count warn">${attention.length}</span>` : ''}</h2></div>${attnHtml}</section>
    <section><div class="section-title"><h2>正在处理</h2></div>${nowHtml}</section>
    ${queueHtml}
  </div>
  <aside class="today-side">
    <section class="card pad side-card"><h2>系统状态</h2><ul class="health">${health.map(([name, ok, text]) => `<li class="${ok ? '' : 'warn'}"><span class="dot ${ok ? 'ok' : 'warn'}"></span><span>${name}</span><span>${text}</span></li>`).join('')}</ul></section>
    <section class="card pad side-card"><div class="side-head"><h2>API 费用</h2><small>累计估算</small></div><div class="big-num"><strong>¥ ${usage.cost.toFixed(2)}</strong>${s ? `<span>${s.monthly_budget ? `月上限 ¥ ${s.monthly_budget}` : '未设月上限'}</span>` : ''}</div><p>按设置中的单价与返回 token 数估算，不是账单金额。</p></section>
    <section class="card pad side-card"><div class="side-head"><h2>NAS 存储</h2><small>数据分区</small></div><div class="big-num"><strong>${bytes(disk.free)}</strong><span>可用 / 共 ${bytes(disk.total)}</span></div><div class="bar"><i style="width:${used.toFixed(1)}%"></i></div><p>文件永久保留，只在任务详情中手动删除。</p></section>
    <section class="card pad side-card"><div class="side-head"><h2>近 7 天</h2><small>新建任务 · UTC</small></div><div class="week" role="img" aria-label="最近七天每天新建任务数：${bars.map(b => b.count).join('、')}">${bars.map(b => `<div title="${b.count} 条"><span>${b.count || ''}</span><i style="height:${Math.max(3, b.count/max*64)}px"></i><span>${b.label}</span></div>`).join('')}</div></section>
    <section class="card pad side-card"><div class="side-head"><h2>最近通知</h2><small>Telegram</small></div>${notices.length ? `<ul class="notices">${notices.slice(0, 4).map(n => `<li>${esc(n.message)}<small>${when(n.created)} · ${n.sent ? '已发送' : n.attempts >= 5 ? '发送失败' : '待发送 / 未配置'}</small></li>`).join('')}</ul>` : '<p>暂无通知。</p>'}</section>
  </aside></div>`;
}

/* ---------- 任务 ---------- */
function drawTasks() {
  $('#main').innerHTML = `<div class="page-head"><h1>任务</h1><label class="search">${icon.search}<input id="task-search" class="input" type="search" aria-label="搜索任务" placeholder="搜索标题或视频 ID" value="${esc(query)}"></label></div>
  <div class="tabs" id="task-tabs"></div>
  <div class="split"><section class="card list" id="task-list" aria-label="任务列表"></section><section class="card detail" id="task-detail" aria-label="任务详情" aria-live="polite"></section></div>`;
  drawTaskList();
  if (taskId) loadDetail(); else drawNoDetail();
}
function filteredTasks() {
  const q = query.toLowerCase();
  return overview.tasks.filter(t => inGroup(filter, t.status) && `${t.title} ${t.video_id}`.toLowerCase().includes(q));
}
function drawTaskList() {
  $('#task-tabs').innerHTML = Object.keys(groups).map(g => `<button class="tab" data-action="filter" data-filter="${g}" aria-pressed="${filter === g}">${groupNames[g]} <span>${overview.tasks.filter(t => inGroup(g, t.status)).length}</span></button>`).join('');
  const list = filteredTasks();
  $('#task-list').innerHTML = list.length ? list.map(t => `<a class="list-row" href="#tasks/${esc(t.id)}" aria-current="${t.id === taskId}"><div><div style="display:flex;gap:8px;align-items:center">${tag(t.status)}<span class="time">${when(t.created)}</span></div><div class="title ellipsis">${esc(t.title || t.video_id)}</div><div class="src">${source(t)} · <span class="mono">${esc(t.video_id)}</span>${t.assets_deleted ? ' · 文件已清理' : ''}</div></div>${icon.chevron}</a>`).join('')
    : overview.tasks.length ? '<p class="empty">没有符合条件的任务。</p>'
    : `<div class="empty"><strong>还没有任务</strong>粘贴视频链接或订阅频道后，任务会出现在这里。<div><button class="btn btn-dark" data-action="add">${icon.plus}添加链接</button></div></div>`;
}
function drawNoDetail() { const box = $('#task-detail'); if (box) box.innerHTML = '<p class="empty">选择左侧的任务查看详情。</p>'; }
async function loadDetail() {
  const id = taskId;
  try { const t = await api('/tasks/'+id); if (id !== taskId || page !== 'tasks') return; detailData = t; drawDetail(); }
  catch (error) { if (id === taskId) { $('#task-detail').innerHTML = `<p class="empty">${esc(error.message)}</p>`; } }
}
function drawDetail() {
  const t = detailData, p = t.payload || {}, id = esc(t.id);
  const names = t.files.map(f => f.name);
  let callout = '';
  if (t.status === 'reconcile') callout = `<div class="callout"><div><h3>投稿结果不明确，请先核对</h3><p>打开 B 站创作中心查看稿件列表，按实际情况选择一项。系统不会自动再次投稿。</p></div>
    <form id="link-form" data-id="${id}"><label class="field">已发布，填写 BV 号<input name="bvid" class="mono" required pattern="BV[0-9A-Za-z]{10}" placeholder="BV1xxxxxxxxx" autocomplete="off"></label><button class="btn btn-dark" type="submit">关联并继续提交字幕</button></form>
    <div class="sep"><span>确认创作中心里没有这条稿件？</span><button class="btn btn-danger" data-action="reset-publication" data-id="${id}">确认未投稿，重新提交</button></div></div>`;
  else if (t.status === 'failed' || t.status === 'waiting') callout = `<div class="callout ${t.status === 'failed' ? 'failed' : ''}"><h3>${t.status === 'failed' ? '处理失败' : '等待配置或处理'} · ${stageNames[t.stage]}</h3>${t.error ? `<pre>${esc(t.error)}</pre>` : ''}<p>${t.status === 'failed' ? '修复原因后重试，会从当前步骤继续。' : '在设置中补全配置或登录凭证，然后点「继续」。'}</p><div class="row-actions">${canResume(t) ? `<button class="btn btn-dark" data-action="task-action" data-command="resume" data-id="${id}">${t.status === 'failed' ? '重试' : '继续'}</button>` : ''}<a class="btn" href="#settings">打开设置</a></div></div>`;
  else if (t.status === 'completed') callout = `<div class="callout ok"><span style="color:var(--ok)">${icon.check}</span>已发布，播放器中可见「中文」与「英文」两条可关闭字幕。</div>`;
  else if (t.error) callout = `<div class="callout"><pre>${esc(t.error)}</pre></div>`;

  const actions = [];
  if (ACTIVE.includes(t.status) && !(t.status === 'running' && t.stage === 'publish')) actions.push(`<button class="btn" data-action="task-action" data-command="pause" data-id="${id}">暂停</button>`);
  if (['paused','cancelled'].includes(t.status) && canResume(t)) actions.push(`<button class="btn btn-dark" data-action="task-action" data-command="resume" data-id="${id}">继续</button>`);
  if (!['completed','cancelled'].includes(t.status) && !(t.status === 'running' && t.stage === 'publish')) actions.push(`<button class="btn btn-quiet" data-action="task-action" data-command="cancel" data-id="${id}">取消任务</button>`);
  actions.push(`<button class="btn btn-quiet btn-danger" data-action="delete-record" data-id="${id}" ${['running','reconcile'].includes(t.status)?'disabled':''}>删除记录</button>`);

  $('#task-detail').innerHTML = `<div class="detail-head">${thumb(t, names.includes('source.jpg'))}<div>${tag(t.status)}<h2>${esc(t.title || t.video_id)}</h2><div class="meta">${source(t)} · <span class="mono">${esc(t.video_id)}</span> · ${Math.round(t.progress)}% · 累计处理 ${Math.round((p.elapsed_seconds || 0)/60)} 分钟</div>
    <div class="links"><a href="${esc(t.url)}" target="_blank" rel="noopener">YouTube 原视频 ${icon.out}</a>${p.bvid ? `<a href="https://www.bilibili.com/video/${esc(p.bvid)}" target="_blank" rel="noopener">B 站稿件 <span class="mono">${esc(p.bvid)}</span> ${icon.out}</a>` : ''}</div></div></div>
    ${stages(t)}
    ${callout}
    ${actions.length ? `<div class="row-actions">${actions.join('')}</div>` : ''}
    <div class="cols"><div><h3>本地文件</h3>${t.files.length ? `<ul class="files">${t.files.map(f => `<li><a href="/api/tasks/${id}/files/${encodeURIComponent(f.name)}">${esc(f.name)}</a><span>${bytes(f.size)}</span></li>`).join('')}</ul>` : `<p class="hint">${p.assets_deleted ? '本地文件已删除。' : '尚未生成文件。'}</p>`}
      <div class="row-actions">${names.includes('bilingual.srt') ? `<button class="btn" data-action="preview" data-id="${id}">预览中英字幕</button>` : ''}${t.files.length ? `<button class="btn btn-quiet btn-danger" data-action="delete-files" data-id="${id}">删除本地文件</button>` : ''}</div>
      <pre id="subtitle-preview" class="subtitle-preview" hidden></pre></div>
    <div><h3>处理记录</h3>${t.events.length ? `<ol class="events">${t.events.map(e => `<li><time>${when(e.created)}</time><span>${esc(e.message)}</span></li>`).join('')}</ol>` : '<p class="hint">任务尚未开始。</p>'}</div></div>`;
}

/* ---------- 频道 ---------- */
async function drawChannels() {
  channelData = await api('/channels');
  if (page !== 'channels') return;
  const rows = channelData.map(c => {
    const o = c.options || {};
    let state, cls = '', dot = 'ok', note;
    if (!c.enabled) { state = '已暂停'; dot = ''; note = '恢复后处理暂停期间的新视频'; }
    else if (c.error) { state = '检查出错'; cls = 'fail'; dot = 'fail'; note = c.error; }
    else if (!c.initialized) { state = '建立基线中'; cls = 'run'; dot = 'run'; note = '首次检查只记录现有视频'; }
    else { state = '订阅中'; note = '新视频会自动进入队列'; }
    const initial = (c.name || '?').trim().slice(0, 1).toUpperCase();
    return `<div class="ch-row ${c.enabled ? '' : 'paused'}"><div class="ch-name"><span class="avatar" aria-hidden="true">${esc(initial)}</span><div style="min-width:0"><strong class="ellipsis">${esc(c.name)}</strong><small class="ellipsis">${esc(c.url.replace(/^https?:\/\/(www\.)?/, ''))}</small></div></div>
      <div><span class="ch-state ${cls}"><span class="dot ${dot}"></span>${state}</span><div class="ch-note ${c.error && c.enabled ? 'fail' : ''}">${esc(note)}</div></div>
      <div class="mono" style="font-size:13px;color:var(--ink-2)">${c.last_poll ? when(c.last_poll) : '尚未检查'}</div>
      <div style="font-size:13px;color:var(--ink-2)">分区 <span class="mono">${esc(o.tid)}</span><div class="ellipsis muted" style="font-size:12px">${esc(o.tags)}</div><div class="muted" style="font-size:12px">${o.season_id ? `合集 #${esc(o.season_id)} · 分节 ${o.section_id ? '#'+esc(o.section_id) : '自动'}` : '不加入合集'}</div></div>
      <div class="ch-actions"><button class="btn btn-quiet" data-action="edit-channel" data-id="${esc(c.id)}">编辑</button><button class="btn" data-action="toggle-channel" data-id="${esc(c.id)}">${c.enabled ? '暂停' : '恢复'}</button></div></div>`;
  }).join('');
  $('#main').innerHTML = `<div class="page-head"><div><h1>频道</h1><p class="sub" style="max-width:640px">添加后先记录现有视频作为基线，之后只处理新发布的视频。Shorts 与直播默认排除。</p></div><button class="btn btn-dark" data-action="add" data-mode="channel">${icon.plus}订阅频道</button></div>
  ${channelData.length ? `<div class="card table-wrap"><div class="ch-table"><div class="ch-row ch-head"><span>频道</span><span>状态</span><span>上次检查</span><span>投稿设置</span><span style="text-align:right">操作</span></div>${rows}</div></div>`
    : `<div class="card empty"><strong>订阅你的第一个频道</strong>新视频会自动进入队列，首次添加不会搬运历史视频。<div><button class="btn btn-dark" data-action="add" data-mode="channel">${icon.plus}订阅频道</button></div></div>`}
  <p class="hint" style="margin-top:16px">暂停订阅不会取消已排队的任务；恢复后会处理暂停期间出现的新视频。要搬运历史视频，请直接粘贴视频链接。</p>`;
}

/* ---------- 设置 ---------- */
function routeCard(purpose, slot, route, enabled) {
  const pre = `${purpose}.${slot}.`, primary = slot === 'primary';
  const ok = !!route.base_url;
  const status = primary ? (ok ? '<span><i class="dot ok"></i>已配置</span>' : '<span><i class="dot warn"></i>未配置</span>') : `<span>${enabled ? '已启用' : '未启用'}</span>`;
  const price = purpose === 'translation'
    ? `<div class="two">${field('输入 ¥/百万 token', pre+'input_per_million', route.input_per_million, 'number', 'min="0" step="any"')}${field('输出 ¥/百万 token', pre+'output_per_million', route.output_per_million, 'number', 'min="0" step="any"')}</div>`
    : field('¥ / 音频分钟', pre+'per_minute', route.per_minute, 'number', 'min="0" step="any"', '0 表示未计价，预算无法约束');
  return `<div class="route ${!primary && !enabled ? 'off' : ''}"><div class="route-head"><strong>${primary ? '主服务' : '备用服务'}</strong>${status}</div>
    <label class="field">接口类型<select name="${pre}protocol">${(purpose==='translation'?[['openai','OpenAI 兼容'],['qwen','千问兼容接口']]:[['openai','OpenAI 音频接口'],['qwen_asr','千问异步识别'],['qwen_audio','千问音频直传']]).map(([v,n])=>`<option value="${v}" ${route.protocol===v?'selected':''}>${n}</option>`).join('')}</select></label>
    ${field('服务名称', pre+'name', route.name)}${field('API 基础地址', pre+'base_url', route.base_url, 'url', 'class="mono" placeholder="https://服务地址/v1"')}${field('模型', pre+'model', route.model, 'text', 'class="mono"')}
    ${field('API Key', pre+'api_key', '', 'password', `autocomplete="new-password" placeholder="${route.key_configured ? '已配置 · 留空则保留' : '局域网无认证服务可不填'}"`)}${price}
    ${primary ? '' : `<label class="check"><input name="${purpose}.fallback_enabled" type="checkbox" ${enabled ? 'checked' : ''}>主服务失败时使用备用服务（可能产生额外费用）</label>`}</div>`;
}
async function drawSettings() {
  settingsData = await api('/settings');
  if (page !== 'settings') return;
  const s = settingsData; settingsDirty = false;
  const asrMissing = !s.transcription.primary.base_url;
  const waitingAsr = overview ? overview.tasks.filter(t => t.status === 'waiting').length : 0;
  const navDot = ok => ok ? '<span class="dot ok"></span>' : '<small>未配置</small>';
  $('#main').innerHTML = `<div class="page-head"><h1>设置</h1><button class="btn btn-quiet" data-action="logout">退出登录</button></div>
  <div class="settings"><nav class="side-nav" aria-label="设置分区">
    <a href="#settings/accounts">平台登录 ${navDot(s.bilibili_configured)}</a>
    <a href="#settings/translation">翻译服务 ${navDot(!!s.translation.primary.base_url)}</a>
    <a href="#settings/transcription">语音识别 ${navDot(!asrMissing)}</a>
    <a href="#settings/notify">Telegram 通知 ${navDot(s.telegram_configured && s.telegram_chat_id)}</a>
    <a href="#settings/run">运行与预算</a>
    <a href="#settings/posting">投稿默认值</a>
  </nav>
  <form id="settings-form" novalidate>
    <section id="accounts" class="card set"><div class="set-head"><h2>平台登录</h2><small>${Object.entries(s.tools).map(([k, v]) => `${esc(k)} ${v ? '✓' : '×'}`).join(' · ')}</small></div><p>凭证只保存在 NAS 数据目录，页面不会回显内容。</p>
      <div class="cred"><div><div><strong>Bilibili 投稿账号</strong><small class="${s.bilibili_configured ? 'ok' : ''}">${s.bilibili_configured ? '已导入 cookies.json · 有效性在投稿时检查' : '尚未导入 biliup 登录文件，无法投稿'}</small></div><button type="button" class="btn ${s.bilibili_configured ? '' : 'btn-dark'}" data-action="credentials" data-provider="bilibili">${s.bilibili_configured ? '重新导入' : '导入 cookies.json'}</button></div>
      <div><div><strong>YouTube <span class="muted" style="font-size:12px;font-weight:400">可选</span></strong><small class="${s.youtube_configured ? 'ok' : ''}">${s.youtube_configured ? '已导入 cookies.txt' : '遇到 403 或年龄限制时导入 Netscape 格式 cookies.txt'}</small></div><button type="button" class="btn" data-action="credentials" data-provider="youtube">导入 cookies.txt</button></div></div>
      <details><summary>用终端扫码登录 B 站</summary><pre>docker compose exec app biliup -u /data/cookies.json login</pre></details></section>
    ${['translation', 'transcription'].map(p => `<section id="${p}" class="card set ${p === 'transcription' && asrMissing ? 'attention' : ''}"><div class="set-head"><h2>${p === 'translation' ? '翻译服务' : '语音识别'}</h2><small>OpenAI 兼容 · ${p === 'translation' ? '/chat/completions' : '/audio/transcriptions'}</small></div>
      ${p === 'transcription' && asrMissing ? `<div class="note">${waitingAsr ? `有 ${waitingAsr} 个任务在等待配置。` : ''}没有英文字幕的视频需要语音识别。服务需返回 verbose_json 分段时间轴，音频每 10 分钟切一段上传。</div>` : `<p>${p === 'translation' ? '翻译字幕、标题与简介。按批次保存进度，中断后从断点继续。' : '没有英文字幕时使用。需返回 verbose_json 分段时间轴，音频每 10 分钟切一段上传。'}</p>`}
      <div class="routes">${routeCard(p, 'primary', s[p].primary)}${routeCard(p, 'fallback', s[p].fallback, s[p].fallback_enabled)}</div>${p==='translation'?`<label class="field">翻译背景与固定译法<textarea name="translation_notes" maxlength="12000" rows="5">${esc(s.translation_notes)}</textarea></label>`:''}</section>`).join('')}
    <section id="notify" class="card set"><h2>Telegram 通知</h2><p>任务完成、失败、需要核对或登录失效时通知你。</p><div class="grid-fields" style="align-items:end">
      ${field('Bot Token', 'telegram_token', '', 'password', `autocomplete="new-password" placeholder="${s.telegram_configured ? '已配置 · 留空则保留' : ''}"`)}${field('Chat ID', 'telegram_chat_id', s.telegram_chat_id, 'text', 'class="mono"')}
      <div><button type="button" class="btn" data-action="test-notification">发送测试通知</button></div></div><p class="hint" style="margin-top:10px">测试前请先保存设置。</p></section>
    <section id="run" class="card set"><h2>运行与预算</h2><p>保存后，新步骤使用新配置；等待中的任务可在详情中继续。</p><div class="grid-fields">
      ${field('YouTube / Telegram 代理', 'proxy', s.proxy, 'text', 'class="mono" placeholder="http://192.168.1.10:7890"', '填 NAS 可访问的地址；容器内的 127.0.0.1 指向容器自身')}
      ${field('频道检查间隔（分钟）', 'poll_minutes', s.poll_minutes, 'number', 'min="5" max="1440"', '5 – 1440')}
      ${field('磁盘最少可用（GB）', 'min_free_gb', s.min_free_gb, 'number', 'min="1" step="any"', '低于此值暂停下载')}
      ${field('每月费用上限（¥）', 'monthly_budget', s.monthly_budget, 'number', 'min="0" step="any"', '0 为不限；按单价估算，不是硬限额')}</div></section>
    <section id="posting" class="card set"><h2>投稿默认值</h2><p>手动导入的视频和新订阅的频道使用这些值；每个频道可单独修改。</p><div class="grid-fields">
      ${field('分区 ID', 'posting.tid', s.posting.tid, 'number', 'min="1"')}${field('标签', 'posting.tags', s.posting.tags, 'text', '', '逗号分隔')}${field('标题前缀', 'posting.title_prefix',s.posting.title_prefix,'text','maxlength="30"')}${field('合集 ID（0 表示不指定）', 'posting.season_id',s.posting.season_id,'number','min="0"')}${field('合集分节 ID', 'posting.section_id',s.posting.section_id,'number','min="0"')}</div></section>
    <div class="save-bar"><span id="save-state">修改后点击保存。</span><div class="row-actions"><button class="btn btn-quiet" type="button" data-action="reset-settings">放弃修改</button><button class="btn" type="submit">保存设置</button></div></div>
  </form></div>`;
}

/* ---------- 添加 / 频道编辑 ---------- */
function detectKind(url) {
  if (/(youtube\.com\/(watch\?|shorts\/|live\/)|youtu\.be\/)/i.test(url)) return 'video';
  if (/youtube\.com\/(@|channel\/|c\/|user\/)/i.test(url)) return 'channel';
  return '';
}
function addModal(prefill = '') {
  modal('添加', `<form id="add-form" novalidate><label class="field">YouTube 视频或频道链接<input id="add-url" name="url" class="big-input" type="url" required autocomplete="off" placeholder="https://www.youtube.com/…" value="${esc(prefill)}"></label><div id="detect"></div><div class="modal-actions"><button class="btn btn-quiet" type="button" data-action="close">取消</button><button class="btn btn-dark" type="submit" id="add-submit">继续</button></div></form>`);
  drawDetect();
  $('#add-url').focus();
}
function drawDetect() {
  const url = $('#add-url').value.trim(), kind = detectKind(url), posting = settingsData?.posting || {tid:171, tags:'YouTube,双语字幕'};
  const box = $('#detect'), submit = $('#add-submit');
  if (kind === 'video') {
    box.innerHTML = `<div class="detect"><span class="detect-kind">识别为单个视频</span><p>创建后会自动下载、翻译并<strong>直接发布到 B 站</strong>，不经过人工审核。投稿分区 <span class="mono">${esc(posting.tid)}</span> · 标签：${esc(posting.tags)}。重复链接会打开已有任务。</p></div>`;
    submit.textContent = '创建任务';
  } else if (kind === 'channel') {
    if (box.dataset.kind !== 'channel') {
      const handle = (url.match(/@([^/?#]+)/) || [])[1] || '';
      box.innerHTML = `<div class="detect"><span class="detect-kind">识别为频道</span>${field('频道名称', 'name', handle, 'text', 'required')}<div class="two">${field('投稿分区 ID', 'tid', posting.tid, 'number', 'min="1" required')}${field('标签', 'tags', posting.tags)}</div>${channelCollectionFields(posting)}<p>首次检查只记录现有视频，<strong>不会搬运历史视频</strong>。之后发布的新视频自动进入队列。</p></div>`;
    }
    submit.textContent = '开始订阅';
  } else {
    box.innerHTML = `<div class="detect unknown"><p>粘贴 youtube.com/watch、youtu.be 视频链接，或 youtube.com/@频道 链接。</p></div>`;
    submit.textContent = '继续';
  }
  box.dataset.kind = kind;
}
function channelCollectionFields(o) {
  return `<div class="two">${field('合集 ID', 'season_id', o.season_id || 0, 'number', 'min="0" step="1" required', '0 表示不加入合集')}${field('合集分节 ID', 'section_id', o.section_id || 0, 'number', 'min="0" step="1" required', '0 自动选择唯一分节；多个分节需填写 ID')}</div><p class="hint">每个频道独立设置，仅用于之后新入队的视频。已排队和已发布的视频保持原设置。</p>`;
}
function channelPostingOptions(data, defaults) {
  return {...defaults, tid: Number(data.get('tid')), tags: data.get('tags'), season_id: Number(data.get('season_id')), section_id: Number(data.get('section_id'))};
}
function channelModal(c) {
  const o = c.options || {};
  modal('编辑频道', `<form id="channel-form" data-id="${esc(c.id)}">${field('频道名称', 'name', c.name, 'text', 'required')}${field('YouTube 频道链接', 'url', c.url, 'url', 'readonly', '更换来源请新建订阅')}<div class="two">${field('投稿分区 ID', 'tid', o.tid, 'number', 'min="1" required')}${field('投稿标签', 'tags', o.tags)}</div>${channelCollectionFields(o)}<div class="modal-actions"><button class="btn btn-quiet" type="button" data-action="close">取消</button><button class="btn btn-dark" type="submit">保存</button></div></form>`);
}

/* ---------- 路由与刷新 ---------- */
function setNav() {
  document.querySelectorAll('[data-page]').forEach(a => { if (a.dataset.page === page) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
  document.title = `${pages[page]} · Tube2Bili`;
}
async function navigate() {
  const [name, sub] = location.hash.slice(1).split('/');
  const next = name === 'overview' ? 'today' : name in pages ? name : 'today';
  if (next === 'settings' && sub) {
    if (page !== 'settings' || !$('#settings-form')) { page = 'settings'; setNav(); await drawSettings(); }
    document.getElementById(sub)?.scrollIntoView({behavior: 'smooth', block: 'start'});
    return;
  }
  const changed = next !== page;
  page = next; setNav();
  if (page === 'tasks') {
    taskId = sub || null;
    if (sub) { const t = overview.tasks.find(x => x.id === sub); if (t && !inGroup(filter, t.status)) filter = 'all'; }
    if (changed || !$('#task-list')) drawTasks();
    else { drawTaskList(); if (taskId) loadDetail(); else drawNoDetail(); }
    if (sub && window.innerWidth <= 760) $('#task-detail')?.scrollIntoView({block: 'start'});
  } else if (page === 'channels') await drawChannels();
  else if (page === 'settings') await drawSettings();
  else drawToday();
  if (changed) window.scrollTo(0, 0);
}
async function refresh(render = true) {
  const [next, first] = await Promise.all([api('/overview'),api('/tasks?size=50')]);
  const tasks = [...first.tasks];
  for(let p=2;p<=first.pages;p++) tasks.push(...(await api(`/tasks?size=50&page=${p}`)).tasks);
  overview = {...next,tasks};
  const active = overview.tasks.filter(t => ACTIVE.includes(t.status)).length;
  const attention = overview.tasks.filter(t => groups.attention.includes(t.status)).length;
  const count = $('#queue-count');
  count.textContent = attention || active; count.className = attention ? 'count warn' : 'count';
  count.title = attention ? `${attention} 个任务需要处理` : `${active} 个任务进行中`;
  $('#connection').innerHTML = overview.worker_enabled ? '<i class="dot ok"></i><span>自动处理中</span>' : '<i class="dot warn"></i><span>工作队列已关闭</span>';
  if (!render) return;
  if (page === 'today') drawToday();
  if (page === 'tasks' && $('#task-list')) {
    drawTaskList();
    const t = overview.tasks.find(x => x.id === taskId);
    const typing = $('#task-detail')?.contains(document.activeElement) && document.activeElement.matches('input');
    if (t && detailData && t.id === detailData.id && t.updated !== detailData.updated && !typing) loadDetail();
  }
}
async function loadSettings() { try { settingsData = await api('/settings'); } catch {} }

document.addEventListener('error', e => { if (e.target.tagName === 'IMG') e.target.remove(); }, true);
document.addEventListener('click', async e => {
  const link = e.target.closest('a[data-filter]');
  if (link) filter = link.dataset.filter;
  const button = e.target.closest('[data-action]'); if (!button) return;
  const {action, id} = button.dataset;
  try {
    if (action === 'close') { $('#modal').close(); return; }
    if (action === 'add') { if (!settingsData) await loadSettings(); addModal(); return; }
    if (action === 'filter') { filter = button.dataset.filter; drawTaskList(); return; }
    if (action === 'edit-channel') { channelModal(channelData.find(c => c.id === id)); return; }
    if (action === 'logout') { await api('/logout', 'POST'); $('#shell').hidden = true; $('#login').hidden = false; return; }
    if (action === 'delete-record') {
      if(!confirm('删除队列记录？本地文件、B 站稿件和投稿去重信息会保留，重新添加原链接可找回。')) return;
      await api(`/tasks/${id}`,'DELETE'); taskId=null; detailData=null; location.hash='#tasks'; await refresh(); toast('记录已删除，本地文件已保留'); return;
    }
    if (action === 'task-action') {
      const command = button.dataset.command;
      if (command === 'cancel' && !confirm('取消这个任务？已发布到 B 站的视频不会被撤回。')) return;
      button.disabled = true;
      await api(`/tasks/${id}/action`, 'POST', {action: command});
      toast({pause:'已暂停', cancel:'已取消', resume:'已重新加入队列'}[command] || '操作已保存');
      await refresh(); if (page === 'tasks' && taskId === id) await loadDetail(); return;
    }
    if (action === 'reset-publication') {
      if (!confirm('已在 B 站创作中心确认没有该稿件？此操作会再次提交视频，判断有误会造成重复投稿。')) return;
      await api(`/tasks/${id}/action`, 'POST', {action: 'reset-publication'}); toast('已重新加入投稿队列');
      await refresh(); await loadDetail(); return;
    }
    if (action === 'toggle-channel') { const c = channelData.find(x => x.id === id); await api('/channels/'+id, 'PUT', {...c, enabled: !c.enabled}); toast(c.enabled ? '已暂停订阅' : '已恢复订阅'); await drawChannels(); return; }
    if (action === 'test-notification') { await api('/notifications/test', 'POST'); toast('测试通知已排队，结果见「今天」的最近通知'); return; }
    if (action === 'reset-settings') { await drawSettings(); return; }
    if (action === 'credentials') { const bili = button.dataset.provider === 'bilibili'; modal('导入登录凭证', `<form id="credential-form" data-provider="${esc(button.dataset.provider)}"><label class="field">选择 ${bili ? 'cookies.json' : 'cookies.txt'}<input type="file" name="file" accept="${bili ? '.json' : '.txt'}" required></label><p class="hint">文件直接保存到 NAS 数据目录，不会在页面中回显。</p><div class="modal-actions"><button class="btn btn-quiet" type="button" data-action="close">取消</button><button class="btn btn-dark" type="submit">导入</button></div></form>`); return; }
    if (action === 'preview') { const r = await fetch(`/api/tasks/${id}/files/bilingual.srt`); if (!r.ok) throw new Error('字幕读取失败'); const pre = $('#subtitle-preview'); pre.textContent = await r.text(); pre.hidden = false; return; }
    if (action === 'delete-files') { if (!confirm('永久删除该任务的本地视频、字幕和处理文件？B 站稿件与任务记录会保留，删除后任务不能继续。')) return; await api(`/tasks/${id}/files`, 'DELETE'); toast('本地文件已删除'); await refresh(); await loadDetail(); }
  } catch (error) { toast(error.message); }
  finally { if (button.isConnected) button.disabled = false; }
});
document.addEventListener('submit', async e => {
  const form = e.target; e.preventDefault();
  const button = form.querySelector('[type=submit]'); if (button) button.disabled = true;
  try {
    const data = new FormData(form);
    if (form.id === 'login-form') { await api('/login', 'POST', {password: data.get('password')}); form.reset(); $('#login-error').textContent = ''; $('#login').hidden = true; $('#shell').hidden = false; await Promise.all([refresh(false), loadSettings()]); await navigate(); }
    if (form.id === 'add-form') {
      const url = data.get('url').trim();
      if (!url) throw new Error('请先粘贴链接');
      if ($('#detect').dataset.kind === 'channel') {
        if (!form.reportValidity()) return;
        await api('/channels', 'POST', {name: data.get('name'), url, enabled: true, options: channelPostingOptions(data, settingsData?.posting || {})});
        $('#modal').close(); toast('已订阅，首次检查会建立基线'); location.hash = '#channels'; if (page === 'channels') await drawChannels();
      } else {
        const result = await api('/tasks', 'POST', {url});
        $('#modal').close(); await refresh(false); toast('任务已进入队列'); location.hash = '#tasks/' + result.id;
      }
    }
    if (form.id === 'channel-form') { if (!form.reportValidity()) return; const id = form.dataset.id, old = channelData.find(c => c.id === id); await api('/channels/'+id, 'PUT', {name: data.get('name'), url: data.get('url'), enabled: !!old.enabled, options: channelPostingOptions(data, old.options)}); $('#modal').close(); await drawChannels(); toast('频道设置已保存'); }
    if (form.id === 'settings-form') {
      if (!form.reportValidity()) return;
      const result = structuredClone(settingsData);
      for (const input of form.querySelectorAll('[name]')) { const keys = input.name.split('.'); let target = result; for (const key of keys.slice(0, -1)) target = target[key]; target[keys.at(-1)] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value; }
      await api('/settings', 'PUT', result); const y = window.scrollY; await drawSettings(); window.scrollTo(0, y); toast('设置已保存');
    }
    if (form.id === 'credential-form') { const file = data.get('file'); if (file.size > 2000000) throw new Error('登录文件不能超过 2 MB'); await api('/credentials/'+form.dataset.provider, 'PUT', {content: await file.text()}); $('#modal').close(); await loadSettings(); if (page === 'settings') await drawSettings(); toast('登录凭证已保存'); }
    if (form.id === 'link-form') { await api(`/tasks/${form.dataset.id}/action`, 'POST', {action: 'link', bvid: data.get('bvid')}); toast('已关联稿件，将继续提交字幕'); await refresh(); await loadDetail(); }
  } catch (error) { if (form.id === 'login-form') $('#login-error').textContent = error.message; else toast(error.message); }
  finally { if (button && button.isConnected) button.disabled = false; }
});
document.addEventListener('input', e => {
  if (e.target.id === 'task-search') { query = e.target.value; drawTaskList(); }
  if (e.target.id === 'add-url') drawDetect();
  if (e.target.closest('#settings-form') && !settingsDirty) { settingsDirty = true; $('#save-state').textContent = '有未保存的修改。'; }
});
document.addEventListener('change', e => { if (e.target.closest('#settings-form') && !settingsDirty) { settingsDirty = true; $('#save-state').textContent = '有未保存的修改。'; } });
window.addEventListener('beforeunload', e => { if (page === 'settings' && settingsDirty) e.preventDefault(); });
window.addEventListener('hashchange', () => navigate().catch(e => toast(e.message)));
async function init() {
  try { await Promise.all([refresh(false), loadSettings()]); $('#shell').hidden = false; await navigate(); }
  catch { $('#login').hidden = false; }
}
setInterval(() => { if (!$('#shell').hidden && !document.hidden) refresh().catch(() => { $('#connection').innerHTML = '<i class="dot fail"></i><span>连接中断 · 正在重连</span>'; }); }, 8000);
init();
