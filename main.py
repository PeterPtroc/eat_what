"""
今天吃什么？
"""

from typing import Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .services.dish_service import DishStorageService, current_time_slot


@register("eat", "AstrBot", "今天吃什么", "0.1.0")
class EatPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._dish_storage: Optional[DishStorageService] = None

    async def initialize(self):
        try:
            base_dir = (
                self.context.get_plugin_data_dir()  # 框架若支持数据目录
                if hasattr(self.context, "get_plugin_data_dir")
                else "."
            )
            self._dish_storage = DishStorageService(base_dir)
            logger.info("本地菜品插件初始化完成")
        except Exception as e:
            logger.error(f"初始化失败: {e}")

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        for attr in ["is_superuser", "is_admin", "is_owner"]:
            if hasattr(event, attr) and getattr(event, attr):
                return True
        return False

    @filter.command("增加菜品")
    async def add_or_extend_dish(self, event: AstrMessageEvent, name: str, location: str, time_slot: str):
        """添加新菜品或为已存在菜品追加时间段

        用法: /增加菜品 名称 地点 时间(中午/晚上)
        """
        if not self._dish_storage:
            yield event.plain_result("菜品存储未就绪")
            return
        if not name or not location or not time_slot:
            usage = "用法: /增加菜品 名称 地点 时间(中午/晚上)"
            yield event.plain_result(f"参数不完整\n{usage}")
            return
        msg = self._dish_storage.add_dish(name, location, time_slot)
        yield event.plain_result(msg)

    @filter.command("吃什么")
    async def what_to_eat(self, event: AstrMessageEvent, location: str = ""):
        """按当前时间段随机推荐菜品，可选限定地点

        用法: /吃什么 [地点]
        """
        if not self._dish_storage:
            yield event.plain_result("菜品存储未就绪")
            return
        slot = current_time_slot()
        dish = self._dish_storage.get_random(location if location else None, slot)
        if not dish:
            tip = "先用 /增加菜品 添加吧" if not location else f"地点 {location} 下暂无菜品"
            yield event.plain_result(f"没找到合适的菜品，{tip}")
            return
        yield event.plain_result(f"{'/'.join(dish.times)}吃{dish.location}的{dish.name} ")

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
        yield event.plain_result(self._dish_storage.remove_dish(name))

    @filter.command("吃什么帮助")
    async def show_help(self, event: AstrMessageEvent):
        """显示插件指令帮助"""
        help_text = (
            "指令:\n"
            "1) /增加菜品 名称 地点 时间(中午/晚上)\n"
            "2) /吃什么 [地点]\n"
            "3) /删除菜品 名称 (管理员)\n"
            "时间段: 08:00-14:00=中午 其它都是晚上\n"
        )
        yield event.plain_result(help_text)

    async def terminate(self):
        logger.info("插件已卸载")
