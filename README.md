# Tube2Bili

个人 NAS 上运行的 YouTube → Bilibili 双语视频工作台。Python / FastAPI + SQLite，原生 HTML/CSS/JavaScript Dashboard，一个容器、一个任务工作线程，无 Redis 或外部数据库。

当前版本：**0.1.0**。变更见 [CHANGELOG.md](CHANGELOG.md)，验证范围见 [VALIDATION.md](VALIDATION.md)。

## 版本管理

- `main` 保存集成代码，后续改动使用 `feat/...`、`fix/...` 分支和 Pull Request。
- 版本号记录在 `VERSION`，发布时同步更新变更记录，并建立注释标签 `vX.Y.Z`；不覆盖已有标签，不强制推送改写主分支历史。
- GitHub Actions 对提交和 PR 执行测试，并构建、启动 Docker 镜像检查健康状态。
- `.env`、登录 Cookie、数据库、媒体、日志和虚拟环境不纳入 Git。`.env.example` 仅保存配置示例。

## 已实现

- 频道订阅：第一次检查建立视频 ID 基线，之后定时抓取新视频；暂停/恢复、频道独立分区和标签。使用频道 `/videos` 页，排除 Shorts 和直播条目。
- 手动输入单条 YouTube 链接，与订阅共用流程，按 YouTube ID 全局去重。
- yt-dlp 下载，优先最高 1080p，保留原声，保存封面与来源。优先下载英文字幕；无英文字幕时使用配置的语音识别 API，每 10 分钟分段，自动识别语言，必要时先生成英文字幕再翻译成中文。
- OpenAI 兼容 API 路由：翻译 `/chat/completions`、语音识别 `/audio/transcriptions`；每种用途配置主备地址、模型、密钥和费用单价，备用须显式启用。
- 中文标题、双语简介及来源署名；输出 `zh.srt`、`en.srt`、`bilingual.srt`。视频不烧录字幕。
- biliup 自动转载投稿，原封面、转载来源、频道投稿配置。播放器中文轨为中英对照，英文轨为英文字幕。
- 任务阶段、翻译批次、投稿 BV 号和字幕提交结果持久化。异常重试、暂停/继续/取消、投稿结果核对和本地文件手动删除。
- Telegram 完成/失败/配置等待通知，失败发送重试；Dashboard 展示任务、事件、近七天任务数、耗时、费用估算、分区磁盘容量。
- 内网密码登录、HttpOnly / SameSite Cookie、修改接口来源校验、登录频率限制；API 密钥与 Cookie 不在读取接口中回显。

## NAS 部署

适用本次目标：Intel N150，16 GB，支持 Docker。无需独立 GPU；不烧录、不配音，语音识别由远程或局域网 API 处理。

1. 将项目复制到 NAS，例如 `/volume1/docker/tube2bili`。
2. 复制 `.env.example` 为 `.env`，修改 `DASHBOARD_PASSWORD`（至少 12 位）。如有 Python，也可运行 `python scripts/bootstrap.py` 随机生成。
3. 设置 `MEDIA_ROOT` 为长期存储目录，例如 `/volume1/docker/tube2bili/data`。该目录保存数据库、所有视频、字幕和凭证，建议整个目录备份。
4. 在项目目录运行：

   ```sh
   docker compose up -d --build
   docker compose logs -f app
   ```

5. 浏览器访问 `http://NAS内网IP:8080`，用 `.env` 中的密码登录。
6. 在「服务设置」填写 YouTube / Telegram 代理、API 路由、分区标签及 Telegram Bot Token / Chat ID。API 基础地址填写包含 `/v1` 的地址；局域网无认证服务可以不填 Key。
7. 登录 B 站：

   ```sh
   docker compose exec app biliup -u /data/cookies.json login
   ```

   按终端提示完成扫码登录。也可以在 Dashboard 导入已有的 biliup `cookies.json`。YouTube 如需登录，导入 Netscape 格式 `cookies.txt`。本项目不读取电脑现有浏览器登录数据。
8. 点击 Telegram 测试通知，然后添加频道或手动视频链接。首次真实验证建议使用你指定的一条可转载视频；创建任务即授权系统自动投稿。

Docker 镜像安装 Node 22、ffmpeg、yt-dlp 与 biliup。构建需要访问 Debian 包源和 PyPI；NAS 的浏览器代理不一定等同于 Docker 构建代理，请在 NAS Docker 配置中设置可用的镜像/网络。

