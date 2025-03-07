import io
import json
import re
import subprocess
import tomllib
from typing import Optional, Union, Dict, List, Tuple
import time
from dataclasses import dataclass, field
from datetime import datetime
import asyncio
from collections import defaultdict
from enum import Enum
import urllib.parse
import mimetypes
import base64

import aiohttp
import filetype
from loguru import logger
import speech_recognition as sr
import os
from WechatAPI import WechatAPIClient
from database.XYBotDB import XYBotDB
from utils.decorators import *
from utils.plugin_base import PluginBase
from gtts import gTTS
import traceback
import shutil
from PIL import Image
import xml.etree.ElementTree as ET

# 常量定义
XYBOT_PREFIX = "-----老夏的金库-----\n"
DIFY_ERROR_MESSAGE = "🙅对不起，Dify出现错误！\n"
INSUFFICIENT_POINTS_MESSAGE = "😭你的积分不够啦！需要 {price} 积分"
VOICE_TRANSCRIPTION_FAILED = "\n语音转文字失败"
TEXT_TO_VOICE_FAILED = "\n文本转语音失败"
CHAT_TIMEOUT = 3600  # 1小时超时
CHAT_AWAY_TIMEOUT = 1800  # 30分钟自动离开
MESSAGE_BUFFER_TIMEOUT = 10  # 消息缓冲区超时时间（秒）
MAX_BUFFERED_MESSAGES = 10  # 最大缓冲消息数

# 聊天室消息模板
CHAT_JOIN_MESSAGE = """✨ 欢迎来到聊天室！让我们开始愉快的对话吧~

💡 基础指引：
   📝 直接发消息与我对话
   🚪 发送"退出聊天"离开
   ⏰ 5分钟不说话自动暂离
   🔄 30分钟无互动将退出

🎮 聊天指令：
   📊 发送"查看状态"
   📈 发送"聊天室排行"
   👤 发送"我的统计"
   💤 发送"暂时离开"

开始聊天吧！期待与你的精彩对话~ 🌟"""

CHAT_LEAVE_MESSAGE = "👋 已退出聊天室，需要再次@我才能继续对话"
CHAT_TIMEOUT_MESSAGE = "由于您已经1小时没有活动，已被移出聊天室。如需继续对话，请重新发送消息。"
CHAT_AWAY_MESSAGE = "💤 已设置为离开状态，其他人将看到你正在休息"
CHAT_BACK_MESSAGE = "🌟 欢迎回来！已恢复活跃状态"
CHAT_AUTO_AWAY_MESSAGE = "由于您已经30分钟没有活动，已被自动设置为离开状态。"

class UserStatus(Enum):
    ACTIVE = "活跃"
    AWAY = "离开"
    INACTIVE = "未加入"

@dataclass
class UserStats:
    total_messages: int = 0
    total_chars: int = 0
    join_count: int = 0
    last_active: float = 0
    total_active_time: float = 0
    status: UserStatus = UserStatus.INACTIVE

@dataclass
class ChatRoomUser:
    wxid: str
    group_id: str
    last_active: float
    status: UserStatus = UserStatus.ACTIVE
    stats: UserStats = field(default_factory=UserStats)
    
@dataclass
class MessageBuffer:
    messages: list[str] = field(default_factory=list)
    last_message_time: float = 0.0
    timer_task: Optional[asyncio.Task] = None
    message_count: int = 0
    files: list[str] = field(default_factory=list)

