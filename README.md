# XYBotV2 Dify 增强插件

[![Awesome Badge](https://img.shields.io/badge/Enhanced-XYBotV2--Dify--Plugin-blue)](https://github.com/your-repo-link)

本仓库提供了一个增强版的 [XYBotV2](https://github.com/your-xybotv2-repo) Dify 插件，在原版基础上增加了更多消息类型支持和更灵活的配置选项。🎉

## 简介

此插件扩展了原有的 XYBotV2 Dify 插件，使其能够处理更多类型的微信消息，并提供更灵活的配置选项，例如控制是否对所有对话进行语音回复。 旨在为 XYBotV2 用户提供更强大的 AI 对话体验。

## 原版插件功能

*   集成 [Dify](https://dify.ai/) 的强大 AI 功能到 XYBotV2 微信机器人中。
*   支持文本消息对话。
*   基于命令触发 AI 对话。

## 增强功能

*   **多消息类型支持：** 除了文本消息，现在还支持处理语音消息、图片消息、视频消息和文件消息。 🖼️🎤🎬
*   **可配置语音回复：** 新增选项，可选择对所有对话进行语音回复，或仅在发送语音消息时回复。 🗣️
*   **更灵活的配置：** 通过 `config.toml` 文件轻松配置 API 密钥、基本 URL、命令等。 ⚙️

## 依赖插件

*   [XYBotV2](https://github.com/your-xybotv2-repo)：微信机器人框架
*   [speech\_recognition](https://pypi.org/project/SpeechRecognition/)：语音识别库
*   [gTTS](https://pypi.org/project/gTTS/)：文本转语音库
*   [filetype](https://github.com/h2non/filetype.py)：文件类型识别库
*   [aiohttp](https://docs.aiohttp.org/en/stable/)：异步 HTTP 客户端

## 安装步骤

1.  **安装 XYBotV2 和原版 Dify 插件：** 确保你已经成功安装并配置了 [XYBotV2](https://github.com/your-xybotv2-repo) 和原版的 Dify 插件。
2.  **替换插件文件：** 将本仓库中的 `Dify` 文件夹复制到 XYBotV2 的 `plugins` 目录下，**覆盖**原有的 Dify 插件文件。
3.  **安装 Python 依赖：** 进入 XYBotV2 根目录，安装插件所需的 Python 依赖。

    ```bash
    pip install SpeechRecognition gTTS filetype aiohttp
    ```

    如果安装 `SpeechRecognition` 失败，请参考 [官方文档](https://pypi.org/project/SpeechRecognition/) 进行安装。
4.  **安装 ffmpeg (语音转文字功能需要)：** 确保你的系统已经安装了 `ffmpeg`。

    *   **Debian/Ubuntu：**

        ```bash
        sudo apt update
        sudo apt install ffmpeg
        ```

    *   **macOS (使用 Homebrew)：**

        ```bash
        brew install ffmpeg
        ```
5.  **配置插件：** 修改 `plugins/Dify/config.toml` 文件，填入你的 Dify API 密钥、基本 URL 等信息。

## 配置

### config.toml

```toml
[Dify]
enable = true  # 是否启用插件
api-key = "你的 Dify API 密钥"  # 替换为你的 Dify API 密钥 🔑
base-url = "你的 Dify API 地址"  # 替换为你的 Dify API 地址 🌐
commands = ["/dify", "/chat"]  # 触发 Dify 对话的命令
command-tip = "请输入你想说的话"  # 命令提示语
price = 1  # 使用 Dify 功能所需积分（XYBotV2主程序功能）💰
admin_ignore = true  # 管理员是否忽略积分检查
whitelist_ignore = true # 白名单是否忽略积分检查
http-proxy = ""  # HTTP 代理（可选）
voice_reply_all = false  # 是否对所有对话进行语音回复 📢
