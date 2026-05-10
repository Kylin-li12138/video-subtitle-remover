# -*- coding: utf-8 -*-
"""
去水印模板管理器 - 保存和加载水印位置模板
模板存储格式：JSON 文件，每个模板包含名称和选区坐标列表
"""
import os
import json
from typing import Optional

TEMPLATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "config", "templates.json")


def _ensure_dir():
    os.makedirs(os.path.dirname(TEMPLATE_FILE), exist_ok=True)


def load_all_templates() -> dict:
    """加载所有模板，返回 {name: [(ymin,ymax,xmin,xmax), ...]}"""
    if not os.path.exists(TEMPLATE_FILE):
        return {}
    try:
        with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        result = {}
        for name, areas in data.items():
            if isinstance(areas, list):
                result[name] = [tuple(a) for a in areas if isinstance(a, (list, tuple)) and len(a) == 4]
        return result
    except (json.JSONDecodeError, IOError):
        return {}


def save_template(name: str, selection_rects: list) -> bool:
    """保存模板（新建或覆盖同名模板）"""
    _ensure_dir()
    templates = load_all_templates()
    templates[name] = [list(r) for r in selection_rects]
    try:
        with open(TEMPLATE_FILE, "w", encoding="utf-8") as f:
            json.dump(templates, f, ensure_ascii=False, indent=2)
        return True
    except IOError:
        return False


def delete_template(name: str) -> bool:
    """删除指定模板"""
    templates = load_all_templates()
    if name not in templates:
        return False
    del templates[name]
    try:
        _ensure_dir()
        with open(TEMPLATE_FILE, "w", encoding="utf-8") as f:
            json.dump(templates, f, ensure_ascii=False, indent=2)
        return True
    except IOError:
        return False


def rename_template(old_name: str, new_name: str) -> bool:
    """重命名模板"""
    templates = load_all_templates()
    if old_name not in templates or new_name in templates:
        return False
    templates[new_name] = templates.pop(old_name)
    try:
        _ensure_dir()
        with open(TEMPLATE_FILE, "w", encoding="utf-8") as f:
            json.dump(templates, f, ensure_ascii=False, indent=2)
        return True
    except IOError:
        return False


def get_template(name: str) -> Optional[list]:
    """获取指定模板的选区列表"""
    templates = load_all_templates()
    return templates.get(name)


def get_template_names() -> list:
    """获取所有模板名称"""
    return list(load_all_templates().keys())