class ChatRoomManager:
    def __init__(self):
        self.active_users = {}
        self.message_buffers = defaultdict(lambda: MessageBuffer([], 0.0, None))
        self.user_stats: Dict[tuple[str, str], UserStats] = defaultdict(UserStats)
        
    def add_user(self, group_id: str, user_wxid: str) -> None:
        key = (group_id, user_wxid)
        self.active_users[key] = ChatRoomUser(
            wxid=user_wxid,
            group_id=group_id,
            last_active=time.time()
        )
        stats = self.user_stats[key]
        stats.join_count += 1
        stats.last_active = time.time()
        stats.status = UserStatus.ACTIVE
        
    def remove_user(self, group_id: str, user_wxid: str) -> None:
        key = (group_id, user_wxid)
        if key in self.active_users:
            user = self.active_users[key]
            stats = self.user_stats[key]
            stats.total_active_time += time.time() - stats.last_active
            stats.status = UserStatus.INACTIVE
            del self.active_users[key]
        if key in self.message_buffers:
            buffer = self.message_buffers[key]
            if buffer.timer_task and not buffer.timer_task.done():
                buffer.timer_task.cancel()
            del self.message_buffers[key]
            
    def update_user_activity(self, group_id: str, user_wxid: str) -> None:
        key = (group_id, user_wxid)
        if key in self.active_users:
            self.active_users[key].last_active = time.time()
            stats = self.user_stats[key]
            stats.total_messages += 1
            stats.last_active = time.time()
            
    def set_user_status(self, group_id: str, user_wxid: str, status: UserStatus) -> None:
        key = (group_id, user_wxid)
        if key in self.active_users:
            self.active_users[key].status = status
            self.user_stats[key].status = status
            
    def get_user_status(self, group_id: str, user_wxid: str) -> UserStatus:
        key = (group_id, user_wxid)
        if key in self.active_users:
            return self.active_users[key].status
        return UserStatus.INACTIVE
        
    def get_user_stats(self, group_id: str, user_wxid: str) -> UserStats:
        return self.user_stats[(group_id, user_wxid)]
        
    def get_room_stats(self, group_id: str) -> List[tuple[str, UserStats]]:
        stats = []
        for (g_id, wxid), user_stats in self.user_stats.items():
            if g_id == group_id:
                stats.append((wxid, user_stats))
        return sorted(stats, key=lambda x: x[1].total_messages, reverse=True)
        
    def get_active_users_count(self, group_id: str) -> tuple[int, int, int]:
        active = 0
        away = 0
        total = 0
        for (g_id, _), user in self.active_users.items():
            if g_id == group_id:
                total += 1
                if user.status == UserStatus.ACTIVE:
                    active += 1
                elif user.status == UserStatus.AWAY:
                    away += 1
        return active, away, total

    async def add_message_to_buffer(self, group_id: str, user_wxid: str, message: str, files: list[str] = None) -> None:
        """添加消息到缓冲区"""
        if files is None:
            files = []
        
        key = (group_id, user_wxid)
        if key not in self.message_buffers:
            self.message_buffers[key] = MessageBuffer()
        
        buffer = self.message_buffers[key]
        buffer.messages.append(message)
        buffer.last_message_time = time.time()
        buffer.message_count += 1
        buffer.files.extend(files)  # 添加文件ID到缓冲区
        
        logger.debug(f"成功添加消息到缓冲区 - 用户: {user_wxid}, 消息: {message}, 当前消息数: {buffer.message_count}, 文件: {files}")

    def get_and_clear_buffer(self, group_id: str, user_wxid: str) -> Tuple[str, list[str]]:
        """获取并清空缓冲区"""
        key = (group_id, user_wxid)
        buffer = self.message_buffers.get(key)
        if buffer:
            messages = "\n".join(buffer.messages)
            files = buffer.files.copy()  # 复制文件ID列表
            logger.debug(f"合并并清空缓冲区 - 用户: {user_wxid}, 合并消息: {messages}, 文件: {files}")
            buffer.messages.clear()
            buffer.message_count = 0
            buffer.files.clear()  # 清空文件ID列表
            return messages, files
        return "", []

    def is_user_active(self, group_id: str, user_wxid: str) -> bool:
        key = (group_id, user_wxid)
        if key not in self.active_users:
            return False
        
        user = self.active_users[key]
        if time.time() - user.last_active > CHAT_TIMEOUT:
            self.remove_user(group_id, user_wxid)
            return False
        return True
        
    def check_and_remove_inactive_users(self) -> list[tuple[str, str]]:
        current_time = time.time()
        inactive_users = []
        
        for (group_id, user_wxid), user in list(self.active_users.items()):
            if user.status == UserStatus.ACTIVE and current_time - user.last_active > CHAT_AWAY_TIMEOUT:
                self.set_user_status(group_id, user_wxid, UserStatus.AWAY)
                inactive_users.append((group_id, user_wxid, "away"))
            elif current_time - user.last_active > CHAT_TIMEOUT:
                inactive_users.append((group_id, user_wxid, "timeout"))
                self.remove_user(group_id, user_wxid)
                
        return inactive_users

    def format_user_stats(self, group_id: str, user_wxid: str, nickname: str = "未知用户") -> str:
        stats = self.get_user_stats(group_id, user_wxid)
        status = self.get_user_status(group_id, user_wxid)
        active_time = int(stats.total_active_time / 60)
        return f"""📊 {nickname} 的聊天室数据：

🏷️ 当前状态：{status.value}
💬 发送消息：{stats.total_messages} 条
📝 总字数：{stats.total_chars} 字
🔄 加入次数：{stats.join_count} 次
⏱️ 活跃时间：{active_time} 分钟"""

    def format_room_status(self, group_id: str) -> str:
        active, away, total = self.get_active_users_count(group_id)
        return f"""🏠 聊天室状态：

👥 当前成员：{total} 人
✨ 活跃成员：{active} 人
💤 暂离成员：{away} 人"""

    async def format_room_ranking(self, group_id: str, bot: WechatAPIClient, limit: int = 5) -> str:
        stats = self.get_room_stats(group_id)
        result = ["🏆 聊天室排行榜：\n"]
        
        for i, (wxid, user_stats) in enumerate(stats[:limit], 1):
            try:
                nickname = await bot.get_nickname(wxid) or "未知用户"
            except:
                nickname = "未知用户"
            result.append(f"{self._get_rank_emoji(i)} {nickname}")
            result.append(f"   💬 {user_stats.total_messages}条消息")
            result.append(f"   📝 {user_stats.total_chars}字")
        return "\n".join(result)

    @staticmethod
    def _get_rank_emoji(rank: int) -> str:
        if rank == 1:
            return "🥇"
        elif rank == 2:
            return "🥈"
        elif rank == 3:
            return "🥉"
        return f"{rank}."

@dataclass
class ModelConfig:
    api_key: str
    base_url: str
    trigger_words: list[str]
    price: int

