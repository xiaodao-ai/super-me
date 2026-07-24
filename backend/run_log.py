# -*- coding: utf-8 -*-
"""执行日志：把「项目 / 任务 / LLM / Agent」四类日志分门别类地记成可直接阅读的文本。

独立日志目录：backend/logs/
  ├── projects/project-<id>.log   每个项目一份完整时间线（生命周期/事件/成员动作/验收/异常）
  ├── tasks/task-<id>.log         每个任务一份完整时间线
  ├── llm/llm-YYYYMMDD.log        当天所有 LLM 调用的完整 prompt / response
  ├── agents/<agent>-YYYYMMDD.log 每个分身当天的动作流（带所属项目/任务标记）
  └── system/system-YYYYMMDD.log  无归属的系统事件（启动、恢复等）兜底

设计要点：
- 关联：用 contextvars 自动给每条记录打上当前 task/proj id，asyncio 子任务自动继承，并发安全。
- 可读：每行「时间 【类别】 详情」，LLM 用 prompt/response 分块，异常带堆栈；直接打开就能看。
- 健壮：任何写入异常都被吞掉，绝不影响主执行流程；每条 flush，崩溃不丢。
"""
import contextvars
import threading
import time
import pathlib

LOG_DIR = pathlib.Path(__file__).resolve().parent / "logs"
PROJ_DIR = LOG_DIR / "projects"
TASK_DIR = LOG_DIR / "tasks"
LLM_DIR = LOG_DIR / "llm"
AGENT_DIR = LOG_DIR / "agents"
SYS_DIR = LOG_DIR / "system"

KEEP_DAYS = 14                 # 日志保留天数，超期自动清理
_MAX_HANDLES = 24              # 最多缓存的文件句柄数（LRU 淘汰）

_lock = threading.Lock()
_handles: dict = {}            # path_str -> file handle
_order: list = []              # LRU 顺序
_enabled = True
_pruned_day = None

_ctx: contextvars.ContextVar = contextvars.ContextVar("runlog_ctx", default=None)

_LEVEL = {"info": "信息", "text": "文本", "tool": "工具",
          "ret": "结果", "result": "收尾", "error": "报错"}


def set_enabled(on: bool):
    """运行时开关（默认开）。"""
    global _enabled
    _enabled = bool(on)


def push_context(**fields):
    """压入运行上下文（task=任务id / proj=项目id），返回 token。子任务自动继承。"""
    cur = dict(_ctx.get() or {})
    cur.update({k: v for k, v in fields.items() if v is not None})
    return _ctx.set(cur)


def pop_context(token):
    try:
        _ctx.reset(token)
    except Exception:
        pass


def _one(s):
    return str(s or "").replace("\n", " ").strip()


def _owner(rec):
    if rec.get("proj") is not None:
        return f"项目#{rec['proj']}"
    if rec.get("task") is not None:
        return f"任务#{rec['task']}"
    return None


def _fmt_line(rec):
    """把一条记录格式化成一行（异常会附多行堆栈）可读文本。"""
    t = rec.get("t", "")
    k = rec.get("kind")
    if k == "lifecycle":
        ev = rec.get("event", "")
        tag = "开始" if ev.endswith("_start") else ("结束" if ev.endswith("_end") else ev)
        d = f"#{rec.get('id', '')} {rec.get('title', '')}"
        if ev.endswith("_end"):
            d += f"  状态={rec.get('status', '')} 耗时={rec.get('secs', '?')}s"
            if rec.get("stuck_step"):
                d += f" 卡在步骤{rec['stuck_step']}"
            if rec.get("error"):
                d += f" | {rec['error']}"
        else:
            if rec.get("steps") is not None:
                d += f"  步骤数={rec['steps']}"
            if rec.get("subtasks") is not None:
                d += f"  子任务={rec['subtasks']}"
            if rec.get("members"):
                d += f"  成员={rec['members']}"
        return f"{t}  【{tag}】 {d}"
    if k == "event":
        return f"{t}  【事件】 {_one(rec.get('text'))}"
    if k == "stream":
        lvl = _LEVEL.get(rec.get("level"), rec.get("level", ""))
        return f"{t}  【{rec.get('agent', '?')}·{lvl}】 {_one(rec.get('txt'))}"
    if k == "llm":
        return f"{t}  【LLM】 {rec.get('purpose', '')} → {_one(rec.get('response'))[:80]}"
    if k == "boundary_deny":
        return f"{t}  【拦截】 {rec.get('agent', '')} {rec.get('tool', '')}: {_one(rec.get('reason'))}"
    if k == "exception":
        line = f"{t}  【异常】 {rec.get('where', '')}: {_one(rec.get('err'))}"
        if rec.get("trace"):
            line += "\n" + "\n".join("    " + ln
                                     for ln in str(rec["trace"]).splitlines()[-8:])
        return line
    return f"{t}  【{k}】 {_one(rec.get('text') or rec.get('txt') or '')}"


