# -*- coding: utf-8 -*-
"""
「超级个体」真实世界状态（无模拟数据）：
- 时钟 = 真实本地时间
- 任务 = 仅来自用户发布，由 qoder_agent_sdk 真实执行
- 事件/进度/产出 = 全部来自真实执行过程
- tick 只负责移动插值与气泡衰减（纯视觉，不产生数据）
"""
import itertools
import time

from personas import PERSONAS, MEETING_CENTER, MEETING_RADIUS, PANTRY
import agent_config as _agent_config
import run_log

TICK_HZ = 10
_id_counter = itertools.count(1)


class Subtask:
    def __init__(self, role, title, brief, stage):
        self.id = next(_id_counter)
        self.role = role
        self.title = title
        self.brief = brief
        self.stage = stage
        self.status = "pending"      # pending/running/done/failed
        self.summary = ""
        self.files = []
        self.started = None
        self.finished = None

    def to_dict(self):
        return {
            "id": self.id, "role": self.role, "title": self.title,
            "brief": self.brief, "stage": self.stage, "status": self.status,
            "summary": self.summary, "files": self.files,
            "started": self.started, "finished": self.finished,
        }

    @classmethod
    def load(cls, d):
        s = cls(d["role"], d["title"], d.get("brief", ""), d.get("stage", 1))
        s.status = d.get("status", "pending")
        s.summary = d.get("summary", "")
        s.files = d.get("files", [])
        s.started, s.finished = d.get("started"), d.get("finished")
        return s


class Task:
    def __init__(self, title, assignee):
        self.id = next(_id_counter)
        self.title = title
        self.assignee = assignee          # 'tl' = 需要拆解
        self.status = "queued"            # queued/clarifying/waiting/splitting/running/done/failed
        self.created = time.time()
        self.finished = None
        self.workdir = ""
        self.subtasks: list[Subtask] = []
        self.questions: list[dict] = []   # TL 澄清问题 [{q, options, answer}]
        self.error = ""

    def to_dict(self):
        return {
            "id": self.id, "title": self.title, "assignee": self.assignee,
            "status": self.status, "created": self.created,
            "finished": self.finished, "workdir": self.workdir,
            "error": self.error,
            "questions": self.questions,
            "subtasks": [s.to_dict() for s in self.subtasks],
        }

    @classmethod
    def load(cls, d):
        t = cls(d["title"], d.get("assignee", "tl"))
        t.id = d.get("id", t.id)
        t.status = d.get("status", "queued")
        t.created = d.get("created", time.time())
        t.finished = d.get("finished")
        t.workdir = d.get("workdir", "")
        t.error = d.get("error", "")
        t.questions = d.get("questions", [])
        t.subtasks = [Subtask.load(s) for s in d.get("subtasks", [])]
        return t


class PAssign:
    """项目步骤中某个成员的任务。"""

    def __init__(self, role, brief):
        self.role = role
        self.brief = brief
        self.status = "pending"      # pending/running/done/failed
        self.summary = ""
        self.files = []

    def to_dict(self):
        return {"role": self.role, "brief": self.brief, "status": self.status,
                "summary": self.summary, "files": self.files}

    @classmethod
    def load(cls, d):
        a = cls(d["role"], d.get("brief", ""))
        a.status = d.get("status", "pending")
        a.summary = d.get("summary", "")
        a.files = d.get("files", [])
        return a


class PStep:
    """项目的一个推进步骤。"""

    def __init__(self, title, assigns):
        self.title = title
        self.assigns: list[PAssign] = assigns
        self.status = "pending"      # pending/running/done/failed
        self.review = ""             # 队长点评

    def to_dict(self):
        return {"title": self.title, "status": self.status,
                "review": self.review,
                "assigns": [a.to_dict() for a in self.assigns]}

    @classmethod
    def load(cls, d):
        s = cls(d["title"], [PAssign.load(x) for x in d.get("assigns", [])])
        s.status = d.get("status", "pending")
        s.review = d.get("review", "")
        return s