class Dify(PluginBase):
    description = "Dify插件"
    author = "HenryXiaoYang"
    version = "1.2.1"

    def __init__(self):
        super().__init__()
        self.chat_manager = ChatRoomManager()
        self.user_models = {}  # 存储用户当前使用的模型
        try:
            with open("main_config.toml", "rb") as f:
                config = tomllib.load(f)
            self.admins = config["XYBot"]["admins"]
        except (FileNotFoundError, tomllib.TOMLDecodeError) as e:
            logger.error(f"加载主配置文件失败: {e}")
            raise

        try:
            with open("plugins/Dify/config.toml", "rb") as f:
                config = tomllib.load(f)
            plugin_config = config["Dify"]
            self.enable = plugin_config["enable"]
            self.default_model = plugin_config["default-model"]
            self.command_tip = plugin_config["command-tip"]
            self.commands = plugin_config["commands"]
            self.admin_ignore = plugin_config["admin_ignore"]
            self.whitelist_ignore = plugin_config["whitelist_ignore"]
            self.http_proxy = plugin_config["http-proxy"]
            self.voice_reply_all = plugin_config["voice_reply_all"]
            self.robot_names = plugin_config.get("robot-names", [])
            self.audio_to_text_url = plugin_config.get("audio-to-text-url", "")
            self.text_to_audio_url = plugin_config.get("text-to-audio-url", "")
            self.remember_user_model = plugin_config.get("remember_user_model", True)

            # 加载所有模型配置
            self.models = {}
            for model_name, model_config in plugin_config.get("models", {}).items():
                self.models[model_name] = ModelConfig(
                    api_key=model_config["api-key"],
                    base_url=model_config["base-url"],
                    trigger_words=model_config["trigger-words"],
                    price=model_config["price"]
                )
            
            # 设置当前使用的模型
            self.current_model = self.models[self.default_model]
        except (FileNotFoundError, tomllib.TOMLDecodeError) as e:
            logger.error(f"加载Dify插件配置文件失败: {e}")
            raise

        self.db = XYBotDB()
        self.image_cache = {}
        self.image_cache_timeout = 60
        # 添加文件存储目录配置
        self.files_dir = "files"
        # 创建文件存储目录
        os.makedirs(self.files_dir, exist_ok=True)

    def get_user_model(self, user_id: str) -> ModelConfig:
        """获取用户当前使用的模型"""
        if self.remember_user_model and user_id in self.user_models:
            return self.user_models[user_id]
        return self.current_model

    def set_user_model(self, user_id: str, model: ModelConfig):
        """设置用户当前使用的模型"""
        if self.remember_user_model:
            self.user_models[user_id] = model

    def get_model_from_message(self, content: str, user_id: str) -> tuple[ModelConfig, str, bool]:
        """根据消息内容判断使用哪个模型，并返回是否是切换模型的命令"""
        content = content.lower()
        # 检查是否是切换模型的命令
        if content.endswith("切换"):
            for model_name, model_config in self.models.items():
                for trigger in model_config.trigger_words:
                    if content.startswith(trigger.lower()):
                        self.set_user_model(user_id, model_config)
                        return model_config, "", True
            return self.get_user_model(user_id), content, False

        # 检查是否是临时使用其他模型
        for model_name, model_config in self.models.items():
            for trigger in model_config.trigger_words:
                if trigger.lower() in content:
                    query = content.replace(trigger.lower(), "").strip()
                    return model_config, query, False

        # 使用用户当前的模型
        return self.get_user_model(user_id), content, False

    async def check_and_notify_inactive_users(self, bot: WechatAPIClient):
        inactive_users = self.chat_manager.check_and_remove_inactive_users()
        for group_id, user_wxid, status in inactive_users:
            if status == "away":
                await bot.send_at_message(group_id, "\n" + CHAT_AUTO_AWAY_MESSAGE, [user_wxid])
            elif status == "timeout":
                await bot.send_at_message(group_id, "\n" + CHAT_TIMEOUT_MESSAGE, [user_wxid])

    async def process_buffered_messages(self, bot: WechatAPIClient, group_id: str, user_wxid: str):
        logger.debug(f"开始处理缓冲消息 - 用户: {user_wxid}, 群组: {group_id}")
        messages, files = self.chat_manager.get_and_clear_buffer(group_id, user_wxid)
        logger.debug(f"从缓冲区获取到的消息: {messages}")
        logger.debug(f"从缓冲区获取到的文件: {files}")
        
        if messages is not None and messages.strip():
            logger.debug(f"合并后的消息: {messages}")
            message = {
                "FromWxid": group_id,
                "SenderWxid": user_wxid,
                "Content": messages,
                "IsGroup": True,
                "MsgType": 1
            }
            logger.debug(f"准备检查积分")
            if await self._check_point(bot, message):
                logger.debug("积分检查通过，开始调用 Dify API")
                try:
                    await self.dify(bot, message, messages, files=files)
                    logger.debug("成功调用 Dify API 并发送消息")
                except Exception as e:
                    logger.error(f"调用 Dify API 失败: {e}")
                    logger.error(traceback.format_exc())
                    await bot.send_at_message(group_id, "\n消息处理失败，请稍后重试。", [user_wxid])
        else:
            logger.debug("缓冲区为空或消息无效，无需处理")

    async def _delayed_message_processing(self, bot: WechatAPIClient, group_id: str, user_wxid: str):
        key = (group_id, user_wxid)
        try:
            logger.debug(f"开始延迟处理 - 用户: {user_wxid}, 群组: {group_id}")
            await asyncio.sleep(MESSAGE_BUFFER_TIMEOUT)
            
            buffer = self.chat_manager.message_buffers.get(key)
            if buffer and buffer.messages:
                logger.debug(f"缓冲区消息数: {len(buffer.messages)}")
                logger.debug(f"最后消息时间: {time.time() - buffer.last_message_time:.2f}秒前")
                
                if time.time() - buffer.last_message_time >= MESSAGE_BUFFER_TIMEOUT:
                    logger.debug("开始处理缓冲消息")
                    await self.process_buffered_messages(bot, group_id, user_wxid)
                else:
                    logger.debug("跳过处理 - 有新消息，重新调度")
                    await self.schedule_message_processing(bot, group_id, user_wxid)
        except asyncio.CancelledError:
            logger.debug(f"定时器被取消 - 用户: {user_wxid}, 群组: {group_id}")
        except Exception as e:
            logger.error(f"处理消息缓冲区时出错: {e}")
            await bot.send_at_message(group_id, "\n消息处理发生错误，请稍后重试。", [user_wxid])

    async def schedule_message_processing(self, bot: WechatAPIClient, group_id: str, user_wxid: str):
        key = (group_id, user_wxid)
        if key not in self.chat_manager.message_buffers:
            self.chat_manager.message_buffers[key] = MessageBuffer()
        
        buffer = self.chat_manager.message_buffers[key]
        logger.debug(f"安排消息处理 - 用户: {user_wxid}, 群组: {group_id}")
        
        # 检查是否有最近的图片
        image_content = await self.get_cached_image(group_id)
        if image_content:
            try:
                logger.debug("发现最近的图片，准备上传到 Dify")
                file_id = await self.upload_file_to_dify(
                    image_content,
                    "image/jpeg",
                    group_id
                )
                if file_id:
                    logger.debug(f"图片上传成功，文件ID: {file_id}")
                    buffer.files.append(file_id)  # 直接添加到buffer的files列表
                    logger.debug(f"当前buffer中的文件: {buffer.files}")
                else:
                    logger.error("图片上传失败")
            except Exception as e:
                logger.error(f"处理图片失败: {e}")
        
        if buffer.message_count >= MAX_BUFFERED_MESSAGES:
            logger.debug("缓冲区已满，立即处理消息")
            await self.process_buffered_messages(bot, group_id, user_wxid)
            return
            
        if buffer.timer_task and not buffer.timer_task.done():
            logger.debug("取消已有定时器")
            buffer.timer_task.cancel()
        
        logger.debug("创建新定时器")
        buffer.timer_task = asyncio.create_task(
            self._delayed_message_processing(bot, group_id, user_wxid)
        )
        logger.debug(f"定时器任务已创建 - 用户: {user_wxid}")

    @on_text_message(priority=20)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

        content = message["Content"].strip()
        command = content.split(" ")[0] if content else ""

        await self.check_and_notify_inactive_users(bot)

        if not message["IsGroup"]:
            # 检查是否有最近的图片
            image_content = await self.get_cached_image(message["FromWxid"])
            files = []
            if image_content:
                try:
                    logger.debug("发现最近的图片，准备上传到 Dify")
                    file_id = await self.upload_file_to_dify(
                        image_content,
                        "image/jpeg",  # 根据实际图片类型调整
                        message["FromWxid"]
                    )
                    if file_id:
                        logger.debug(f"图片上传成功，文件ID: {file_id}")
                        files = [file_id]
                    else:
                        logger.error("图片上传失败")
                except Exception as e:
                    logger.error(f"处理图片失败: {e}")

            if command in self.commands:
                query = content[len(command):].strip()
            else:
                query = content
            if query and self.current_model.api_key:
                if await self._check_point(bot, message):
                    await self.dify(bot, message, query, files=files)
            return

        group_id = message["FromWxid"]
        user_wxid = message["SenderWxid"]
            
        if content == "退出聊天":
            if self.chat_manager.is_user_active(group_id, user_wxid):
                self.chat_manager.remove_user(group_id, user_wxid)
                await bot.send_at_message(group_id, "\n" + CHAT_LEAVE_MESSAGE, [user_wxid])
            return

        is_at = self.is_at_message(message)
        is_command = command in self.commands

        # 检查是否有最近的图片
        files = []
        image_content = await self.get_cached_image(group_id)
        if image_content:
            try:
                logger.debug("发现最近的图片，准备上传到 Dify")
                file_id = await self.upload_file_to_dify(
                    image_content,
                    "image/jpeg",
                    group_id
                )
                if file_id:
                    logger.debug(f"图片上传成功，文件ID: {file_id}")
                    files = [file_id]
                else:
                    logger.error("图片上传失败")
            except Exception as e:
                logger.error(f"处理图片失败: {e}")

        if not self.chat_manager.is_user_active(group_id, user_wxid):
            if is_at or is_command:
                self.chat_manager.add_user(group_id, user_wxid)
                await bot.send_at_message(group_id, "\n" + CHAT_JOIN_MESSAGE, [user_wxid])
                query = content
                for robot_name in self.robot_names:
                    query = query.replace(f"@{robot_name}", "").strip()
                if command in self.commands:
                    query = query[len(command):].strip()
                if query:
                    if await self._check_point(bot, message):
                        await self.dify(bot, message, query, files=files)
            return

        if content == "查看状态":
            status_msg = self.chat_manager.format_room_status(group_id)
            await bot.send_at_message(group_id, "\n" + status_msg, [user_wxid])
            return
        elif content == "暂时离开":
            self.chat_manager.set_user_status(group_id, user_wxid, UserStatus.AWAY)
            await bot.send_at_message(group_id, "\n" + CHAT_AWAY_MESSAGE, [user_wxid])
            return
        elif content == "回来了":
            self.chat_manager.set_user_status(group_id, user_wxid, UserStatus.ACTIVE)
            await bot.send_at_message(group_id, "\n" + CHAT_BACK_MESSAGE, [user_wxid])
            return
        elif content == "我的统计":
            try:
                nickname = await bot.get_nickname(user_wxid) or "未知用户"
            except:
                nickname = "未知用户"
            stats_msg = self.chat_manager.format_user_stats(group_id, user_wxid, nickname)
            await bot.send_at_message(group_id, "\n" + stats_msg, [user_wxid])
            return
        elif content == "聊天室排行":
            ranking_msg = await self.chat_manager.format_room_ranking(group_id, bot)
            await bot.send_at_message(group_id, "\n" + ranking_msg, [user_wxid])
            return

        self.chat_manager.update_user_activity(group_id, user_wxid)
        
        if self.chat_manager.get_user_status(group_id, user_wxid) == UserStatus.AWAY:
            self.chat_manager.set_user_status(group_id, user_wxid, UserStatus.ACTIVE)
            await bot.send_at_message(group_id, "\n" + CHAT_BACK_MESSAGE, [user_wxid])

        if content:
            if is_at or is_command:
                query = content
                for robot_name in self.robot_names:
                    query = query.replace(f"@{robot_name}", "").strip()
                if command in self.commands:
                    query = query[len(command):].strip()
                if query:
                    if await self._check_point(bot, message):
                        await self.dify(bot, message, query, files=files)
            else:
                await self.chat_manager.add_message_to_buffer(group_id, user_wxid, content, files)
                await self.schedule_message_processing(bot, group_id, user_wxid)
        return

    @on_at_message(priority=20)
    async def handle_at(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

        if not self.current_model.api_key:
            await bot.send_at_message(message["FromWxid"], "\n你还没配置Dify API密钥！", [message["SenderWxid"]])
            return False

        await self.check_and_notify_inactive_users(bot)

        content = message["Content"].strip()
        query = content
        for robot_name in self.robot_names:
            query = query.replace(f"@{robot_name}", "").strip()

        group_id = message["FromWxid"]
        user_wxid = message["SenderWxid"]

        if query == "退出聊天":
            if self.chat_manager.is_user_active(group_id, user_wxid):
                self.chat_manager.remove_user(group_id, user_wxid)
                await bot.send_at_message(group_id, "\n" + CHAT_LEAVE_MESSAGE, [user_wxid])
            return False

        if not self.chat_manager.is_user_active(group_id, user_wxid):
            self.chat_manager.add_user(group_id, user_wxid)
            await bot.send_at_message(group_id, "\n" + CHAT_JOIN_MESSAGE, [user_wxid])

        logger.debug(f"提取到的 query: {query}")

        if not query:
            await bot.send_at_message(message["FromWxid"], "\n请输入你的问题或指令。", [message["SenderWxid"]])
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

        if not self.current_model.api_key:
            await bot.send_text_message(message["FromWxid"], "你还没配置Dify API密钥！")
            return False

        query = await self.audio_to_text(bot, message)
        if not query:
            await bot.send_text_message(message["FromWxid"], VOICE_TRANSCRIPTION_FAILED)
            return False

        logger.debug(f"语音转文字结果: {query}")

        user_wxid = message["SenderWxid"]
        try:
            user_username = await bot.get_nickname(user_wxid) or "未知用户"
        except:
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
        """发送消息到Dify API"""
        if files is None:
            files = []

        # 根据消息内容选择模型
        model, processed_query, is_switch = self.get_model_from_message(query, message["SenderWxid"])
        
        # 如果是切换模型的命令
        if is_switch:
            model_name = next(name for name, config in self.models.items() if config == model)
            await bot.send_text_message(
                message["FromWxid"], 
                f"已切换到{model_name.upper()}模型，将一直使用该模型直到下次切换。"
            )
            return

        # 处理文件上传
        formatted_files = []
        for file_id in files:
            formatted_files.append({
                "type": "image",  # 修改为image类型
                "transfer_method": "local_file",
                "upload_file_id": file_id
            })

        try:
            logger.debug(f"开始调用 Dify API - 用户消息: {processed_query}")
            logger.debug(f"文件列表: {formatted_files}")
            conversation_id = self.db.get_llm_thread_id(message["FromWxid"], namespace="dify")
            headers = {"Authorization": f"Bearer {model.api_key}", "Content-Type": "application/json"}

            user_wxid = message["SenderWxid"]
            try:
                user_username = await bot.get_nickname(user_wxid) or "未知用户"
            except:
                user_username = "未知用户"

            inputs = {
                "user_wxid": user_wxid,
                "user_username": user_username
            }
            
            payload = {
                "inputs": inputs,
                "query": processed_query,
                "response_mode": "streaming",
                "conversation_id": conversation_id,
                "user": message["FromWxid"],
                "files": formatted_files,
                "auto_generate_name": False,
            }

            logger.debug(f"发送请求到 Dify - URL: {model.base_url}/chat-messages, Payload: {json.dumps(payload)}")
            ai_resp = ""
            async with aiohttp.ClientSession(proxy=self.http_proxy) as session:
                async with session.post(url=f"{model.base_url}/chat-messages", headers=headers, data=json.dumps(payload)) as resp:
                    if resp.status in (200, 201):
                        async for line in resp.content:
                            line = line.decode("utf-8").strip()
                            if not line or line == "event: ping":
                                continue
                            elif line.startswith("data: "):
                                line = line[6:]
                            try:
                                resp_json = json.loads(line)
                            except json.JSONDecodeError:
                                logger.error(f"Dify返回的JSON解析错误: {line}")
                                continue

                            event = resp_json.get("event", "")
                            if event == "message":
                                ai_resp += resp_json.get("answer", "")
                            elif event == "message_replace":
                                ai_resp = resp_json.get("answer", "")
                            elif event == "message_file":
                                file_url = resp_json.get("url", "")
                                await self.dify_handle_image(bot, message, file_url)
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
                        ai_resp = ai_resp.rstrip()
                        logger.debug(f"Dify响应: {ai_resp}")
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
            else:
                logger.warning("Dify未返回有效响应")
        except Exception as e:
            logger.error(f"Dify API 调用失败: {e}")
            await self.hendle_exceptions(bot, message)

    async def download_file(self, url: str) -> tuple[bytes, str]:
        """
        下载文件并返回文件内容和MIME类型
        """
        async with aiohttp.ClientSession(proxy=self.http_proxy) as session:
            async with session.get(url) as resp:
                content_type = resp.headers.get('Content-Type', '')
                return await resp.read(), content_type

    async def upload_file_to_dify(self, file_content: bytes, mime_type: str, user: str) -> Optional[str]:
        """
        上传文件到Dify并返回文件ID
        """
        try:
            # 验证并处理图片数据
            try:
                image = Image.open(io.BytesIO(file_content))
                # 转换为RGB模式(去除alpha通道)
                if image.mode in ('RGBA', 'LA'):
                    background = Image.new('RGB', image.size, (255, 255, 255))
                    background.paste(image, mask=image.split()[-1])
                    image = background
                # 保存为JPEG
                output = io.BytesIO()
                image.save(output, format='JPEG', quality=95)
                file_content = output.getvalue()
                mime_type = 'image/jpeg'
                logger.debug("图片格式转换成功")
            except Exception as e:
                logger.warning(f"图片格式转换失败: {e}")
                return None

            headers = {"Authorization": f"Bearer {self.current_model.api_key}"}
            formdata = aiohttp.FormData()
            formdata.add_field("file", file_content, 
                              filename=f"file.{mime_type.split('/')[-1]}", 
                              content_type=mime_type)
            formdata.add_field("user", user)

            url = f"{self.current_model.base_url}/files/upload"
            async with aiohttp.ClientSession(proxy=self.http_proxy) as session:
                async with session.post(url, headers=headers, data=formdata) as resp:
                    if resp.status in (200, 201):
                        result = await resp.json()
                        logger.debug(f"文件上传成功: {result}")
                        return result.get("id")
                    else:
                        error_text = await resp.text()
                        logger.error(f"文件上传失败: HTTP {resp.status} - {error_text}")
                        return None
        except Exception as e:
            logger.error(f"上传文件时发生错误: {e}")
            return None

    async def dify_handle_text(self, bot: WechatAPIClient, message: dict, text: str):
        # 匹配Dify返回的图片引用格式
        image_pattern = r'\[(.*?)\]\((.*?)\)'
        matches = re.findall(image_pattern, text)
        
        # 移除所有图片引用文本
        text = re.sub(image_pattern, '', text)
        
        # 先发送文字内容
        if text:
            if message["MsgType"] == 34 or self.voice_reply_all:
                await self.text_to_voice_message(bot, message, text)
            else:
                paragraphs = text.split("//n")
                for paragraph in paragraphs:
                    if paragraph.strip():
                        await bot.send_text_message(message["FromWxid"], paragraph.strip())
        
        # 如果有图片引用，只处理最后一个
        if matches:
            filename, url = matches[-1]  # 只取最后一个图片
            try:
                # 如果URL是相对路径,添加base_url
                if url.startswith('/files'):
                    # 移除base_url中可能的v1路径
                    base_url = self.current_model.base_url.replace('/v1', '')
                    url = f"{base_url}{url}"
                
                logger.debug(f"处理图片链接: {url}")
                headers = {"Authorization": f"Bearer {self.current_model.api_key}"}
                async with aiohttp.ClientSession(proxy=self.http_proxy) as session:
                    async with session.get(url, headers=headers) as resp:
                        if resp.status == 200:
                            image_data = await resp.read()
                            await bot.send_image_message(message["FromWxid"], image_data)
                        else:
                            logger.error(f"下载图片失败: HTTP {resp.status}")
                            await bot.send_text_message(message["FromWxid"], f"下载图片失败: HTTP {resp.status}")
            except Exception as e:
                logger.error(f"处理图片 {url} 失败: {e}")
                await bot.send_text_message(message["FromWxid"], f"处理图片失败: {str(e)}")

        # 处理其他类型的链接
        pattern = r"\]$$(https?:\/\/[^\s$$]+)\)"
        links = re.findall(pattern, text)
        for url in links:
            try:
                file = await self.download_file(url)
                extension = filetype.guess_extension(file)
                if extension in ('wav', 'mp3'):
                    await bot.send_voice_message(message["FromWxid"], voice=file, format=extension)
                elif extension in ('jpg', 'jpeg', "png", "gif", "bmp", "svg"):
                    await bot.send_image_message(message["FromWxid"], file)
                elif extension in ('mp4', 'avi', 'mov', 'mkv', 'flv'):
                    await bot.send_video_message(message["FromWxid"], video=file, image="None")
            except Exception as e:
                logger.error(f"下载文件 {url} 失败: {e}")
                await bot.send_text_message(message["FromWxid"], f"下载文件 {url} 失败")

        # 识别普通文件链接
        file_pattern = r'https?://[^\s<>"]+?/[^\s<>"]+\.(?:pdf|doc|docx|xls|xlsx|txt|zip|rar|7z|tar|gz)'
        file_links = re.findall(file_pattern, text)
        for url in file_links:
            await self.download_and_send_file(bot, message, url)

        pattern = r'\$\$[^$$]+\]\$\$https?:\/\/[^\s$$]+\)'
        text = re.sub(pattern, '', text)

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
            if self.db.get_points(wxid) < self.current_model.price:
                await bot.send_text_message(message["FromWxid"],
                                            XYBOT_PREFIX +
                                            INSUFFICIENT_POINTS_MESSAGE.format(price=self.current_model.price))
                return False
            self.db.add_points(wxid, -self.current_model.price)
            return True

    async def audio_to_text(self, bot: WechatAPIClient, message: dict) -> str:
        if not shutil.which("ffmpeg"):
            logger.error("未找到ffmpeg，请安装并配置到环境变量")
            await bot.send_text_message(message["FromWxid"], "服务器缺少ffmpeg，无法处理语音")
            return ""
        
        silk_file = "temp_audio.silk"
        mp3_file = "temp_audio.mp3"
        try:
            with open(silk_file, "wb") as f:
                f.write(message["Content"])

            command = f"ffmpeg -y -i {silk_file} -ar 16000 -ac 1 -f mp3 {mp3_file}"
            process = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
            if process.returncode != 0:
                logger.error(f"ffmpeg 执行失败: {process.stderr}")
                return ""

            if self.audio_to_text_url:
                headers = {"Authorization": f"Bearer {self.current_model.api_key}"}
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
                            if "failed" in text.lower() or "code" in text.lower():
                                logger.error(f"Dify API 返回错误: {text}")
                            else:
                                logger.info(f"语音转文字结果 (Dify API): {text}")
                                return text
                        else:
                            logger.error(f"audio-to-text 接口调用失败: {resp.status} - {await resp.text()}")

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
        except Exception as e:
            logger.error(f"语音处理失败: {e}")
            return ""
        finally:
            for temp_file in [silk_file, mp3_file, silk_file.replace('.silk', '.wav')]:
                if os.path.exists(temp_file):
                    os.remove(temp_file)

    async def text_to_voice_message(self, bot: WechatAPIClient, message: dict, text: str):
        try:
            url = self.text_to_audio_url if self.text_to_audio_url else f"{self.current_model.base_url}/text-to-audio"
            headers = {"Authorization": f"Bearer {self.current_model.api_key}", "Content-Type": "application/json"}
            data = {"text": text, "user": message["SenderWxid"]}
            async with aiohttp.ClientSession(proxy=self.http_proxy) as session:
                async with session.post(url, headers=headers, json=data) as resp:
                    if resp.status == 200:
                        audio = await resp.read()
                        await bot.send_voice_message(message["FromWxid"], voice=audio, format="mp3")
                    else:
                        logger.error(f"text-to-audio 接口调用失败: {resp.status} - {await resp.text()}")
                        await bot.send_text_message(message["FromWxid"], TEXT_TO_VOICE_FAILED)
        except Exception as e:
            logger.error(f"text-to-audio 接口调用异常: {e}")
            await bot.send_text_message(message["FromWxid"], f"{TEXT_TO_VOICE_FAILED}: {str(e)}")

    @on_image_message(priority=20)
    async def handle_image(self, bot: WechatAPIClient, message: dict):
        """处理图片消息"""
        if not self.enable:
            return

        try:
            # 解析XML获取图片信息
            xml_content = message.get("Content")
            if isinstance(xml_content, str):
                try:
                    # 从XML中提取base64图片数据
                    image_base64 = xml_content.split(',')[-1]  # 获取base64部分
                    # 转换base64为二进制
                    try:
                        image_content = base64.b64decode(image_base64)
                        # 验证是否为有效的图片数据
                        Image.open(io.BytesIO(image_content))
                        
                        self.image_cache[message["FromWxid"]] = {
                            "content": image_content,
                            "timestamp": time.time()
                        }
                        logger.debug(f"已缓存用户 {message['FromWxid']} 的图片")
                    except Exception as e:
                        logger.error(f"图片数据无效: {e}")
                except Exception as e:
                    logger.error(f"处理base64数据失败: {e}")
                    logger.debug(f"Base64数据: {image_base64[:100]}...")  # 只打印前100个字符
            else:
                logger.error("图片消息内容不是字符串格式")
            
        except Exception as e:
            logger.error(f"处理图片消息失败: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")

    async def get_cached_image(self, user_wxid: str) -> Optional[bytes]:
        """获取用户最近的图片"""
        if user_wxid in self.image_cache:
            cache_data = self.image_cache[user_wxid]
            if time.time() - cache_data["timestamp"] <= self.image_cache_timeout:
                try:
                    # 确保我们有有效的二进制数据
                    image_content = cache_data["content"]
                    if not isinstance(image_content, bytes):
                        logger.error("缓存的图片内容不是二进制格式")
                        del self.image_cache[user_wxid]
                        return None
                    
                    # 尝试验证图片数据
                    try:
                        Image.open(io.BytesIO(image_content))
                    except Exception as e:
                        logger.error(f"缓存的图片数据无效: {e}")
                        del self.image_cache[user_wxid]
                        return None
                    
                    # 清除缓存
                    del self.image_cache[user_wxid]
                    return image_content
                except Exception as e:
                    logger.error(f"处理缓存图片失败: {e}")
                    del self.image_cache[user_wxid]
                    return None
            else:
                # 超时清除
                del self.image_cache[user_wxid]
        return None

    async def download_and_send_file(self, bot: WechatAPIClient, message: dict, url: str):
        """下载并发送文件"""
        try:
            # 从URL中获取文件名
            parsed_url = urllib.parse.urlparse(url)
            filename = os.path.basename(parsed_url.path)
            if not filename:
                filename = "downloaded_file"
            
            logger.debug(f"开始下载文件: {url}")
            async with aiohttp.ClientSession(proxy=self.http_proxy) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        await bot.send_text_message(message["FromWxid"], f"下载文件失败: HTTP {resp.status}")
                        return
                    
                    content = await resp.read()
                    
                    # 检测文件类型
                    kind = filetype.guess(content)
                    if kind is None:
                        # 如果无法检测文件类型,尝试从Content-Type或URL获取
                        content_type = resp.headers.get('Content-Type', '')
                        ext = mimetypes.guess_extension(content_type) or os.path.splitext(filename)[1]
                        if not ext:
                            await bot.send_text_message(message["FromWxid"], f"无法识别文件类型: {filename}")
                            return
                    else:
                        ext = f".{kind.extension}"
                        
                    # 确保文件名有扩展名
                    if not os.path.splitext(filename)[1]:
                        filename = f"{filename}{ext}"
                        
                    # 根据文件类型发送不同类型的消息
                    if ext.lower() in ['.jpg', '.jpeg', '.png', '.gif', '.bmp']:
                        await bot.send_image_message(message["FromWxid"], content)
                    elif ext.lower() in ['.mp3', '.wav', '.ogg', 'm4a']:
                        await bot.send_voice_message(message["FromWxid"], voice=content, format=ext[1:])
                    elif ext.lower() in ['.mp4', '.avi', '.mov', '.mkv']:
                        await bot.send_video_message(message["FromWxid"], video=content, image="None")
                    else:
                        # 其他类型文件，发送文件内容
                        await bot.send_text_message(message["FromWxid"], f"文件名: {filename}\n内容长度: {len(content)} 字节")
                    
                    logger.debug(f"文件 {filename} 发送成功")
                    
        except Exception as e:
            logger.error(f"下载或发送文件失败: {e}")
            await bot.send_text_message(message["FromWxid"], f"处理文件失败: {str(e)}")

    @on_file_message(priority=20)
    async def handle_file(self, bot: WechatAPIClient, message: dict):
        """处理文件消息"""
        if not self.enable:
            return

        temp_path = None
        saved_path = None
        try:
            # 解析XML
            xml_content = message.get("Content")
            if not xml_content:
                return
                
            root = ET.fromstring(xml_content)
            appmsg = root.find("appmsg")
            if appmsg is None:
                return
                
            # 获取文件信息
            title = appmsg.find("title").text if appmsg.find("title") is not None else ""
            file_ext = ""
            attachinfo = appmsg.find("appattach")
            if attachinfo is not None:
                file_ext = attachinfo.find("fileext").text if attachinfo.find("fileext") is not None else ""
                attachid = attachinfo.find("attachid").text if attachinfo.find("attachid") is not None else ""
                totallen = attachinfo.find("totallen")
                file_size = int(totallen.text) if totallen is not None else 0
                
            if not title or not attachid:
                logger.error("文件信息不完整")
                return
                
            # 检查文件大小限制 (设置为10MB)
            MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB in bytes
            if file_size > MAX_FILE_SIZE:
                logger.error(f"文件过大: {file_size} 字节 (最大限制: {MAX_FILE_SIZE} 字节)")
                await bot.send_text_message(message["FromWxid"], 
                    f"文件过大（{file_size/1024/1024:.1f}MB），超出上传限制（{MAX_FILE_SIZE/1024/1024:.1f}MB）。\n"
                    f"请压缩文件或分割后重试。")
                return
                
            logger.info(f"检测到文件: {title}, 类型: {file_ext}, 附件ID: {attachid}, 大小: {file_size/1024/1024:.1f}MB")
            
            try:
                # 下载文件
                logger.info(f"开始下载文件，总大小: {file_size} 字节")
                file_content = await self.download_large_file(bot, attachid, file_size)
                if not file_content:
                    logger.error("文件下载失败")
                    await bot.send_text_message(message["FromWxid"], "文件下载失败，请重试。")
                    return

                # 验证文件大小
                actual_size = len(file_content)
                if actual_size != file_size:
                    logger.error(f"文件大小不匹配: 预期 {file_size} 字节, 实际 {actual_size} 字节")
                    await bot.send_text_message(message["FromWxid"], 
                        f"文件下载不完整。\n"
                        f"预期大小：{file_size/1024:.1f}KB\n"
                        f"实际大小：{actual_size/1024:.1f}KB\n"
                        f"请重试或联系管理员。")
                    return

                # 保存文件
                filename = title
                if not os.path.splitext(filename)[1] and file_ext:
                    filename = f"{filename}.{file_ext}"
                    
                # 临时文件路径
                temp_path = os.path.join("temp", filename)
                os.makedirs("temp", exist_ok=True)
                
                # 永久存储路径
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                saved_filename = f"{os.path.splitext(filename)[0]}_{timestamp}{os.path.splitext(filename)[1]}"
                saved_path = os.path.join(self.files_dir, saved_filename)
                
                try:
                    # 先写入临时文件
                    with open(temp_path, "wb") as f:
                        f.write(file_content)
                    
                    # 验证临时文件大小
                    temp_size = os.path.getsize(temp_path)
                    if temp_size != actual_size:
                        raise Exception(f"临时文件大小不匹配: 预期 {actual_size} 字节, 实际 {temp_size} 字节")
                    
                    # 验证成功后移动到永久存储目录
                    shutil.move(temp_path, saved_path)
                    
                    # 再次验证永久文件大小
                    saved_size = os.path.getsize(saved_path)
                    if saved_size != actual_size:
                        raise Exception(f"保存的文件大小不匹配: 预期 {actual_size} 字节, 实际 {saved_size} 字节")
                        
                    logger.info(f"文件已成功保存到: {saved_path}")
                    logger.info(f"文件大小: {saved_size} 字节")
                except Exception as e:
                    logger.error(f"保存文件失败: {e}")
                    # 清理可能存在的不完整文件
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    if os.path.exists(saved_path):
                        os.remove(saved_path)
                    await bot.send_text_message(message["FromWxid"], f"保存文件失败: {str(e)}")
                    return
                
                # 上传文件到 Dify
                dify_file_id = None
                try:
                    # 准备上传请求
                    headers = {"Authorization": f"Bearer {self.current_model.api_key}"}
                    formdata = aiohttp.FormData()
                    formdata.add_field("file", file_content,
                                    filename=filename,
                                    content_type=mimetypes.guess_type(filename)[0] or 'application/octet-stream')
                    formdata.add_field("user", message["FromWxid"])

                    # 上传文件
                    url = f"{self.current_model.base_url}/files/upload"
                    async with aiohttp.ClientSession(proxy=self.http_proxy) as session:
                        async with session.post(url, headers=headers, data=formdata) as resp:
                            response_text = await resp.text()
                            if resp.status in (200, 201):
                                result = await resp.json()
                                dify_file_id = result.get("id")
                                if dify_file_id:
                                    logger.info(f"文件上传成功 - ID: {dify_file_id}")
                                    
                                    # 根据文件扩展名确定文件类型
                                    file_type = "document"  # 默认类型为document
                                    ext = file_ext.lower()
                                    if ext in ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg']:
                                        file_type = "image"
                                    elif ext in ['mp3', 'm4a', 'wav', 'webm', 'amr']:
                                        file_type = "audio"
                                    elif ext in ['mp4', 'mov', 'mpeg', 'mpga']:
                                        file_type = "video"
                                    elif ext in ['txt', 'md', 'markdown', 'pdf', 'html', 'xlsx', 'xls', 'docx', 'csv', 'eml', 'msg', 'pptx', 'ppt', 'xml', 'epub']:
                                        file_type = "document"
                                    else:
                                        file_type = "document"  # 其他类型都作为document处理
                                    
                                    chat_payload = {
                                        "inputs": {},
                                        "query": f"请分析这个文件的内容：{filename}",
                                        "response_mode": "streaming",
                                        "user": message["FromWxid"],
                                        "files": [{
                                            "type": file_type,
                                            "transfer_method": "local_file",
                                            "upload_file_id": dify_file_id
                                        }]
                                    }
                                    
                                    # 发送聊天消息
                                    chat_url = f"{self.current_model.base_url}/chat-messages"
                                    async with session.post(chat_url, headers=headers, json=chat_payload) as chat_resp:
                                        if chat_resp.status == 200:
                                            ai_resp = ""
                                            async for line in chat_resp.content:
                                                line = line.decode("utf-8").strip()
                                                if not line or line == "event: ping":
                                                    continue
                                                elif line.startswith("data: "):
                                                    line = line[6:]
                                                try:
                                                    resp_json = json.loads(line)
                                                    event = resp_json.get("event", "")
                                                    if event == "message":
                                                        ai_resp += resp_json.get("answer", "")
                                                    elif event == "message_end":
                                                        break
                                                except json.JSONDecodeError:
                                                    continue
                                            
                                            await bot.send_text_message(message["FromWxid"], 
                                                f"文件上传成功！\n"
                                                f"文件名: {filename}\n"
                                                f"文件ID: {dify_file_id}\n"
                                                f"本地保存位置: {saved_path}\n\n"
                                                f"AI 分析结果：\n{ai_resp}")
                                        else:
                                            error_text = await chat_resp.text()
                                            logger.error(f"发送聊天消息失败: {error_text}")
                                            await bot.send_text_message(message["FromWxid"], 
                                                f"文件已上传，但分析失败。\n"
                                                f"文件ID: {dify_file_id}\n"
                                                f"本地保存位置: {saved_path}")
                                else:
                                    logger.error(f"文件上传成功但未返回ID: {response_text}")
                                    await bot.send_text_message(message["FromWxid"], 
                                        f"文件上传成功但未获取到ID\n"
                                        f"本地保存位置: {saved_path}")
                            elif resp.status == 400:
                                error_info = json.loads(response_text)
                                error_msg = {
                                    "no_file_uploaded": "未提供文件",
                                    "too_many_files": "一次只能上传一个文件",
                                    "unsupported_preview": "该文件不支持预览",
                                    "unsupported_estimate": "该文件不支持估算"
                                }.get(error_info.get("error"), "未知错误")
                                logger.error(f"上传失败: {error_msg}")
                                await bot.send_text_message(message["FromWxid"], 
                                    f"文件上传失败: {error_msg}")
                            elif resp.status == 413:
                                logger.error("文件太大")
                                await bot.send_text_message(message["FromWxid"], 
                                    "文件太大，无法上传")
                            elif resp.status == 415:
                                logger.error("不支持的文件类型")
                                await bot.send_text_message(message["FromWxid"], 
                                    "不支持的文件类型")
                            elif resp.status == 503:
                                error_info = json.loads(response_text)
                                error_msg = {
                                    "s3_connection_failed": "无法连接到存储服务",
                                    "s3_permission_denied": "无权限上传文件",
                                    "s3_file_too_large": "文件超出大小限制"
                                }.get(error_info.get("error"), "存储服务异常")
                                logger.error(f"存储服务错误: {error_msg}")
                                await bot.send_text_message(message["FromWxid"], 
                                    f"文件上传失败: {error_msg}")
                            else:
                                logger.error(f"上传失败: HTTP {resp.status} - {response_text}")
                                await bot.send_text_message(message["FromWxid"], 
                                    f"文件上传失败\n"
                                    f"状态码: {resp.status}\n"
                                    f"错误信息: {response_text}")
                except Exception as e:
                    logger.error(f"上传文件时发生错误: {e}")
                    logger.error(traceback.format_exc())
                    await bot.send_text_message(message["FromWxid"], f"处理文件失败: {str(e)}")
                
                # 根据文件类型处理预览
                if file_ext.lower() in ['jpg', 'jpeg', 'png', 'gif', 'bmp']:
                    # 验证图片文件完整性
                    try:
                        with open(saved_path, 'rb') as f:
                            Image.open(io.BytesIO(f.read()))
                        await bot.send_image_message(message["FromWxid"], file_content)
                    except Exception as e:
                        logger.error(f"图片文件验证失败: {e}")
                        raise
                elif file_ext.lower() in ['mp3', 'wav', 'ogg', 'm4a']:
                    await bot.send_voice_message(message["FromWxid"], voice=file_content, format=file_ext)
                elif file_ext.lower() in ['mp4', 'avi', 'mov', 'mkv']:
                    await bot.send_video_message(message["FromWxid"], video=file_content, image="None")
                
                # 如果是图片，添加到缓存
                if file_ext.lower() in ['jpg', 'jpeg', 'png', 'gif', 'bmp']:
                    self.image_cache[message["FromWxid"]] = {
                        "content": file_content,
                        "timestamp": time.time()
                    }
                
            except Exception as e:
                logger.error(f"处理文件失败: {e}")
                logger.error(traceback.format_exc())
                await bot.send_text_message(message["FromWxid"], f"处理文件失败: {str(e)}")
                # 如果保存失败，清理已创建的文件
                if saved_path and os.path.exists(saved_path):
                    try:
                        os.remove(saved_path)
                    except Exception as cleanup_error:
                        logger.error(f"清理失败的文件时出错: {cleanup_error}")
                
        except Exception as e:
            logger.error(f"解析文件消息失败: {e}")
            logger.error(traceback.format_exc())
        finally:
            # 只清理临时文件，保留永久存储的文件
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception as e:
                    logger.error(f"清理临时文件失败: {e}")

    async def download_large_file(self, bot: WechatAPIClient, attachid: str, total_size: int) -> Optional[bytes]:
        """分块下载大文件"""
        try:
            logger.info(f"开始下载文件，总大小: {total_size} 字节")
            # 尝试直接下载整个文件
            file_content = await bot.download_attach(attachid)
            
            if not file_content:
                logger.error("文件下载失败")
                return None
                
            # 处理返回的数据
            if isinstance(file_content, str):
                try:
                    if file_content.startswith('data:'):
                        _, file_content = file_content.split(',', 1)
                        file_content = base64.b64decode(file_content)
                    elif file_content.startswith('b\'') or file_content.startswith('b"'):
                        file_content = eval(file_content)
                    else:
                        file_content = base64.b64decode(file_content)
                except Exception as e:
                    logger.error(f"转换文件数据失败: {e}")
                    file_content = file_content.encode('utf-8')
            
            if not isinstance(file_content, bytes):
                logger.error(f"文件数据类型错误: {type(file_content)}")
                return None
                
            actual_size = len(file_content)
            if actual_size != total_size:
                logger.error(f"文件大小不匹配: 预期 {total_size} 字节, 实际 {actual_size} 字节")
                return None
                
            logger.info(f"文件下载完成: {actual_size} 字节")
            return file_content
            
        except Exception as e:
            logger.error(f"下载文件时发生错误: {e}")
            logger.error(traceback.format_exc())
            return None
