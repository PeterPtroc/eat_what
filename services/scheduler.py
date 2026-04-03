from __future__ import annotations
"""
定时推荐调度器

在指定时间向已订阅群组推送随机菜品推荐。
"""
import asyncio
import json
import os
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Dict, List, Optional

from astrbot.api import logger
from astrbot.api.event import MessageChain

if TYPE_CHECKING:
    from astrbot.api.star import Context
    from .dish_service import DishStorageService

_SCHEDULE_FILE = "scheduled_groups.json"

DEFAULT_TIMES = [time(11, 50), time(17, 20)]


def _parse_time(s: str) -> time:
    """解析 HH:MM 格式的时间字符串"""
    parts = s.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"时间格式错误: {s}，请使用 HH:MM 格式")
    h, m = int(parts[0]), int(parts[1])
    return time(h, m)


def _fmt_time(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


class ScheduledRecommender:
    def __init__(
        self,
        context: "Context",
        dish_service: "DishStorageService",
        base_dir: str,
    ):
        self._context = context
        self._dish_service = dish_service
        self._file_path = os.path.join(base_dir, _SCHEDULE_FILE)
        # group_id -> 推荐时间列表
        self._groups: Dict[str, List[time]] = {}
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._load()

    # ---------- 持久化 ----------
    def _load(self):
        if not os.path.exists(self._file_path):
            return
        try:
            with open(self._file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 兼容旧格式 {"groups": ["123", ...]}
            if "groups" in data and isinstance(data["groups"], list) and data["groups"] and isinstance(data["groups"][0], str):
                self._groups = {gid: list(DEFAULT_TIMES) for gid in data["groups"]}
            else:
                # 新格式 {"schedules": {"group_id": ["11:50", "17:20"]}}
                for gid, times_str in data.get("schedules", {}).items():
                    self._groups[gid] = [_parse_time(t) for t in times_str]
            logger.info(f"已加载 {len(self._groups)} 个定时推荐群组")
        except Exception as e:
            logger.error(f"读取定时推荐配置失败: {e}")

    def _save(self):
        tmp = self._file_path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self._file_path) or ".", exist_ok=True)
            schedules = {
                gid: [_fmt_time(t) for t in times]
                for gid, times in sorted(self._groups.items())
            }
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"schedules": schedules}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._file_path)
        except Exception as e:
            logger.error(f"保存定时推荐配置失败: {e}")

    # ---------- 群组管理 ----------
    def add_group(self, group_id: str, times: Optional[List[time]] = None) -> str:
        schedule = times or list(DEFAULT_TIMES)
        if group_id in self._groups:
            # 已存在则更新时间
            self._groups[group_id] = schedule
            self._save()
            self._restart()
            desc = "、".join(_fmt_time(t) for t in schedule)
            return f"已更新定时推荐时间为: {desc}"
        self._groups[group_id] = schedule
        self._save()
        self._ensure_running()
        desc = "、".join(_fmt_time(t) for t in schedule)
        return f"已开启定时推荐，每天 {desc} 推荐菜品"

    def remove_group(self, group_id: str) -> str:
        if group_id not in self._groups:
            return "该群未开启定时推荐"
        del self._groups[group_id]
        self._save()
        if not self._groups:
            self.stop()
        else:
            self._restart()
        return "已关闭定时推荐"

    def get_schedule_info(self, group_id: str) -> Optional[str]:
        times = self._groups.get(group_id)
        if not times:
            return None
        return "、".join(_fmt_time(t) for t in times)

    # ---------- 调度 ----------
    def start(self):
        if self._groups:
            self._ensure_running()

    def _ensure_running(self):
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(self._loop())

    def _restart(self):
        self.stop()
        self.start()

    def stop(self):
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _loop(self):
        try:
            while not self._stop_event.is_set():
                wait, due_groups = self._next_fire()
                if wait <= 0:
                    wait = 60
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=wait
                    )
                    break
                except asyncio.TimeoutError:
                    pass

                # 重新计算哪些群到时间了（避免 sleep 漂移）
                _, due_groups = self._next_fire()
                if due_groups:
                    await self._send_recommendations(due_groups)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"定时推荐循环异常: {e}")

    def _next_fire(self) -> tuple[float, list[str]]:
        """计算距离下一次推荐的秒数，以及到期的群列表"""
        now = datetime.now()
        earliest = None
        # 收集所有群的所有时间点，找最近的
        for gid, times in self._groups.items():
            for t in times:
                dt = datetime.combine(now.date(), t)
                if dt <= now:
                    dt += timedelta(days=1)
                if earliest is None or dt < earliest:
                    earliest = dt

        if earliest is None:
            return 60.0, []

        wait = (earliest - now).total_seconds()

        # 找出在 earliest 这一分钟内需要推荐的群
        due = []
        for gid, times in self._groups.items():
            for t in times:
                dt = datetime.combine(now.date(), t)
                if dt <= now:
                    dt += timedelta(days=1)
                if abs((dt - earliest).total_seconds()) < 60:
                    due.append(gid)
                    break

        return wait, due

    def _current_slot(self) -> str:
        hour = datetime.now().hour
        if 8 <= hour < 14:
            return "中午"
        return "晚上"

    async def _send_recommendations(self, group_ids: list[str]):
        slot = self._current_slot()
        for group_id in group_ids:
            try:
                dish = await self._dish_service.get_random(None, slot)
                if not dish:
                    continue
                text = f"[定时推荐] {slot}吃{dish.location}的{dish.name}怎么样？"
                session_str = f"default:GroupMessage:{group_id}"
                chain = MessageChain().message(text)
                await self._context.send_message(session_str, chain)
                logger.info(f"向群 {group_id} 推荐: {dish.name}")
            except Exception as e:
                logger.error(f"向群 {group_id} 发送推荐失败: {e}")
