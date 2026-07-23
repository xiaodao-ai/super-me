# -*- coding: utf-8 -*-
"""
「超级个体」服务端（真实模式）：
- 静态托管 frontend/ + /ws 10Hz 广播世界快照
- POST /api/task 发布真实任务 → TaskRunner 用 qoder_agent_sdk 执行
- 世界状态（任务/事件/完成数）落盘 data/state.json
"""
import asyncio
import faulthandler
import json
import pathlib
import re
import signal
import weakref

from aiohttp import web, WSMsgType

# 调试：kill -USR1 <pid> 可打印所有线程调用栈，排查事件循环阻塞
# SIGUSR1 仅在类 Unix 平台存在，Windows 上跳过
if hasattr(signal, "SIGUSR1"):
    if hasattr(signal, "SIGUSR1"):
        faulthandler.register(signal.SIGUSR1)

import storage
import agent_config
import project_memory
from scheduler import Scheduler
from world import World, Task, Project, TICK_HZ
from runner import TaskRunner

ROOT = pathlib.Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

agent_config.load()
world = World(saved=storage.load_state())
runner = TaskRunner(world, ROOT)
scheduler = Scheduler()
sockets: "weakref.WeakSet[web.WebSocketResponse]" = weakref.WeakSet()


async def index(_request):
    return web.FileResponse(FRONTEND / "index.html")


async def ws_handler(request):
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    sockets.add(ws)
    try:
        async for msg in ws:
            if msg.type == WSMsgType.ERROR:
                break
    finally:
        sockets.discard(ws)
    return ws


async def task_handler(request):
    """发布真实任务：{assignee: 成员id 或 'tl', title}"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    title = (data.get("title") or "").strip()[:60]
    assignee = data.get("assignee", "")
    if not title or assignee not in world.by_id:
        return web.json_response(
            {"ok": False, "error": "请填写任务内容并选择接收人"}, status=400)

    task = Task(title, assignee)
    world.tasks.append(task)
    world.log(f"📨 收到真实任务「{title}」→ {world.by_id[assignee].spec['name']}")
    storage.append_task({"assignee": assignee, "title": title,
                         "via": "tl-split" if assignee == "tl" else "direct"})
    asyncio.get_event_loop().create_task(runner.run(task))
    return web.json_response({"ok": True, "taskId": task.id,
                              "assignee": world.by_id[assignee].spec["name"],
                              "title": title})


async def project_handler(request):
    """发起项目：{title, desc, folder} → TL 规划并督导推进。"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    title = (data.get("title") or "").strip()[:60]
    desc = (data.get("desc") or "").strip()[:800]
    if not title:
        return web.json_response({"ok": False, "error": "请填写项目名称"}, status=400)
    # 自定义文件夹名：只允许字母/数字/中文/-/_，防止路径穿越
    raw_folder = (data.get("folder") or "").strip()
    folder = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", raw_folder)[:40].strip("-.")
    if raw_folder and not folder:
        return web.json_response(
            {"ok": False, "error": "文件夹名只能包含中文、字母、数字、- 和 _"},
            status=400)
    if folder and (ROOT / "projects" / folder).exists():
        return web.json_response(
            {"ok": False, "error": f"文件夹「{folder}」已存在，换一个名字吧"},
            status=400)
    proj = Project(title, desc, folder, members=data.get("members", []))
    world.projects.append(proj)
    world.log(f"🗂 收到新项目「{title}」，等待队长桑规划"
              + (f"（目录：projects/{folder}）" if folder else ""))
    storage.append_task({"assignee": "tl", "title": title, "via": "project"})
    runner.proj_tasks[proj.id] = asyncio.get_event_loop().create_task(
        runner.run_project(proj))
    return web.json_response({"ok": True, "projectId": proj.id, "title": title,
                              "folder": folder})


async def project_cancel_handler(request):
    """手动终止进行中的项目。"""
    try:
        pid = int(request.match_info["pid"])
    except ValueError:
        return web.json_response({"ok": False, "error": "bad id"}, status=400)
    res = runner.cancel_project(pid)
    return web.json_response(res, status=200 if res.get("ok") else 400)


