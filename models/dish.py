from __future__ import annotations
"""可增删菜品数据模型

支持存储到 JSON 文件。字段:
- name: 菜品名称 (唯一)
- location: 地点/店名/档口
- times: 供应时间段列表 ["中午", "晚上"] 允许其中一个或两个
"""
from dataclasses import dataclass, asdict
from typing import List, Dict, Any

VALID_TIME_SLOTS = ["中午", "晚上"]

@dataclass
class Dish:
    name: str
    location: str
    times: List[str]

    def __post_init__(self):
        self.name = self.name.strip()
        self.location = self.location.strip()
        # 去重并保持原有顺序
        cleaned = []
        for t in self.times:
            t = t.strip()
            if t and t in VALID_TIME_SLOTS and t not in cleaned:
                cleaned.append(t)
        if not cleaned:
            # 默认都可
            cleaned = VALID_TIME_SLOTS.copy()
        self.times = cleaned
        if not self.name:
            raise ValueError("菜品名称不能为空")
        if not self.location:
            raise ValueError("地点不能为空")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Dish:
        return cls(name=data.get("name", ""), location=data.get("location", ""), times=data.get("times", []) or VALID_TIME_SLOTS.copy())
