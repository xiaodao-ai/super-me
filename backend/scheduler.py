# -*- coding: utf-8 -*-
"""
定时任务调度器：按指定间隔 + 时间窗口，定时触发 Agent 执行指令。

数据存储在 data/schedules.json，结构：
[{id, agent, interval, windows, prompt, enabled, last_run, running}]
"""
import json
import pathlib
import time
import itertools

DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
SCHEDULE_FILE = DATA_DIR / "schedules.json"

_id_counter = itertools.count(1)


def _now_hhmm():
    """当前本地时间 HH:MM 字符串。"""
    return time.strftime("%H:%M")


def _in_window(windows: list) -> bool:
    """判断当前时间是否在任一时间窗口内。windows 为空表示全天。"""
    if not windows:
        return True
    now = _now_hhmm()
    for w in windows:
        start = w.get("start", "00:00")
        end = w.get("end", "23:59")
        if start <= now <= end:
            return True
    return False


class Scheduler:
    def __init__(self):
        self.jobs: list[dict] = []
        self._running: set = set()   # 正在执行中的 job id（防并发）
        self._load()

    # ── 持久化 ──────────────────────────────────────────
    def _load(self):
        if SCHEDULE_FILE.exists():
            try:
                self.jobs = json.loads(
                    SCHEDULE_FILE.read_text(encoding="utf-8"))
            except Exception:
                self.jobs = []
        # 初始化 id 计数器
        if self.jobs:
            top = max(int(j.get("id", "0").split("_")[-1])
                      for j in self.jobs if j.get("id"))
            global _id_counter
            _id_counter = itertools.count(top + 1)

    def _save(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SCHEDULE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.jobs, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(SCHEDULE_FILE)

    # ── CRUD ────────────────────────────────────────────
    def list_all(self) -> list[dict]:
        return list(self.jobs)

    def list_by_agent(self, agent_id: str) -> list[dict]:
        return [j for j in self.jobs if j["agent"] == agent_id]

    def add(self, agent: str, interval: int, windows: list,
            prompt: str) -> dict | str:
        interval = max(1, min(1440, int(interval)))
        prompt = str(prompt).strip()[:500]
        if not prompt:
            return "prompt 不能为空"
        if not agent:
            return "请指定 Agent"
        # 校验 windows 格式
        clean_wins = []
        for w in (windows or []):
            s, e = str(w.get("start", "")).strip(), str(w.get("end", "")).strip()
            if s and e and len(s) == 5 and len(e) == 5:
                clean_wins.append({"start": s, "end": e})
        job = {
            "id": f"cron_{next(_id_counter)}",
            "agent": agent,
            "interval": interval,
            "windows": clean_wins,
            "prompt": prompt,
            "enabled": True,
            "last_run": None,
        }
        self.jobs.append(job)
        self._save()
        return job

    def update(self, sid: str, data: dict) -> dict | str:
        job = next((j for j in self.jobs if j["id"] == sid), None)
        if not job:
            return "任务不存在"
        if "interval" in data:
            job["interval"] = max(1, min(1440, int(data["interval"])))
        if "prompt" in data:
            p = str(data["prompt"]).strip()[:500]
            if not p:
                return "prompt 不能为空"
            job["prompt"] = p
        if "windows" in data:
            clean_wins = []
            for w in (data["windows"] or []):
                s, e = str(w.get("start", "")).strip(), str(w.get("end", "")).strip()
                if s and e and len(s) == 5 and len(e) == 5:
                    clean_wins.append({"start": s, "end": e})
            job["windows"] = clean_wins
        if "enabled" in data:
            job["enabled"] = bool(data["enabled"])
        self._save()
        return job

    def remove(self, sid: str) -> bool:
        before = len(self.jobs)
        self.jobs = [j for j in self.jobs if j["id"] != sid]
        if len(self.jobs) < before:
            self._running.discard(sid)
            self._save()
            return True
        return False

    def toggle(self, sid: str) -> dict | str:
        job = next((j for j in self.jobs if j["id"] == sid), None)
        if not job:
            return "任务不存在"
        job["enabled"] = not job["enabled"]
        self._save()
        return job

    # ── 调度 tick（每秒调用）──────────────────────────────
    def tick(self) -> list[dict]:
        """检查并返回本次需要触发的 job 列表。"""
        now = time.time()
        to_fire = []
        for job in self.jobs:
            if not job["enabled"]:
                continue
            if job["id"] in self._running:
                continue   # 上次还在执行中，跳过
            if not _in_window(job.get("windows", [])):
                continue
            last = job.get("last_run") or 0
            if now - last < job["interval"] * 60:
                continue
            # 触发
            job["last_run"] = now
            to_fire.append(job)
        if to_fire:
            self._save()
        return to_fire

    def mark_running(self, sid: str):
        self._running.add(sid)

    def mark_done(self, sid: str):
        self._running.discard(sid)