async def project_delete_handler(request):
    """删除项目：{delete_files: bool}"""
    try:
        pid = int(request.match_info["pid"])
    except ValueError:
        return web.json_response({"ok": False, "error": "bad id"}, status=400)
    try:
        data = await request.json()
    except Exception:
        data = {}
    delete_files = bool(data.get("delete_files", False))
    res = runner.delete_project(pid, delete_files=delete_files)
    return web.json_response(res, status=200 if res.get("ok") else 400)


async def project_retry_handler(request):
    """重试失败的项目：从失败步骤继续执行。支持携带 {message} 补充指示。"""
    try:
        pid = int(request.match_info["pid"])
    except ValueError:
        return web.json_response({"ok": False, "error": "bad id"}, status=400)
    proj = next((p for p in world.projects if p.id == pid), None)
    if not proj:
        return web.json_response({"ok": False, "error": "项目不存在"}, status=404)
    if proj.status != "failed":
        return web.json_response(
            {"ok": False, "error": "只有失败的项目才能重试"}, status=400)
    try:
        data = await request.json()
    except Exception:
        data = {}
    hint = (data.get("message") or "").strip()[:300]
    runner.proj_tasks[proj.id] = asyncio.get_event_loop().create_task(
        runner.retry_project(proj, hint=hint))
    return web.json_response({"ok": True, "title": proj.title})


async def project_memory_get(request):
    """查看项目记忆。"""
    try:
        pid = int(request.match_info["pid"])
    except ValueError:
        return web.json_response({"ok": False, "error": "bad id"}, status=400)
    proj = next((p for p in world.projects if p.id == pid), None)
    if not proj or not proj.dir:
        return web.json_response({"ok": False, "error": "项目不存在或未初始化"}, status=404)
    pdir = ROOT / proj.dir
    entries = project_memory.load(pdir)
    return web.json_response({"ok": True, "entries": entries,
                              "labels": project_memory.TYPE_LABELS})


async def project_memory_post(request):
    """手动添加项目记忆：{type, text}"""
    try:
        pid = int(request.match_info["pid"])
    except ValueError:
        return web.json_response({"ok": False, "error": "bad id"}, status=400)
    proj = next((p for p in world.projects if p.id == pid), None)
    if not proj or not proj.dir:
        return web.json_response({"ok": False, "error": "项目不存在或未初始化"}, status=404)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    pdir = ROOT / proj.dir
    mem_type = (data.get("type") or "context").strip()
    text = (data.get("text") or "").strip()[:200]
    if not text:
        return web.json_response({"ok": False, "error": "请填写记忆内容"}, status=400)
    added = project_memory.add(pdir, mem_type, text)
    if added:
        world.log(f"📝 你为项目「{proj.title}」添加了一条项目记忆")
        world.dirty = True
    return web.json_response({"ok": True, "added": added})


async def project_memory_delete(request):
    """删除指定索引的项目记忆。"""
    try:
        pid = int(request.match_info["pid"])
        idx = int(request.match_info["idx"])
    except ValueError:
        return web.json_response({"ok": False, "error": "bad id"}, status=400)
    proj = next((p for p in world.projects if p.id == pid), None)
    if not proj or not proj.dir:
        return web.json_response({"ok": False, "error": "项目不存在"}, status=404)
    pdir = ROOT / proj.dir
    removed = project_memory.remove(pdir, idx)
    return web.json_response({"ok": removed},
                             status=200 if removed else 400)


