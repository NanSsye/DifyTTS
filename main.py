import io
import json
import re
import subprocess
import tomllib
from typing import Optional, Union

import aiohttp
import filetype
from loguru import logger
import speech_recognition as sr  # 导入speech_recognition
import os
from WechatAPI import WechatAPIClient
from database.XYBotDB import XYBotDB
from utils.decorators import *
from utils.plugin_base import PluginBase
from gtts import gTTS

class Dify(PluginBase):
    description = "Dify插件"
    author = "老夏的金库"
    version = "1.1.1"

    def __init__(self):
        super().__init__()

        with open("main_config.toml", "rb") as f:
            config = tomllib.load(f)

        self.admins = config["XYBot"]["admins"]

        with open("plugins/Dify/config.toml", "rb") as f:
            config = tomllib.load(f)

        plugin_config = config["Dify"]

        self.enable = plugin_config["enable"]
        self.api_key = plugin_config["api-key"]
        self.base_url = plugin_config["base-url"]

        self.commands = plugin_config["commands"]
        self.command_tip = plugin_config["command-tip"]

        self.price = plugin_config["price"]
        self.admin_ignore = plugin_config["admin_ignore"]
        self.whitelist_ignore = plugin_config["whitelist_ignore"]

        self.http_proxy = plugin_config["http-proxy"]
        self.voice_reply_all = plugin_config["voice_reply_all"]  # 新增属性

        self.db = XYBotDB()

    @on_text_message(priority=20)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

        command = str(message["Content"]).strip().split(" ")

        if (not command or command[0] not in self.commands) and message["IsGroup"]:  # 不是指令，且是群聊
            return
        elif len(command) == 1 and command[0] in self.commands:  # 只是指令，但没请求内容
            await bot.send_at_message(message["FromWxid"], "\n" + self.command_tip, [message["SenderWxid"]])
            return

        if not self.api_key:
            await bot.send_at_message(message["FromWxid"], "\n你还没配置Dify API密钥！", [message["SenderWxid"]])
            return False

        if await self._check_point(bot, message):
            await self.dify(bot, message, message["Content"])
        return False

    @on_at_message(priority=20)
    async def handle_at(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

        if not self.api_key:
            await bot.send_at_message(message["FromWxid"], "\n你还没配置Dify API密钥！", [message["SenderWxid"]])
            return False

        if await self._check_point(bot, message):
            await self.dify(bot, message, message["Content"])

        return False

    @on_voice_message(priority=20)
    async def handle_voice(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

        if message["IsGroup"]:
            return

        if not self.api_key:
            await bot.send_at_message(message["FromWxid"], "\n你还没配置Dify API密钥！", [message["SenderWxid"]])
            return False

        if await self._check_point(bot, message):
            try:
                text = await self.audio_to_text(bot, message)
                if text:
                    await self.dify(bot, message, text)
                else:
                    await bot.send_at_message(message["FromWxid"], "\n语音转文字失败", [message["SenderWxid"]])
            except Exception as e:
                logger.error(f"语音转文字失败: {e}")
                await bot.send_at_message(message["FromWxid"], "\n语音转文字失败", [message["SenderWxid"]])

        return False

    @on_image_message(priority=20)
    async def handle_image(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

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

        return False

    @on_video_message(priority=20)
    async def handle_video(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

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

        return False

    @on_file_message(priority=20)
    async def handle_file(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

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

                        event = resp_json.get("event", "")
                        if event == "message":  # LLM 返回文本块事件
                            ai_resp += resp_json.get("answer", "")
                        elif event == "message_replace":  # 消息内容替换事件
                            ai_resp = resp_json("answer", "")
                        elif event == "message_file":  # 文件事件 目前dify只输出图片
                            await self.dify_handle_image(bot, message, resp_json.get("url", ""))
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
        formdata.add_field("file", file, filename=kind.extension, content_type=kind.mime)

        url = f"{self.base_url}/files/upload"

        async with aiohttp.ClientSession(proxy=self.http_proxy) as session:
            async with session.post(url, headers=headers, data=formdata) as resp:
                resp_json = await resp.json()

        return resp_json.get("id", "")

    async def dify_handle_text(self, bot: WechatAPIClient, message: dict, text: str):
        pattern = r"\]$$(https?:\/\/[^\s$$]+)\)"
        links = re.findall(pattern, text)
        for url in links:
            file = await self.download_file(url)
            extension = filetype.guess_extension(file)
            if extension in ('wav', 'mp3'):
                await bot.send_voice_message(message["FromWxid"], voice=file, format=filetype.guess_extension(file))
            elif extension in ('jpg', 'jpeg', 'png', 'gif', 'bmp', 'svg'):
                await bot.send_image_message(message["FromWxid"], file)
            elif extension in ('mp4', 'avi', 'mov', 'mkv', 'flv'):
                await bot.send_video_message(message["FromWxid"], video=file, image="None")

        pattern = r'$$[^$$]+\]$$https?:\/\/[^\s$$]+\)'
        text = re.sub(pattern, '', text)
        if text:
            if message["MsgType"] == 34 or self.voice_reply_all:  # 根据消息类型和配置选项决定是否语音回复
                await self.text_to_voice_message(bot, message, text)
            else:
                await bot.send_at_message(message["FromWxid"], "\n" + text, [message["SenderWxid"]])

    async def download_file(self, url: str) -> bytes:
        async with aiohttp.ClientSession(proxy=self.http_proxy) as session:
            async with session.get(url) as resp:
                return await resp.read()

    async def dify_handle_image(self, bot: WechatAPIClient, message: dict, image: Union[str, bytes]):
        if isinstance(image, str) and image.startswith("http"):
            async with aiohttp.ClientSession(proxy=self.http_proxy) as session:
                async with session.get(image) as resp:
                    image = bot.byte_to_base64(await resp.read())
        elif isinstance(image, bytes):
            image = bot.byte_to_base64(image)

        await bot.send_image_message(message["FromWxid"], image)

    @staticmethod
    async def dify_handle_audio(bot: WechatAPIClient, message: dict, audio: str):
        pass

    @staticmethod
    async def dify_handle_error(bot: WechatAPIClient, message: dict, task_id: str, message_id: str, status: str,
                                code: int, err_message: str):
        output = ("-----XYBot-----\n"
                  "🙅对不起，Dify出现错误！\n"
                  f"任务 ID：{task_id}\n"
                  f"消息唯一 ID：{message_id}\n"
                  f"HTTP 状态码：{status}\n"
                  f"错误码：{code}\n"
                  f"错误信息：{err_message}")
        await bot.send_at_message(message["FromWxid"], "\n" + output, [message["SenderWxid"]])

    @staticmethod
    async def handle_400(bot: WechatAPIClient, message: dict, resp: aiohttp.ClientResponse):
        output = ("-----XYBot-----\n"
                  "🙅对不起，出现错误！\n"
                  f"错误信息：{(await resp.content.read()).decode('utf-8')}")
        await bot.send_at_message(message["FromWxid"], "\n" + output, [message["SenderWxid"]])

    @staticmethod
    async def handle_500(bot: WechatAPIClient, message: dict):
        output = "-----XYBot-----\n🙅对不起，Dify服务内部异常，请稍后再试。"
        await bot.send_at_message(message["FromWxid"], "\n" + output, [message["SenderWxid"]])

    @staticmethod
    async def handle_other_status(bot: WechatAPIClient, message: dict, resp: aiohttp.ClientResponse):
        ai_resp = ("-----XYBot-----\n"
                   f"🙅对不起，出现错误！\n"
                   f"状态码：{resp.status}\n"
                   f"错误信息：{(await resp.content.read()).decode('utf-8')}")
        await bot.send_at_message(message["FromWxid"], "\n" + ai_resp, [message["SenderWxid"]])

    @staticmethod
    async def hendle_exceptions(bot: WechatAPIClient, message: dict):
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

    async def audio_to_text(self, bot: WechatAPIClient, message: dict) -> str:
        logger.info("进入 audio_to_text 函数")
        try:
            silk_file = "temp_audio.silk"
            wav_file = "temp_audio.wav"

            with open(silk_file, "wb") as f:
                f.write(message["Content"])

            command = f"ffmpeg -y -i {silk_file} {wav_file}"
            subprocess.run(command, shell=True, check=True, capture_output=True)
            
            r = sr.Recognizer()
            with sr.AudioFile(wav_file) as source:
                audio = r.record(source)

            text = r.recognize_google(audio, language="zh-CN")
            logger.info(f"Dify 返回文本: {text}")
            return text

        except Exception as e:
            logger.error(f"语音格式转换失败: {e}")
            return ""
        finally:
            if os.path.exists(silk_file):
                os.remove(silk_file)
            if os.path.exists(wav_file):
                os.remove(wav_file)

    async def text_to_voice_message(self, bot: WechatAPIClient, message: dict, text: str):
        """使用 Dify 的 text-to-audio 接口将文本转换为语音消息并发送."""
        try:
            url = f"{self.base_url}/text-to-audio"
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            data = {"text": text, "user": message["SenderWxid"]}  # 使用 SenderWxid 作为用户标识
            async with aiohttp.ClientSession(proxy=self.http_proxy) as session:
                async with session.post(url, headers=headers, json=data) as resp:
                    if resp.status == 200:
                        audio = await resp.read()  # 获取音频数据
                        await bot.send_voice_message(message["FromWxid"], voice=audio, format="mp3")  # 假设 Dify 返回 MP3 格式
                    else:
                        logger.error(f"text-to-audio 接口调用失败: {resp.status} - {await resp.text()}")
                        await bot.send_at_message(message["FromWxid"], "\n文本转语音失败", [message["SenderWxid"]])
        except Exception as e:
            logger.error(f"text-to-audio 接口调用异常: {e}")
            await bot.send_at_message(message["FromWxid"], "\n文本转语音失败", [message["SenderWxid"]])