class Project:
    """复杂需求 → 队长督导的多步骤项目。"""

    def __init__(self, title, desc, folder="", members=None):
        self.id = next(_id_counter)
        self.title = title
        self.desc = desc
        self.folder = folder         # 用户指定的项目文件夹名（空 = 自动生成）
        self.members = members or [] # 参与成员 id 列表，空 = 全部成员
        self.status = "planning"     # clarifying/waiting/planning/running/done/failed
        self.created = time.time()
        self.finished = None
        self.dir = ""
        self.steps: list[PStep] = []
        self.questions: list[dict] = []   # TL 澄清问题
        self.error = ""

    def current_step(self):
        for i, s in enumerate(self.steps):
            if s.status in ("pending", "running"):
                return i, s
        return len(self.steps), None

    def to_dict(self):
        idx, _ = self.current_step()
        return {
            "id": self.id, "title": self.title, "desc": self.desc,
            "status": self.status, "created": self.created,
            "finished": self.finished, "dir": self.dir, "error": self.error,
            "folder": self.folder, "members": self.members,
            "stepIndex": idx,
            "questions": self.questions,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def load(cls, d):
        p = cls(d["title"], d.get("desc", ""), d.get("folder", ""),
                d.get("members", []))
        p.id = d.get("id", p.id)
        p.status = d.get("status", "planning")
        p.created = d.get("created", time.time())
        p.finished = d.get("finished")
        p.dir = d.get("dir", "")
        p.error = d.get("error", "")
        p.questions = d.get("questions", [])
        p.steps = [PStep.load(s) for s in d.get("steps", [])]
        return p


class Agent:
    """真实 Agent 的可视化状态（数据全部来自真实执行回调）。"""

    def __init__(self, spec):
        self.spec = spec
        self.id = spec["id"]
        self.pos = list(spec["desk"])
        self.target = list(spec["desk"])
        self.state = "idle"          # idle/working/meeting(拆解中)
        self.activity = ""           # 实时动作，如 "🔧 Write index.html"
        self.bubble = ""
        self.bubble_ttl = 0.0
        self.current = None          # 正在执行的 Subtask
        self.done_count = 0
        self.stream = []             # qodercli 会话流式日志（ring buffer）
        self.stream_seq = 0
        self.stream_tag = ""         # 当前会话标签（任务#N / 项目#N…），随日志写入
        self.hints = []              # 用户插话（作用于当前/下一次执行）
        self.hint_log = []           # 插话历史（供复盘沉淀）
        self.exec_task = None        # 正在执行的 SDK 会话（可被插话中断）
        self.interrupted = False
        self.last_activity = 0.0     # 最近一次流事件时间（monotonic），用于空闲/卡死判定
        self.pending_tool = ""       # 当前正在执行的工具名（非空=工具执行中，静默属正常）
        self.pending_tool_since = 0.0  # 当前工具开始执行的时刻（monotonic），供心跳展示工具自身耗时

    def push_stream(self, kind, text):
        """记录一条 SDK 会话流事件。kind: info/text/tool/ret/result/error"""
        self.last_activity = time.monotonic()   # 任何流事件都算"还在产出"，刷新活动时间
        self.stream_seq += 1
        self.stream.append({
            "seq": self.stream_seq,
            "t": time.strftime("%H:%M:%S"),
            "k": kind,
            "tag": self.stream_tag,
            "txt": str(text)[:2000],
        })
        if len(self.stream) > 400:
            self.stream = self.stream[-400:]
        # 持久化一份到磁盘（不截断，供事后复盘）
        try:
            run_log.write("stream", agent=self.id, tag=self.stream_tag,
                          level=kind, txt=str(text)[:4000])
        except Exception:
            pass

    def stream_since(self, since):
        return [e for e in self.stream if e["seq"] > since]

    def say(self, text, ttl=6.0):
        self.bubble = (text or "").strip()[:60]
        self.bubble_ttl = ttl

    def go(self, x, y):
        self.target = [x, y]

    def go_desk(self):
        self.target = list(self.spec["desk"])

    def tick(self, dt):
        k = min(1.0, 2.6 * dt)
        self.pos[0] += (self.target[0] - self.pos[0]) * k
        self.pos[1] += (self.target[1] - self.pos[1]) * k
        if self.bubble_ttl > 0:
            self.bubble_ttl -= dt
            if self.bubble_ttl <= 0:
                self.bubble = ""

    def to_dict(self):
        s = self.spec
        cur = self.current
        return {
            "id": self.id, "name": s["name"], "role": s["role"],
            "emoji": s["emoji"], "color": s["color"],
            "accessory": s["accessory"], "zone": s["zone"],
            "desk": list(s["desk"]),
            "pos": [round(self.pos[0], 4), round(self.pos[1], 4)],
            "state": self.state,
            "activity": self.activity,
            "bubble": self.bubble,
            "doneCount": self.done_count,
            "streamSeq": self.stream_seq,
            "current": ({"title": getattr(cur, "title", None)
                                  or (cur.brief[:24] if getattr(cur, "brief", "") else "项目任务")}
                        if cur else None),
        }


class World:
    def __init__(self, saved=None):
        # 内置 + 自定义 Agent
        all_specs = list(PERSONAS) + _agent_config.get_custom_agents()
        self.agents = [Agent(s) for s in all_specs]
        self.by_id = {a.id: a for a in self.agents}
        self.tasks: list[Task] = []
        self.projects: list[Project] = []
        self.events: list[dict] = []
        self.links: list[dict] = []      # 真实分发动画 {from,to,label,progress}
        self.dirty = False               # 有数据变更需要落盘
        if saved:
            self._restore(saved)
            self.log("💾 已从文件恢复任务与事件记录")
        else:
            self.log("🌱 真实模式启动：任务由你发布，Agent 用 Qoder SDK 真实执行")

    # ── 持久化 ────────────────────────────────────────────
    def export_state(self):
        return {
            "tasks": [t.to_dict() for t in self.tasks[-50:]],
            "projects": [p.to_dict() for p in self.projects[-20:]],
            "events": self.events[-80:],
            "done": {a.id: a.done_count for a in self.agents},
        }

    def _restore(self, saved):
        try:
            self.tasks = [Task.load(d) for d in saved.get("tasks", [])]
            self.projects = [Project.load(d) for d in saved.get("projects", [])]
            self.events = list(saved.get("events", []))
            for aid, n in saved.get("done", {}).items():
                if aid in self.by_id:
                    self.by_id[aid].done_count = n
            # 上次运行中断的任务/项目标记为失败
            for t in self.tasks:
                if t.status in ("queued", "clarifying", "waiting",
                                "splitting", "running"):
                    t.status = "failed"
                    t.error = "服务重启导致中断"
                    for s in t.subtasks:
                        if s.status in ("pending", "running"):
                            s.status = "failed"
            for p in self.projects:
                if p.status in ("clarifying", "waiting", "planning", "running"):
                    p.status = "failed"
                    p.error = "服务重启导致中断"
                    for st in p.steps:
                        if st.status in ("pending", "running"):
                            st.status = "failed"
                        # 同步重置 step 内部未完成的 assign（否则遗留 running
                        # 僵尸状态，retry 时重置不到 → 不跑任务直接秒挂）
                        for a in st.assigns:
                            if a.status in ("pending", "running"):
                                a.status = "failed"
            # 让 id 生成器跳过已有 id
            top = max([t.id for t in self.tasks] +
                      [s.id for t in self.tasks for s in t.subtasks] +
                      [p.id for p in self.projects] + [0])
            global _id_counter
            _id_counter = itertools.count(top + 1)
        except Exception:
            self.tasks, self.projects, self.events = [], [], []

    # ── 事件 ──────────────────────────────────────────────
    def log(self, text):
        self.events.append({"t": time.strftime("%H:%M:%S"), "text": text})
        if len(self.events) > 120:
            self.events = self.events[-120:]
        try:
            run_log.write("event", text=str(text))
        except Exception:
            pass
        self.dirty = True

    # ── 真实分发动画（由 runner 触发）─────────────────────
    def add_link(self, src_id, dst_id, label):
        self.links.append({"from": src_id, "to": dst_id,
                           "label": label, "progress": 0.0})

    # ── 主循环（仅视觉）───────────────────────────────────
    def tick(self, dt):
        for a in self.agents:
            a.tick(dt)
        keep = []
        for l in self.links:
            l["progress"] = round(l["progress"] + dt / 3.5, 3)
            if l["progress"] < 1.0:
                keep.append(l)
        self.links = keep

    def running_count(self):
        n = sum(1 for t in self.tasks
                if t.status in ("queued", "clarifying", "waiting",
                                "splitting", "running"))
        n += sum(1 for p in self.projects
                 if p.status in ("clarifying", "waiting", "planning", "running"))
        return n

    def snapshot(self):
        now = time.localtime()
        return {
            "real": True,
            "clock": time.strftime("%H:%M:%S", now),
            "date": time.strftime("%m-%d %a", now),
            "minutes": now.tm_hour * 60 + now.tm_min,
            "running": self.running_count(),
            "agents": [a.to_dict() for a in self.agents],
            "links": [dict(l) for l in self.links],
            "events": self.events[-40:],
            "tasks": [t.to_dict() for t in self.tasks[-12:]][::-1],
            "projects": [p.to_dict() for p in self.projects[-5:]][::-1],
            "meeting": {"center": list(MEETING_CENTER), "radius": MEETING_RADIUS},
            "pantry": list(PANTRY),
            "ts": time.time(),
        }