async def answer_handler(request):
    """用户回答队长的澄清问题：{kind: task|project, id, answers: [str]}"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    kind = data.get("kind")
    if kind not in ("task", "project"):
        return web.json_response({"ok": False, "error": "bad kind"}, status=400)
    res = runner.answer(kind, int(data.get("id", 0)),
                        list(data.get("answers", [])))
    return web.json_response(res, status=200 if res.get("ok") else 400)


async def followup_handler(request):
    """对已完成/失败的任务追问：{message} → 原执行人在原目录继续。"""
    try:
        tid = int(request.match_info["tid"])
    except ValueError:
        return web.json_response({"ok": False, "error": "bad id"}, status=400)
    task = next((t for t in world.tasks if t.id == tid), None)
    if not task:
        return web.json_response({"ok": False, "error": "任务不存在"}, status=404)
    if task.status not in ("done", "failed"):
        return web.json_response(
            {"ok": False, "error": "任务还在进行中，可直接在终端插话"}, status=400)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    message = (data.get("message") or "").strip()[:300]
    if not message:
        return web.json_response({"ok": False, "error": "请填写追问内容"}, status=400)
    storage.append_task({"assignee": task.assignee, "title": task.title,
                         "via": "followup", "message": message})
    asyncio.get_event_loop().create_task(runner.followup_task(task, message))
    return web.json_response({"ok": True, "taskId": tid})


async def open_dir_handler(request):
    """在系统文件管理器（macOS Finder）中打开产出目录。"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    rel = str(data.get("path") or "").strip().lstrip("/")
    if not rel:
        return web.json_response({"ok": False, "error": "缺少路径"}, status=400)
    p = (ROOT / rel).resolve()
    allowed = [(ROOT / "workspace").resolve(), (ROOT / "projects").resolve()]
    if not any(p.is_relative_to(a) for a in allowed):
        return web.json_response({"ok": False, "error": "只能打开产出目录"}, status=403)
    if not p.is_dir():
        return web.json_response({"ok": False, "error": "目录不存在（可能还没产出）"}, status=404)
    import subprocess
    subprocess.Popen(["open", str(p)])   # macOS：Finder 打开，立即返回
    return web.json_response({"ok": True, "path": str(p)})


async def open_file_handler(request):
    """在 Finder 中定位并选中文件（open -R）。"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    rel = str(data.get("path") or "").strip().lstrip("/")
    if not rel:
        return web.json_response({"ok": False, "error": "缺少路径"}, status=400)
    p = (ROOT / rel).resolve()
    allowed = [(ROOT / "workspace").resolve(), (ROOT / "projects").resolve()]
    if not any(p.is_relative_to(a) for a in allowed):
        return web.json_response({"ok": False, "error": "只能打开产出目录"}, status=403)
    if not p.exists():
        return web.json_response({"ok": False, "error": "文件不存在"}, status=404)
    import subprocess
    subprocess.Popen(["open", "-R", str(p)])   # macOS：Finder 中定位选中文件
    return web.json_response({"ok": True, "path": str(p)})


async def open_in_editor_handler(request):
    """用 Qoder/VS Code 打开项目目录。"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    rel = str(data.get("path") or "").strip().lstrip("/")
    if not rel:
        return web.json_response({"ok": False, "error": "缺少路径"}, status=400)
    p = (ROOT / rel).resolve()
    allowed = [(ROOT / "workspace").resolve(), (ROOT / "projects").resolve()]
    if not any(p.is_relative_to(a) for a in allowed):
        return web.json_response({"ok": False, "error": "只能打开产出目录"}, status=403)
    if not p.is_dir():
        return web.json_response({"ok": False, "error": "目录不存在"}, status=404)
    import subprocess
    import shutil
    # 使用 qoder 打开项目目录
    editor = "qoder" if shutil.which("qoder") else None
    if not editor:
        return web.json_response({"ok": False, "error": "未找到 qoder 命令，请确认已安装"}, status=400)
    subprocess.Popen([editor, str(p)])
    return web.json_response({"ok": True, "path": str(p), "editor": editor})


