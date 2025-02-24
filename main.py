import asyncio
import json
import re
import tomllib
import traceback

import aiohttp
import filetype
from loguru import logger

from WechatAPI import WechatAPIClient
from database.XYBotDB import XYBotDB
from utils.decorators import *
from utils.plugin_base import PluginBase
import httpx
import ormsgpack
from typing import Union, Literal, Annotated
from pydantic import BaseModel, conint


class ServeReferenceAudio(BaseModel):
    audio: bytes
    text: str


class ServeTTSRequest(BaseModel):
    text: str
    chunk_length: Annotated[int, conint(ge=100, le=300, strict=True)] = 200
    # Audio format
    format: Literal["wav", "pcm", "mp3"] = "mp3"
    mp3_bitrate: Literal[64, 128, 192] = 128
    # References audios for in-context learning
    references: list[ServeReferenceAudio] = []
    # Reference id
    # For example, if you want use https://fish.audio/m/7f92f8afb8ec43bf81429cc1c9199cb1/
    # Just pass 7f92f8afb8ec43bf81429cc1c9199cb1
    reference_id: str | None = None
    # Normalize text for en & zh, this increase stability for numbers
    normalize: bool = True
    # Balance mode will reduce latency to 300ms, but may decrease stability
    latency: Literal["normal", "balanced"] = "normal"


