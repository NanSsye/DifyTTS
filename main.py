import io
import json
import re
import subprocess
import tomllib
from typing import Optional, Union

import aiohttp
import filetype
from loguru import logger
import os
from WechatAPI import WechatAPIClient
from database.XYBotDB import XYBotDB
from utils.decorators import *
from utils.plugin_base import PluginBase
from gtts import gTTS
import traceback

# 常量定义
XYBOT_PREFIX = "-----XYBot-----\n"
DIFY_ERROR_MESSAGE = "🙅对不起，Dify出现错误！\n"
INSUFFICIENT_POINTS_MESSAGE = "😭你的积分不够啦！需要 {price} 积分"
VOICE_TRANSCRIPTION_FAILED = "\n语音转文字失败"
TEXT_TO_VOICE_FAILED = "\n文本转语音失败"


class Dify(PluginBase):
    description = "Dify插件"
    author = "HenryXiaoYang/老夏的金库"
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
        self.voice_reply_all = plugin_config["voice_reply_all"]
        self.robot_names = plugin_config.get("robot-names", [])

        self.audio_to_text_url = plugin_config.get("audio-to-text-url", "")
        self.text_to_audio_url = plugin_config.get("text-to-audio-url", "")

        self.db = XYBotDB()

    @on_text_message(priority=20)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

        content = message["Content"].strip()
        command = content.split(" ")[0] if content else ""

        if message["IsGroup"]:
            if not (command in self.commands or self.is_at_message(message)):
                return

        if command in self.commands and len(content.split()) == 1:
            if message["IsGroup"]:
                await bot.send_at_message(message["FromWxid"], "\n" + self.command_tip,
                                          [message["SenderWxid"]])
            else:
                await bot.send_text_message(message["FromWxid"], self.command_tip)
            return

        if not self.api_key:
            if message["IsGroup"]:
                await bot.send_at_message(message["FromWxid"], "\n你还没配置Dify API密钥！",
                                          [message["SenderWxid"]])
            else:
                await bot.send_text_message(message["FromWxid"], "你还没配置Dify API密钥！")
            return False

        query = content
        for robot_name in self.robot_names:
            query = query.replace(f"@{robot_name}", "").strip()
        if command in self.commands:
            query = query[len(command):].strip()

        logger.debug(f"提取到的 query: {query}")

        user_wxid = message["SenderWxid"]
        try:
            user_username = await bot.get_nickname(user_wxid)
            if not user_username:
                user_username = "未知用户"
            logger.debug(f"用户 {user_wxid} 的昵称: {user_username}")
        except Exception as e:
            logger.error(f"获取用户 {user_wxid} 昵称失败: {e}")
            user_username = "未知用户"

        if not query:
            if message["IsGroup"]:
                await bot.send_at_message(message["FromWxid"], "\n请在命令后输入你的问题或指令。", [message["SenderWxid"]])
            else:
                await bot.send_text_message(message["FromWxid"], "\n请输入你的问题或指令。")
            return False

        if await self._check_point(bot, message):
            await self.dify(bot, message, query)
        return False

    @on_at_message(priority=20)
    async def handle_at(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

        if not self.api_key:
            await bot.send_at_message(message["FromWxid"], "\n你还没配置Dify API密钥！", [message["SenderWxid"]])
            return False

        content = message["Content"].strip()
        query = content
        for robot_name in self.robot_names:
            query = query.replace(f"@{robot_name}", "").strip()

        logger.debug(f"提取到的 query: {query}")

        if not query:
            await bot.send_at_message(message["FromWxid"], "\n请在 @ 机器人后输入你的问题或指令。", [message["SenderWxid"]])
            return False

        if await self._check_point(bot, message):
            await self.dify(bot, message, query)
        return False

    @on_voice_message(priority=20)
    async def handle_voice(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

        if message["IsGroup"]:
            return

        if not self.api_key:
            await bot.send_text_message(message["FromWxid"], "你还没配置Dify API密钥！")
            return False

        query = await self.audio_to_text(bot, message)
        if not query:
            await bot.send_text_message(message["FromWxid"], VOICE_TRANSCRIPTION_FAILED)
            return False

        logger.debug(f"语音转文字结果: {query}")

        user_wxid = message["SenderWxid"]
        try:
            user_username = await bot.get_nickname(user_wxid)
            if not user_username:
                user_username = "未知用户"
            logger.debug(f"用户 {user_wxid} 的昵称: {user_username}")
        except Exception as e:
            logger.error(f"获取用户 {user_wxid} 昵称失败: {e}")
            user_username = "未知用户"

        if await self._check_point(bot, message):
            await self.dify(bot, message, query)
        return False

    def is_at_message(self, message: dict) -> bool:
        if not message["IsGroup"]:
            return False

        content = message["Content"]
        for robot_name in self.robot_names:
            if f"@{robot_name}" in content:
                return True
        return False

    async def dify(self, bot: WechatAPIClient, message: dict, query: str, files=None):
        if files is None:
            files = []
        conversation_id = self.db.get_llm_thread_id(message["FromWxid"], namespace="dify")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        user_wxid = message["SenderWxid"]
        try:
            user_username = await bot.get_nickname(user_wxid)
            if not user_username:
                user_username = "未知用户"
        except Exception as e:
            logger.error(f"获取用户昵称失败: {e}")
            user_username = "未知用户"

        inputs = {
            "user_wxid": user_wxid,
            "user_username": user_username
        }
        logger.debug(f"Dify Inputs: {inputs}")
        payload = json.dumps({
            "inputs": inputs,
            "query": query,
            "response_mode": "streaming",
            "conversation_id": conversation_id,
            "user": message["FromWxid"],
            "files": files,
            "auto_generate_name": False,
        })
        url = f"{self.base_url}/chat-messages"

        ai_resp = ""
        try:
            async with aiohttp.ClientSession(proxy=self.http_proxy) as session:
                async with session.post(url=url, headers=headers, data=payload) as resp:
                    if resp.status == 200:
                        async for line in resp.content:
                            line = line.decode("utf-8").strip()
                            if not line or line == "event: ping":
                                continue
                            elif line.startswith("data: "):
                                line = line[6:]

                            try:
                                resp_json = json.loads(line)
                            except json.decoder.JSONDecodeError:
                                logger.error(f"Dify返回的JSON解析错误，请检查格式: {line}")
                                continue

                            event = resp_json.get("event", "")
                            if event == "message":
                                ai_resp += resp_json.get("answer", "")
                            elif event == "message_replace":
                                ai_resp = resp_json.get("answer", "")
                            elif event == "message_file":
                                await self.dify_handle_image(bot, message, resp_json.get("url", ""))
                            elif event == "error":
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

        except Exception as e:
            logger.error(f"Dify API 调用失败: {e}")
            await self.hendle_exceptions(bot, message)

    async def upload_file(self, user: str, file: bytes):
        headers = {"Authorization": f"Bearer {self.api_key}"}
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
            try:
                file = await self.download_file(url)
                extension = filetype.guess_extension(file)
                if extension in ('wav', 'mp3'):
                    await bot.send_voice_message(message["FromWxid"], voice=file, format=filetype.guess_extension(file))
                elif extension in ('jpg', 'jpeg', "png", "gif", "bmp", "svg"):
                    await bot.send_image_message(message["FromWxid"], file)
                elif extension in ('mp4', 'avi', 'mov', 'mkv', 'flv'):
                    await bot.send_video_message(message["FromWxid"], video=file, image="None")
            except Exception as e:
                logger.error(f"下载文件 {url} 失败: {e}")
                await bot.send_text_message(message["FromWxid"], f"下载文件 {url} 失败")

        pattern = r'\$\$[^$$]+\]\$\$https?:\/\/[^\s$$]+\)'
        text = re.sub(pattern, '', text)
        if text:
            logger.debug(f"准备处理回复: {text}, MsgType: {message['MsgType']}, voice_reply_all: {self.voice_reply_all}")
            if message["MsgType"] == 34 or self.voice_reply_all:
                await self.text_to_voice_message(bot, message, text)
            else:
                if message["IsGroup"]:
                    await bot.send_at_message(message["FromWxid"], "\n" + text, [message["SenderWxid"]])
                else:
                    await bot.send_text_message(message["FromWxid"], text)

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
                logger.error(f"下载图片 {image} 失败: {e}")
                await bot.send_text_message(message["FromWxid"], f"下载图片 {image} 失败")
                return
        elif isinstance(image, bytes):
            image = bot.byte_to_base64(image)

        await bot.send_image_message(message["FromWxid"], image)

    @staticmethod
    async def dify_handle_error(bot: WechatAPIClient, message: dict, task_id: str, message_id: str, status: str,
                                code: int, err_message: str):
        output = (XYBOT_PREFIX +
                  DIFY_ERROR_MESSAGE +
                  f"任务 ID：{task_id}\n"
                  f"消息唯一 ID：{message_id}\n"
                  f"HTTP 状态码：{status}\n"
                  f"错误码：{code}\n"
                  f"错误信息：{err_message}")
        await bot.send_text_message(message["FromWxid"], output)

    @staticmethod
    async def handle_400(bot: WechatAPIClient, message: dict, resp: aiohttp.ClientResponse):
        output = (XYBOT_PREFIX +
                  "🙅对不起，出现错误！\n"
                  f"错误信息：{(await resp.content.read()).decode('utf-8')}")
        await bot.send_text_message(message["FromWxid"], output)

    @staticmethod
    async def handle_500(bot: WechatAPIClient, message: dict):
        output = XYBOT_PREFIX + "🙅对不起，Dify服务内部异常，请稍后再试。"
        await bot.send_text_message(message["FromWxid"], output)

    @staticmethod
    async def handle_other_status(bot: WechatAPIClient, message: dict, resp: aiohttp.ClientResponse):
        ai_resp = (XYBOT_PREFIX +
                   f"🙅对不起，出现错误！\n"
                   f"状态码：{resp.status}\n"
                   f"错误信息：{(await resp.content.read()).decode('utf-8')}")
        await bot.send_text_message(message["FromWxid"], ai_resp)

    @staticmethod
    async def hendle_exceptions(bot: WechatAPIClient, message: dict):
        output = (XYBOT_PREFIX +
                  "🙅对不起，出现错误！\n"
                  f"错误信息：\n"
                  f"{traceback.format_exc()}")
        await bot.send_text_message(message["FromWxid"], output)

    async def _check_point(self, bot: WechatAPIClient, message: dict) -> bool:
        wxid = message["SenderWxid"]

        if wxid in self.admins and self.admin_ignore:
            return True
        elif self.db.get_whitelist(wxid) and self.whitelist_ignore:
            return True
        else:
            if self.db.get_points(wxid) < self.price:
                await bot.send_text_message(message["FromWxid"],
                                            XYBOT_PREFIX +
                                            INSUFFICIENT_POINTS_MESSAGE.format(price=self.price))
                return False

            self.db.add_points(wxid, -self.price)
            return True

    async def audio_to_text(self, bot: WechatAPIClient, message: dict) -> str:
        logger.info("进入 audio_to_text 函数")
        silk_file = "temp_audio.silk"
        mp3_file = "temp_audio.mp3"
        try:
            # 将 silk 转为 MP3（16kHz 单声道）
            with open(silk_file, "wb") as f:
                f.write(message["Content"])

            command = f"ffmpeg -y -i {silk_file} -ar 16000 -ac 1 -f mp3 {mp3_file}"
            process = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
            if process.returncode != 0:
                logger.error(f"ffmpeg 执行失败: {process.stderr}")
                return ""

            # 使用 Dify 的 audio-to-text 接口
            if self.audio_to_text_url:
                headers = {"Authorization": f"Bearer {self.api_key}"}
                formdata = aiohttp.FormData()
                with open(mp3_file, "rb") as f:
                    mp3_data = f.read()
                formdata.add_field("file", mp3_data, filename="audio.mp3", content_type="audio/mp3")
                formdata.add_field("user", message["SenderWxid"])
                async with aiohttp.ClientSession(proxy=self.http_proxy) as session:
                    async with session.post(self.audio_to_text_url, headers=headers, data=formdata) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            text = result.get("text", "")
                            # 检查返回内容是否包含错误信息
                            if "failed" in text.lower() or "code" in text.lower():
                                logger.error(f"Dify API 返回错误: {text}")
                            else:
                                logger.info(f"语音转文字结果 (Dify API): {text}")
                                return text
                        else:
                            logger.error(f"audio-to-text 接口调用失败: {resp.status} - {await resp.text()}")

            # 回退到 Google Speech Recognition
            command = f"ffmpeg -y -i {mp3_file} {silk_file.replace('.silk', '.wav')}"
            process = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
            if process.returncode != 0:
                logger.error(f"ffmpeg 转为 WAV 失败: {process.stderr}")
                return ""

            r = sr.Recognizer()
            with sr.AudioFile(silk_file.replace('.silk', '.wav')) as source:
                audio = r.record(source)

            text = r.recognize_google(audio, language="zh-CN")
            logger.info(f"语音转文字结果 (Google): {text}")
            return text

        except FileNotFoundError:
            logger.error("ffmpeg 未找到，请确认已安装并配置到环境变量")
            return ""
        except Exception as e:
            logger.error(f"语音处理失败: {e}")
            return ""
        finally:
            for temp_file in [silk_file, mp3_file, silk_file.replace('.silk', '.wav')]:
                if os.path.exists(temp_file):
                    os.remove(temp_file)

    async def text_to_voice_message(self, bot: WechatAPIClient, message: dict, text: str):
        logger.info(f"进入 text_to_voice_message 函数，文本: {text}")
        try:
            url = self.text_to_audio_url if self.text_to_audio_url else f"{self.base_url}/text-to-audio"
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            data = {"text": text, "user": message["SenderWxid"]}
            async with aiohttp.ClientSession(proxy=self.http_proxy) as session:
                async with session.post(url, headers=headers, json=data) as resp:
                    if resp.status == 200:
                        audio = await resp.read()
                        await bot.send_voice_message(message["FromWxid"], voice=audio, format="mp3")
                        logger.info("语音消息发送成功")
                    else:
                        logger.error(f"text-to-audio 接口调用失败: {resp.status} - {await resp.text()}")
                        await bot.send_text_message(message["FromWxid"], TEXT_TO_VOICE_FAILED)
        except Exception as e:
            logger.error(f"text-to-audio 接口调用异常: {e}")
            await bot.send_text_message(message["FromWxid"], TEXT_TO_VOICE_FAILED)
