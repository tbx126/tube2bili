const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const names = {overview:'总览',tasks:'任务队列',channels:'频道订阅',settings:'服务设置'};
const states = {queued:'排队中',running:'处理中',waiting:'等待配置 / 处理',paused:'已暂停',cancelled:'已取消',failed:'失败',retrying:'等待重试',reconcile:'需核对投稿',completed:'已完成'};
const stages = {download:'下载视频',translate:'翻译字幕',publish:'提交投稿',subtitles:'提交字幕',verify:'确认字幕',collection:'加入合集'};
let page = 'overview', overview, channelData = [], settingsData, filter = '', query = '', toastTimer;
const date = t => t ? new Date(t * 1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '尚未检查';
const bytes = n => n >= 1024**3 ? (n/1024**3).toFixed(1)+' GB' : (n/1024**2).toFixed(1)+' MB';

const icons = {
  plus: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  check: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>',
  running: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>',
  clock: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
  alert: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
  play: '<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" stroke="none" aria-hidden="true"><polygon points="6 4 20 12 6 20 6 4"/></svg>',
  pause: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
  external: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
  arrowRight: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>',
  trash: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  file: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
  video: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>',
  discover: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"/></svg>',
  download: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
  translate: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 8 6 6"/><path d="m4 14 6-6 2-3"/><path d="M2 5h12"/><path d="M7 2h1"/><path d="m22 22-5-10-5 10"/><path d="M14 18h6"/></svg>',
  upload: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>',
  subtitles: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="20" height="15" x="2" y="4.5" rx="2"/><path d="M7 15h4M15 15h2M7 11h2M13 11h4"/></svg>',
  cost: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>',
  checkCircle: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
  alertCircle: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>'
};

const badge = state => `<span class="badge ${esc(state)}">${esc(states[state] || state)}</span>`;

async function api(path, method='GET', data) {
  const response = await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-Requested-With':'Tube2Bili'},body:data===undefined?undefined:JSON.stringify(data)});
  if (response.status===401) { $('#shell').hidden=true; $('#login').hidden=false; }
  const value = await response.json();
  if (!response.ok) throw new Error(value.detail || '请求失败');
  return value;
}

function toast(message) {
  clearTimeout(toastTimer);
  $('#toast').innerHTML = `${icons.check}<span>${esc(message)}</span>`;
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 6500);
}

function modal(title, content) {
  $('#modal-title').textContent = title;
  $('#modal-body').innerHTML = content;
  if (!$('#modal').open) $('#modal').showModal();
}

function head(title, subtitle, action='new-task', label='新建任务') {
  return `<div class="page-head">
    <div>
      <p class="eyebrow"><span class="nav-icon" style="width:14px;height:14px">${icons.video}</span>VIDEO OPERATIONS</p>
      <h1>${title}</h1>
      <p>${subtitle}</p>
    </div>
    ${action ? `<button class="primary" data-action="${action}">${icons.plus}<span>${label}</span></button>` : ''}
  </div>`;
}

function empty(title, text, action, label) {
  return `<div class="empty">
    <div class="empty-symbol" aria-hidden="true">${icons.play}</div>
    <h3>${title}</h3>
    <p>${text}</p>
    ${action ? `<button class="primary" data-action="${action}">${icons.plus}<span>${label}</span></button>` : ''}
  </div>`;
}

const isActive = t => ['queued','running','retrying'].includes(t.status);
const needsAttention = t => ['waiting','failed','reconcile'].includes(t.status);

