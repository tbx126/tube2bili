# 本地验收记录

日期：2026-09-12。

## 已通过

- `python -m pytest -q`：28 项测试通过。覆盖来源 URL 限制、认证和请求来源检查、任务去重、订阅基线、暂停恢复、凭证隐藏、费用门槛、主备路由、翻译缓存、非英文语音识别、字幕时间轴、投稿中断保护、字幕重试不重复投稿、手动清理及子进程终止。
- `node --check app/static/app.js`：通过。
- `python -m pip check`：无依赖冲突。
- Compose YAML 语法解析及 yt-dlp 格式选择器解析：通过。
- 本地浏览器：登录、手动建任务、暂停、添加频道、暂停订阅、保存设置均成功；浏览器未捕获脚本错误。
- 375 × 812 响应式验收：无页面级横向溢出；已恢复桌面视口。
- 已清除本次 UI 验收创建的临时任务和临时频道。
- 本地预览 `http://127.0.0.1:18080` 已启动正常后台工作队列。登录密码位于本地 `.env`。
- GitHub Actions Linux / Python 3.12：28 项测试通过，依赖检查及 JavaScript 语法检查通过。
- GitHub Actions：Docker 镜像构建成功，容器实际启动并通过 `/healthz` 检查。[首次 CI 记录](https://github.com/tbx126/tube2bili/actions/runs/34679500946)。

## 尚未验证

- 目标 NAS 部署、卷权限、代理连接及持续运行：镜像已在 GitHub Actions 验证，但尚未连接目标 NAS。
- 实际 YouTube 下载：用户代理、频道与示例视频尚未配置。
- 实际翻译和语音识别服务：未提供 API 地址/模型/密钥。
- B 站真实投稿、字幕权限与字幕审核：未提供登录文件。已核对 biliup 1.2.4 命令参数和 bilibili-api-python 17.4.2 方法签名，不能据此声称外部平台联调通过。
- Telegram 实际送达：未提供 Bot Token 和 Chat ID。

测试环境 Python 3.10，部署镜像 Python 3.12。测试输出含第三方 Starlette 测试客户端的两条弃用提示；不影响此次测试结果。
