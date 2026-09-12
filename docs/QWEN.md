# 千问接入与验证

当前候选版本增加 `Route.protocol`，旧配置默认 `openai`，不会自动改写已有服务。

## 推荐配置（北京地域）

| 字段 | 字幕与文案翻译 | 无字幕视频语音识别 |
| --- | --- | --- |
| 接口类型 | 千问文本翻译 (`qwen`) | 千问音频直传 (`qwen_audio`) |
| API 基础地址 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `https://dashscope.aliyuncs.com/api/v1` |
| 模型 | `qwen-plus` | `qwen-audio-3.0-asr-flash` |
| API Key | 北京地域控制台 Key | 同地域同账号 Key 可复用 |

新加坡可将域名替换为 `dashscope-intl.aliyuncs.com`，必须使用新加坡地域 Key。也支持控制台提供的工作空间专属域名，保留对应路径。不要使用千问聊天网页地址或 Coding Plan 的端点。

费用字段填写控制台人民币单价；0 表示未计价，不能实现有效预算限制。长上下文、思考模式及地域定价可能不同，统计是估算，不代表账单。备用服务只有明确开启时才调用。

文本请求使用 `/chat/completions`，显式 `enable_thinking: false` 和 JSON object 输出。普通 OpenAI 路由不附加千问参数。保存后可以单独测试主服务或备用服务；测试会发起少量付费 token 调用并记入统计，不创建或发布视频。

可选的 Filetrans 录音接口使用 `/services/audio/asr/transcription`，通过 `X-DashScope-Async: enable` 提交 `input.file_url`，轮询 `/tasks/{id}`，读取 `output.result.transcription_url`，将句子毫秒时间转换为字幕秒时间。普通 `qwen3-asr-flash` 不使用此协议，不能填入此路由。

Filetrans 音频每 10 分钟分段。任务编号保存在各分段检查点，暂停或查询超时后继续查询已有任务，结果缓存后不再请求。成功结果链接有效期为 24 小时；过期任务需处理失败后重试。提交请求成功但连接在返回任务编号前断开时，无法恢复未知编号，后续重试仍可能再次计费。

**推荐的音频直传使用 `/services/aigc/multimodal-generation/generation`，每 5 分钟切片并内嵌 Base64 MP3，无需 OSS。请求 `X-DashScope-SSE: enable`，支持短录音 JSON 响应和长录音 SSE 响应，只接收 `sentence_end: true` 的最终句子及毫秒时间轴。** 分段长度写入 manifest，已有 10 分钟检查点不会被错误解释为 5 分钟偏移。

可选 Filetrans 上传采用官方临时存储（48 小时过期），适合联调验证，官方不推荐用于生产环境。 不会将 NAS 暴露为公网文件服务器。模型 API Key 仅发送到配置的 API 地址，不随请求发往返回的存储上传、结果下载地址。

## 账号

Bilibili 可在设置页扫码登录。采用 biliup 对应的 BiliTV QR 协议，手机确认后完整凭据写入 NAS 的 `cookies.json`，页面只返回登录状态。二维码有效期内临时数据保存在内存，重启后失效。可点击「验证登录」核对当前账号。

YouTube 继续使用 Netscape cookies.txt 导入。凭据只保存在忽略版本控制的数据目录，不上传 GitHub。本机 Edge 占用 Cookie 数据库时，yt-dlp 无法正常导出；需要用户关闭浏览器后重试或通过浏览器导出文件。不能把“已导入”视为有效性或下载成功的证据。

## 依据

- [千问接口地域和基础地址](https://help.aliyun.com/en/model-studio/base-url)
- [千问 OpenAI 兼容 Chat](https://help.aliyun.com/en/model-studio/compatibility-of-openai-with-dashscope)
- [Qwen Audio 3.0 Base64、SSE 与时间轴](https://help.aliyun.com/zh/model-studio/fun-asr-flash-recorded-speech-recognition-http-api)
- [Qwen ASR 请求、轮询、结果格式](https://help.aliyun.com/zh/model-studio/qwen-asr-api-reference)
- [临时上传凭证、文件上传及资源解析 Header](https://help.aliyun.com/zh/model-studio/get-temporary-file-url)
- [biliup 登录结构与扫码流程源码](https://github.com/biliup/biliup/blob/master/crates/biliup/src/uploader/credential.rs)

模拟协议测试不能代替真实模型调用、视频下载、投稿成功和播放器字幕确认；这些仍是全流程验收条件。