function taskTable(tasks) {
  if (!tasks.length) {
    return empty(
      query || filter ? '没有匹配的任务' : '队列已清空',
      query || filter ? '尝试其他关键词，或切换状态筛选。' : '粘贴 YouTube 链接，开始处理下一个视频。',
      query || filter ? 'clear-filter' : 'new-task',
      query || filter ? '清除筛选' : '添加视频链接'
    );
  }
  return `<div class="task-list">${tasks.map(t => {
    const coverClass = t.status === 'completed' ? 'cover-completed' : t.status === 'running' ? 'cover-running' : ['waiting','failed','reconcile'].includes(t.status) ? 'cover-attention' : 'cover-neutral';
    const coverIcon = t.status === 'completed' ? icons.check : t.status === 'running' ? icons.running : ['waiting','failed','reconcile'].includes(t.status) ? icons.alert : ['paused','cancelled'].includes(t.status) ? icons.pause : icons.play;
    const channel = channelData.find(c => c.id === t.channel_id);
    const sourceText = channel ? channel.name : (t.channel_id ? '频道订阅' : '手动导入');
    const stageText = t.status === 'completed'
      ? '发布与双语字幕已完成'
      : t.status === 'running'
      ? `正在处理：${stages[t.stage] || t.stage}`
      : t.status === 'waiting'
      ? `等待配置 / 处理：${stages[t.stage] || t.stage}`
      : t.status === 'retrying'
      ? `等待重试：${stages[t.stage] || t.stage}`
      : t.status === 'reconcile'
      ? '需核对投稿结果'
      : t.status === 'paused'
      ? `已暂停：${stages[t.stage] || t.stage}`
      : stages[t.stage] || t.stage;

    return `<article class="task-card ${t.status === 'running' ? 'is-running' : ''} state-${t.status}">
      <div class="task-card-header">
        <div class="task-card-identity">
          <div class="task-cover ${coverClass}" aria-hidden="true">${coverIcon}</div>
          <div class="task-title-area">
            <div class="task-title-row">
              <button class="task-title" data-action="detail" data-id="${t.id}" title="${esc(t.title || t.video_id)}">
                ${esc(t.title || t.video_id)}
              </button>
              <div class="task-status-badges">
                ${badge(t.status)}
                ${t.bvid ? `<a href="https://www.bilibili.com/video/${esc(t.bvid)}" target="_blank" rel="noopener" class="bvid-pill" title="在新窗口打开 B 站稿件"><span class="bvid-tag">B站</span><span class="bvid-code">${esc(t.bvid)}</span>${icons.external}</a>` : ''}
              </div>
            </div>
            <div class="task-meta">
              <span class="meta-item"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M4.93 4.93a10 10 0 0 0 0 14.14"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>${esc(sourceText)}</span>
              <a href="${esc(t.url || 'https://www.youtube.com/watch?v=' + t.video_id)}" target="_blank" rel="noopener" class="meta-item meta-link task-id" title="查看 YouTube 原视频">
                <span class="yt-badge">YT</span>
                <span>${esc(t.video_id)}</span>
                ${icons.external}
              </a>
              <span class="meta-item"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>${date(t.created)}</span>
              ${t.assets_deleted ? `<span class="meta-item meta-status-muted"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>本地文件已清理</span>` : ''}
            </div>
          </div>
        </div>
        <div class="task-actions">
          ${['waiting','paused','failed','retrying'].includes(t.status) && !t.assets_deleted ? `<button class="primary" data-action="task-action" data-id="${t.id}" data-command="resume">${icons.play}<span>继续处理</span></button>` : ''}
          ${t.status === 'running' && t.stage !== 'publish' ? `<button class="ghost" data-action="task-action" data-id="${t.id}" data-command="pause">${icons.pause}<span>暂停</span></button>` : ''}
          <button class="ghost" data-action="detail" data-id="${t.id}">查看详情</button>
          <button class="ghost danger" data-action="delete-task" data-id="${t.id}" ${['running','reconcile'].includes(t.status)?'disabled title="请先停止执行或核对投稿"':''}>${icons.trash}<span>删除记录</span></button>
        </div>
      </div>

      <div class="task-progress-block">
        <div class="task-progress-head">
          <span class="stage-label">
            <span class="stage-dot stage-dot-${t.status}"></span>
            ${stageText}
          </span>
          <span class="stage-percentage">${Math.round(t.progress)}%</span>
        </div>
        <div class="progress" role="progressbar" aria-label="任务进度" aria-valuenow="${Math.round(t.progress)}" aria-valuemin="0" aria-valuemax="100">
          <i style="width:${t.progress}%"></i>
        </div>
      </div>

      ${t.error && t.status !== 'cancelled' ? `
        <div class="task-error">
          <span class="error-icon">${icons.alert}</span>
          <span class="error-text">${esc(t.error)}</span>
        </div>
      ` : ''}
    </article>`;
  }).join('')}</div>`;
}

function drawOverview() {
  const {stats,usage,disk,daily,tasks,notices} = overview;
  const success = stats.total ? Math.round((stats.completed||0) / stats.total * 100) : 0;
  const bars = Array.from({length:7}, (_,i) => {
    const day = new Date(Date.now() - (6-i) * 86400000).toISOString().slice(0,10);
    return {day, count: daily.find(d => d.day === day)?.count || 0};
  });
  const max = Math.max(1, ...bars.map(d => d.count));
  const pipelineSteps = [
    { name: '发现视频', icon: icons.discover },
    { name: '下载保存', icon: icons.download },
    { name: '翻译字幕', icon: icons.translate },
    { name: '转载投稿', icon: icons.upload },
    { name: '字幕确认', icon: icons.subtitles }
  ];

  $('#main').innerHTML = head('视频工作台','查看运行状态、处理异常，让每条视频有序发布。') + `
    <div class="metrics">
      <article class="metric">
        <div class="metric-head"><label>累计任务</label><span class="metric-icon">${icons.video}</span></div>
        <div class="value">${stats.total}</div>
        <small>订阅抓取 + 手动导入</small>
      </article>
      <article class="metric">
        <div class="metric-head"><label>已完成发布</label><span class="metric-icon" style="color:var(--green)">${icons.checkCircle}</span></div>
        <div class="value">${stats.completed||0}</div>
        <small>完成率 ${success}% · 含字幕确认</small>
      </article>
      <article class="metric">
        <div class="metric-head"><label>需要关注</label><span class="metric-icon" style="color:${stats.attention?'var(--danger)':'var(--muted-dark)'}">${icons.alertCircle}</span></div>
        <div class="value" style="${stats.attention?'color:var(--danger)':''}">${stats.attention||0}</div>
        <small>待配置、异常或投稿核对</small>
      </article>
      <article class="metric">
        <div class="metric-head"><label>累计 API 费用估算</label><span class="metric-icon" style="color:var(--accent)">${icons.cost}</span></div>
        <div class="value">¥ ${usage.cost.toFixed(2)}</div>
        <small>按配置单价估算 · 非账单金额</small>
      </article>
    </div>
    <div class="grid-two">
      <section class="panel">
        <div class="panel-head"><h2>自动化工作流</h2><small>原声 · 独立字幕</small></div>
        <div class="pipeline">
          ${pipelineSteps.map((s,i) => `
            <div class="step">
              <div class="step-circle" title="步骤 ${i+1}：${s.name}">${s.icon}</div>
              <span>${s.name}</span>
            </div>
          `).join('')}
        </div>
        <div class="pipeline-note">
          <span>无需人工审核 · 单任务顺序处理</span>
          <span>失败自动重试</span>
        </div>
      </section>
      <section class="panel">
        <div class="panel-head"><h2>近 7 天任务</h2><small>UTC · 新建任务数</small></div>
        <div class="panel-body">
          <div class="bars" aria-label="最近七天任务数量">
            ${bars.map(d => `
              <div class="bar-group" title="${d.day}：${d.count} 条">
                <small>${d.count}</small>
                <div class="bar ${d.count?'has-data':''}" style="height:${Math.max(4, d.count/max*85)}px"></div>
                <span>${d.day.slice(5).replace('-','/')}</span>
              </div>
            `).join('')}
          </div>
        </div>
      </section>
    </div>
    <div class="section-head">
      <h2>最近任务</h2>
      <a href="#tasks"><span>查看全部</span> ${icons.arrowRight}</a>
    </div>
    <section class="panel">${taskTable(tasks.slice(0,5))}</section>
    <div class="grid-two" style="margin-top:26px">
      <section class="panel">
        <div class="panel-head"><h2>运行通知</h2><small>Telegram & 工作台</small></div>
        <div class="panel-body">
          ${notices.length ? notices.slice(0,4).map(n => `
            <div class="notice">
              ${esc(n.message)}
              <small>${date(n.created)} · ${n.sent ? 'Telegram 已发送' : n.attempts>=5 ? 'Telegram 发送失败' : 'Telegram 待发送 / 未配置'}</small>
            </div>
          `).join('') : '<p class="muted" style="margin:0">暂无通知。任务完成或需要处理时会显示在这里。</p>'}
        </div>
      </section>
      <section class="panel">
        <div class="panel-head"><h2>NAS 存储</h2><small>数据所在分区</small></div>
        <div class="panel-body">
          <div class="row between">
            <strong>${bytes(disk.free)} 可用</strong>
            <small>共 ${bytes(disk.total)}</small>
          </div>
          <div class="progress" style="width:100%;margin:18px 0">
            <i style="width:${(1-disk.free/disk.total)*100}%"></i>
          </div>
          <small class="muted">原视频与字幕长期留存，系统不会自动删除文件。</small>
        </div>
      </section>
    </div>`;
}

