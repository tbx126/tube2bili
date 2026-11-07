# NAS 故障恢复（2026-11-01）

## 网络

- 订阅地址保存在 NAS `/vol2/docker/mihomo-system/config/subscription.url`，权限 0600，不加入 Git。
- `tube2bili-proxy-refresh.timer` 每天更新节点。只读取订阅中的节点，保留本机 DNS、TUN、内网规则和控制器凭据；排除流量/到期提示伪节点。
- 更新前运行 Mihomo 配置检查，并使用独立监听器实际访问 YouTube；失败保留原配置。订阅未变化时不重启代理。节点组使用 fallback，每 180 秒检查，自动切换存活节点。
- Cloudflare 使用 HTTP/2，配置备份为 `/vol2/docker/cloudflared/compose.yaml.before-http2-20261101`。没有改动 Tunnel Token。
- `tube2bili-network-watch.timer` 每分钟检查代理、Tunnel `127.0.0.1:42081/ready` 和应用；此端口避开 NAS 已有 Cloudflare 进程。代理健康但 Tunnel 连续三次未就绪时重启 Tunnel，重启间隔至少 15 分钟。监控不保证上游节点、订阅余额或 ISP 永远可用。
- 状态写入数据目录 `network-health.json`，Dashboard 显示网络异常或超过三分钟未更新。查看 `systemctl status tube2bili-network-watch.service`、`journalctl -u tube2bili-proxy-refresh.service` 排查。
- 订阅服务需保持有效；更新脚本不购买套餐、不续费。订阅失效时保留现有节点。

## 翻译与语音识别

- 对欠费、额度不足和认证错误记录固定安全诊断，不记录服务原文、API Key 或带凭证 URL。
- 每个路由按地址、模型、协议与密钥的散列隔离熔断状态。备用路由启用时优先尝试备用；全部不可用时共享暂停一小时，保留检查点且不消耗任务重试次数。
- 账户告警每日最多一次；暂停到期后由任务实际请求复查。接口测试成功可立即解除账户暂停并继续对应任务。
- 网络中断后每五分钟用无 Cookie 的 YouTube 204 地址检测恢复，只继续标记为网络中断的任务，保留用户暂停和独立的 429 冷却。
- HTTP 416 时保留旧临时文件至 `partial-recovery-*`，从头下载一次；再次 416 则暂停，避免重复循环。
- Telegram 网络错误和限流采用共享延迟，网络错误不消耗消息次数；无效 Bot 或权限错误显示 HTTP 状态。

## 套餐使用范围

Token Plan 个人版、团队版均有交互式使用范围限制，官方排除自动化脚本和应用后端；不要因套餐含模型或支持 API 就推断允许后台自动调用。切换前按用户授权与官方条款核实用途。

官方文档：https://help.aliyun.com/zh/model-studio/more-tools

## 回滚

应用可将根 compose.yaml 恢复为发布前备份，再执行 `docker compose up -d --no-build`；保留数据卷。网络更新前配置位于 `config.yaml.before-subscription-20261101`；最新成功替换前配置位于 `config.last-good.yaml`。恢复配置后重启 `mihomo-nas.service`。

暂停自动更新可执行 `systemctl disable --now tube2bili-proxy-refresh.timer`。暂停 Tunnel 自动恢复可执行 `systemctl disable --now tube2bili-network-watch.timer`。两者不删除数据或凭证。

## 2026-11-06：固定日本节点及备用入口

- 当前使用用户提供的日本节点文件，私有副本为 `/vol2/docker/mihomo-system/config/jp-source-20261106.yaml`，权限 0600。`source.json` 指定 `mode=file`，默认运行更新脚本也不会再回退到旧订阅。旧订阅 timer 已停用。
- 配置更新同时验证独立候选监听器和正式监听器。正式监听器检查失败时恢复备份；文件缺失或格式错误时保留原配置，不尝试其他来源。
- 两个独立站点连续三次均无法通过代理时，仅重启一次当前 Mihomo 配置；网络恢复后才允许下一次故障尝试。单一站点故障不会触发全局代理重启。上游节点失效仍需要节点运营方恢复或用户提供备用节点。
- 不依赖 Cloudflare 的维护入口为 NAS ZeroTier 地址 `10.239.235.220`。SSH 使用原 tbx12 身份密钥，HostKeyAlias=192.168.5.6 核对原主机身份；没有开放新的端口或放宽认证。

## 2026-11-07：公网探测与历史字幕修复

- 本地 `/ready` 为 200 时，公网仍可能返回 Cloudflare 1033。监控现在通过 NAS 代理单独请求公网 `/healthz`，记录 `public_status` 和 `public_failures`，使用明确的 `Tube2Bili-NetworkWatch` 标识。
- 只有连续三次收到带 1033 的 HTTP 530，且本地 Tunnel、应用和代理都正常时，才尝试一次 Tunnel 重启。同一次公网故障不重复重启；公网恢复 200 后才重置，仍遵守 15 分钟间隔。403、502、网络超时均不触发这条恢复规则；它不能修正错误的 DNS 或 Cloudflare 控制台配置。
- 历史字幕另发现英文轨道混入中文、中文轨道含模型译者说明。利用保留的原始 ASR 时间片恢复英文，仅重译不合法的中文条目；原文件、旧工作目录和回执保存在任务 `historical-content-repair-backup`，没有重新投稿视频。
- 平台逐条比对结果保存在 `/data/maintenance/subtitle-acceptance-20261107.json`；该目录还保留修复脚本和运行日志。`verified` 表示公开轨道内容、数量和时间均匹配，`pending_review` 仅表示已提交，不能当成审核通过。

## 开头短语字幕

- 实测三个 B 站转码音频与 CC 首句时间相差约 0.02–0.12 秒，未发现全片固定提前；用户确认问题是完整句子先显示、后半句尚未说到。
- `intro_phrase_seconds` 为开头短语处理范围，0 关闭，当前目标为 20 秒。只依据词级时间戳分成较短短语；开头之后的字幕时间和文字保留。没有词级时间戳或与原文差异过大时停止修改。
- 识别结果、翻译结果及修复阶段保留检查点。原字幕和提交回执保存在各任务的 `intro-published-backup-*`；中英文、双语 SRT 和黄中白英 ASS 一起更新。
- `scripts/repair_intro_subtitles.py --task ID` 用于单条验证，`--all` 用于串行处理已投稿且保留文件的任务。脚本只提交 CC，不重新上传视频；用内容摘要避免重复提交，实际公开轨道一致后才记录 verified，平台未显示时记录 pending_review。

批量维护容器需设置 `PYTHONPATH=/app`，并挂载修复脚本。先调用 `app.worker.begin_subtitle_maintenance()` 暂缓队列；遇到正在提交的稿件会拒绝开始。维护完成自动恢复快照，异常退出后最多两小时恢复；原有用户暂停保持。