代理地址必须能从容器访问，例如 `http://192.168.1.10:7890`；不要使用容器内 `127.0.0.1` 指向 NAS 代理。B 站连接保持直连，API 地址各自独立。Telegram 使用同一个可选代理。

仅在可信内网开放 8080。`BIND_ADDRESS` 可设置 NAS 的指定内网 IP。使用 HTTPS 反向代理时将 `COOKIE_SECURE=1`。修改密码并重启会使原登录会话失效。数据目录内含明文 API 密钥和登录 Cookie，应限制 NAS 目录访问权限并保护备份。

## 状态和恢复

```text
queued → download → translate → publish → subtitles → verify → completed
                    ↘ waiting / retrying / failed / paused / cancelled
                              publish → reconcile（提交结果不明确）
```

“完成”要求播放器可见中英对照与英文两条字幕，仅生成本地 SRT 或返回投稿成功都不算完成。平台转码/字幕审核未完成时，每 10 分钟检查一次，最多 24 小时，之后通知并等待手动重试。

普通临时错误最多尝试 3 次。API 未配置、余额预算已用尽、登录缺失或字幕校验失败时进入等待状态；修复后在任务详情点击「继续 / 重试」。预算是按配置单价和 API 返回 token 数估算，0 单价无法约束费用，单次调用可能超过剩余预算，不是支付平台的硬限额。

投稿开始前先持久化提交标记。如果进程中断或结果无法解析，任务进入「需核对投稿」，不会自动再次提交。到 B 站创作中心核对：已投稿则填写 BV 号继续字幕；确认确实未投稿后才可清除检查点重试。关联 BV 号会检查登录账号归属及简介中的来源链接。

暂停/取消会停止下载子进程；正在进行的 API 请求可能需要等待返回。正在提交投稿的任务不接受中断操作。服务正常关闭时运行中的任务暂停，重启后可在界面继续；意外退出时恢复队列，但保留投稿保护标记。

视频、封面、音频分段和字幕永久保留，仅手动删除。删除本地文件不删除 B 站稿件，任务去重记录保留；清理后的任务不能直接继续。

首次频道扫描期间会显示“基线尚未建立”，扫描成功后开始追踪后续出现的视频。暂停订阅不会取消已经排队的任务，恢复后会处理暂停期间的新条目。手动导入可处理历史视频。

## 本地开发和测试

Python 3.10+，Node 22+，ffmpeg：

```sh
python -m venv .venv
# Linux/NAS
. .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python scripts/bootstrap.py
uvicorn app.main:app --host 127.0.0.1 --port 8080
python -m pytest -q
```

仅使用一个 Uvicorn worker；不要水平复制当前版本，否则会重复启动频道轮询器。`DISABLE_WORKER=1` 可关闭后台任务，用于 UI 演示或测试。`DATA_DIR` 可改变本地数据目录。

## 验证范围与外部限制

自动测试覆盖数据库去重、订阅基线、认证/来源校验、密钥隐藏、字幕时间轴、处理中断后的投稿保护、字幕重试不重复投稿、文件删除和预算。已对照安装的 biliup 1.2.4 CLI 与 bilibili-api-python 17.4.2 的 `Video.submit_subtitle` / `get_subtitle` 方法验证接口签名。

**真实下载、付费 API、B 站投稿和播放器字幕仍需你的配置与账号联调。** 仓库不会声称已发布未经测试的视频；B 站接入使用社区实现，账号权限、接口变化和平台审核可能导致任务暂停。镜像构建和容器健康检查已通过 GitHub Actions；目标 NAS 的卷权限、代理和持续运行仍需部署时验证。

首版不包含配音、烧录字幕、封面重做、播放量/点赞统计、多账号、多用户、NAS 硬件监控或公网访问部署。

## 参考

千问接口、配置示例、候选版验证范围见 [千问接入说明](docs/QWEN.md)。

- [yt-dlp 项目文档](https://github.com/yt-dlp/yt-dlp)
- [biliup 项目和 CLI](https://github.com/biliup/biliup)
- [bilibili-api-python 社区库](https://github.com/Nemo2011/bilibili-api)
- [B 站官方开放平台](https://open.bilibili.com/doc)