def _fmt_llm_block(rec):
    """LLM 记录在专属日志里用 prompt/response 分块，完整可查。"""
    owner = _owner(rec)
    head = (f"━━━ {rec.get('t')}  agent={rec.get('agent', '')}  "
            f"purpose={rec.get('purpose', '')}"
            + (f"  {owner}" if owner else "") + " ━━━")
    return (f"{head}\n[PROMPT]\n{rec.get('prompt', '')}\n"
            f"[RESPONSE]\n{rec.get('response', '')}\n")


def _prune():
    global _pruned_day
    day = time.strftime("%Y%m%d")
    if _pruned_day == day:
        return
    _pruned_day = day
    try:
        cutoff = time.time() - KEEP_DAYS * 86400
        for p in LOG_DIR.rglob("*.log"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except Exception:
                pass
    except Exception:
        pass


def _emit(path, text):
    """追加一段文本到指定日志文件（带句柄缓存、加锁、flush）。"""
    ps = str(path)
    h = _handles.get(ps)
    if h is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        h = open(path, "a", encoding="utf-8")
        _handles[ps] = h
        _order.append(ps)
        while len(_order) > _MAX_HANDLES:
            old = _order.pop(0)
            oh = _handles.pop(old, None)
            if oh is not None:
                try:
                    oh.close()
                except Exception:
                    pass
    else:
        _order.remove(ps)
        _order.append(ps)
    h.write(text + "\n")
    h.flush()


def write(kind: str, **fields):
    """记录一条日志，按「项目/任务/LLM/Agent」自动分流到对应文件。

    kind：event / stream / llm / lifecycle / boundary_deny / exception。
    fields：结构化字段；自动合并当前运行上下文（task/proj）并据此分流。
    """
    if not _enabled:
        return
    ctx = _ctx.get() or {}
    rec = {"ts": round(time.time(), 3),
           "t": time.strftime("%Y-%m-%d %H:%M:%S"),
           "kind": str(kind)}
    rec.update(ctx)
    rec.update(fields)
    day = time.strftime("%Y%m%d")
    ag = rec.get("agent")
    owner = _owner(rec)
    try:
        line = _fmt_line(rec)
    except Exception:
        line = f"{rec.get('t', '')}  【{kind}】 (格式化失败)"
    with _lock:
        _prune()
        # ① 项目 / 任务：完整时间线
        if rec.get("proj") is not None:
            _safe(PROJ_DIR / f"project-{rec['proj']}.log", line)
        if rec.get("task") is not None:
            _safe(TASK_DIR / f"task-{rec['task']}.log", line)
        # ② LLM：完整 prompt / response
        if kind == "llm":
            try:
                _safe(LLM_DIR / f"llm-{day}.log", _fmt_llm_block(rec))
            except Exception:
                pass
        # ③ Agent：分身动作流（带所属项目/任务标记，便于跨运行浏览）
        if ag:
            _safe(AGENT_DIR / f"{ag}-{day}.log",
                  line + (f"   （{owner}）" if owner else ""))
        # ④ 无归属的系统事件兜底
        if rec.get("proj") is None and rec.get("task") is None \
                and kind != "llm" and not ag:
            _safe(SYS_DIR / f"system-{day}.log", line)


def _safe(path, text):
    try:
        _emit(path, text)
    except Exception:
        pass
