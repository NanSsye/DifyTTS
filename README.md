# XYBotV2 Dify 插件 🤖

## 简介

本插件为 [XYBotV2](https://github.com/HenryXiaoYang/XYBotV2) 提供 Dify 集成，允许机器人通过 Dify 与用户进行对话、处理语音和图片消息，并支持聊天室管理等功能。让你的 XYBot 瞬间变身智能助理！✨

<img src="https://github.com/user-attachments/assets/a2627960-69d8-400d-903c-309dbeadf125" width="400" height="600">
## 特性

* **多模型支持:**  支持配置多个 Dify 模型，并可根据用户指令动态切换。想用哪个用哪个！ 🤩
* **聊天室管理:**  支持用户加入、退出聊天室，并提供用户状态管理和统计功能。再也不怕聊天室乱糟糟啦！ 🧹
* **消息缓冲:**  支持消息缓冲机制，避免频繁调用 Dify API。省钱小能手！ 💰
* **语音转文字:**  支持将语音消息转换为文字，并发送给 Dify 处理。懒得打字？说出来！ 🗣️
* **文本转语音:**  支持将 Dify 返回的文本转换为语音消息，并发送给用户。让机器人开口说话！ 📢
* **图片上传识别:**  支持上传图片到 Dify 进行识别，需要大模型支持（例如支持多模态的 Dify 版本）。让机器人也能看图说话！ 🖼️
* **积分系统集成:**  支持与 XYBotV2 的积分系统集成，控制 Dify API 的使用。谁也不能白嫖！ 😈
* **Dify 自带语言转换:**  支持 Dify 本身的语言转换功能。跨语种交流无障碍！ 🌍
* **自动离开与超时机制:**  当用户长时间不活动时，自动设置为离开状态或退出聊天室。防止资源浪费！ ⏳

## 安装

1. 将本插件的代码放入 XYBotV2 的 `plugins` 目录下，例如 `plugins/Dify`。
2. 在 `plugins/Dify` 目录下创建 `config.toml` 文件，并根据下面的配置说明进行配置。
3. 安装依赖： `pip install -r requirements.txt` ,requirements.txt文件内容如下：
    ```
    aiohttp
    filetype
    loguru
    speech_recognition
    gTTS
    Pillow
    ```
4. 重启 XYBotV2。

## 配置

在 `plugins/Dify/config.toml` 文件中进行如下配置：

```toml
[Dify]
enable = true  # 是否启用本插件
default-model = "学姐" # 默认使用的模型
commands = ["老夏", "学姐", "聊天", "AI"] # 触发 Dify 的指令，例如 "老夏"，"@学姐" 等
command-tip = """-----XYBot-----
💬AI聊天指令：

切换模型（将会一直保持到下次切换）：
@学姐 切换：切换到学姐模型
@老夏 切换：切换到老夏模型
临时使用其他模型：
@学姐 消息内容：临时使用学姐模型
@老夏 消息内容：临时使用老夏模型""" # 指令提示，当用户输入错误指令时，会提示用户
admin_ignore = true  # 管理员是否忽略积分限制
whitelist_ignore = true # 白名单用户是否忽略积分限制
http-proxy = ""  # HTTP 代理，如果需要的话
voice_reply_all = false  # 是否对所有回复都使用语音
robot-names = ["毛球", "DifyBot", "智能助手"] # 机器人的名字，用于判断是否是 @ 消息
audio-to-text-url = "http://your-dify-server/v1/audio-to-text" # (可选)外部语音转文字API地址。留空则使用谷歌的speech_recognition。
text-to-audio-url = "http://your-dify-server/v1/text-to-audio" # (可选)外部文本转语音API地址，如果留空则使用Dify本身的文本转语音功能。
remember_user_model = true # 是否记住用户选择的模型

[Dify.models]

# 学姐模型配置
[Dify.models."学姐"]
api-key = "your-xuejie-api-key"  # Dify API 密钥
base-url = "http://your-dify-server/v1"  # Dify API 地址
trigger-words = ["@学姐", "学姐"]  # 触发该模型的关键词
price = 0  # 使用该模型所需的积分

# 老夏模型配置
[Dify.models."老夏"]
api-key = "your-laoxia-api-key"
base-url = "http://your-dify-server/v1"
trigger-words = ["@老夏", "老夏"]
price = 0

# 其他模型配置示例
[Dify.models.gpt4]
api-key = "your-gpt4-api-key"
base-url = "your-gpt4-api-url"
trigger-words = ["@gpt4", "gpt4", "GPT4"]
price = 5

[Dify.models.claude]
api-key = "your-claude-api-key"
base-url = "your-claude-api-url"
trigger-words = ["@claude", "claude", "Claude"]
price = 3

[Dify.models.gemini]
api-key = "your-gemini-api-key"
base-url = "your-gemini-api-url"
trigger-words = ["@gemini", "gemini", "Gemini"]
price = 2

[Dify.models.chatglm]
api-key = "your-chatglm-api-key"
base-url = "your-chatglm-api-url"
trigger-words = ["@chatglm", "chatglm", "ChatGLM"]
price = 1
```

* **enable**: 是否启用 Dify 插件。
* **default-model**: 默认使用的模型名称，必须是 **[Dify.models]** **下定义的模型之一。**
* **commands**: 触发 Dify 的指令列表，例如 **["老夏", "学姐", "聊天", "AI"]**。
* **command-tip**: 命令提示文本，当用户输入错误的指令时，会显示此提示。
* **admin\_ignore**: 是否忽略管理员的积分限制，**true** **表示管理员可以免费使用 Dify。**
* **whitelist\_ignore**: 是否忽略白名单用户的积分限制，**true** **表示白名单用户可以免费使用 Dify。**
* **http-proxy**: HTTP 代理设置，如果你的服务器需要通过代理才能访问 Dify API，请在此设置代理地址。
* **voice\_reply\_all**: 是否对所有回复都使用语音回复，**true** **表示所有回复都将转换为语音消息。**
* **robot-names**: 机器人的名称列表，用于判断是否是 @ 消息，例如 **["毛球", "DifyBot", "智能助手"]**。
* **audio-to-text-url**: (可选) 外部语音转文字 API 地址，如果留空则使用 Google 的 **speech\_recognition**。
* **text-to-audio-url**: (可选) 外部文本转语音 API 地址，如果留空则使用 Dify 本身的文本转语音功能。
* **remember\_user\_model**: (可选) 是否记住用户上次选择的模型，默认为 **true**。
* **[Dify.models.\*]**: 每个模型的配置，包括 **api-key** **(Dify API 密钥)、**base-url **(Dify API 地址)、**trigger-words **(触发该模型的关键词)、**price **(使用该模型所需的积分)。**


## 使用方法

* **直接对话 (私聊):** **在私聊中，直接发送消息即可与 Dify 进行对话。简单直接！ 💬**
* **群聊 @ 机器人:** **在群聊中，@ 机器人 并加上你的问题即可与 Dify 进行对话。如果配置了** **commands**，也可以使用指令触发，如 **老夏 你好**。一起来聊天吧！ 🗣️
* **切换模型:** **发送** **模型名称 切换** **可以切换到指定的模型，例如** **@学姐 切换**。后续的对话将一直使用该模型，直到下次切换。换个口味试试！ 🔄
* **临时使用模型:** **发送** **模型名称 消息内容** **可以临时使用指定的模型，例如** **@学姐 今天天气怎么样**。灵活使用！ 💫
* **语音消息:** **发送语音消息，插件会自动将语音转换为文字，并发送给 Dify 处理。解放双手！ 🙌**
* **图片消息:** **发送图片消息，插件会将图片上传到 Dify 进行识别（如果 Dify 支持多模态）。让你的机器人更加智能！ 🤓**
* **聊天室管理命令:**

  * **退出聊天**: 退出聊天室。拜拜！ 👋
  * **查看状态**: 查看当前聊天室状态。看看谁在摸鱼！ 👀
  * **暂时离开**: 设置为离开状态，其他人将看到你正在休息。休息一下！ 😴
  * **回来了**: 恢复活跃状态。我又回来啦！ 😄
  * **我的统计**: 查看你在聊天室的统计数据。看看你有多活跃！ 📊
  * **聊天室排行**: 查看聊天室成员的活跃度排行。争当聊天室之王！ 👑

## 注意事项

* **请确保你的服务器可以访问 Dify API。防火墙什么的，注意一下哦！ 🛡️**
* **如果使用语音转文字功能，请确保服务器安装了** **ffmpeg**，并配置到环境变量中。安装好了才能用！ 🛠️
* **如果使用 Google 的** **speech\_recognition** **进行语音转文字，请确保服务器可以访问 Google API。谷歌大法好！ 👍**
* **配置多个模型时，请确保每个模型的** **api-key** **和** **base-url** **正确。填错了可就不好使了！ ❌**
* **如果使用图片上传识别功能，请确保 Dify 版本支持多模态。不是所有 Dify 都支持哦！ ⚠️**
* **remember\_user\_model = true** **意味着插件会记住用户上次使用的模型，方便用户下次使用。省心！ 🥰**

## 更新日志

* **1.2.1 (当前版本)**

  * **增加 Dify 自带语言转换识别功能。**
  * **增加语音转文字/文本转语音 API 支持。**
  * **增加图片上传识别功能，需要大模型支持。**
  * **修复了一些 bug。 🐛 修复完毕！ ✅**
* **1.0.0**

  * **初始版本，实现了基本 Dify 集成和聊天室管理功能。🎉**

## 感谢

## 感谢

**感谢** [XYBotV2](https://github.com/HenryXiaoYang/XYBotV2) **提供的平台。也感谢你的使用！ 🙏**
```

