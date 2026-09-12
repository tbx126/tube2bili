const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const names = {overview:'总览',tasks:'任务队列',channels:'频道订阅',settings:'服务设置'};
const states = {queued:'排队中',running:'处理中',waiting:'等待配置 / 处理',paused:'已暂停',cancelled:'已取消',failed:'失败',retrying:'等待重试',reconcile:'需核对投稿',completed:'已完成'};
const stages = {download:'下载视频',translate:'翻译字幕',publish:'提交投稿',subtitles:'提交字幕',verify:'确认字幕'};
let page = 'overview', overview, channelData = [], settingsData, filter = '', query = '', toastTimer;
const date = t => t ? new Date(t * 1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '尚未检查';
const bytes = n => n >= 1024**3 ? (n/1024**3).toFixed(1)+' GB' : (n/1024**2).toFixed(1)+' MB';
const badge = state => `<span class="badge ${esc(state)}">${esc(states[state] || state)}</span>`;

async function api(path, method='GET', data) {
  const response = await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-Requested-With':'Tube2Bili'},body:data===undefined?undefined:JSON.stringify(data)});
  if (response.status===401) { $('#shell').hidden=true; $('#login').hidden=false; }
  const value = await response.json();
  if (!response.ok) throw new Error(value.detail || '请求失败');
  return value;
}
function toast(message) { clearTimeout(toastTimer); $('#toast').textContent=message; $('#toast').hidden=false; toastTimer=setTimeout(()=>$('#toast').hidden=true,6500); }
function modal(title,content) { $('#modal-title').textContent=title; $('#modal-body').innerHTML=content; if (!$('#modal').open) $('#modal').showModal(); }
function head(title,subtitle,action='new-task',label='新建任务') { return `<div class="page-head"><div><p class="eyebrow">VIDEO OPERATIONS</p><h1>${title}</h1><p>${subtitle}</p></div>${action?`<button class="primary" data-action="${action}"><span aria-hidden="true">＋</span>${label}</button>`:''}</div>`; }
function empty(title,text,action,label) { return `<div class="empty"><div class="empty-symbol" aria-hidden="true">▷</div><h3>${title}</h3><p>${text}</p>${action?`<button data-action="${action}">${label}</button>`:''}</div>`; }
function taskTable(tasks) {
  if (!tasks.length) return empty('还没有视频任务','添加一个频道，或粘贴视频链接，开始你的第一个双语视频。','new-task','添加视频链接');
  return `<div class="table-wrap"><table><thead><tr><th>视频</th><th>状态</th><th>当前步骤</th><th>创建时间</th><th>操作</th></tr></thead><tbody>${tasks.map(t=>`<tr><td><div class="video-cell"><span class="video-thumb" aria-hidden="true">▷</span><div><strong class="video-name" title="${esc(t.title)}">${esc(t.title||'等待读取视频信息')}</strong><span class="video-sub">${esc(t.video_id)} · ${t.channel_id?'频道订阅':'手动导入'}${t.assets_deleted?' · 文件已清理':''}</span></div></div></td><td>${badge(t.status)}</td><td><small>${stages[t.stage]}</small><div class="progress" role="progressbar" aria-label="任务进度" aria-valuenow="${Math.round(t.progress)}" aria-valuemin="0" aria-valuemax="100"><i style="width:${t.progress}%"></i></div></td><td class="muted">${date(t.created)}</td><td><button class="ghost" data-action="detail" data-id="${t.id}">详情 →</button></td></tr>`).join('')}</tbody></table></div>`;
}
function drawOverview() {
  const {stats,usage,disk,daily,tasks,notices}=overview;
  const success = stats.total?Math.round((stats.completed||0)/stats.total*100):0;
  const bars=Array.from({length:7},(_,i)=>{const day=new Date(Date.now()-(6-i)*86400000).toISOString().slice(0,10);return {day,count:daily.find(d=>d.day===day)?.count||0};});
  const max=Math.max(1,...bars.map(d=>d.count));
  $('#main').innerHTML=head('每一条内容，都有新的可能。','从 YouTube 到 Bilibili，让双语视频有序流转。')+`
    <div class="metrics"><article class="metric"><label>累计任务</label><div class="value">${stats.total}</div><small>订阅抓取 + 手动导入</small></article><article class="metric"><label>已完成发布</label><div class="value">${stats.completed||0}</div><small>完成率 ${success}% · 含字幕确认</small></article><article class="metric"><label>需要关注</label><div class="value">${stats.attention||0}</div><small>待配置、异常或投稿核对</small></article><article class="metric"><label>累计 API 费用估算</label><div class="value">¥ ${usage.cost.toFixed(2)}</div><small>按配置单价估算 · 非账单金额</small></article></div>
    <div class="grid-two"><section class="panel"><div class="panel-head"><h2>自动化工作流</h2><small>原声 · 独立字幕</small></div><div class="pipeline">${['发现视频','下载保存','翻译字幕','转载投稿','字幕确认'].map((s,i)=>`<div class="step"><i>${String(i+1).padStart(2,'0')}</i><span>${s}</span></div>`).join('')}</div><div class="pipeline-note"><span>无需人工审核 · 单任务顺序处理</span><span>失败自动重试</span></div></section><section class="panel"><div class="panel-head"><h2>近 7 天任务</h2><small>UTC · 新建任务数</small></div><div class="panel-body"><div class="bars" aria-label="最近七天任务数量">${bars.map(d=>`<div class="bar-group" title="${d.day}：${d.count} 条"><small>${d.count}</small><div class="bar ${d.count?'has-data':''}" style="height:${Math.max(3,d.count/max*70)}px"></div><span>${d.day.slice(5).replace('-','/')}</span></div>`).join('')}</div></div></section></div>
    <div class="section-head"><h2>最近任务</h2><a href="#tasks">查看全部 →</a></div><section class="panel">${taskTable(tasks.slice(0,5))}</section>
    <div class="grid-two" style="margin-top:24px"><section class="panel"><div class="panel-head"><h2>运行通知</h2><small>Telegram & 工作台</small></div><div class="panel-body">${notices.length?notices.slice(0,4).map(n=>`<div class="notice">${esc(n.message)}<small>${date(n.created)} · ${n.sent?'Telegram 已发送':n.attempts>=5?'Telegram 发送失败':'Telegram 待发送 / 未配置'}</small></div>`).join(''):'<p class="muted" style="margin:0">暂无通知。任务完成或需要处理时会显示在这里。</p>'}</div></section><section class="panel"><div class="panel-head"><h2>NAS 存储</h2><small>数据所在分区</small></div><div class="panel-body"><div class="row between"><strong>${bytes(disk.free)} 可用</strong><small>共 ${bytes(disk.total)}</small></div><div class="progress" style="width:100%;margin:17px 0"><i style="width:${(1-disk.free/disk.total)*100}%"></i></div><small>原视频与字幕长期留存，系统不会自动删除文件。</small></div></section></div>`;
}
function drawTasks() {
  $('#main').innerHTML=head('任务队列','查看处理进度，继续、重试或管理本地文件。')+`<div class="toolbar"><input id="task-search" aria-label="搜索任务" placeholder="搜索视频标题或 ID…" value="${esc(query)}"><select id="task-filter" aria-label="筛选状态"><option value="">全部状态</option>${Object.entries(states).map(([k,v])=>`<option value="${k}" ${filter===k?'selected':''}>${v}</option>`).join('')}</select></div><section id="task-table" class="panel"></section><p class="help" style="margin-top:14px">显示最近 300 条任务。暂停在可中断步骤生效；已发布视频不会因取消本地任务而被撤回。</p>`;
  drawFiltered();
}
function drawFiltered(){ $('#task-table').innerHTML=taskTable(overview.tasks.filter(t=>(!filter||t.status===filter)&&(`${t.title} ${t.video_id}`.toLowerCase().includes(query.toLowerCase())))); }
async function drawChannels(){
  channelData=await api('/channels');
  $('#main').innerHTML=head('频道订阅','首次检查建立基线，之后自动处理新视频；默认排除 Shorts 和直播。','new-channel','订阅频道')+(channelData.length?`<div class="channel-grid">${channelData.map(c=>`<article class="panel channel-card"><div class="row between"><h2>${esc(c.name)}</h2><span class="badge ${c.enabled?'completed':'paused'}">${c.enabled?'订阅中':'已暂停'}</span></div><p class="channel-url">${esc(c.url)}</p><div class="row between"><small>${c.initialized?'基线已建立':'首次检查中 · 尚未建立基线'}</small><small>检查：${date(c.last_poll)}</small></div>${c.error?`<p class="error" style="margin-top:14px">${esc(c.error)}</p>`:''}<div class="row"><button data-action="edit-channel" data-id="${c.id}">编辑设置</button><button class="ghost" data-action="toggle-channel" data-id="${c.id}">${c.enabled?'暂停订阅':'恢复订阅'}</button></div></article>`).join('')}</div>`:`<section class="panel">${empty('订阅你的第一个频道','新视频会自动进入队列，首次添加不会搬运历史视频。','new-channel','添加频道')}</section>`);
}
const field=(label,name,value,type='text',extra='')=>`<label>${label}<input name="${name}" type="${type}" value="${esc(value)}" ${extra}></label>`;
function routeForm(purpose,slot,route){ const prefix=`${purpose}.${slot}.`;return `<div class="route-box"><h3>${slot==='primary'?'主服务':'备用服务'}</h3>${field('服务名称',prefix+'name',route.name)}<label>接口类型<select name="${prefix}protocol">${[['openai','OpenAI 兼容'],[purpose==='translation'?'qwen':'qwen_asr',purpose==='translation'?'千问文本翻译':'千问录音识别（Filetrans）']].map(([v,label])=>`<option value="${v}" ${route.protocol===v?'selected':''}>${label}</option>`).join('')}</select></label><button type="button" data-action="qwen-preset" data-purpose="${purpose}" data-slot="${slot}">填入千问北京配置</button>${purpose==='translation'?`<button type="button" data-action="test-translation" data-slot="${slot}">测试已保存的配置</button>`:''}${field('API 基础地址',prefix+'base_url',route.base_url,'url','placeholder="https://服务地址/v1"')}${field('模型名称',prefix+'model',route.model)}${field(route.key_configured?'API Key · 已配置，留空保留':'API Key',prefix+'api_key','','password','autocomplete="new-password"')}<div class="fields">${purpose==='translation'?field('输入 ¥ / 百万 token',prefix+'input_per_million',route.input_per_million,'number','min="0" step="any"')+field('输出 ¥ / 百万 token',prefix+'output_per_million',route.output_per_million,'number','min="0" step="any"'):field('¥ / 音频分钟',prefix+'per_minute',route.per_minute,'number','min="0" step="any"')}</div></div>`;}
async function drawSettings(){
  settingsData=await api('/settings'); const s=settingsData;
  $('#main').innerHTML=head('服务设置','连接你的模型服务、投稿账号和通知渠道。',null)+`<form id="settings-form" class="settings-layout">
    <section class="panel"><div class="panel-head"><h2>运行与投稿</h2><small>${Object.entries(s.tools).map(([k,v])=>`${esc(k)} ${v?'就绪':'未检测到'}`).join(' · ')}</small></div><div class="panel-body fields">${field('YouTube / Telegram 代理','proxy',s.proxy,'text','placeholder="http://192.168.1.10:7890"')}${field('频道检查间隔（分钟）','poll_minutes',s.poll_minutes,'number','min="5" max="1440"')}${field('磁盘最少可用空间（GB）','min_free_gb',s.min_free_gb,'number','min="1" step="any"')}${field('每月费用估算上限（¥，0 为不限）','monthly_budget',s.monthly_budget,'number','min="0" step="any"')}${field('默认投稿分区 ID','posting.tid',s.posting.tid,'number','min="1"')}${field('默认标签（逗号分隔）','posting.tags',s.posting.tags)}<p class="help full">代理使用 NAS 可访问的地址，容器内的 127.0.0.1 指向容器自身。API 服务按各自地址连接。单价统一填写人民币；0 表示尚未计价，预算无法约束未计价调用。</p></div></section>
    ${['translation','transcription'].map(p=>`<section class="panel"><div class="panel-head"><h2>${p==='translation'?'字幕与文案翻译':'无字幕视频 · 语音识别'}</h2><small>千问 / OpenAI 兼容</small></div><div class="panel-body"><p class="help">${p==='translation'?'支持 /chat/completions，按字幕段落翻译并保存进度。':'千问请选择 Filetrans 接口；其他服务需支持 verbose_json 分段时间轴。音频每 10 分钟分段上传。千问当前使用官方临时存储（48 小时有效，适合试用验证）；长期运行建议接入自有 OSS。'}</p><div class="route-grid">${routeForm(p,'primary',s[p].primary)}${routeForm(p,'fallback',s[p].fallback)}</div><label class="check-label" style="margin-top:18px;margin-bottom:0"><input name="${p}.fallback_enabled" type="checkbox" ${s[p].fallback_enabled?'checked':''}>允许主服务失败时调用备用服务（可能产生费用）</label></div></section>`).join('')}
    <section class="panel"><div class="panel-head"><h2>Telegram 通知</h2><small>完成 · 失败 · 登录失效</small></div><div class="panel-body fields">${field(s.telegram_configured?'Bot Token · 已配置，留空保留':'Bot Token','telegram_token','','password','autocomplete="new-password"')}${field('Chat ID','telegram_chat_id',s.telegram_chat_id)}<div class="full"><button type="button" data-action="test-notification">发送测试通知</button><small> 请先保存设置。</small></div></div></section>
    <section class="panel"><div class="panel-head"><h2>平台登录凭证</h2><small>仅保存在 NAS 数据目录</small></div><div class="panel-body"><div class="row between"><div><strong>Bilibili</strong><p class="help">${s.bilibili_configured?'已导入登录文件（有效性在投稿时检查）':'尚未导入 biliup 登录文件'}</p></div><div class="row"><button type="button" data-action="bili-login">扫码登录</button><button type="button" data-action="bili-check">验证登录</button><button type="button" data-action="credentials" data-provider="bilibili">导入 cookies.json</button></div></div><div class="row between"><div><strong>YouTube</strong><p class="help">${s.youtube_configured?'已配置 cookies.txt':'可选：遇到登录限制时导入 Netscape cookies.txt'}</p></div><button type="button" data-action="credentials" data-provider="youtube">导入 cookies.txt</button></div><p class="help">也可以在 NAS 终端执行：<code>docker compose exec app biliup -u /data/cookies.json login</code></p></div></section>
    <div class="save-bar"><small>保存后新步骤使用新配置；等待中的任务可在详情中继续。</small><button class="primary" type="submit">保存设置</button></div></form>`;
}
async function navigate(){
  page=location.hash.slice(1) in names?location.hash.slice(1):'overview';
  document.querySelectorAll('nav a').forEach(a=>{a.classList.toggle('active',a.dataset.page===page);if(a.dataset.page===page)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
  $('#breadcrumb').textContent=names[page];
  if(page==='channels')await drawChannels();else if(page==='settings')await drawSettings();else if(page==='tasks')drawTasks();else drawOverview();
}
async function refresh(render=true){ overview=await api('/overview'); $('#queue-count').textContent=overview.tasks.filter(t=>['queued','running','retrying'].includes(t.status)).length;$('#connection').textContent=overview.worker_enabled?'服务在线 · 自动处理':'服务在线 · 工作队列已关闭';if(render){if(page==='overview')drawOverview();if(page==='tasks'&&!$('#task-search')?.matches(':focus'))drawFiltered();}}
async function showDetail(id){
  const t=await api('/tasks/'+id);const payload=t.payload;
  modal('任务详情',`<h3 class="detail-title">${esc(t.title||t.video_id)}</h3><div class="row">${badge(t.status)}<small>${stages[t.stage]} · ${Math.round(t.progress)}% · 累计处理 ${Math.round((payload.elapsed_seconds||0)/60)} 分钟</small></div>${t.error?`<p class="error" style="margin-top:18px">${esc(t.error)}</p>`:''}<p style="margin-top:16px"><a href="${esc(t.url)}" target="_blank" rel="noopener">YouTube 原视频 ↗</a>${payload.bvid?` · <a href="https://www.bilibili.com/video/${esc(payload.bvid)}" target="_blank" rel="noopener">B 站投稿 ↗</a>`:''}</p><div class="row detail-actions">${!['completed','cancelled','reconcile'].includes(t.status)?`<button data-action="task-action" data-id="${id}" data-command="pause">暂停</button>`:''}${!['running','completed','reconcile'].includes(t.status)&&!payload.assets_deleted?`<button class="primary" data-action="task-action" data-id="${id}" data-command="resume">继续 / 重试</button>`:''}${!['completed','cancelled'].includes(t.status)?`<button class="ghost" data-action="task-action" data-id="${id}" data-command="cancel">取消任务</button>`:''}<button class="ghost" data-action="detail" data-id="${id}">刷新详情</button></div>${t.status==='reconcile'?`<form id="link-form" data-id="${id}">${field('已发布稿件 BV 号','bvid','','text','required pattern="BV[0-9A-Za-z]{10}"')}<button type="submit">关联稿件并继续字幕</button><p class="help">请先在创作中心核对；无法确定结果时不会再次投稿。</p></form>`:''}<h3>本地文件</h3><div class="file-list">${t.files.map(f=>`<a href="/api/tasks/${id}/files/${encodeURIComponent(f.name)}">${esc(f.name)} <small>${bytes(f.size)}</small></a>`).join('')||'<small>尚未生成文件</small>'}</div>${t.files.some(f=>f.name==='bilingual.srt')?`<button data-action="preview" data-id="${id}">预览中英字幕</button><pre id="subtitle-preview" class="subtitle-preview" hidden></pre>`:''}${t.files.length?`<p style="margin-top:18px"><button class="ghost danger" data-action="delete-files" data-id="${id}">删除本地视频和处理文件</button></p>`:''}<h3 style="margin-top:24px">处理记录</h3>${t.events.map(e=>`<div class="notice">${esc(e.message)}<small>${date(e.created)}</small></div>`).join('')||'<small>任务尚未开始</small>'}`);
  if(t.status==='reconcile') $('#link-form').insertAdjacentHTML('beforeend',`<button type="button" class="ghost danger" data-action="reset-publication" data-id="${id}">确认未投稿，重新提交</button>`);
}
function channelModal(channel){ const options=channel?.options||settingsData?.posting||{tid:171,tags:'YouTube,双语字幕'};modal(channel?'编辑频道':'订阅频道',`<form id="channel-form" data-id="${channel?.id||''}">${field('频道名称','name',channel?.name||'','text','required')}${field('YouTube 频道链接','url',channel?.url||'','url',`required ${channel?'readonly':''} placeholder="https://www.youtube.com/@channel"`)}<div class="fields">${field('投稿分区 ID','tid',options.tid,'number','min="1" required')}${field('投稿标签','tags',options.tags)}</div><p class="help">首次检查只记录现有视频，后续新视频自动进入任务队列。恢复订阅会处理暂停期间发现的新视频。</p><button class="primary" type="submit">${channel?'保存频道':'开始订阅'}</button></form>`); }

document.addEventListener('click',async e=>{
  const button=e.target.closest('[data-action]');if(!button)return;
  const {action,id}=button.dataset;
  try{
    if(action==='test-translation'){button.disabled=true;try{const result=await api('/routes/translation/'+button.dataset.slot+'/test','POST');toast('翻译接口正常：'+result.text);}finally{button.disabled=false;}return;}
    if(action==='bili-check'){button.disabled=true;try{const result=await api('/accounts/bilibili/check','POST');toast(result.valid?`已登录：${result.name}（UID ${result.mid}）`:'登录已失效，请重新扫码');}finally{button.disabled=false;}return;}
    if(action==='bili-login'){
      button.disabled=true;let login;try{login=await api('/accounts/bilibili/qr','POST');}finally{button.disabled=false;}
      modal('Bilibili 扫码登录',`<p>请使用 Bilibili App 扫描二维码并确认登录。</p><img id="bili-qr" alt="Bilibili 登录二维码" width="280" height="280"><p id="bili-login-status" role="status">等待扫码…</p>`);
      $('#bili-qr').src=login.image;
      const image=$('#bili-qr');
      async function poll(){
        if(!$('#modal').open||$('#bili-qr')!==image)return;
        try{
          const result=await api('/accounts/bilibili/qr/'+encodeURIComponent(login.id),'POST');
          if(!$('#modal').open||$('#bili-qr')!==image)return;
          $('#bili-login-status').textContent={scan:'等待扫码…',confirm:'已扫码，请在手机上确认',expired:'二维码已过期，请关闭后重新登录',done:'登录成功，凭据已保存到 NAS'}[result.status]||'等待响应…';
          if(result.status==='done'){await drawSettings();toast('Bilibili 登录凭据已保存');return;}
          if(result.status!=='expired')setTimeout(poll,2500);
        }catch(error){if($('#bili-qr')===image)$('#bili-login-status').textContent=error.message;}
      }
      setTimeout(poll,2500);return;
    }
    if(action==='qwen-preset'){
      const {purpose,slot}=button.dataset;const translation=purpose==='translation';
      const values={name:translation?'Qwen 翻译':'Qwen 录音识别',protocol:translation?'qwen':'qwen_asr',base_url:'https://dashscope.aliyuncs.com/'+(translation?'compatible-mode/v1':'api/v1'),model:translation?'qwen-plus':'qwen3-asr-flash-filetrans'};
      for(const [key,value] of Object.entries(values))$('#settings-form').elements.namedItem(`${purpose}.${slot}.${key}`).value=value;
      toast('已填入千问北京配置；请填写该地域的 API Key 和控制台单价，然后保存');return;
    }
    if(action==='close'){$('#modal').close();return;}
    if(action==='new-task'){modal('手动转载视频',`<form id="task-form">${field('YouTube 视频链接','url','','url','required placeholder="https://www.youtube.com/watch?v=…"')}<p class="help">提交后自动下载、翻译并发布到 B 站，不经过人工审核。重复链接会打开已有任务。</p><button class="primary" type="submit">创建并开始任务 →</button></form>`);return;}
    if(action==='new-channel'){if(!settingsData)settingsData=await api('/settings');channelModal();return;}
    if(action==='edit-channel'){channelModal(channelData.find(c=>c.id===id));return;}
    if(action==='detail'){await showDetail(id);return;}
    if(action==='logout'){await api('/logout','POST');$('#shell').hidden=true;$('#login').hidden=false;return;}
    if(action==='task-action'){await api(`/tasks/${id}/action`,'POST',{action:button.dataset.command});toast('操作已保存');await showDetail(id);await refresh();return;}
    if(action==='reset-publication'){if(!confirm('已在 B 站创作中心确认没有该稿件？此操作会再次提交视频，若判断有误会重复投稿。'))return;await api(`/tasks/${id}/action`,'POST',{action:'reset-publication'});await showDetail(id);await refresh();return;}
    if(action==='toggle-channel'){const channel=channelData.find(c=>c.id===id);await api('/channels/'+id,'PUT',{...channel,enabled:!channel.enabled});await drawChannels();return;}
    if(action==='test-notification'){await api('/notifications/test','POST');toast('测试通知已排队，发送结果可在总览查看');return;}
    if(action==='credentials'){modal('导入登录凭证',`<form id="credential-form" data-provider="${button.dataset.provider}"><label>选择 ${button.dataset.provider==='bilibili'?'cookies.json':'cookies.txt'}<input type="file" name="file" accept=".json,.txt" required></label><p class="help">文件直接保存至 NAS，不会在页面中回显。</p><button class="primary" type="submit">导入凭证</button></form>`);return;}
    if(action==='preview'){const response=await fetch(`/api/tasks/${id}/files/bilingual.srt`);if(!response.ok)throw new Error('字幕读取失败');$('#subtitle-preview').textContent=await response.text();$('#subtitle-preview').hidden=false;return;}
    if(action==='delete-files'){if(!confirm('永久删除该任务的本地视频、字幕和处理文件？B 站稿件与任务记录会保留。'))return;await api(`/tasks/${id}/files`,'DELETE');toast('本地文件已删除');await showDetail(id);await refresh();}
  }catch(error){toast(error.message);}
});
document.addEventListener('submit',async e=>{
  const form=e.target;e.preventDefault();const button=form.querySelector('[type=submit]');if(button)button.disabled=true;
  try{
    const data=new FormData(form);
    if(form.id==='login-form'){await api('/login','POST',{password:data.get('password')});form.reset();$('#login-error').textContent='';$('#login').hidden=true;$('#shell').hidden=false;await refresh(false);await navigate();}
    if(form.id==='task-form'){const result=await api('/tasks','POST',{url:data.get('url')});await refresh();await showDetail(result.id);toast('任务已进入队列');}
    if(form.id==='channel-form'){const id=form.dataset.id;const old=channelData.find(c=>c.id===id);await api('/channels'+(id?'/'+id:''),id?'PUT':'POST',{name:data.get('name'),url:data.get('url'),enabled:old?!!old.enabled:true,options:{tid:Number(data.get('tid')),tags:data.get('tags')}});$('#modal').close();if(page==='channels')await drawChannels();toast('频道设置已保存');}
    if(form.id==='settings-form'){const result=structuredClone(settingsData);for(const input of form.querySelectorAll('[name]')){const keys=input.name.split('.');let target=result;for(const key of keys.slice(0,-1))target=target[key];target[keys.at(-1)]=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value;}await api('/settings','PUT',result);await drawSettings();toast('设置已保存');}
    if(form.id==='credential-form'){const file=data.get('file');if(file.size>2000000)throw new Error('登录文件不能超过 2 MB');await api('/credentials/'+form.dataset.provider,'PUT',{content:await file.text()});$('#modal').close();if(page==='settings')await drawSettings();toast('登录凭证已保存');}
    if(form.id==='link-form'){await api(`/tasks/${form.dataset.id}/action`,'POST',{action:'link',bvid:data.get('bvid')});await showDetail(form.dataset.id);await refresh();toast('已关联稿件，将继续提交字幕');}
  }catch(error){if(form.id==='login-form')$('#login-error').textContent=error.message;else toast(error.message);}
  finally{if(button)button.disabled=false;}
});
document.addEventListener('input',e=>{if(e.target.id==='task-search'){query=e.target.value;drawFiltered();}});
document.addEventListener('change',e=>{if(e.target.id==='task-filter'){filter=e.target.value;drawFiltered();}});
window.addEventListener('hashchange',()=>navigate().catch(e=>toast(e.message)));
async function init(){try{await refresh(false);$('#shell').hidden=false;await navigate();}catch{$('#login').hidden=false;}}
setInterval(()=>{if(!$('#shell').hidden)refresh().catch(()=>{$('#connection').textContent='连接中断 · 正在重连';});},8000);
init();
