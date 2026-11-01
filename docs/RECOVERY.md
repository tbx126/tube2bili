# NAS 故障恢复（2026-11-01）

## 网络

- 订阅地址保存在 NAS `/vol2/docker/mihomo-system/config/subscription.url`，权限 0600，不加入 Git。
- `tube2bili-proxy-refresh.timer` 每天更新节点。只读取订阅中的节点，保留本机 DNS、TUN、内网规则和控制器凭据；排除流量/到期提示伪节点。
- 更新前运行 Mihomo 配置检查，并使用独立监听器实际访问 YouTube；失败保留原配置。节点组使用 fallback，每 180 秒检查，自动切换存活节点。
- Cloudflare 使用 HTTP/2，配置备份为 `/vol2/docker/cloudflared/compose.yaml.before-http2-20261101`。没有改动 Tunnel Token。
- `tube2bili-network-watch.timer` 每分钟检查代理、Tunnel `/ready` 和应用；代理健康但 Tunnel 连续三次未就绪时重启 Tunnel，重启间隔至少 15 分钟。监控不保证上游节点、订阅余额或 ISP 永远可用。
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