async def interject_handler(request):
    """执行中插话纠正：{aid, hint} → 中断当前会话携带纠正重跑。"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    res = runner.interject(data.get("aid", ""), data.get("hint", ""))
    return web.json_response(res, status=200 if res.get("ok") else 400)


HOME = pathlib.Path.home()


async def browse_handler(request):
    """目录浏览（限制在用户主目录内），供设置页选择工作目录。"""
    raw = request.query.get("path", "") or str(ROOT)
    p = pathlib.Path(raw).expanduser()
    if not p.is_absolute():
        p = ROOT / p
    try:
        p = p.resolve()
    except Exception:
        p = HOME
    if not str(p).startswith(str(HOME)):     # 安全边界：仅主目录内
        p = HOME
    if not p.is_dir():
        p = p.parent if p.parent.is_dir() else HOME
    try:
        dirs = sorted(d.name for d in p.iterdir()
                      if d.is_dir() and not d.name.startswith("."))[:200]
    except Exception:
        dirs = []
    return web.json_response({
        "ok": True,
        "path": str(p),
        "parent": str(p.parent) if str(p) != str(HOME) else None,
        "dirs": dirs,
        "shortcuts": [
            {"label": "🏠 主目录", "path": str(HOME)},
            {"label": "📦 本项目", "path": str(ROOT)},
            {"label": "🗂 workspace", "path": str(ROOT / "workspace")},
        ],
    })


async def stream_handler(request):
    """某分身的 qodercli 流式日志（增量拉取：?since=<seq>）。"""
    aid = request.match_info["aid"]
    a = world.by_id.get(aid)
    if not a:
        return web.json_response({"ok": False, "error": "unknown agent"}, status=404)
    try:
        since = int(request.query.get("since", 0))
    except ValueError:
        since = 0
    return web.json_response({
        "ok": True,
        "seq": a.stream_seq,
        "state": a.state,
        "activity": a.activity,
        "items": a.stream_since(since),
    })


async def config_get(_request):
    """全部分身配置 + 本机可用 Skill / 模型列表（每次打开刷新）。"""
    skills = await agent_config.list_skills(refresh=True)
    models = await agent_config.list_models(refresh=True)
    return web.json_response({
        "ok": True,
        "agents": {a.id: agent_config.get(a.id) for a in world.agents},
        "global": agent_config.get_global(),
        "skills": skills,
        "models": models,
    })


async def config_post(request):
    """保存某个分身的配置；aid=global 时保存全局配置（并发数即时生效）。"""
    aid = request.match_info["aid"]
    if aid == "global":
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "bad json"}, status=400)
        g = agent_config.save_global(data)
        await runner.gate.set_limit(g["max_concurrency"])
        world.log(f"🚦 全局并发数调整为 {g['max_concurrency']}")
        world.dirty = True
        return web.json_response({"ok": True, "config": g})
    if aid not in world.by_id:
        return web.json_response({"ok": False, "error": "unknown agent"}, status=404)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    # 工作目录合法性预检查
    wd = str(data.get("workdir", "")).strip()
    if wd:
        p = pathlib.Path(wd).expanduser()
        if not p.is_absolute():
            p = ROOT / p
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return web.json_response(
                {"ok": False, "error": f"工作目录不可用：{e}"}, status=400)
    cfg = agent_config.save(aid, data)
    a = world.by_id[aid]
    world.log(f"⚙️ {a.spec['emoji']} {a.spec['name']} 的配置已更新"
              f"（目录:{cfg['workdir'] or '默认'} · Skill:{len(cfg['skills'])} 个"
              f" · 规则:{'有' if cfg['rules'] else '无'}）")
    a.say("我的配置更新啦～", 5)
    return web.json_response({"ok": True, "config": cfg})


async def snap_handler(request):
    """接收前端同帧捕获的画布截图（dataURL），落盘用于调试验证。"""
    import base64
    body = await request.text()
    prefix = "data:image/jpeg;base64,"
    if not body.startswith(prefix):
        return web.json_response({"ok": False}, status=400)
    path = ROOT / ".canvas-snap.jpg"
    path.write_bytes(base64.b64decode(body[len(prefix):]))
    return web.json_response({"ok": True})


# ── Agent 管理 API ───────────────────────────────────
async def agents_list_handler(_request):
    """返回所有 Agent（内置 + 自定义）。"""
    agents = []
    for a in world.agents:
        agents.append({
            "id": a.id, "name": a.spec["name"], "role": a.spec["role"],
            "emoji": a.spec["emoji"], "color": a.spec["color"],
            "custom": a.spec.get("custom", False),
        })
    return web.json_response({"ok": True, "agents": agents})


async def agents_add_handler(request):
    """新增自定义 Agent：{id, name, emoji, role, sys, color}"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    result = agent_config.add_custom_agent(data)
    if isinstance(result, str):
        return web.json_response({"ok": False, "error": result}, status=400)
    # 动态加入世界
    from world import Agent
    new_agent = Agent(result)
    world.agents.append(new_agent)
    world.by_id[new_agent.id] = new_agent
    runner.ensure_lock(new_agent.id)
    world.log(f"✨ 新成员加入：{result['emoji']} {result['name']}（{result['role']}）")
    world.dirty = True
    return web.json_response({"ok": True, "agent": result})