class DifyTTS(PluginBase):
    description = "Dify AI对话并转换为语音回复"
    author = "老夏的金库"
    version = "1.0.0"

    def __init__(self):
        super().__init__()

        with open("main_config.toml", "rb") as f:
            config = tomllib.load(f)

        self.admins = config["XYBot"]["admins"]

        # 加载 Dify 配置
        with open("plugins/DifyTTS/config.toml", "rb") as f:
            config = tomllib.load(f)

        plugin_config = config["DifyTTS"]

        self.enable = plugin_config["enable"]
        self.api_key = plugin_config["api-key"]
        self.base_url = plugin_config["base-url"]

        self.commands = plugin_config["commands"]
        self.other_plugin_cmd = plugin_config["other-plugin-cmd"]
        self.command_tip = plugin_config["command-tip"]

        self.price = plugin_config["price"]
        self.admin_ignore = plugin_config["admin_ignore"]
        self.whitelist_ignore = plugin_config["whitelist_ignore"]

        self.http_proxy = plugin_config["http-proxy"]

        # 加载 TTS 配置
        self.tts_api_key = plugin_config.get("tts_api_key")
        self.tts_model_id = plugin_config.get("tts_model_id")
        self.tts_format = plugin_config.get("tts_format", "mp3")
        self.tts_api_url = plugin_config.get("tts_api_url", "https://api.fish.audio/v1/tts")

        self.db = XYBotDB()
        self.processed_message_ids = set()  # 用于存储已处理的消息ID

    async def _text_to_speech(self, text: str) -> Union[bytes, None]:
        """使用原始 HTTP API 进行 TTS."""
        try:
            request = ServeTTSRequest(
                text=text,
                reference_id=self.tts_model_id,
                format=self.tts_format
            )
            headers = {
                "authorization": f"Bearer {self.tts_api_key}",
                "content-type": "application/msgpack",
            }
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.tts_api_url,
                    content=ormsgpack.packb(request, option=ormsgpack.OPT_SERIALIZE_PYDANTIC),
                    headers=headers,
                    timeout=10,
                )
                if response.status_code == 200:
                    return response.content
                else:
                    error_message = f"TTS API 请求失败: {response.status_code} - {response.content.decode('utf-8', errors='ignore')}"
                    logger.error(error_message)
                    return None
        except httpx.HTTPError as e:
            logger.exception(f"TTS API 请求异常: {e}")
            return None
        except Exception as e:
            logger.exception(f"TTS 转换过程中发生异常: {e}")
            return None

    @on_text_message(priority=20)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

        message_id = message.get("MsgId")
        if message_id in self.processed_message_ids:
            logger.debug(f"消息 {message_id} 已经处理过，跳过")
            return  # 消息已经处理过，跳过

        command = str(message["Content"]).strip().split(" ")

        if (not command or command[0] not in self.commands) and message["IsGroup"]:  # 不是指令，且是群聊
            return
        elif len(command) == 1 and command[0] in self.commands:  # 只是指令，但没请求内容
            await bot.send_at_message(message["FromWxid"], "\n" + self.command_tip, [message["SenderWxid"]])
            return
        elif command and command[0] in self.other_plugin_cmd:  # 指令来自其他插件
            return

        if not self.api_key:
            await bot.send_at_message(message["FromWxid"], "\n你还没配置Dify API密钥！", [message["SenderWxid"]])
            return False

        if not self.tts_api_key:
            await bot.send_at_message(message["FromWxid"], "\n你还没配置TTS API密钥！", [message["SenderWxid"]])
            return False

        if await self._check_point(bot, message):
            await self.dify(bot, message, message["Content"])

        self.processed_message_ids.add(message_id)  # 添加到已处理的消息ID集合中
        return False

    @on_at_message(priority=20)
    async def handle_at(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return
        message_id = message.get("MsgId")
        if message_id in self.processed_message_ids:
            logger.debug(f"消息 {message_id} 已经处理过，跳过")
            return  # 消息已经处理过，跳过

        if not self.api_key:
            await bot.send_at_message(message["FromWxid"], "\n你还没配置Dify API密钥！", [message["SenderWxid"]])
            return False

        if not self.tts_api_key:
            await bot.send_at_message(message["FromWxid"], "\n你还没配置TTS API密钥！", [message["SenderWxid"]])
            return False

        if await self._check_point(bot, message):
            await self.dify(bot, message, message["Content"])

        self.processed_message_ids.add(message_id)  # 添加到已处理的消息ID集合中
        return False

    # 其他消息类型的处理函数（handle_voice, handle_image, handle_video, handle_file）保持不变
    @on_voice_message(priority=20)
    async def handle_voice(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return
        message_id = message.get("MsgId")
        if message_id in self.processed_message_ids:
            logger.debug(f"消息 {message_id} 已经处理过，跳过")
            return  # 消息已经处理过，跳过

        if message["IsGroup"]:
            return

        if not self.api_key:
            await bot.send_at_message(message["FromWxid"], "\n你还没配置Dify API密钥！", [message["SenderWxid"]])
            return False

        if await self._check_point(bot, message):
            upload_file_id = await self.upload_file(message["FromWxid"], message["Content"])

            files = [
                {
                    "type": "audio",
                    "transfer_method": "local_file",
                    "upload_file_id": upload_file_id
                }
            ]

            await self.dify(bot, message, " \n", files)

        self.processed_message_ids.add(message_id)  # 添加到已处理的消息ID集合中
        return False

    @on_image_message(priority=20)
    async def handle_image(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

        message_id = message.get("MsgId")
        if message_id in self.processed_message_ids:
            logger.debug(f"消息 {message_id} 已经处理过，跳过")
            return  # 消息已经处理过，跳过

        if message["IsGroup"]:
            return

        if not self.api_key:
            await bot.send_at_message(message["FromWxid"], "\n你还没配置Dify API密钥！", [message["SenderWxid"]])
            return False

        if await self._check_point(bot, message):
            upload_file_id = await self.upload_file(message["FromWxid"], bot.base64_to_byte(message["Content"]))

            files = [
                {
                    "type": "image",
                    "transfer_method": "local_file",
                    "upload_file_id": upload_file_id
                }
            ]

            await self.dify(bot, message, " \n", files)

        self.processed_message_ids.add(message_id)  # 添加到已处理的消息ID集合中
        return False

    @on_video_message(priority=20)
    async def handle_video(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return
        message_id = message.get("MsgId")
        if message_id in self.processed_message_ids:
            logger.debug(f"消息 {message_id} 已经处理过，跳过")
            return  # 消息已经处理过，跳过

        if message["IsGroup"]:
            return

        if not self.api_key:
            await bot.send_at_message(message["FromWxid"], "\n你还没配置Dify API密钥！", [message["SenderWxid"]])
            return False

        if await self._check_point(bot, message):
            upload_file_id = await self.upload_file(message["FromWxid"], bot.base64_to_byte(message["Video"]))

            files = [
                {
                    "type": "video",
                    "transfer_method": "local_file",
                    "upload_file_id": upload_file_id
                }
            ]

            await self.dify(bot, message, " \n", files)

        self.processed_message_ids.add(message_id)  # 添加到已处理的消息ID集合中
        return False

    @on_file_message(priority=20)
    async def handle_file(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

        message_id = message.get("MsgId")
        if message_id in self.processed_message_ids:
            logger.debug(f"消息 {message_id} 已经处理过，跳过")
            return  # 消息已经处理过，跳过


        if message["IsGroup"]:
            return

        if not self.api_key:
            await bot.send_at_message(message["FromWxid"], "\n你还没配置Dify API密钥！", [message["SenderWxid"]])
            return False

        if await self._check_point(bot, message):
            upload_file_id = await self.upload_file(message["FromWxid"], message["Content"])

            files = [
                {
                    "type": "document",
                    "transfer_method": "local_file",
                    "upload_file_id": upload_file_id
                }
            ]

            await self.dify(bot, message, " \n", files)
        self.processed_message_ids.add(message_id)  # 添加到已处理的消息ID集合中
        return False


    async def dify(self, bot: WechatAPIClient, message: dict, query: str, files=None):
        if files is None:
            files = []
        conversation_id = self.db.get_llm_thread_id(message["FromWxid"],
                                                    namespace="dify")
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        payload = json.dumps({
            "inputs": {},
            "query": query,
            "response_mode": "streaming",
            "conversation_id": conversation_id,
            "user": message["FromWxid"],
            "files": files,
            "auto_generate_name": False,
        })
        url = f"{self.base_url}/chat-messages"

        ai_resp = ""
        async with aiohttp.ClientSession(proxy=self.http_proxy) as session:
            async with session.post(url=url, headers=headers, data=payload) as resp:
                if resp.status == 200:
                    # 读取响应
                    async for line in resp.content:  # 流式传输
                        line = line.decode("utf-8").strip()
                        if not line or line == "event: ping":  # 空行或ping
                            continue
                        elif line.startswith("data: "):  # 脑瘫吧，为什么前面要加 "data: " ？？？
                            line = line[6:]

                        try:
                            resp_json = json.loads(line)
                        except json.decoder.JSONDecodeError:
                            logger.error(f"Dify返回的JSON解析错误，请检查格式: {line}")
                            logger.debug(f"原始数据: {await resp.text()}")
                            continue

                        event = resp_json.get("event", "")
                        if event == "message":  # LLM 返回文本块事件
                            ai_resp += resp_json.get("answer", "")
                        elif event == "message_replace":  # 消息内容替换事件
                            ai_resp = resp_json("answer", "")
                        elif event == "message_file":  # 文件事件 目前dify只输出图片
                            await self.dify_handle_image(bot, message, resp_json.get("url", ""))
                        elif event == "tts_message":  # TTS 音频流结束事件
                            await self.dify_handle_audio(bot, message, resp_json.get("audio", ""))
                        elif event == "error":  # 流式输出过程中出现的异常
                            await self.dify_handle_error(bot, message,
                                                         resp_json.get("task_id", ""),
                                                         resp_json.get("message_id", ""),
                                                         resp_json.get("status", ""),
                                                         resp_json.get("code", ""),
                                                         resp_json.get("message", ""))

                    new_con_id = resp_json.get("conversation_id", "")
                    if new_con_id and new_con_id != conversation_id:
                        self.db.save_llm_thread_id(message["FromWxid"], new_con_id, "dify")

                elif resp.status == 404:
                    self.db.save_llm_thread_id(message["FromWxid"], "", "dify")
                    return await self.dify(bot, message, query)

                elif resp.status == 400:
                    return await self.handle_400(bot, message, resp)

                elif resp.status == 500:
                    return await self.handle_500(bot, message)

                else:
                    return await self.handle_other_status(bot, message, resp)

        if ai_resp:
            await self.dify_handle_text(bot, message, ai_resp)

    async def upload_file(self, user: str, file: bytes):
        headers = {"Authorization": f"Bearer {self.api_key}"}

        # user multipart/form-data
        kind = filetype.guess(file)
        formdata = aiohttp.FormData()
        formdata.add_field("user", user)
        filename = "unknown"
        content_type = "application/octet-stream"
        if kind:
            filename = f"file.{kind.extension}"
            content_type = kind.mime

        formdata.add_field("file", file, filename=filename, content_type=content_type)

        url = f"{self.base_url}/files/upload"
        try:
            async with aiohttp.ClientSession(proxy=self.http_proxy) as session:
                async with session.post(url, headers=headers, data=formdata) as resp:
                    resp_json = await resp.json()
            return resp_json.get("id", "")
        except Exception as e:
            logger.exception(f"文件上传失败: {e}")
            return ""

    async def dify_handle_text(self, bot: WechatAPIClient, message: dict, text: str):
        pattern = r"\]$$(https?:\/\/[^\s]+)$$"
        links = re.findall(pattern, text)
        for url in links:
            try:
                file = await self.download_file(url)
                extension = filetype.guess_extension(file)
                if extension in ('wav', 'mp3'):
                    await bot.send_voice_message(message["FromWxid"], voice=file, format=extension)
                elif extension in ('jpg', 'jpeg', 'png', 'gif', 'bmp', 'svg'):
                    await bot.send_image_message(message["FromWxid"], file)
                elif extension in ('mp4', 'avi', 'mov', 'mkv', 'flv'):
                    await bot.send_video_message(message["FromWxid"], video=file, image="None")
            except Exception as e:
                logger.exception(f"下载或发送文件失败: {e}")

        pattern = r'$$.*?$$$$(https?:\/\/[^\s]+)$$'
        text = re.sub(pattern, '', text)
        if text:
            # 将文本转换为语音
            audio_data = await self._text_to_speech(text)
            if audio_data:
                await bot.send_voice_message(message["FromWxid"], voice=audio_data, format=self.tts_format)
                logger.info(f"发送语音消息到 {message['FromWxid']}")
            else:
                await bot.send_at_message(message["FromWxid"], "-----XYBot-----\n❌TTS转换失败！", [message["SenderWxid"]])

    async def download_file(self, url: str) -> bytes:
        async with aiohttp.ClientSession(proxy=self.http_proxy) as session:
            async with session.get(url) as resp:
                return await resp.read()

    async def dify_handle_image(self, bot: WechatAPIClient, message: dict, image: Union[str, bytes]):
        if isinstance(image, str) and image.startswith("http"):
            try:
                async with aiohttp.ClientSession(proxy=self.http_proxy) as session:
                    async with session.get(image) as resp:
                        image = bot.byte_to_base64(await resp.read())
            except Exception as e:
                logger.exception(f"下载图片失败: {e}")
                return
        elif isinstance(image, bytes):
            image = bot.byte_to_base64(image)

        await bot.send_image_message(message["FromWxid"], image)

    async def dify_handle_audio(self, bot: WechatAPIClient, message: dict, audio: str):
        # 确认 Dify 返回的 audio 是符合微信要求的格式
        await bot.send_voice_message(message["FromWxid"], audio)

    async def dify_handle_error(self, bot: WechatAPIClient, message: dict, task_id: str, message_id: str, status: str,
                                code: int, err_message: str):
        output = ("-----XYBot-----\n"
                  "🙅对不起，Dify出现错误！\n"
                  f"任务 ID：{task_id}\n"
                  f"消息唯一 ID：{message_id}\n"
                  f"HTTP 状态码：{status}\n"
                  f"错误码：{code}\n"
                  f"错误信息：{err_message}")
        await bot.send_at_message(message["FromWxid"], "\n" + output, [message["SenderWxid"]])

    async def handle_400(self, bot: WechatAPIClient, message: dict, resp: aiohttp.ClientResponse):
        output = ("-----XYBot-----\n"
                  "🙅对不起，出现错误！\n"
                  f"错误信息：{(await resp.text())}")
        await bot.send_at_message(message["FromWxid"], "\n" + output, [message["SenderWxid"]])

    async def handle_500(self, bot: WechatAPIClient, message: dict):
        output = "-----XYBot-----\n🙅对不起，Dify服务内部异常，请稍后再试。"
        await bot.send_at_message(message["FromWxid"], "\n" + output, [message["SenderWxid"]])

    async def handle_other_status(self, bot: WechatAPIClient, message: dict, resp: aiohttp.ClientResponse):
        output = ("-----XYBot-----\n"
                   f"🙅对不起，出现错误！\n"
                   f"状态码：{resp.status}\n"
                   f"错误信息：{(await resp.text())}")
        await bot.send_at_message(message["FromWxid"], "\n" + output, [message["SenderWxid"]])

    async def hendle_exceptions(self, bot: WechatAPIClient, message: dict):
        output = ("-----XYBot-----\n"
                  "🙅对不起，出现错误！\n"
                  f"错误信息：\n"
                  f"{traceback.format_exc()}")
        await bot.send_at_message(message["FromWxid"], "\n" + output, [message["SenderWxid"]])

    async def _check_point(self, bot: WechatAPIClient, message: dict) -> bool:
        wxid = message["SenderWxid"]

        if wxid in self.admins and self.admin_ignore:
            return True
        elif self.db.get_whitelist(wxid) and self.whitelist_ignore:
            return True
        else:
            if self.db.get_points(wxid) < self.price:
                await bot.send_at_message(message["FromWxid"],
                                          f"\n-----XYBot-----\n"
                                          f"😭你的积分不够啦！需要 {self.price} 积分",
                                          [wxid])
                return False

            self.db.add_points(wxid, -self.price)
            return True