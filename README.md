# 适用于 XYBotV2 🤖🗣️ 的 DifyTTS 插件

## 简介

DifyTTS 插件是一款强大的 XYBotV2 扩展，它将 Dify AI 的智能对话能力与文本转语音 (TTS) 功能完美结合，让你的聊天机器人不仅能说会道，还能拥有个性化的声音！ 想象一下，你的机器人用 邓紫棋 🎤 的声音和你聊天，是不是超酷 der？ 😎

## 主要特性 🌟

*   **Dify AI 集成 🧠**： 接入 Dify AI 平台，赋予机器人强大的对话能力，让它成为真正的聊天大师。
*   **TTS 语音合成 🗣️**： 将 Dify AI 的回复实时转换为语音，支持多种语音格式，让你的机器人“声”临其境。
*   **多种消息类型支持 💬**： 无论是文本、@消息，还是语音、图片、视频、文件，DifyTTS 都能轻松处理。
*   **灵活的配置 ⚙️**： 通过简单的 `config.toml` 文件，即可配置 Dify AI 和 TTS 服务的 API 密钥、地址等参数。
*   **经济实惠的积分系统 💰**： 内置积分系统，可以控制用户使用插件的频率，防止滥用，让你的机器人可持续发展。

## 安装指南 🛠️

1.  **下载插件 📥**： 将 `main.py` 文件保存到 `plugins/DifyTTS/` 目录下。
2.  **配置参数 ⚙️**： 在 `plugins/DifyTTS/` 目录下创建 `config.toml` 文件，并按照下面的示例配置参数。
3.  **重启 XYBotV2 🔄**： 重启 XYBotV2，让插件加载生效。

## config.toml 配置示例 📝

```toml
[DifyTTS]
enable = true  # 启用插件
api-key = "YOUR_DIFY_API_KEY"  # Dify AI 平台的 API 密钥
base-url = ""  # Dify AI 平台的 API 地址

commands = ["tts"]  # 触发对话的命令列表
other-plugin-cmd = []  # 其他插件的命令列表（用于避免冲突）
command-tip = "请输入要对话的内容"  # 命令提示信息

price = 0  # 每次对话消耗的积分
admin_ignore = true  # 管理员是否忽略积分限制
whitelist_ignore = true  # 白名单用户是否忽略积分限制

http-proxy = ""  # HTTP 代理地址（可选，留空表示不使用代理）

tts_api_key = ""  # TTS 服务的 API 密钥
tts_model_id = ""  # TTS 服务的模型 ID
tts_format = "mp3"  # TTS 服务的音频格式（例如：mp3, wav）
tts_api_url = "https://api.fish.audio/v1/tts"  # TTS 服务的 API 地址
 ```
## 详细参数说明 🧐

*   `DifyTTS.enable`： 启用/禁用插件。
*   `DifyTTS.api-key`： 你的 Dify AI 平台的 API 密钥。🔑
*   `DifyTTS.base-url`： 你的 Dify AI 平台的 API 地址。 🌐
*   `DifyTTS.commands`： 用于触发 Dify 对话的命令列表。 可以自定义，例如 `["tts", "对话"]`。 🗣️
*   `DifyTTS.other-plugin-cmd`： 用于避免与其他插件命令冲突的命令列表。 🚫
*   `DifyTTS.command-tip`： 当用户输入错误命令时，机器人给出的提示信息。 ℹ️
*   `DifyTTS.price`： 每次使用 DifyTTS 插件消耗的积分。 💰
*   `DifyTTS.admin_ignore`： 管理员是否忽略积分限制。 👑
*   `DifyTTS.whitelist_ignore`： 白名单用户是否忽略积分限制。 ✅
*   `DifyTTS.http-proxy`： HTTP 代理地址，如果需要使用代理才能访问 Dify AI 平台，请设置此项。 ☁️
*   `DifyTTS.tts_api_key`： 你的 TTS 服务的 API 密钥。 🔑
*   `DifyTTS.tts_model_id`： 你的 TTS 服务的模型 ID，用于指定使用的语音模型。 🗣️
*   `DifyTTS.tts_format`： TTS 服务的音频格式，常用的有 "mp3" 和 "wav"。 🎵
*   `DifyTTS.tts_api_url`： 你的 TTS 服务的 API 地址。 🌐

## 使用方法 🚀

在微信中，使用配置的命令（例如 `tts 你好`）与机器人对话，DifyTTS 插件会将你的文本发送给 Dify AI，然后将 Dify AI 返回的文本转换为语音，最后将语音消息发送给你。

## 依赖 🧩

*   XYBotV2
*   aiohttp
*   filetype
*   loguru
*   httpx
*   ormsgpack
*   pydantic

## 常见问题 🤔

*   **Q: 为什么机器人没有回复？**
    *   A: 请检查 XYBotV2 的日志文件（`logs/xybot.log`），查看是否有错误信息。 确保 Dify AI 和 TTS 服务的 API 密钥和地址配置正确，并且服务正常运行。
*   **Q: 为什么语音消息发送失败？**
    *   A: 请检查 TTS 服务的 API 密钥和地址配置是否正确，以及 TTS 服务是否正常运行。 还要确保您选择的 TTS 模型支持中文，并与 DifyTTS 插件配置的编码格式一致。


## 许可证 📜

MIT