function drawTasks() {
  $('#main').innerHTML = head('任务队列','从下载到发布，掌握每一步进展。') + `
    <div class="queue-summary" id="queue-summary"></div>
    <section class="panel queue-panel">
      <div class="toolbar">
        <div class="search-field">
          <label for="task-search">搜索任务</label>
          <input id="task-search" placeholder="输入标题或视频 ID" value="${esc(query)}">
        </div>
        <div class="filter-field">
          <label for="task-filter">处理状态</label>
          <select id="task-filter">
            <option value="">全部状态</option>
            <option value="active" ${filter==='active'?'selected':''}>进行中</option>
            <option value="attention" ${filter==='attention'?'selected':''}>需要处理</option>
            ${Object.entries(states).map(([k,v])=>`<option value="${k}" ${filter===k?'selected':''}>${v}</option>`).join('')}
          </select>
        </div>
        <button class="ghost" data-action="refresh-tasks">${icons.refresh}<span>刷新列表</span></button>
      </div>
      <div class="list-heading">
        <strong id="result-count"></strong>
        <small>每 5 秒自动更新</small>
      </div>
      <div id="task-table"></div>
    </section>
    <p class="help queue-note">显示最近 300 条记录。删除记录保留本地文件和 B 站稿件；重新添加原链接可找回记录，避免重复投稿。</p>`;
  drawFiltered();
}

function drawFiltered() {
  const selected = overview.tasks.filter(t => 
    (!filter || (filter==='active' ? isActive(t) : filter==='attention' ? needsAttention(t) : t.status===filter)) &&
    (`${t.title} ${t.video_id}`.toLowerCase().includes(query.toLowerCase()))
  );
  $('#task-table').innerHTML = taskTable(selected);
  if ($('#result-count')) $('#result-count').textContent = `${selected.length} 条记录`;
  if ($('#queue-summary')) {
    $('#queue-summary').innerHTML = [
      ['active', '进行中', overview.tasks.filter(isActive).length, '正在执行或等待执行'],
      ['attention', '需要处理', overview.tasks.filter(needsAttention).length, '检查配置或核对投稿'],
      ['completed', '已完成', overview.tasks.filter(t=>t.status==='completed').length, '视频与字幕均已确认'],
      ['', '全部记录', overview.tasks.length, '当前队列中的任务']
    ].map(([value, label, count, hint]) => `
      <button class="queue-stat ${filter===value?'selected':''}" data-action="filter-tasks" data-filter="${value}" aria-pressed="${filter===value}">
        <span>${label}</span>
        <strong>${count}</strong>
        <small>${hint}</small>
      </button>
    `).join('');
  }
}

async function drawChannels() {
  channelData = await api('/channels');
  $('#main').innerHTML = head('频道订阅','首次检查建立基线，之后自动处理新视频；默认排除 Shorts 和直播。','new-channel','订阅频道') + 
    (channelData.length ? `<div class="channel-grid">${channelData.map(c => `
      <article class="panel channel-card">
        <div class="row between">
          <h2>${esc(c.name)}</h2>
          <span class="badge ${c.enabled?'completed':'paused'}">${c.enabled?'订阅中':'已暂停'}</span>
        </div>
        <p class="channel-url">${esc(c.url)}</p>
        <div class="row between">
          <small>${c.initialized?'基线已建立':'首次检查中 · 尚未建立基线'}</small>
          <small>检查：${date(c.last_poll)}</small>
        </div>
        ${c.error ? `<p class="error" style="margin-top:14px">${esc(c.error)}</p>` : ''}
        <div class="row">
          <button data-action="edit-channel" data-id="${c.id}">编辑设置</button>
          <button class="ghost" data-action="toggle-channel" data-id="${c.id}">${c.enabled?'暂停订阅':'恢复订阅'}</button>
        </div>
      </article>
    `).join('')}</div>` : `<section class="panel">${empty('订阅你的第一个频道','新视频会自动进入队列，首次添加不会搬运历史视频。','new-channel','添加频道')}</section>`);
}

