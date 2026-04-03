"""
今天吃什么？
"""

from typing import Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .services.dish_service import DishStorageService, current_time_slot
from .services.scheduler import ScheduledRecommender, _parse_time
from .models.dish import VALID_TIME_SLOTS


@register("eat", "AstrBot", "今天吃什么", "0.1.0")
class EatPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._dish_storage: Optional[DishStorageService] = None
        self._scheduler: Optional[ScheduledRecommender] = None

    async def initialize(self):
        try:
            data_dir = StarTools.get_data_dir()
            data_dir.mkdir(parents=True, exist_ok=True)
            base_dir = str(data_dir)
            self._dish_storage = DishStorageService(base_dir)
            self._scheduler = ScheduledRecommender(
                self.context, self._dish_storage, base_dir
            )
            self._scheduler.start()
            logger.info("本地菜品插件初始化完成")
        except Exception as e:
            logger.error(f"初始化失败: {e}")

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        for attr in ("is_superuser", "is_admin", "is_owner"):
            if hasattr(event, attr) and getattr(event, attr):
                return True
        return False

    def _get_group_id(self, event: AstrMessageEvent) -> Optional[str]:
        """从事件中提取群组 ID"""
        for attr in ("group_id", "chat_id", "channel_id"):
            val = getattr(event, attr, None)
            if val:
                return str(val)
        return None

    def _extract_args(self, event: AstrMessageEvent, command: str) -> str:
        """从原始消息中提取命令后的参数文本"""
        raw = getattr(event, "message_str", "") or getattr(event, "raw_message", "")
        if not raw:
            return ""
        for prefix in (f"/{command}", command):
            idx = raw.find(prefix)
            if idx != -1:
                return raw[idx + len(prefix):].strip()
        return ""

    # ──────────────── 菜品管理 ────────────────

    @filter.command("增加菜品")
    async def add_or_extend_dish(
        self,
        event: AstrMessageEvent,
        name: str = "",
        location: str = "",
        time_slot: str = "",
    ):
        """添加新菜品或为已存在菜品追加时间段

        用法: /增加菜品 名称 地点 时间(中午/晚上)
        """
        if not self._dish_storage:
            yield event.plain_result("菜品存储未就绪")
            return

        usage = "用法: /增加菜品 名称 地点 时间(中午/晚上)"
        example = "示例: /增加菜品 大勺炒饭 小西门 晚上"

        missing = []
        if not name:
            missing.append("name(菜名)")
        if not location:
            missing.append("location(地点)")
        if not time_slot:
            missing.append("time_slot(时间段)")

        if missing:
            if len(missing) == 3:
                yield event.plain_result(
                    "需要三个参数: 名称 地点 时间(中午/晚上)\n" f"{usage}\n{example}"
                )
                return
            yield event.plain_result(
                "缺少参数: "
                + ", ".join(missing)
                + "\n"
                + f"{usage}\n{example}"
            )
            return

        ts = time_slot.strip()
        if ts not in VALID_TIME_SLOTS:
            yield event.plain_result(
                "时间段只能为: " + "/".join(VALID_TIME_SLOTS)
            )
            return

        msg = await self._dish_storage.add_dish(name, location, ts)
        yield event.plain_result(msg)

    @filter.command("删除菜品")
    async def delete_dish(self, event: AstrMessageEvent, name: str):
        """删除指定菜品 (管理员限定)

        用法: /删除菜品 名称
        """
        if not self._is_admin(event):
            yield event.plain_result("只有管理员可删除菜品")
            return
        if not self._dish_storage:
            yield event.plain_result("菜品存储未就绪")
            return
        yield event.plain_result(await self._dish_storage.remove_dish(name))

    # ──────────────── 查询与推荐 ────────────────

    @filter.command("吃什么")
    async def what_to_eat(self, event: AstrMessageEvent, location: str = ""):
        """按当前时间段随机推荐菜品，可选限定地点

        用法: /吃什么 [地点]
        """
        if not self._dish_storage:
            yield event.plain_result("菜品存储未就绪")
            return
        slot = current_time_slot()
        dish = await self._dish_storage.get_random(
            location if location else None, slot
        )
        if not dish:
            tip = (
                "先用 /增加菜品 添加吧"
                if not location
                else f"地点 {location} 下暂无菜品"
            )
            yield event.plain_result(f"没找到合适的菜品，{tip}")
            return
        yield event.plain_result(
            f"{'/'.join(dish.times)}吃{dish.location}的{dish.name} "
        )

    @filter.command("菜单")
    async def show_menu(self, event: AstrMessageEvent, location: str = ""):
        """查看所有菜品，可选按地点筛选

        用法: /菜单 [地点]
        """
        if not self._dish_storage:
            yield event.plain_result("菜品存储未就绪")
            return

        grouped = await self._dish_storage.list_grouped(
            location if location else None
        )
        if not grouped:
            if location:
                yield event.plain_result(f"地点 {location} 下暂无菜品")
            else:
                yield event.plain_result("还没有添加任何菜品，先用 /增加菜品 添加吧")
            return

        lines = []
        for loc, dishes in grouped.items():
            lines.append(f"📍 {loc}:")
            for d in dishes:
                lines.append(f"  - {d.name} ({'/'.join(d.times)})")
        total = sum(len(ds) for ds in grouped.values())
        lines.append(f"\n共 {total} 道菜品")
        yield event.plain_result("\n".join(lines))

    # ──────────────── 数据导入导出 ────────────────

    @filter.command("导出菜品")
    async def export_dishes(self, event: AstrMessageEvent):
        """导出全部菜品数据为 JSON (管理员限定)

        用法: /导出菜品
        """
        if not self._is_admin(event):
            yield event.plain_result("只有管理员可导出菜品")
            return
        if not self._dish_storage:
            yield event.plain_result("菜品存储未就绪")
            return
        count = await self._dish_storage.count()
        if count == 0:
            yield event.plain_result("菜品库为空，无数据可导出")
            return
        data = await self._dish_storage.export_json()
        yield event.plain_result(f"菜品数据 ({count} 条):\n{data}")

    @filter.command("导入菜品")
    async def import_dishes(self, event: AstrMessageEvent):
        """从 JSON 导入菜品数据 (管理员限定)

        用法: /导入菜品 <JSON数据>
        支持单行或多行 JSON
        """
        if not self._is_admin(event):
            yield event.plain_result("只有管理员可导入菜品")
            return
        if not self._dish_storage:
            yield event.plain_result("菜品存储未就绪")
            return

        json_text = self._extract_args(event, "导入菜品")
        if not json_text:
            yield event.plain_result(
                "请在命令后粘贴 JSON 数据\n"
                "格式: /导入菜品 [JSON]\n"
                '示例: /导入菜品 [{"name":"炒饭","location":"食堂","times":["中午"]}]'
            )
            return
        result = await self._dish_storage.import_json(json_text)
        yield event.plain_result(result)

    # ──────────────── 定时推荐 ────────────────

    @filter.command("定时推荐")
    async def enable_schedule(self, event: AstrMessageEvent):
        """为当前群开启定时推荐，可自定义时间 (管理员限定)

        用法: /定时推荐 [HH:MM ...]
        示例: /定时推荐          → 默认 11:50 和 17:20
              /定时推荐 12:00    → 每天 12:00
              /定时推荐 11:30 17:00 → 每天 11:30 和 17:00
        """
        if not self._is_admin(event):
            yield event.plain_result("只有管理员可操作定时推荐")
            return
        if not self._scheduler:
            yield event.plain_result("调度器未就绪")
            return
        group_id = self._get_group_id(event)
        if not group_id:
            yield event.plain_result("无法获取群组信息，定时推荐仅支持群聊")
            return

        custom_times = None
        time_str = self._extract_args(event, "定时推荐")
        if time_str:
            try:
                custom_times = [_parse_time(t) for t in time_str.split()]
            except ValueError as e:
                yield event.plain_result(str(e))
                return

        yield event.plain_result(self._scheduler.add_group(group_id, custom_times))

    @filter.command("取消定时推荐")
    async def disable_schedule(self, event: AstrMessageEvent):
        """为当前群关闭定时推荐 (管理员限定)

        用法: /取消定时推荐
        """
        if not self._is_admin(event):
            yield event.plain_result("只有管理员可操作定时推荐")
            return
        if not self._scheduler:
            yield event.plain_result("调度器未就绪")
            return
        group_id = self._get_group_id(event)
        if not group_id:
            yield event.plain_result("无法获取群组信息")
            return
        yield event.plain_result(self._scheduler.remove_group(group_id))

    # ──────────────── 帮助 ────────────────

    @filter.command("吃什么帮助")
    async def show_help(self, event: AstrMessageEvent):
        """显示插件指令帮助"""
        help_text = (
            "指令:\n"
            "1) /增加菜品 名称 地点 时间(中午/晚上)\n"
            "2) /吃什么 [地点] — 随机推荐\n"
            "3) /菜单 [地点] — 查看菜品列表\n"
            "4) /删除菜品 名称 (管理员)\n"
            "5) /导出菜品 — 导出 JSON 数据 (管理员)\n"
            "6) /导入菜品 <JSON> — 导入数据 (管理员)\n"
            "7) /定时推荐 [HH:MM ...] — 开启定时推荐 (管理员)\n"
            "8) /取消定时推荐 — 关闭定时推荐 (管理员)\n"
            "\n"
            "时间段: 08:00-14:00=中午 其它都是晚上\n"
            "地点支持模糊匹配，如 /吃什么 西门\n"
            "定时推荐默认 11:50 和 17:20，可自定义:\n"
            "  /定时推荐 12:00 17:30"
        )
        yield event.plain_result(help_text)

    async def terminate(self):
        if self._scheduler:
            self._scheduler.stop()
        logger.info("插件已卸载")