async def agents_delete_handler(request):
    """删除自定义 Agent。"""
    aid = request.match_info["aid"]
    if aid in agent_config.BUILTIN_IDS:
        return web.json_response({"ok": False, "error": "内置角色不可删除"}, status=400)
    if not agent_config.remove_custom_agent(aid):
        return web.json_response({"ok": False, "error": "该 Agent 不存在"}, status=404)
    # 从世界中移除（不中断正在执行的任务）
    a = world.by_id.pop(aid, None)
    if a:
        world.agents = [x for x in world.agents if x.id != aid]
        world.log(f"🗑 移除了自定义成员：{a.spec['emoji']} {a.spec['name']}")
        world.dirty = True
    return web.json_response({"ok": True})


# ── 定时任务 API ───────────────────────────────────
async def schedules_list_handler(request):
    """列出定时任务（可按 agent 过滤）。"""
    aid = request.query.get("agent", "")
    jobs = scheduler.list_by_agent(aid) if aid else scheduler.list_all()
    return web.json_response({"ok": True, "jobs": jobs})


async def schedules_add_handler(request):
    """新增定时任务：{agent, interval, windows, prompt}"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    result = scheduler.add(
        agent=data.get("agent", ""),
        interval=int(data.get("interval", 5)),
        windows=data.get("windows", []),
        prompt=data.get("prompt", ""),
    )
    if isinstance(result, str):
        return web.json_response({"ok": False, "error": result}, status=400)
    a = world.by_id.get(result["agent"])
    name = a.spec["name"] if a else result["agent"]
    world.log(f"⏰ 为 {name} 添加了定时任务（每 {result['interval']} 分钟）")
    world.dirty = True
    return web.json_response({"ok": True, "job": result})


async def schedules_update_handler(request):
    """更新定时任务。"""
    sid = request.match_info["sid"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    result = scheduler.update(sid, data)
    if isinstance(result, str):
        return web.json_response({"ok": False, "error": result}, status=400)
    return web.json_response({"ok": True, "job": result})


async def schedules_delete_handler(request):
    """删除定时任务。"""
    sid = request.match_info["sid"]
    removed = scheduler.remove(sid)
    return web.json_response({"ok": removed},
                             status=200 if removed else 404)


async def schedules_toggle_handler(request):
    """启用/停用定时任务。"""
    sid = request.match_info["sid"]
    result = scheduler.toggle(sid)
    if isinstance(result, str):
        return web.json_response({"ok": False, "error": result}, status=404)
    return web.json_response({"ok": True, "job": result})


async def _safe_send(ws, payload):
    """单个连接发送：1 秒超时，失败即关闭，绝不拖垮广播循环。"""
    try:
        await asyncio.wait_for(ws.send_str(payload), timeout=1)
    except Exception:
        try:
            await ws.close()
        except Exception:
            pass


async def game_loop(_app):
    dt = 1.0 / TICK_HZ
    ticks = 0
    while True:
        try:
            world.tick(dt)
            ticks += 1
            if ticks % 30 == 0 and world.dirty:      # 有变更才落盘
                world.dirty = False
                storage.save_state(world.export_state())
            # 定时任务调度（每秒检查一次）
            if ticks % TICK_HZ == 0:
                for job in scheduler.tick():
                    _fire_scheduled(job)
            if sockets:
                snap = world.snapshot()
                snap["gate"] = {"active": runner.gate.active,
                                "limit": runner.gate.limit}
                payload = json.dumps(snap, ensure_ascii=False)
                await asyncio.gather(
                    *(_safe_send(ws, payload) for ws in set(sockets) if not ws.closed),
                    return_exceptions=True,
                )
        except asyncio.CancelledError:
            return
        except Exception:
            import traceback
            print("game_loop error:\n" + traceback.format_exc(), flush=True)
        await asyncio.sleep(dt)


def _fire_scheduled(job):
    """触发一个定时任务：创建 Task 直接指派给 Agent 执行。"""
    aid = job["agent"]
    if aid not in world.by_id:
        return
    scheduler.mark_running(job["id"])
    task = Task(f"⏰ {job['prompt'][:30]}", aid)
    world.tasks.append(task)
    a = world.by_id[aid]
    world.log(f"⏰ 定时任务触发 → {a.spec['emoji']} {a.spec['name']}：{job['prompt'][:40]}")
    world.dirty = True

    async def _run_and_cleanup():
        try:
            await runner.run(task)
        finally:
            scheduler.mark_done(job["id"])

    asyncio.get_event_loop().create_task(_run_and_cleanup())


async def on_startup(app):
    app["loop_task"] = asyncio.create_task(game_loop(app))
    # 预热 Skill / 模型缓存，避免首次打开设置页等待
    asyncio.create_task(agent_config.list_skills())
    asyncio.create_task(agent_config.list_models())


async def on_cleanup(app):
    app["loop_task"].cancel()
    await asyncio.gather(app["loop_task"], return_exceptions=True)
    storage.save_state(world.export_state())


def main():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_post("/api/task", task_handler)
    app.router.add_post("/api/task/{tid}/followup", followup_handler)
    app.router.add_post("/api/project", project_handler)
    app.router.add_post("/api/project/{pid}/cancel", project_cancel_handler)
    app.router.add_post("/api/project/{pid}/delete", project_delete_handler)
    app.router.add_post("/api/project/{pid}/retry", project_retry_handler)
    app.router.add_get("/api/project/{pid}/memory", project_memory_get)
    app.router.add_post("/api/project/{pid}/memory", project_memory_post)
    app.router.add_delete("/api/project/{pid}/memory/{idx}", project_memory_delete)
    app.router.add_post("/api/answer", answer_handler)
    app.router.add_post("/api/open-dir", open_dir_handler)
    app.router.add_post("/api/open-file", open_file_handler)
    app.router.add_post("/api/open-editor", open_in_editor_handler)
    app.router.add_post("/api/interject", interject_handler)
    app.router.add_get("/api/stream/{aid}", stream_handler)
    app.router.add_get("/api/browse", browse_handler)
    app.router.add_get("/api/config", config_get)
    app.router.add_post("/api/config/{aid}", config_post)
    app.router.add_post("/snap", snap_handler)
    app.router.add_get("/api/agents", agents_list_handler)
    app.router.add_post("/api/agents", agents_add_handler)
    app.router.add_delete("/api/agents/{aid}", agents_delete_handler)
    app.router.add_get("/api/schedules", schedules_list_handler)
    app.router.add_post("/api/schedules", schedules_add_handler)
    app.router.add_put("/api/schedules/{sid}", schedules_update_handler)
    app.router.add_delete("/api/schedules/{sid}", schedules_delete_handler)
    app.router.add_post("/api/schedules/{sid}/toggle", schedules_toggle_handler)
    # 产出文件预览（只暴露产出目录，不暴露源码）
    (ROOT / "workspace").mkdir(exist_ok=True)
    (ROOT / "projects").mkdir(exist_ok=True)
    app.router.add_static("/files/workspace/", ROOT / "workspace",
                          show_index=True)
    app.router.add_static("/files/projects/", ROOT / "projects",
                          show_index=True)
    app.router.add_static("/", FRONTEND)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    print("🌟 super-me [REAL MODE] running at http://localhost:8787")
    web.run_app(app, host="127.0.0.1", port=8787, print=None)


if __name__ == "__main__":
    main()