const field = (label, name, value, type='text', extra='') => `<label>${label}<input name="${name}" type="${type}" value="${esc(value)}" ${extra}></label>`;

function routeForm(purpose, slot, route) {
  const prefix = `${purpose}.${slot}.`;
  return `<div class="route-box">
    <h3>${slot==='primary'?'主服务':'备用服务'}</h3>
    ${field('服务名称', prefix+'name', route.name)}
    <label>接口类型
      <select name="${prefix}protocol">
        ${[['openai','OpenAI 兼容'],...(purpose==='translation'?[['qwen','千问文本翻译']]:[['qwen_audio','千问音频直传（推荐）'],['qwen_asr','千问录音识别（Filetrans）']])].map(([v,label])=>`<option value="${v}" ${route.protocol===v?'selected':''}>${label}</option>`).join('')}
      </select>
    </label>
    <button type="button" data-action="qwen-preset" data-purpose="${purpose}" data-slot="${slot}">填入千问北京配置</button>
    ${purpose==='translation'?`<button type="button" data-action="test-translation" data-slot="${slot}">测试已保存的配置</button>`:''}
    ${field('API 基础地址', prefix+'base_url', route.base_url, 'url', 'placeholder="https://服务地址/v1"')}
    ${field('模型名称', prefix+'model', route.model)}
    ${field(route.key_configured?'API Key · 已配置，留空保留':'API Key', prefix+'api_key', '', 'password', 'autocomplete="new-password"')}
    <div class="fields">
      ${purpose==='translation' ? field('输入 ¥ / 百万 token', prefix+'input_per_million', route.input_per_million, 'number', 'min="0" step="any"') + field('输出 ¥ / 百万 token', prefix+'output_per_million', route.output_per_million, 'number', 'min="0" step="any"') : field('¥ / 音频分钟', prefix+'per_minute', route.per_minute, 'number', 'min="0" step="any"')}
    </div>
  </div>`;
}

async function drawSettings() {
  settingsData = await api('/settings');
  const s = settingsData;
  $('#main').innerHTML = head('服务设置','连接你的模型服务、投稿账号和通知渠道。', null) + `<form id="settings-form" class="settings-layout">
    <section class="panel">
      <div class="panel-head">
        <h2>运行与投稿</h2>
        <small>${Object.entries(s.tools).map(([k,v]) => `${esc(k)} ${v?'就绪':'未检测到'}`).join(' · ')}</small>
      </div>
      <div class="panel-body fields">
        ${field('YouTube / Telegram 代理','proxy',s.proxy,'text','placeholder="http://192.168.1.10:7890"')}
        <small class="help full">YouTube 下载由 NAS 容器发起；429 限流时会统一冷却下载队列。请确保此处填写的是 NAS 可访问的代理地址。</small>
        ${field('频道检查间隔（分钟）','poll_minutes',s.poll_minutes,'number','min="5" max="1440"')}
        ${field('磁盘最少可用空间（GB）','min_free_gb',s.min_free_gb,'number','min="1" step="any"')}
        ${field('每月费用估算上限（¥，0 为不限）','monthly_budget',s.monthly_budget,'number','min="0" step="any"')}
        ${field('默认投稿分区 ID','posting.tid',s.posting.tid,'number','min="1"')}
        ${field('默认标签（逗号分隔）','posting.tags',s.posting.tags)}
        ${field('默认合集 ID（0 为不加入）','posting.season_id',s.posting.season_id,'number','min="0"')}
        ${field('默认小节 ID（单小节可填 0）','posting.section_id',s.posting.section_id,'number','min="0"')}
        ${field('默认标题前缀（可留空）','posting.title_prefix',s.posting.title_prefix)}
        <button type="button" data-action="list-collections">查看我的 B 站合集与小节 ID</button>
        <label class="full">全局翻译资料与术语<textarea name="translation_notes" rows="5" maxlength="12000">${esc(s.translation_notes)}</textarea><small>用于字幕、标题与双语简介；任务首次翻译时保存资料快照。频道资料优先。</small></label>
        <p class="help full">代理使用 NAS 可访问的地址，容器内的 127.0.0.1 指向容器自身。API 服务按各自地址连接。单价统一填写人民币；0 表示尚未计价，预算无法约束未计价调用。</p>
      </div>
    </section>
    ${['translation','transcription'].map(p => `
      <section class="panel">
        <div class="panel-head">
          <h2>${p==='translation'?'字幕与文案翻译':'无字幕视频 · 语音识别'}</h2>
          <small>千问 / OpenAI 兼容</small>
        </div>
        <div class="panel-body">
          <p class="help">${p==='translation'?'支持 /chat/completions，按字幕段落翻译并保存进度。':'推荐千问音频直传：每 5 分钟分段，无需 OSS。Filetrans 使用临时存储，适合联调；其他服务需支持 verbose_json 时间轴。'}</p>
          <div class="route-grid">
            ${routeForm(p,'primary',s[p].primary)}
            ${routeForm(p,'fallback',s[p].fallback)}
          </div>
          <label class="check-label" style="margin-top:18px;margin-bottom:0">
            <input name="${p}.fallback_enabled" type="checkbox" ${s[p].fallback_enabled?'checked':''}>
            允许主服务失败时调用备用服务（可能产生费用）
          </label>
        </div>
      </section>
    `).join('')}
    <section class="panel">
      <div class="panel-head"><h2>Telegram 通知</h2><small>完成 · 失败 · 登录失效</small></div>
      <div class="panel-body fields">
        ${field(s.telegram_configured?'Bot Token · 已配置，留空保留':'Bot Token','telegram_token','','password','autocomplete="new-password"')}
        ${field('Chat ID','telegram_chat_id',s.telegram_chat_id)}
        <div class="full">
          <button type="button" data-action="test-notification">发送测试通知</button>
          <small> 请先保存设置。</small>
        </div>
      </div>
    </section>
    <section class="panel">
      <div class="panel-head"><h2>平台登录凭证</h2><small>仅保存在 NAS 数据目录</small></div>
      <div class="panel-body">
        <div class="row between">
          <div>
            <strong>Bilibili</strong>
            <p class="help">${s.bilibili_configured?'已导入登录文件（有效性在投稿时检查）':'尚未导入 biliup 登录文件'}</p>
          </div>
          <div class="row">
            <button type="button" data-action="bili-login">扫码登录</button>
            <button type="button" data-action="bili-check">验证登录</button>
            <button type="button" data-action="credentials" data-provider="bilibili">导入 cookies.json</button>
          </div>
        </div>
        <div class="row between" style="margin-top:16px">
          <div>
            <strong>YouTube</strong>
            <p class="help">${s.youtube_pot_configured ? '已配置自动 PO Token 服务（实际可用性在下载时检查）' : '未配置自动 PO Token 服务'}</p>
            <p class="help">${s.youtube_configured?'已配置 cookies.txt':'可选：遇到登录限制时导入 Netscape cookies.txt'}</p>
            <p class="help">匿名优先，仅登录或请求验证失败时尝试 Cookie。文件保存在 NAS 数据卷，下载使用独立副本。更新后自动恢复因 YouTube 登录等待的任务。</p>
            <p class="help">${s.youtube_extension_paired?'已配对 Edge 自动同步；YouTube 登录失效时仍需在浏览器重新登录。':'可配对 Edge 扩展，在 Cookie 变化后自动同步至 NAS。'}</p>
          </div>
          <div class="row"><button type="button" data-action="pair-youtube-extension">${s.youtube_extension_paired?'重新配对 Edge':'配对 Edge 自动同步'}</button><button type="button" data-action="credentials" data-provider="youtube">导入 cookies.txt</button></div>
        </div>
        <small class="help">扩展只读取 youtube.com Cookie，并发送至本 NAS；验证登录有效后才替换当前 Cookie。使用 HTTP 时仅应在可信局域网操作。</small>
        ${field('YouTube 下载最小间隔（秒）','youtube_sleep_seconds',s.youtube_sleep_seconds,'number','min="5" max="120"')}
        <p class="help" style="margin-top:16px">也可以在 NAS 终端执行：<code>docker compose exec app biliup -u /data/cookies.json login</code></p>
      </div>
    </section>
    <div class="save-bar">
      <small>保存后新步骤使用新配置；等待中的任务可在详情中继续。</small>
      <button class="primary" type="submit">${icons.check}<span>保存设置</span></button>
    </div>
  </form>`;
}

