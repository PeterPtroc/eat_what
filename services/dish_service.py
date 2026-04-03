from __future__ import annotations
"""
菜品增删与随机选择服务
"""
import asyncio
import json
import os
import random
from typing import Dict, List, Optional
from datetime import datetime
from collections import defaultdict

from astrbot.api import logger

from ..models.dish import Dish, VALID_TIME_SLOTS

_DEFAULT_FILE = "dishes.json"


class DishStorageService:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.file_path = os.path.join(base_dir, _DEFAULT_FILE)
        self._lock = asyncio.Lock()
        self._dishes: Dict[str, Dish] = {}
        self._load_sync()

    # ---------- internal helpers ----------
    def _load_sync(self):
        """同步加载，仅在 __init__ 中使用"""
        if not os.path.exists(self.file_path):
            self._dishes = {}
            return
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._dishes = {
                item["name"]: Dish.from_dict(item)
                for item in data
                if isinstance(item, dict) and item.get("name")
            }
            logger.info(f"加载本地菜品 {len(self._dishes)} 条")
        except Exception as e:
            logger.error(f"读取菜品文件失败: {e}")
            self._dishes = {}

    def _persist_sync(self):
        """同步持久化（在锁内调用）"""
        tmp_path = self.file_path + ".tmp"
        data = [dish.to_dict() for dish in self._dishes.values()]
        try:
            os.makedirs(os.path.dirname(self.file_path) or ".", exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.file_path)
        except Exception as e:
            logger.error(f"写入菜品文件失败: {e}")

    # ---------- public API ----------
    async def add_dish(self, name: str, location: str, time_slot: str) -> str:
        name = name.strip()
        location = location.strip()
        time_slot = time_slot.strip() if time_slot else ""
        if not name or not location or not time_slot:
            return "参数不完整，格式: /增加菜品 名称 地点 时间(中午/晚上)"
        if time_slot not in VALID_TIME_SLOTS:
            return "时间只能为 中午 或 晚上"
        async with self._lock:
            if name in self._dishes:
                dish = self._dishes[name]
                if time_slot in dish.times:
                    return "已存在该菜品且时间段已包含，无需重复添加"
                dish.times.append(time_slot)
                self._persist_sync()
                return f"已为 {name} 增加时间段 {time_slot}"
            dish = Dish(name=name, location=location, times=[time_slot])
            self._dishes[name] = dish
            self._persist_sync()
            return f"已添加菜品 {location} 的 {name} ({time_slot})"

    async def remove_dish(self, name: str) -> str:
        name = name.strip()
        if not name:
            return "请输入要删除的菜品名称"
        async with self._lock:
            if name not in self._dishes:
                return "未找到该菜品"
            del self._dishes[name]
            self._persist_sync()
            return f"已删除菜品 {name}"

    async def get_random(
        self, location: Optional[str], current_slot: Optional[str]
    ) -> Optional[Dish]:
        async with self._lock:
            candidates: List[Dish] = list(self._dishes.values())
            if location:
                location = location.strip()
                candidates = [d for d in candidates if location in d.location]
            if current_slot in VALID_TIME_SLOTS:
                candidates = [d for d in candidates if current_slot in d.times]
            if not candidates:
                return None
            return random.choice(candidates)

    async def count(self) -> int:
        async with self._lock:
            return len(self._dishes)

    async def list_all(self, location: Optional[str] = None) -> List[Dish]:
        async with self._lock:
            dishes = list(self._dishes.values())
            if location:
                location = location.strip()
                dishes = [d for d in dishes if location in d.location]
            return dishes

    async def list_grouped(
        self, location: Optional[str] = None
    ) -> Dict[str, List[Dish]]:
        """按地点分组返回菜品"""
        dishes = await self.list_all(location)
        grouped: Dict[str, List[Dish]] = defaultdict(list)
        for d in dishes:
            grouped[d.location].append(d)
        return dict(grouped)

    async def export_json(self) -> str:
        """导出全部菜品为 JSON 字符串"""
        async with self._lock:
            data = [dish.to_dict() for dish in self._dishes.values()]
            return json.dumps(data, ensure_ascii=False, indent=2)

    async def import_json(self, text: str) -> str:
        """从 JSON 字符串导入菜品，与现有数据合并

        返回操作结果摘要。
        """
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            return f"JSON 解析失败: {e}"

        if not isinstance(data, list):
            return "格式错误: 需要一个 JSON 数组"

        added = 0
        updated = 0
        skipped = 0
        errors = []

        async with self._lock:
            for i, item in enumerate(data):
                if not isinstance(item, dict):
                    errors.append(f"第 {i+1} 项不是对象，已跳过")
                    skipped += 1
                    continue
                try:
                    dish = Dish.from_dict(item)
                except (ValueError, KeyError) as e:
                    errors.append(f"第 {i+1} 项无效: {e}")
                    skipped += 1
                    continue

                if dish.name in self._dishes:
                    existing = self._dishes[dish.name]
                    new_times = [t for t in dish.times if t not in existing.times]
                    if new_times:
                        existing.times.extend(new_times)
                        updated += 1
                    else:
                        skipped += 1
                else:
                    self._dishes[dish.name] = dish
                    added += 1

            self._persist_sync()

        parts = []
        if added:
            parts.append(f"新增 {added}")
        if updated:
            parts.append(f"更新 {updated}")
        if skipped:
            parts.append(f"跳过 {skipped}")
        summary = "导入完成: " + "、".join(parts) if parts else "无有效数据"
        if errors:
            summary += "\n" + "\n".join(errors[:5])
        return summary


# 时间段判断

def current_time_slot(now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    hour = now.hour
    if 8 <= hour < 14:
        return "中午"
    return "晚上"
