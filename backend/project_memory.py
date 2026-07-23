# -*- coding: utf-8 -*-
"""
项目记忆模块：让 Agent 切换项目时快速感知项目上下文。

每个项目目录下维护 .memory.json，存储：
- decisions  关键决策（技术选型、方案取舍）
- conventions 项目约定（命名规范、目录规范、风格偏好）
- context    项目上下文（核心功能、用户偏好、已知限制）
- lessons    经验教训（踩坑记录、返工原因）

Agent 执行项目任务时自动注入；复盘时自动沉淀；用户可通过 API 手动管理。
"""
import json
import pathlib
import time

MEMORY_FILE = ".memory.json"
MAX_ENTRIES = 20          # 每类最多保留条数
MAX_TOTAL = 50            # 总条目上限
MAX_TEXT_LEN = 200        # 单条记忆最大字数

VALID_TYPES = ("decision", "convention", "context", "lesson")
TYPE_LABELS = {
    "decision": "决策",
    "convention": "约定",
    "context": "上下文",
    "lesson": "经验",
}


def _memory_path(project_dir: pathlib.Path) -> pathlib.Path:
    return project_dir / MEMORY_FILE


def load(project_dir: pathlib.Path) -> list[dict]:
    """读取项目记忆列表。"""
    path = _memory_path(project_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("entries", [])
    except Exception:
        return []


def save(project_dir: pathlib.Path, entries: list[dict]):
    """原子写入项目记忆。"""
    path = _memory_path(project_dir)
    payload = {"entries": entries, "updated": time.time()}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(path)


def add(project_dir: pathlib.Path, mem_type: str, text: str) -> bool:
    """添加一条项目记忆（去重、限长、FIFO 淘汰）。"""
    mem_type = mem_type if mem_type in VALID_TYPES else "context"
    text = str(text).strip()[:MAX_TEXT_LEN]
    if not text:
        return False
    entries = load(project_dir)
    # 去重：同类型同内容不重复添加
    for e in entries:
        if e["type"] == mem_type and e["text"] == text:
            return False
    entries.append({"type": mem_type, "text": text, "ts": time.time()})
    # 按类型淘汰超限
    entries = _trim(entries)
    save(project_dir, entries)
    return True


def add_batch(project_dir: pathlib.Path, items: list[dict]) -> int:
    """批量添加记忆（复盘沉淀用），返回实际新增条数。"""
    entries = load(project_dir)
    added = 0
    for item in items:
        mem_type = item.get("type", "context")
        if mem_type not in VALID_TYPES:
            mem_type = "context"
        text = str(item.get("text", "")).strip()[:MAX_TEXT_LEN]
        if not text:
            continue
        # 去重
        if any(e["type"] == mem_type and e["text"] == text for e in entries):
            continue
        entries.append({"type": mem_type, "text": text, "ts": time.time()})
        added += 1
    if added:
        entries = _trim(entries)
        save(project_dir, entries)
    return added


def remove(project_dir: pathlib.Path, index: int) -> bool:
    """按索引删除一条记忆。"""
    entries = load(project_dir)
    if 0 <= index < len(entries):
        entries.pop(index)
        save(project_dir, entries)
        return True
    return False


def clear(project_dir: pathlib.Path):
    """清空项目记忆。"""
    save(project_dir, [])


def format_for_prompt(project_dir: pathlib.Path) -> str:
    """将项目记忆格式化为可注入提示词的文本。无记忆时返回空串。"""
    entries = load(project_dir)
    if not entries:
        return ""
    lines = ["【项目记忆（本项目历史沉淀，必须遵守）】"]
    # 按类型分组展示
    by_type: dict[str, list[str]] = {}
    for e in entries:
        t = e.get("type", "context")
        by_type.setdefault(t, []).append(e["text"])
    for t in VALID_TYPES:
        if t in by_type:
            label = TYPE_LABELS.get(t, t)
            for text in by_type[t]:
                lines.append(f"- [{label}] {text}")
    return "\n".join(lines)


def _trim(entries: list[dict]) -> list[dict]:
    """按类型 FIFO 淘汰 + 总量上限。"""
    # 按类型限制
    by_type: dict[str, list[dict]] = {}
    for e in entries:
        by_type.setdefault(e.get("type", "context"), []).append(e)
    result = []
    for t, items in by_type.items():
        result.extend(items[-MAX_ENTRIES:])
    # 总量限制：保留最新的
    if len(result) > MAX_TOTAL:
        result.sort(key=lambda x: x.get("ts", 0))
        result = result[-MAX_TOTAL:]
    # 按时间排序
    result.sort(key=lambda x: x.get("ts", 0))
    return result