async function navigate() {
  page = location.hash.slice(1) in names ? location.hash.slice(1) : 'overview';
  document.querySelectorAll('nav a').forEach(a => {
    const active = a.dataset.page === page;
    a.classList.toggle('active', active);
    if (active) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  $('#breadcrumb').textContent = names[page];
  if (page === 'channels') await drawChannels();
  else if (page === 'settings') await drawSettings();
  else if (page === 'tasks') drawTasks();
  else drawOverview();
}

async function refresh(render=true) {
  overview = await api('/overview');
  $('#queue-count').textContent = overview.tasks.filter(t => ['queued','running','retrying'].includes(t.status)).length;
  const connText = $('#connection .conn-text');
  if (connText) {
    connText.textContent = overview.worker_enabled ? '服务在线 · 自动处理' : '服务在线 · 工作队列已关闭';
  } else {
    $('#connection').textContent = overview.worker_enabled ? '服务在线 · 自动处理' : '服务在线 · 工作队列已关闭';
  }
  if (render) {
    if (page === 'overview') drawOverview();
    if (page === 'tasks' && !$('#task-search')?.matches(':focus')) drawFiltered();
  }
}

async function showDetail(id) {
  const t = await api('/tasks/' + id);
  const payload = t.payload;
  modal('任务详情', `<h3 class="detail-title">${esc(t.title||t.video_id)}</h3>
    <div class="row">
      ${badge(t.status)}
      <small>${stages[t.stage] || t.stage} · ${Math.round(t.progress)}% · 累计处理 ${Math.round((payload.elapsed_seconds||0)/60)} 分钟</small>
    </div>
    ${t.error ? `<p class="error" style="margin-top:18px">${esc(t.error)}</p>` : ''}
    <p style="margin-top:16px">
      <a href="${esc(t.url)}" target="_blank" rel="noopener">YouTube 原视频 ${icons.external}</a>
      ${payload.bvid ? ` · <a href="https://www.bilibili.com/video/${esc(payload.bvid)}" target="_blank" rel="noopener">B 站投稿 ${icons.external}</a>` : ''}
    </p>
    <div class="row detail-actions">
      ${!['completed','cancelled','reconcile'].includes(t.status) ? `<button data-action="task-action" data-id="${id}" data-command="pause">${icons.pause}<span>暂停</span></button>` : ''}
      ${!['running','completed','reconcile'].includes(t.status) && !payload.assets_deleted ? `<button class="primary" data-action="task-action" data-id="${id}" data-command="resume">${icons.play}<span>继续 / 重试</span></button>` : ''}
      ${!['completed','cancelled'].includes(t.status) ? `<button class="ghost" data-action="task-action" data-id="${id}" data-command="cancel">取消任务</button>` : ''}
      <button class="ghost" data-action="detail" data-id="${id}">刷新详情</button>
    </div>
    ${t.status==='reconcile' ? `<form id="link-form" data-id="${id}">
      ${field('已发布稿件 BV 号','bvid','','text','required pattern="BV[0-9A-Za-z]{10}"')}
      <button type="submit">关联稿件并继续字幕</button>
      <p class="help">请先在创作中心核对；无法确定结果时不会再次投稿。</p>
    </form>` : ''}
    ${payload.bvid && !payload.assets_deleted && (t.status === 'completed' || t.stage === 'collection') ? `<form id="task-collection-form" data-id="${id}">
      <h3>加入 B 站合集</h3>
      <div class="fields">${field('合集 ID', 'season_id', payload.collection_target?.season_id || '', 'number', 'min="1" required')}${field('小节 ID（单小节可填 0）', 'section_id', payload.collection_target?.section_id || 0, 'number', 'min="0"')}</div>
      <p class="help">可在服务设置查看合集 ID。仅执行加入合集；已加入其他合集的稿件请在 B 站手动调整。</p>
      <button type="submit">保存并加入合集</button>
    </form>` : ''}
    <h3>本地文件</h3>
    <div class="file-list">
      ${t.files.map(f => `<a href="/api/tasks/${id}/files/${encodeURIComponent(f.name)}" download>${icons.file}<span>${esc(f.name)}</span> <small>${bytes(f.size)}</small></a>`).join('') || '<small>尚未生成文件</small>'}
    </div>
    ${t.files.some(f => f.name==='bilingual.srt') ? `<button data-action="preview" data-id="${id}">预览中英字幕</button><pre id="subtitle-preview" class="subtitle-preview" hidden></pre>` : ''}
    ${t.files.length ? `<p style="margin-top:18px"><button class="ghost danger" data-action="delete-files" data-id="${id}">${icons.trash}<span>删除本地视频和处理文件</span></button></p>` : ''}
    <h3 style="margin-top:24px">处理记录</h3>
    ${t.events.map(e => `<div class="notice">${esc(e.message)}<small>${date(e.created)}</small></div>`).join('') || '<small>任务尚未开始</small>'}`);
  if (t.status==='reconcile') $('#link-form').insertAdjacentHTML('beforeend', `<button type="button" class="ghost danger" data-action="reset-publication" data-id="${id}">确认未投稿，重新提交</button>`);
}

function channelModal(channel) {
  const options = channel?.options || settingsData?.posting || {tid:171, tags:'YouTube,双语字幕'};
  modal(channel ? '编辑频道' : '订阅频道', `<form id="channel-form" data-id="${channel?.id||''}">
    ${field('频道名称', 'name', channel?.name||'', 'text', 'required')}
    ${field('YouTube 频道链接', 'url', channel?.url||'', 'url', `required ${channel?'readonly':''} placeholder="https://www.youtube.com/@channel"`)}
    <div class="fields">
      ${field('投稿分区 ID', 'tid', options.tid, 'number', 'min="1" required')}
      ${field('投稿标签', 'tags', options.tags)}
      ${field('合集 ID（0 为不加入）', 'season_id', options.season_id || 0, 'number', 'min="0"')}
      ${field('小节 ID（单小节可填 0）', 'section_id', options.section_id || 0, 'number', 'min="0"')}
      ${field('标题前缀（可留空）', 'title_prefix', options.title_prefix || '')}
    </div>
    <button type="button" data-action="load-channel-collections">从 B 站读取合集</button>
    <div id="collection-picker"></div>
    <label>频道翻译资料与术语<textarea name="translation_notes" rows="5" maxlength="12000">${esc(options.translation_notes || '')}</textarea></label>
    <p class="help">请先在 B 站创作中心创建合集。新任务按转载顺序追加；设置修改不改变已有任务。合集失败只重试加入步骤。</p>
    <p class="help">首次检查只记录现有视频，后续新视频自动进入任务队列。恢复订阅会处理暂停期间发现的新视频。</p>
    <button class="primary" type="submit">${channel ? '保存频道' : '开始订阅'}</button>
  </form>`);
}

document.addEventListener('click', async e => {
  const button = e.target.closest('[data-action]');
  if (!button) return;
  const {action, id} = button.dataset;
  try {
    if (action === 'list-collections' || action === 'load-channel-collections') {
      const items = await api('/bilibili/collections');
      const rows = items.flatMap(c => c.sections.map(s => ({...s, season: c.id, label: `${c.title} / ${s.title}（合集 ${c.id}，小节 ${s.id}）`})));
      if (action === 'list-collections') {
        modal('我的 B 站合集', rows.map(r => `<p>${esc(r.label)}</p>`).join('') || '<p>当前账号暂无合集，请先在 B 站创作中心创建。</p>');
      } else {
        $('#collection-picker').innerHTML = rows.length ? `<label>选择合集小节<select id="collection-select"><option value="">请选择</option>${rows.map(r => `<option value="${Number(r.season)},${Number(r.id)}">${esc(r.label)}</option>`).join('')}</select></label>` : '<p>当前账号暂无合集，请先在 B 站创建。</p>';
        $('#collection-select')?.addEventListener('change', e => {
          if (!e.target.value) return;
          const [season, section] = e.target.value.split(',');
          $('#channel-form [name="season_id"]').value = season;
          $('#channel-form [name="section_id"]').value = section;
        });
      }
    }
    if (action === 'clear-filter') { filter=''; query=''; drawTasks(); return; }
    if (action === 'filter-tasks') { filter = button.dataset.filter; drawTasks(); return; }
    if (action === 'refresh-tasks') {
      button.disabled = true;
      try { await refresh(); toast('队列已更新'); }
      finally { button.disabled = false; }
      return;
    }
    if (action === 'pair-youtube-extension') {
      const result = await api('/youtube/extension-pair', 'POST');
      modal('配对 Edge Cookie 同步', `<p>1. 在 Edge 打开 <code>edge://extensions</code>，启用「开发人员模式」，选择「加载解压缩的扩展」。</p>
        <p>2. 选择项目目录 <code>${esc(result.extension_path)}</code>，再点击扩展图标填写 NAS 地址和配对码。</p>
        <p>3. 登录 YouTube。扩展会在 Cookie 更新后同步；YouTube 要求验证时仍需你手动登录。</p>
        <label>一次性配对码<input readonly value="${esc(result.token)}" autocomplete="off"></label>
        <p class="help">配对码只显示一次。重新配对会立即撤销之前的扩展密钥。</p>`);
      return;
    }
    if (action === 'delete-task') {
      const task = overview.tasks.find(t => t.id === id);
      modal('删除任务记录', `<p>将「${esc(task?.title||task?.video_id||id)}」从队列中移除。</p>
        <div class="delete-note">尚未执行的任务会停止排队。本地视频、字幕、已发布稿件及去重信息均保留。需要清理磁盘时，请在任务详情中单独删除文件。</div>
        <div class="row confirm-actions">
          <button class="ghost" data-action="close" autofocus>保留记录</button>
          <button class="danger-solid" data-action="confirm-delete-task" data-id="${id}">删除记录</button>
        </div>`);
      return;
    }
    if (action === 'confirm-delete-task') {
      button.disabled = true;
      try {
        await api(`/tasks/${id}`, 'DELETE');
        $('#modal').close();
        await refresh();
        toast('记录已从队列移除，本地文件已保留');
      } finally { button.disabled = false; }
      return;
    }
    if (action === 'test-translation') {
      button.disabled = true;
      try {
        const result = await api('/routes/translation/' + button.dataset.slot + '/test', 'POST');
        toast('翻译接口正常：' + result.text);
      } finally { button.disabled = false; }
      return;
    }
    if (action === 'bili-check') {
      button.disabled = true;
      try {
        const result = await api('/accounts/bilibili/check', 'POST');
        toast(result.valid ? `已登录：${result.name}（UID ${result.mid}）` : '登录已失效，请重新扫码');
      } finally { button.disabled = false; }
      return;
    }
    if (action === 'bili-login') {
      button.disabled = true;
      let login;
      try { login = await api('/accounts/bilibili/qr', 'POST'); }
      finally { button.disabled = false; }
      modal('Bilibili 扫码登录', `<p>请使用 Bilibili App 扫描二维码并确认登录。</p>
        <div style="text-align:center;padding:16px 0">
          <img id="bili-qr" alt="Bilibili 登录二维码" width="260" height="260" style="border-radius:12px;background:#fff;padding:8px">
        </div>
        <p id="bili-login-status" role="status" style="text-align:center;font-weight:500">等待扫码…</p>`);
      $('#bili-qr').src = login.image;
      const image = $('#bili-qr');
      async function poll() {
        if (!$('#modal').open || $('#bili-qr') !== image) return;
        try {
          const result = await api('/accounts/bilibili/qr/' + encodeURIComponent(login.id), 'POST');
          if (!$('#modal').open || $('#bili-qr') !== image) return;
          $('#bili-login-status').textContent = {
            scan: '等待扫码…',
            confirm: '已扫码，请在手机上确认',
            expired: '二维码已过期，请关闭后重新登录',
            done: '登录成功，凭据已保存到 NAS'
          }[result.status] || '等待响应…';
          if (result.status === 'done') {
            await drawSettings();
            toast('Bilibili 登录凭据已保存');
            return;
          }
          if (result.status !== 'expired') setTimeout(poll, 2500);
        } catch(error) {
          if ($('#bili-qr') === image) $('#bili-login-status').textContent = error.message;
        }
      }
      setTimeout(poll, 2500);
      return;
    }
    if (action === 'qwen-preset') {
      const {purpose, slot} = button.dataset;
      const translation = purpose === 'translation';
      const values = {
        name: translation ? 'Qwen 翻译' : 'Qwen 录音识别',
        protocol: translation ? 'qwen' : 'qwen_audio',
        base_url: 'https://dashscope.aliyuncs.com/' + (translation ? 'compatible-mode/v1' : 'api/v1'),
        model: translation ? 'qwen-plus' : 'qwen-audio-3.0-asr-flash'
      };
      for (const [key, value] of Object.entries(values)) {
        $('#settings-form').elements.namedItem(`${purpose}.${slot}.${key}`).value = value;
      }
      toast('已填入千问北京配置；请填写该地域的 API Key 和控制台单价，然后保存');
      return;
    }
    if (action === 'close') { $('#modal').close(); return; }
    if (action === 'new-task') {
      modal('手动转载视频', `<form id="task-form">
        ${field('YouTube 视频链接', 'url', '', 'url', 'required placeholder="https://www.youtube.com/watch?v=…"')}
        <p class="help">提交后自动下载、翻译并发布到 B 站，不经过人工审核。重复链接会打开已有任务。</p>
        <button class="primary" type="submit">${icons.plus}<span>创建并开始任务</span></button>
      </form>`);
      return;
    }
    if (action === 'new-channel') {
      if (!settingsData) settingsData = await api('/settings');
      channelModal();
      return;
    }
    if (action === 'edit-channel') {
      channelModal(channelData.find(c => c.id === id));
      return;
    }
    if (action === 'detail') { await showDetail(id); return; }
    if (action === 'logout') {
      await api('/logout', 'POST');
      $('#shell').hidden = true;
      $('#login').hidden = false;
      return;
    }
    if (action === 'task-action') {
      await api(`/tasks/${id}/action`, 'POST', {action: button.dataset.command});
      toast('操作已保存');
      await showDetail(id);
      await refresh();
      return;
    }
    if (action === 'reset-publication') {
      if (!confirm('已在 B 站创作中心确认没有该稿件？此操作会再次提交视频，若判断有误会重复投稿。')) return;
      await api(`/tasks/${id}/action`, 'POST', {action: 'reset-publication'});
      await showDetail(id);
      await refresh();
      return;
    }
    if (action === 'toggle-channel') {
      const channel = channelData.find(c => c.id === id);
      await api('/channels/' + id, 'PUT', {...channel, enabled: !channel.enabled});
      await drawChannels();
      return;
    }
    if (action === 'test-notification') {
      await api('/notifications/test', 'POST');
      toast('测试通知已排队，发送结果可在总览查看');
      return;
    }
    if (action === 'credentials') {
      modal('导入登录凭证', `<form id="credential-form" data-provider="${button.dataset.provider}">
        <label>选择 ${button.dataset.provider==='bilibili'?'cookies.json':'cookies.txt'}
          <input type="file" name="file" accept=".json,.txt" required>
        </label>
        <p class="help">文件直接保存至 NAS，不会在页面中回显。</p>
        <button class="primary" type="submit">${icons.upload}<span>导入凭证</span></button>
      </form>`);
      return;
    }
    if (action === 'preview') {
      const response = await fetch(`/api/tasks/${id}/files/bilingual.srt`);
      if (!response.ok) throw new Error('字幕读取失败');
      $('#subtitle-preview').textContent = await response.text();
      $('#subtitle-preview').hidden = false;
      return;
    }
    if (action === 'delete-files') {
      if (!confirm('永久删除该任务的本地视频、字幕和处理文件？B 站稿件与任务记录会保留。')) return;
      await api(`/tasks/${id}/files`, 'DELETE');
      toast('本地文件已删除');
      await showDetail(id);
      await refresh();
    }
  } catch (error) { toast(error.message); }
});

document.addEventListener('submit', async e => {
  const form = e.target;
  e.preventDefault();
  const button = form.querySelector('[type=submit]');
  if (button) button.disabled = true;
  try {
    const data = new FormData(form);
    if (form.id === 'login-form') {
      await api('/login', 'POST', {password: data.get('password')});
      form.reset();
      $('#login-error').textContent = '';
      $('#login').hidden = true;
      $('#shell').hidden = false;
      await refresh(false);
      await navigate();
    }
    if (form.id === 'task-form') {
      const result = await api('/tasks', 'POST', {url: data.get('url')});
      $('#modal').close();
      await refresh();
      await showDetail(result.id);
      toast('已打开任务；已有视频保留原处理状态');
    }
    if (form.id === 'task-collection-form') {
      await api('/tasks/' + form.dataset.id + '/collection', 'PUT', {season_id: Number(data.get('season_id')), section_id: Number(data.get('section_id'))});
      await showDetail(form.dataset.id);
      toast('已排队加入合集');
    }
    if (form.id === 'channel-form') {
      const id = form.dataset.id;
      const old = channelData.find(c => c.id === id);
      await api('/channels' + (id ? '/' + id : ''), id ? 'PUT' : 'POST', {
        name: data.get('name'),
        url: data.get('url'),
        enabled: old ? !!old.enabled : true,
        options: { tid: Number(data.get('tid')), tags: data.get('tags'), season_id: Number(data.get('season_id')), section_id: Number(data.get('section_id')), title_prefix: data.get('title_prefix'), translation_notes: data.get('translation_notes') }
      });
      $('#modal').close();
      if (page === 'channels') await drawChannels();
      toast('频道设置已保存');
    }
    if (form.id === 'settings-form') {
      const result = structuredClone(settingsData);
      for (const input of form.querySelectorAll('[name]')) {
        const keys = input.name.split('.');
        let target = result;
        for (const key of keys.slice(0, -1)) target = target[key];
        target[keys.at(-1)] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value;
      }
      await api('/settings', 'PUT', result);
      await drawSettings();
      toast('设置已保存');
    }
    if (form.id === 'credential-form') {
      const file = data.get('file');
      if (file.size > 2000000) throw new Error('登录文件不能超过 2 MB');
      const result = await api('/credentials/' + form.dataset.provider, 'PUT', {content: await file.text()});
      $('#modal').close();
      if (page === 'settings') await drawSettings();
      toast(result.resumed ? `登录凭证已保存，已恢复 ${result.resumed} 个 YouTube 等待任务` : '登录凭证已保存');
    }
    if (form.id === 'link-form') {
      await api(`/tasks/${form.dataset.id}/action`, 'POST', {action: 'link', bvid: data.get('bvid')});
      await showDetail(form.dataset.id);
      await refresh();
      toast('已关联稿件，将继续提交字幕');
    }
  } catch (error) {
    if (form.id === 'login-form') $('#login-error').textContent = error.message;
    else toast(error.message);
  } finally {
    if (button) button.disabled = false;
  }
});

document.addEventListener('input', e => {
  if (e.target.id === 'task-search') {
    query = e.target.value;
    drawFiltered();
  }
});

document.addEventListener('change', e => {
  if (e.target.id === 'task-filter') {
    filter = e.target.value;
    drawFiltered();
  }
});

window.addEventListener('hashchange', () => navigate().catch(e => toast(e.message)));

async function init() {
  try {
    await refresh(false);
    $('#shell').hidden = false;
    await navigate();
  } catch {
    $('#login').hidden = false;
  }
}

setInterval(() => {
  if (!$('#shell').hidden) {
    refresh().catch(() => {
      const connText = $('#connection .conn-text');
      if (connText) connText.textContent = '连接中断 · 正在重连';
      else $('#connection').textContent = '连接中断 · 正在重连';
    });
  }
}, 8000);

init();
