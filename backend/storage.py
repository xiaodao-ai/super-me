# -*- coding: utf-8 -*-
"""
文件存储层：
- data/state.json   世界状态快照（重启后续跑）
- data/tasks.jsonl  用户发布的任务流水（追加式日志）
"""
import json
import pathlib
import time

DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
STATE_FILE = DATA_DIR / "state.json"
TASKS_FILE = DATA_DIR / "tasks.jsonl"


def load_state():
    """读取上次保存的世界状态，损坏或不存在时返回 None。"""
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_state(payload: dict):
    """原子写入世界状态（先写临时文件再替换）。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(STATE_FILE)


def append_task(record: dict):
    """追加一条用户任务流水。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = {"ts": round(time.time(), 3), **record}
    with TASKS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
