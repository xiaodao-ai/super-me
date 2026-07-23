# -*- coding: utf-8 -*-
"""
真实任务编排器：用 qoder_agent_sdk 驱动分身真实干活。

流程：
  发布给 TL   → TL(LLM) 拆解为子任务 JSON → 按 stage 分批执行
  直接指派个人 → 单个子任务立即执行
每个子任务 = 一次真实 SDK 会话，在任务专属 workspace 目录里读写文件。
执行过程中的工具调用/文本输出实时回传到 3D 世界（气泡/活动/事件流）。
"""
import asyncio
import json
import os
import pathlib
import re
import time

from qoder_agent_sdk import query, QoderAgentOptions, qodercli_auth

import agent_config
import project_memory
from personas import (
    MEETING_CENTER, TL_SPLIT_PROMPT, LEADER_ID,
    PROJECT_PLAN_PROMPT, PROJECT_ACCEPT_PROMPT, TASK_ACCEPT_PROMPT,
    CLARIFY_PROMPT, EVOLVE_PROMPT, MEMBER_ASK_NOTE, TL_ANSWER_PROMPT,
    PROJECT_MEMORY_PROMPT,
)
from world import Subtask, PStep, PAssign

VALID_ROLES = ("dev", "qa", "reviewer")       # 内置角色（动态角色通过 _valid_roles() 获取）
WRITE_TOOLS = ("write", "edit", "multiedit", "searchreplace",
               "write_file", "edit_file", "create_file")
MAX_REWORK = 2                   # 每个验收点最多打回返工轮数
# 验收会话允许 TL 读文件，但禁止任何写操作与 Bash（覆盖大小写命名差异）
READONLY_DISALLOWED = [
    "Bash", "Write", "Edit", "MultiEdit", "SearchReplace",
    "write", "edit", "multiedit", "searchreplace",
    "write_file", "edit_file", "create_file",
]


class GlobalGate:
    """全局并发闸门：限制同时运行的 qodercli 会话数，上限可在线调整。"""

    def __init__(self, limit):
        self.limit = max(1, limit)
        self.active = 0
        self._cond = asyncio.Condition()

    async def __aenter__(self):
        async with self._cond:
            while self.active >= self.limit:
                await self._cond.wait()
            self.active += 1

    async def __aexit__(self, *_exc):
        async with self._cond:
            self.active -= 1
            self._cond.notify_all()

    async def set_limit(self, n):
        async with self._cond:
            self.limit = max(1, n)
            self._cond.notify_all()   # 调大时放行排队中的会话


class TaskRunner:
    def __init__(self, world, root: pathlib.Path):
        self.world = world
        self.ws_root = root / "workspace"
        self.ws_root.mkdir(exist_ok=True)
        self.locks = {a.id: asyncio.Lock() for a in world.agents}
        self.pending = {}          # (kind, id) → asyncio.Event 等待用户确认
        self.proj_tasks = {}       # proj.id → asyncio.Task（供手动终止）
        self.gate = GlobalGate(agent_config.get_global()["max_concurrency"])

    def _valid_roles(self, members=None):
        """获取当前可用的成员 id 集合（排除 TL）。
        members 非空时返回其与有效 agent 的交集；为空时返回全部。"""
        all_roles = {a.id for a in self.world.agents if a.id != LEADER_ID}
        if members:
            return all_roles & set(members)
        return all_roles

    def _members_desc(self, members=None):
        """生成项目可用成员的描述文本（供 TL 规划/拆解用）。"""
        roles = self._valid_roles(members)
        lines = []
        for a in self.world.agents:
            if a.id in roles:
                lines.append(f"- {a.id}：{a.spec['name']}（{a.spec['role']}）")
        return "\n".join(lines) or "- dev：全栈工程师"

    def ensure_lock(self, aid):
        """确保动态添加的 Agent 有对应的锁。"""
        if aid not in self.locks:
            self.locks[aid] = asyncio.Lock()

    def _gate_note(self, agent):
        """全局并发额度已满时，播报排队状态（在进入闸门前调用）。"""
        if self.gate.active >= self.gate.limit:
            agent.push_stream(
                "info", f"⏸ 等待全局并发额度（{self.gate.active}/{self.gate.limit} 占用中）")
            agent.activity = "⏸ 排队等并发额度"
            self.world.dirty = True

    # ── 手动终止项目 ─────────────────────────────────────
    def cancel_project(self, pid):
        """终止进行中的项目：标记状态并取消整条执行协程链。"""
        w = self.world
        proj = next((p for p in w.projects if p.id == pid), None)
        if not proj:
            return {"ok": False, "error": "项目不存在"}
        if proj.status in ("done", "failed", "canceled"):
            return {"ok": False, "error": "项目已结束，无需终止"}
        proj.status = "canceled"
        proj.error = "用户手动终止"
        proj.finished = time.time()
        for step in proj.steps:
            if step.status == "running":
                step.status = "failed"
            for a in step.assigns:
                if a.status == "running":
                    a.status = "failed"
        # 唤醒可能挂起的澄清等待，再取消执行协程（先标状态，取消不会被 except 覆盖）
        ev = self.pending.pop(("project", pid), None)
        if ev:
            ev.set()
        t = self.proj_tasks.pop(pid, None)
        if t and not t.done():
            t.cancel()
        # 重置相关分身的展示状态（协程 finally 也会做，这里保证即时生效）
        roles = {a.role for s in proj.steps for a in s.assigns} | {LEADER_ID}
        for r in roles:
            a = w.by_id.get(r)
            if a and getattr(a, "current", None) in (
                    None, *[g for s in proj.steps for g in s.assigns]):
                a.state = "idle"
                a.activity = ""
                a.current = None
                a.go_desk()
        w.by_id[LEADER_ID].say(f"「{proj.title}」已按你的要求叫停 🛑", 6)
        w.log(f"🛑 你手动终止了项目「{proj.title}」")
        w.dirty = True
        return {"ok": True}

    # ── 删除项目（从列表移除，可选删除文件）────────────────
    def delete_project(self, pid, delete_files=False):
        """删除项目：从列表中移除，进行中的先终止。delete_files=True 时删除项目目录。"""
        import shutil
        w = self.world
        proj = next((p for p in w.projects if p.id == pid), None)
        if not proj:
            return {"ok": False, "error": "项目不存在"}
        # 进行中的项目先终止
        if proj.status in ("clarifying", "waiting", "planning", "running"):
            self.cancel_project(pid)
        title = proj.title
        # 从列表移除
        w.projects = [p for p in w.projects if p.id != pid]
        # 删除文件
        if delete_files and proj.dir:
            target = self.ws_root.parent / proj.dir
            if target.is_dir():
                try:
                    shutil.rmtree(target)
                except Exception:
                    pass
        w.log(f"🗑 你删除了项目「{title}」" + ("（含文件）" if delete_files else ""))
        w.dirty = True
        return {"ok": True}

    # ── 澄清确认环节 ─────────────────────────────────────
    async def _clarify(self, kind, obj, title, desc):
        """TL 真实判断是否有疑问；有则挂起等待用户回答。
        kind 取 "task"/"project"（与前端 /api/answer 一致）。
        返回要注入后续执行的「用户澄清」上下文文本（可为空串）。"""
        kind_label = {"task": "任务", "project": "项目"}[kind]
        w = self.world
        tl = w.by_id[LEADER_ID]
        obj.status = "clarifying"
        w.dirty = True
        async with self.locks[LEADER_ID]:
            tl.activity = "🤔 审题中"
            tl.stream_tag = f"{kind_label}#{obj.id} 澄清"
            try:
                text = await self._query_text(
                    CLARIFY_PROMPT.format(kind=kind_label, title=title,
                                          desc=desc or "（无）"),
                    tl, timeout=180)
                data = self._parse_json(text)
                qs = []
                for q in data.get("questions", [])[:3]:
                    if q.get("q"):
                        qs.append({
                            "q": str(q["q"])[:120],
                            "options": [str(o)[:60]
                                        for o in q.get("options", [])][:4],
                        })
            except Exception:
                qs = []      # 澄清失败不阻塞，按原需求执行
            finally:
                tl.activity = ""

        if not qs:
            return ""

        obj.questions = qs
        obj.status = "waiting"
        w.dirty = True
        ev = asyncio.Event()
        self.pending[(kind, obj.id)] = ev
        tl.state = "meeting"
        tl.go(*MEETING_CENTER)
        tl.activity = "🙋 等待你的确认"
        tl.say(f"关于「{title}」我有 {len(qs)} 个问题想确认～", 12)
        w.log(f"🙋 队长桑对「{title}」有 {len(qs)} 个疑问，请在页面弹窗中确认")
        try:
            await ev.wait()
        finally:
            self.pending.pop((kind, obj.id), None)
            tl.state = "idle"
            tl.activity = ""
            tl.go_desk()
        answered = [q for q in obj.questions if q.get("answer")]
        if answered:
            w.log(f"🙆 已收到你对「{title}」的 {len(answered)} 条确认，继续推进")
            tl.say("明白了，按你说的办！", 6)
        else:
            w.log(f"⏭ 你选择跳过确认，「{title}」按队长的理解执行")
            tl.say("那我就按自己的理解来啦～", 6)
        qa = "\n".join(
            f"Q：{q['q']}\nA：{q.get('answer') or '（用户未回答，按你的最佳理解处理）'}"
            for q in obj.questions)
        return f"\n\n【用户澄清确认（必须遵守）】\n{qa}"

    def answer(self, kind, obj_id, answers):
        """用户提交回答（answers 与「未回答」的问题按顺序一一对应）。"""
        pool = self.world.tasks if kind == "task" else self.world.projects
        obj = next((o for o in pool if o.id == obj_id), None)
        if not obj or obj.status != "waiting":
            return {"ok": False, "error": "该确认请求已失效"}
        i = 0
        for q in obj.questions:
            if "answer" in q:        # 已答过的（如立项澄清）不再覆盖
                continue
            q["answer"] = (str(answers[i]).strip()[:120]
                           if i < len(answers) else "")
            i += 1
        ev = self.pending.get((kind, obj_id))
        if not ev:
            return {"ok": False, "error": "执行协程已不存在（服务可能重启过）"}
        ev.set()
        return {"ok": True}

    # ── 用户插话纠正 ─────────────────────────────────────
    def interject(self, aid, hint):
        """执行中：中断当前会话并携带纠正重跑；空闲时：注入下一次执行。"""
        a = self.world.by_id.get(aid)
        hint = (hint or "").strip()[:200]
        if not a or not hint:
            return {"ok": False, "error": "请填写纠正内容"}
        a.hints = (getattr(a, "hints", []) + [hint])[-5:]
        a.hint_log = (getattr(a, "hint_log", []) + [hint])[-10:]  # 供复盘沉淀
        a.push_stream("info", f"💬 用户插话：{hint}")
        self.world.log(f"💬 你对 {a.spec['emoji']} {a.spec['name']} 插话：{hint[:60]}")
        task = getattr(a, "exec_task", None)
        if task and not task.done():
            a.interrupted = True
            task.cancel()
            a.say("收到！中断当前思路，马上调整 🫡", 7)
            return {"ok": True, "applied": "restart",
                    "msg": "已中断当前会话，携带纠正重跑"}
        a.say("记下了，下个任务就用上", 6)
        return {"ok": True, "applied": "queued",
                "msg": "当前空闲，将注入下一次执行"}

    async def _execute_with_hints(self, base_prompt, agent, sub, cwd,
                                  timeout, force_cwd=False):
        """执行 + 插话重跑循环：被用户插话中断时带着纠正提示重来。"""
        while True:
            prompt = base_prompt
            hints = getattr(agent, "hints", [])
            if hints:
                prompt += ("\n\n【用户即时纠正（最高优先级，必须遵守）】\n"
                           + "\n".join(f"- {h}" for h in hints)
                           + "\n（工作目录里可能已有部分产出，请在其基础上按纠正调整。）")
            agent.interrupted = False
            try:
                return await self._execute(prompt, agent, sub, cwd,
                                           timeout, force_cwd)
            except asyncio.CancelledError:
                if getattr(agent, "interrupted", False):
                    agent.push_stream("info", "⟲ 会话已被你中断，携带纠正重跑")
                    agent.activity = "⟲ 按纠正重跑"
                    self.world.dirty = True
                    continue
                raise

    # ── 成员提问链：队长解答 → 拿不准升级问用户 → 继续执行 ──
    async def _execute_with_qa(self, prompt, agent, sub, kind, obj, cwd,
                               timeout, force_cwd=False):
        """成员会话末尾输出 QUESTION: 求助时，走解答链后带答案续跑（最多 1 轮）。"""
        w = self.world
        full = prompt + MEMBER_ASK_NOTE
        summary = await self._execute_with_hints(
            full, agent, sub, cwd=cwd, timeout=timeout, force_cwd=force_cwd)
        m = re.search(r"QUESTION[:：]\s*(\S[\s\S]*)", summary or "")
        if not m:
            return summary
        question = m.group(1).strip()[:300]
        agent.say("遇到关键问题，先问下队长 🙋", 6)
        agent.activity = "🙋 等待解答"
        agent.push_stream("info", f"🙋 向队长求助：{question[:150]}")
        w.log(f"🙋 {agent.spec['emoji']} {agent.spec['name']} 提问：{question[:60]}")
        w.dirty = True
        answer = await self._resolve_member_question(kind, obj, agent, question)
        agent.push_stream("info", f"💡 收到解答：{answer[:200]}")
        full += (f"\n\n【你此前的提问】{question}\n【解答】{answer}\n"
                 "请基于解答直接完成任务，不要再提问。")
        return await self._execute_with_hints(
            full, agent, sub, cwd=cwd, timeout=timeout, force_cwd=force_cwd)

    async def _resolve_member_question(self, kind, obj, agent, question):
        """三级解答：TL 真实判断能否拍板；拿不准就挂起升级给用户确认。"""
        w = self.world
        tl = w.by_id[LEADER_ID]
        kind_label = {"task": "任务", "project": "项目"}[kind]
        data = {}
        async with self.locks[LEADER_ID]:
            tl.activity = "💬 解答成员提问"
            tl.stream_tag = f"{kind_label}#{obj.id} 答疑"
            try:
                context = (f"{kind_label}「{obj.title}」"
                           + (f"：{obj.desc[:200]}" if getattr(obj, "desc", "") else "")
                           + getattr(obj, "clarify_ctx", ""))
                text = await self._query_text(
                    TL_ANSWER_PROMPT.format(name=agent.spec["name"],
                                            context=context, question=question),
                    tl, timeout=180)
                data = self._parse_json(text)
            except Exception:
                data = {}
            finally:
                tl.activity = ""
        if data.get("verdict") == "answer" and data.get("answer"):
            tl.say("这个我来拍板 👌", 5)
            w.log(f"👑 队长桑解答了 {agent.spec['emoji']} {agent.spec['name']} 的疑问")
            w.dirty = True
            return f"队长的解答：{str(data['answer'])[:400]}"
        # 队长拿不准 → 追加问题并挂起，复用确认弹窗 / /api/answer
        q_entry = {
            "q": (f"{agent.spec['emoji']} {agent.spec['name']} 求助："
                  + str(data.get("question") or question)[:200]),
            "options": [str(o)[:60] for o in data.get("options", [])][:4],
            "src": "member",
        }
        obj.questions = obj.questions + [q_entry]
        prev = obj.status
        obj.status = "waiting"
        tl.activity = "🙋 等待你的确认"
        w.log(f"🙋 队长桑也拿不准，「{obj.title}」的疑问升级给你确认")
        w.dirty = True
        ev = asyncio.Event()
        self.pending[(kind, obj.id)] = ev
        try:
            await asyncio.wait_for(ev.wait(), timeout=24 * 3600)
        except asyncio.TimeoutError:
            pass
        finally:
            self.pending.pop((kind, obj.id), None)
            if obj.status == "waiting":     # 手动终止时不要覆盖 canceled 状态
                obj.status = prev
            tl.activity = ""
            w.dirty = True
        ans = (q_entry.get("answer") or "").strip()
        if ans:
            w.log(f"💬 你的解答已转给 {agent.spec['emoji']} {agent.spec['name']}")
            return f"用户（需求方）的解答：{ans}"
        return "需求方未给出明确答案，请按你的最佳判断选择最常规的方案继续。"

    # ── 复盘自进化：任务/项目结束后沉淀规则 ────────────────
    async def _evolve(self, kind, title, context, participants):
        """有返工/失败/插话/澄清等信号时，TL 复盘并更新成员自学规则。"""
        w = self.world
        tl = w.by_id[LEADER_ID]
        try:
            async with self.locks[LEADER_ID]:
                tl.activity = "🧠 复盘沉淀中"
                tl.stream_tag = f"复盘·{title[:10]}"
                text = await self._query_text(
                    EVOLVE_PROMPT.format(kind=kind, title=title,
                                         context=context), tl, timeout=180)
                tl.activity = ""
            data = self._parse_json(text)
            for r in data.get("rules", [])[:4]:
                role, rule = r.get("role"), (r.get("rule") or "").strip()
                if role not in list(VALID_ROLES) + [LEADER_ID] or not rule:
                    continue
                if role not in participants and role != LEADER_ID:
                    continue
                if agent_config.add_learned(role, rule):
                    m = w.by_id[role]
                    w.log(f"🧠 自进化：{m.spec['emoji']} {m.spec['name']} 学到新规则「{rule[:70]}」")
                    m.say("学到了，下次注意！📝", 7)
            w.dirty = True
        except Exception:
            tl.activity = ""       # 复盘失败静默跳过，不影响交付

    @staticmethod
    def _has_signal(*groups):
        return any(groups)

    async def _maybe_evolve_task(self, task):
        """任务收尾复盘：有返工/失败/插话/澄清信号时沉淀规则。"""
        w = self.world
        parts = sorted({s.role for s in task.subtasks})
        hint_logs = {r: list(getattr(w.by_id[r], "hint_log", []))
                     for r in parts + [LEADER_ID]
                     if getattr(w.by_id[r], "hint_log", [])}
        reworked = [s for s in task.subtasks if getattr(s, "rework_round", 0)]
        failed = [s for s in task.subtasks if s.status == "failed"]
        answered = [q for q in task.questions if q.get("answer")]
        if not self._has_signal(hint_logs, reworked, failed, answered):
            return
        lines = [f"- 澄清 Q：{q['q']} → 用户答：{q.get('answer') or '（跳过）'}"
                 for q in task.questions]
        for s in task.subtasks:
            l = f"- [{s.role}]「{s.title}」{s.status}：{s.summary[:80]}"
            if getattr(s, "rework_round", 0):
                l += f"（被打回返工 {s.rework_round} 轮）"
            lines.append(l)
        for r, hl in hint_logs.items():
            lines.append(f"- 用户对 {r} 的插话纠正：{'；'.join(hl)}")
        await self._evolve("任务", task.title, "\n".join(lines), parts)
        for r in hint_logs:
            w.by_id[r].hint_log = []

    async def _maybe_evolve_project(self, proj):
        """项目收尾复盘：同任务，粒度到步骤。"""
        w = self.world
        parts = sorted({a.role for s in proj.steps for a in s.assigns})
        hint_logs = {r: list(getattr(w.by_id[r], "hint_log", []))
                     for r in parts + [LEADER_ID]
                     if getattr(w.by_id[r], "hint_log", [])}
        reworked = [a for s in proj.steps for a in s.assigns
                    if getattr(a, "rework_round", 0)]
        failed = [a for s in proj.steps for a in s.assigns
                  if a.status == "failed"]
        answered = [q for q in proj.questions if q.get("answer")]
        if not self._has_signal(hint_logs, reworked, failed, answered):
            return
        lines = [f"- 澄清 Q：{q['q']} → 用户答：{q.get('answer') or '（跳过）'}"
                 for q in proj.questions]
        for i, s in enumerate(proj.steps):
            lines.append(f"- 步骤{i + 1}「{s.title}」{s.status}，队长验收：{s.review[:80]}")
            for a in s.assigns:
                if getattr(a, "rework_round", 0):
                    lines.append(f"  - [{a.role}] 被打回返工 {a.rework_round} 轮")
        for r, hl in hint_logs.items():
            lines.append(f"- 用户对 {r} 的插话纠正：{'；'.join(hl)}")
        await self._evolve("项目", proj.title, "\n".join(lines), parts)
        for r in hint_logs:
            w.by_id[r].hint_log = []

    # ── 项目记忆沉淀：每步验收通过后 + 项目结项时 ──────────
    async def _evolve_project_memory(self, proj, pdir, extra=""):
        """TL 提炼项目记忆，写入项目目录 .memory.json。"""
        w = self.world
        tl = w.by_id[LEADER_ID]
        try:
            existing = project_memory.format_for_prompt(pdir) or "（暂无）"
            async with self.locks[LEADER_ID]:
                tl.activity = "📝 沉淀项目记忆"
                tl.stream_tag = f"项目「{proj.title[:10]}」记忆"
                text = await self._query_text(
                    PROJECT_MEMORY_PROMPT.format(
                        title=proj.title, desc=proj.desc or "（无）",
                        extra=extra, existing=existing),
                    tl, timeout=180)
                tl.activity = ""
            data = self._parse_json(text)
            items = data.get("memories", [])[:4]
            added = project_memory.add_batch(pdir, items)
            if added:
                w.log(f"📝 项目「{proj.title}」沉淀了 {added} 条项目记忆")
                tl.say(f"记下了 {added} 条项目经验 📝", 6)
            w.dirty = True
        except Exception:
            tl.activity = ""       # 记忆沉淀失败静默跳过，不影响交付

    # ── 入口 ──────────────────────────────────────────────
    async def run(self, task):
        w = self.world
        try:
            # 开工前澄清：TL 有疑问就先问用户
            task.clarify_ctx = await self._clarify(
                "task", task, task.title, "")
            if task.assignee == LEADER_ID:
                ok = await self._split(task)
                if not ok:
                    return
            else:
                a = w.by_id[task.assignee]
                task.subtasks = [Subtask(task.assignee, task.title,
                                         f"完成任务：{task.title}", 1)]
                w.log(f"📮 「{task.title}」直接指派给 {a.spec['emoji']} {a.spec['name']}")

            # 任务工作目录（真实文件产出地）
            slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", task.title)[:24].strip("-")
            workdir = self.ws_root / f"task-{task.id}-{slug}"
            workdir.mkdir(exist_ok=True)
            task.workdir = str(workdir.relative_to(self.ws_root.parent))
            (workdir / "TASK.md").write_text(
                f"# {task.title}\n\n发布时间：{time.strftime('%F %T', time.localtime(task.created))}\n\n"
                + "\n".join(f"- [{s.role}] {s.title}：{s.brief}" for s in task.subtasks),
                encoding="utf-8")

            task.status = "running"
            w.dirty = True
            for stage in sorted({s.stage for s in task.subtasks}):
                batch = [s for s in task.subtasks if s.stage == stage]
                await asyncio.gather(*(self._run_subtask(task, s) for s in batch))

            failed = [s for s in task.subtasks if s.status != "done"]
            # TL 拆解的任务：交付前终验，不合格打回返工
            if not failed and task.assignee == LEADER_ID and task.subtasks:
                await self._accept_task(task)
                failed = [s for s in task.subtasks if s.status != "done"]
            task.status = "failed" if failed else "done"
            task.finished = time.time()
            files = sorted(p.name for p in workdir.iterdir() if p.is_file())
            if failed:
                w.log(f"⚠️ 任务「{task.title}」部分失败（{len(failed)}/{len(task.subtasks)}）")
            else:
                w.log(f"🎉 任务「{task.title}」全部完成！产出：{'、'.join(files[:8])}")
                w.by_id[LEADER_ID].say(f"「{task.title}」交付完毕！🚀")
            await self._maybe_evolve_task(task)     # 复盘自进化
        except Exception as e:
            task.status = "failed"
            task.error = str(e)[:200]
            w.log(f"❌ 任务「{task.title}」异常：{str(e)[:80]}")
        finally:
            w.dirty = True

    # ── 已完成任务的追问（原班人马在原目录继续）────────────
    async def followup_task(self, task, message):
        """对已完成/失败的任务追问：在原工作目录基于已有产出继续会话。"""
        w = self.world
        # 执行人：直接指派的本人；TL 拆解的选产出最多的成员
        # （历史任务的旧角色如 fe/be 已不存在，统一兜底到现有成员）
        if task.assignee != LEADER_ID and task.assignee in w.by_id:
            role = task.assignee
        else:
            done_roles = [s.role for s in task.subtasks
                          if s.status == "done" and s.role in w.by_id]
            role = max(set(done_roles), key=done_roles.count) if done_roles else "dev"
        agent = w.by_id[role]
        prev = "\n".join(
            f"- [{s.role}] {s.title}：{(s.summary or '完成')[:100]}"
            for s in task.subtasks if s.summary)
        try:
            files = sorted(p.name for p in
                           (self.ws_root.parent / task.workdir).iterdir()
                           if p.is_file()) if task.workdir else []
        except OSError:
            files = []
        brief = (
            f"这是用户对已完成任务「{task.title}」的追问，"
            f"必须基于当前目录里的已有产出继续，不要另起炉灶。\n"
            f"用户追问内容：{message}\n\n"
            f"此前各子任务产出：\n{prev or '（无）'}\n"
            f"当前目录已有文件：{'、'.join(files[:12]) or '（空）'}\n\n"
            f"要求：先阅读当前目录的现有文件，再按追问修改或扩展；"
            f"完成后用 3 句话以内说明改了什么、产出在哪个文件。")
        sub = Subtask(role, f"追问：{message[:18]}", brief, 9)
        sub.force_task_dir = True      # 固定在任务目录，不走个人专属目录
        task.subtasks.append(sub)
        task.status = "running"
        task.finished = None
        task.error = ""
        w.log(f"💬 追问任务「{task.title}」，{agent.spec['emoji']} {agent.spec['name']} 在原目录继续")
        agent.say(f"收到追问：{message[:26]}", 8)
        w.dirty = True
        try:
            await self._run_subtask(task, sub)
            task.status = "failed" if sub.status != "done" else "done"
            task.finished = time.time()
            if sub.status == "done":
                w.log(f"🎉 「{task.title}」追问处理完成：{(sub.summary or '')[:60]}")
        except Exception as e:
            task.status = "failed"
            task.error = str(e)[:200]
            w.log(f"❌ 「{task.title}」追问失败：{str(e)[:80]}")
        finally:
            w.dirty = True

    # ── TL 真实拆解 ───────────────────────────────────────
    async def _split(self, task):
        w = self.world
        tl = w.by_id[LEADER_ID]
        task.status = "splitting"
        w.dirty = True
        async with self.locks[LEADER_ID]:
            tl.state = "meeting"
            tl.go(*MEETING_CENTER)
            tl.activity = "🧠 需求拆解中"
            tl.stream_tag = f"任务#{task.id} 拆解"
            tl.say(f"收到需求「{task.title}」，我来拆解")
            w.log(f"👑 队长桑开始拆解「{task.title}」（真实 LLM 调用）")
            try:
                text = await self._query_text(
                    TL_SPLIT_PROMPT.format(title=task.title)
                    + getattr(task, "clarify_ctx", ""),
                    tl, timeout=240)
                data = self._parse_json(text)
                subs = []
                for d in data.get("subtasks", [])[:4]:
                    role = d.get("role")
                    if role in VALID_ROLES and d.get("title"):
                        subs.append(Subtask(role, d["title"][:60],
                                            d.get("brief", "")[:200],
                                            int(d.get("stage", 1))))
                if not subs:
                    raise ValueError("拆解结果为空")
                task.subtasks = subs
                for s in subs:
                    m = w.by_id[s.role]
                    w.add_link(LEADER_ID, s.role, s.title[:14])
                    w.log(f"🧩 拆解 → {m.spec['emoji']} {m.spec['name']}：「{s.title}」")
                tl.say(f"拆成 {len(subs)} 个子任务，开工！")
                return True
            except Exception as e:
                task.status = "failed"
                task.error = f"拆解失败：{str(e)[:120]}"
                w.log(f"❌ 拆解「{task.title}」失败：{str(e)[:80]}")
                tl.say("这个需求我拆不动了…😢")
                return False
            finally:
                tl.state = "idle"
                tl.activity = ""
                tl.go_desk()
                w.dirty = True

    # ── 子任务真实执行 ────────────────────────────────────
    async def _run_subtask(self, task, sub):
        w = self.world
        agent = w.by_id[sub.role]
        rework_note = getattr(sub, "rework_note", "")
        async with self.locks[sub.role]:
            sub.status = "running"
            sub.started = time.time()
            agent.state = "working"
            agent.current = sub
            agent.go_desk()
            agent.stream_tag = f"任务#{task.id} {sub.title[:12]}"
            if rework_note:
                agent.activity = "🔁 整改中"
                agent.say("收到整改意见，马上改！", 8)
                w.log(f"🔁 {agent.spec['emoji']} {agent.spec['name']} 开始整改「{sub.title}」")
            else:
                agent.activity = "🚀 启动会话"
                agent.say(f"开工：{sub.title}", 8)
                model = agent_config.get(sub.role)["model"]
                w.log(f"🔧 {agent.spec['emoji']} {agent.spec['name']} 开始「{sub.title}」"
                      + (f"（模型：{model}）" if model else ""))
            w.dirty = True
            abs_workdir = str((self.ws_root.parent / task.workdir).resolve())
            prompt = (f"子任务：{sub.title}\n具体要求：{sub.brief}\n"
                      f"（所属需求：{task.title}）\n"
                      f"工作目录（绝对路径）：{abs_workdir}\n"
                      f"所有产出文件必须直接写入该工作目录，禁止另建任务目录/子文件夹，"
                      f"也不要把文件写到工作目录之外。"
                      + getattr(task, "clarify_ctx", ""))
            if rework_note:
                prompt += (f"\n\n【队长终验未通过，这是第 "
                           f"{getattr(sub, 'rework_round', 1)} 轮整改，"
                           f"必须优先解决以下问题】\n{rework_note}\n"
                           f"（你此前的产出总结：{sub.summary[:150]}。"
                           f"请在原有产出基础上修改，不要推倒重来。）")
            try:
                summary = await self._execute_with_qa(
                    prompt, agent, sub, "task", task,
                    cwd=self.ws_root.parent / task.workdir, timeout=600,
                    # TL 拆解的多人协作任务必须在任务目录聚合文件；
                    # 只有用户直接指派的单人任务才使用成员专属目录；
                    # 追问会话固定在原任务目录
                    force_cwd=(task.assignee == LEADER_ID
                               or getattr(sub, "force_task_dir", False)))
                sub.status = "done"
                sub.rework_note = ""
                sub.summary = (summary or "已完成")[:200]
                agent.done_count += 1
                files = "、".join(sub.files[:5]) or "见工作目录"
                w.log(f"✅ {agent.spec['emoji']} {agent.spec['name']} 完成「{sub.title}」 📁 {files}")
                agent.say(sub.summary[:48] or "搞定！✅", 8)
            except Exception as e:
                sub.status = "failed"
                sub.summary = f"失败：{str(e)[:150]}"
                w.log(f"❌ {agent.spec['emoji']} {agent.spec['name']} 「{sub.title}」失败：{str(e)[:60]}")
                agent.say("呜，这个任务翻车了…", 6)
            finally:
                sub.finished = time.time()
                agent.state = "idle"
                agent.current = None
                agent.activity = ""
                agent.hints = []          # 插话只作用于本次执行
                w.dirty = True

    # ── 项目编排：TL 规划 → 逐步执行 → TL 点评督促 → 结项 ──
    async def run_project(self, proj):
        w = self.world
        tl = w.by_id[LEADER_ID]
        try:
            # 立项前澄清：TL 有疑问就先问用户
            proj.clarify_ctx = await self._clarify(
                "project", proj, proj.title, proj.desc)
            proj.status = "planning"
            w.dirty = True
            # 先确定项目目录路径（规划时需要告知 TL 绝对路径）
            if proj.folder:
                pdir = self.ws_root.parent / "projects" / proj.folder
            else:
                slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", proj.title)[:24].strip("-")
                pdir = self.ws_root.parent / "projects" / f"proj-{proj.id}-{slug}"
            abs_pdir = str(pdir.resolve())
            # 1) TL 真实规划
            async with self.locks[LEADER_ID]:
                tl.state = "meeting"
                tl.go(*MEETING_CENTER)
                tl.activity = "📋 项目规划中"
                tl.stream_tag = f"项目「{proj.title[:10]}」规划"
                tl.say(f"新项目「{proj.title}」，我来做整体规划", 8)
                w.log(f"🗂 项目「{proj.title}」立项，队长桑开始规划（真实 LLM）")
                w.dirty = True
                try:
                    proj_roles = self._valid_roles(proj.members)
                    text = await self._query_text(
                        PROJECT_PLAN_PROMPT.format(
                            title=proj.title, desc=proj.desc or "（无补充描述）",
                            project_dir=abs_pdir,
                            members_desc=self._members_desc(proj.members))
                        + getattr(proj, "clarify_ctx", ""),
                        tl, timeout=300)
                    data = self._parse_json(text)
                    steps = []
                    for sd in data.get("steps", [])[:4]:
                        assigns = [PAssign(a["role"], a.get("brief", "")[:300])
                                   for a in sd.get("assignments", [])[:3]
                                   if a.get("role") in proj_roles]
                        if sd.get("title") and assigns:
                            steps.append(PStep(sd["title"][:60], assigns))
                    if not steps:
                        raise ValueError("规划结果为空")
                    proj.steps = steps
                finally:
                    tl.state = "idle"
                    tl.activity = ""
                    tl.go_desk()

            # 2) 建项目目录：shared/ + members/<role>/
            (pdir / "shared").mkdir(parents=True, exist_ok=True)
            roles = {a.role for s in proj.steps for a in s.assigns}
            for r in roles:
                (pdir / "members" / r).mkdir(parents=True, exist_ok=True)
            proj.dir = str(pdir.relative_to(self.ws_root.parent))
            plan_md = [f"# 项目：{proj.title}", "", proj.desc or "", "", "## 计划"]
            for i, s in enumerate(proj.steps):
                plan_md.append(f"\n### 步骤 {i + 1}：{s.title}")
                plan_md += [f"- [{a.role}] {a.brief}" for a in s.assigns]
            (pdir / "PROJECT.md").write_text("\n".join(plan_md), encoding="utf-8")
            names = "、".join(w.by_id[r].spec["name"] for r in roles)
            w.log(f"📋 规划完成：{len(proj.steps)} 个步骤，参与成员：{names}")
            tl.say(f"计划排好了，{len(proj.steps)} 步走起！", 6)

            # 3) 逐步推进：执行 → 队长验收 → 不合格打回返工 → 通过才放行
            proj.status = "running"
            w.dirty = True
            for i, step in enumerate(proj.steps):
                step.status = "running"
                w.log(f"📍 项目步骤 {i + 1}/{len(proj.steps)}「{step.title}」启动")
                for a in step.assigns:
                    w.add_link(LEADER_ID, a.role, step.title[:14])
                w.dirty = True
                await asyncio.gather(
                    *(self._run_passign(proj, step, a, pdir) for a in step.assigns))
                failed = [a for a in step.assigns if a.status != "done"]
                if not failed:
                    await self._accept_step(proj, i, step, pdir)   # 验收+返工循环
                    failed = [a for a in step.assigns if a.status != "done"]
                step.status = "failed" if failed else "done"
                if failed:
                    proj.status = "failed"
                    proj.error = f"步骤「{step.title}」有 {len(failed)} 个任务失败"
                    w.log(f"⚠️ 项目「{proj.title}」在步骤 {i + 1} 受阻，队长暂停推进")
                    return
                # 每步验收通过后沉淀项目记忆
                step_extra = (f"刚完成步骤 {i + 1}「{step.title}」，"
                              f"队长验收：{step.review[:150]}")
                await self._evolve_project_memory(proj, pdir, extra=step_extra)
            proj.status = "done"
            proj.finished = time.time()
            shared = sorted(p.name for p in (pdir / "shared").iterdir()
                            if p.is_file())
            w.log(f"🏁 项目「{proj.title}」全部完成！shared/ 交付：{'、'.join(shared[:8]) or '见项目目录'}")
            tl.say(f"项目「{proj.title}」顺利结项！🎊", 8)
            # 结项时最终沉淀一次项目记忆（汇总全局视角）
            final_extra = (f"项目已全部完成，共 {len(proj.steps)} 个步骤。"
                           f"shared/ 交付物：{'、'.join(shared[:8]) or '见目录'}")
            await self._evolve_project_memory(proj, pdir, extra=final_extra)
            await self._maybe_evolve_project(proj)  # 复盘自进化
        except Exception as e:
            proj.status = "failed"
            proj.error = str(e)[:200]
            w.log(f"❌ 项目「{proj.title}」异常：{str(e)[:80]}")
            tl.say("项目推进遇到问题了…", 6)
        finally:
            self.proj_tasks.pop(proj.id, None)
            w.dirty = True

    # ── 项目失败后重试：从失败的步骤继续执行 ───────────
    async def retry_project(self, proj, hint=""):
        """对失败的项目从失败步骤继续执行。hint 为用户补充指示。"""
        w = self.world
        tl = w.by_id[LEADER_ID]
        if proj.status != "failed":
            return
        # 找到第一个失败的步骤
        start_idx = 0
        for i, step in enumerate(proj.steps):
            if step.status == "failed":
                start_idx = i
                break
        pdir = self.ws_root.parent / proj.dir if proj.dir else None
        if not pdir or not pdir.is_dir():
            proj.error = "项目目录不存在，无法重试"
            w.dirty = True
            return
        # 用户补充指示注入 clarify_ctx，供后续执行使用
        if hint:
            existing_ctx = getattr(proj, "clarify_ctx", "") or ""
            proj.clarify_ctx = (existing_ctx
                                + f"\n\n【用户追加指示（重试时提供，必须遵守）】\n{hint}")
        proj.status = "running"
        proj.error = ""
        w.log(f"🔄 项目「{proj.title}」从步骤 {start_idx + 1} 重试"
              + (f"（用户指示：{hint[:40]}）" if hint else ""))
        tl.say(f"「{proj.title}」重试，从步骤 {start_idx + 1} 继续！", 8)
        w.dirty = True
        try:
            for i in range(start_idx, len(proj.steps)):
                step = proj.steps[i]
                # 重置失败的子任务状态
                for a in step.assigns:
                    if a.status == "failed":
                        a.status = "pending"
                        a.summary = ""
                step.status = "running"
                w.log(f"📍 项目步骤 {i + 1}/{len(proj.steps)}「{step.title}」重试启动")
                for a in step.assigns:
                    w.add_link(LEADER_ID, a.role, step.title[:14])
                w.dirty = True
                redo = [a for a in step.assigns if a.status == "pending"]
                if redo:
                    await asyncio.gather(
                        *(self._run_passign(proj, step, a, pdir) for a in redo))
                failed = [a for a in step.assigns if a.status != "done"]
                if not failed:
                    await self._accept_step(proj, i, step, pdir)
                    failed = [a for a in step.assigns if a.status != "done"]
                step.status = "failed" if failed else "done"
                if failed:
                    proj.status = "failed"
                    proj.error = f"步骤「{step.title}」仍有 {len(failed)} 个任务失败"
                    w.log(f"⚠️ 项目「{proj.title}」重试后仍在步骤 {i + 1} 受阻")
                    return
                step_extra = (f"重试完成步骤 {i + 1}「{step.title}」，"
                              f"队长验收：{step.review[:150]}")
                await self._evolve_project_memory(proj, pdir, extra=step_extra)
            proj.status = "done"
            proj.finished = time.time()
            shared = sorted(p.name for p in (pdir / "shared").iterdir()
                            if p.is_file())
            w.log(f"🏁 项目「{proj.title}」重试后全部完成！")
            tl.say(f"项目「{proj.title}」终于完成了！🎊", 8)
            await self._maybe_evolve_project(proj)
        except Exception as e:
            proj.status = "failed"
            proj.error = str(e)[:200]
            w.log(f"❌ 项目「{proj.title}」重试异常：{str(e)[:80]}")
        finally:
            self.proj_tasks.pop(proj.id, None)
            w.dirty = True

    async def _run_passign(self, proj, step, assign, pdir):
        """项目里某成员的一步任务（cwd 固定在项目根，保证文件聚合）。"""
        w = self.world
        agent = w.by_id[assign.role]
        rework_note = getattr(assign, "rework_note", "")
        async with self.locks[assign.role]:
            assign.status = "running"
            assign.title = step.title          # 供成员卡/快照展示
            agent.state = "working"
            agent.current = assign
            agent.go_desk()
            agent.stream_tag = f"项目「{proj.title[:10]}」{step.title[:12]}"
            if rework_note:
                agent.activity = "🔁 整改中"
                agent.say("收到整改意见，马上改！", 8)
                w.log(f"🔁 {agent.spec['emoji']} {agent.spec['name']} 开始整改「{step.title}」")
            else:
                agent.activity = "🚀 项目任务"
                agent.say(f"项目任务：{step.title}", 8)
                w.log(f"🔧 {agent.spec['emoji']} {agent.spec['name']} 开始项目任务「{step.title}」")
            w.dirty = True
            done_ctx = "\n".join(
                f"- 已完成步骤「{s.title}」：" + "；".join(
                    f"{a.role}:{(a.summary or '完成')[:60]}" for a in s.assigns)
                for s in proj.steps if s.status == "done") or "（这是第一个步骤）"
            # 注入项目记忆：让 Agent 快速感知项目上下文
            mem_ctx = project_memory.format_for_prompt(pdir)
            abs_pdir = str(pdir.resolve())
            prompt = (
                f"项目：{proj.title}\n项目描述：{proj.desc or '无'}\n"
                f"当前步骤：{step.title}\n你的任务：{assign.brief}\n\n"
                f"项目根目录（绝对路径）：{abs_pdir}\n"
                f"目录规范（必须使用绝对路径读写文件）：\n"
                f"- 最终交付物写入：{abs_pdir}/shared/\n"
                f"- 个人草稿放入：{abs_pdir}/members/{assign.role}/\n"
                f"先阅读 {abs_pdir}/shared/ 里已有的产出再动手。\n"
                f"❗目录约束：你只能在 {abs_pdir} 内操作，严禁 cd 到其他目录或在项目外读写文件。\n"
                f"❗路径要求：所有文件操作必须使用绝对路径（如 {abs_pdir}/shared/xxx.html），"
                f"不要使用相对路径。总结产出时也请写明绝对路径。\n\n"
                f"此前进展：\n{done_ctx}"
                + (f"\n\n{mem_ctx}" if mem_ctx else "")
                + getattr(proj, "clarify_ctx", ""))
            if rework_note:
                prompt += (f"\n\n【队长验收未通过，这是第 "
                           f"{getattr(assign, 'rework_round', 1)} 轮整改，"
                           f"必须优先解决以下问题】\n{rework_note}\n"
                           f"（你此前的产出总结：{assign.summary[:150]}。"
                           f"请在原有产出基础上修改，不要推倒重来。）")
            try:
                summary = await self._execute_with_qa(
                    prompt, agent, assign, "project", proj,
                    cwd=pdir, timeout=600, force_cwd=True)
                assign.status = "done"
                assign.rework_note = ""
                assign.summary = (summary or "已完成")[:200]
                agent.done_count += 1
                w.log(f"✅ {agent.spec['emoji']} {agent.spec['name']} 完成项目任务"
                      f" 📁 {'、'.join(assign.files[:5]) or 'shared/'}")
                agent.say(assign.summary[:48] or "这步搞定！", 8)
            except Exception as e:
                assign.status = "failed"
                assign.summary = f"失败：{str(e)[:150]}"
                w.log(f"❌ {agent.spec['emoji']} {agent.spec['name']} 项目任务失败：{str(e)[:60]}")
            finally:
                agent.state = "idle"
                agent.current = None
                agent.activity = ""
                agent.hints = []          # 插话只作用于本次执行
                w.dirty = True

    async def _tl_verdict(self, prompt, cwd):
        """TL 真实验收：打开 cwd 下交付文件检查后裁决。超时自动重试一次。
        返回 (verdict, comment, rework)。"""
        tl = self.world.by_id[LEADER_ID]
        for attempt in range(2):   # 最多尝试 2 次
            try:
                text = await self._query_review(prompt, tl, cwd=cwd, timeout=300)
                data = self._parse_json(text)
                verdict = "rework" if data.get("verdict") == "rework" else "pass"
                comment = str(data.get("comment", ""))[:300]
                rework = [r for r in data.get("rework", [])
                          if r.get("role") in self._valid_roles()
                          and r.get("instruction")][:3]
                if verdict == "rework" and not rework:
                    verdict = "pass"        # 没给整改对象就当通过
                return verdict, comment, rework
            except Exception as e:
                if attempt == 0:
                    tl.push_stream("info", f"⚠ 验收第 1 次失败（{type(e).__name__}），重试中…")
                    self.world.dirty = True
                    await asyncio.sleep(2)   # 短暂等待后重试
                    continue
                # 第 2 次仍失败：视为通过但记录警告
                self.world.log(f"⚠️ 队长验收超时/异常，视为通过（{str(e)[:60]}）")
                return "pass", f"（验收超时，视为通过：{str(e)[:80]}）", []
        return "pass", "（验收异常，视为通过）", []

    async def _accept_step(self, proj, idx, step, pdir):
        """队长验收本步骤；不合格则带整改意见打回返工，最多 MAX_REWORK 轮。"""
        w = self.world
        tl = w.by_id[LEADER_ID]
        for rnd in range(1, MAX_REWORK + 2):
            final_round = rnd > MAX_REWORK
            summaries = "\n".join(
                f"- {w.by_id[a.role].spec['name']}（{a.status}）：{a.summary[:100]}"
                for a in step.assigns)
            rest = [s.title for s in proj.steps[idx + 1:]]
            remaining = ("剩余步骤：" + "、".join(rest)) if rest else "这是最后一步。"
            rework_note = (f"注意：本步骤已返工 {rnd - 1} 次，达到上限，"
                           "只能输出 pass，并在 comment 中记录遗留问题。"
                           if final_round else
                           (f"这是第 {rnd - 1} 次返工后的复验。" if rnd > 1 else ""))
            async with self.locks[LEADER_ID]:
                tl.state = "meeting"
                tl.go(*MEETING_CENTER)
                tl.activity = "🧐 验收产出中"
                tl.stream_tag = f"项目「{proj.title[:10]}」步骤{idx + 1}验收"
                verdict, comment, rework = await self._tl_verdict(
                    PROJECT_ACCEPT_PROMPT.format(
                        title=proj.title, idx=idx + 1, total=len(proj.steps),
                        step=step.title, summaries=summaries,
                        remaining=remaining, rework_note=rework_note,
                        project_dir=str(pdir.resolve())),
                    cwd=pdir)
                tl.state = "idle"
                tl.activity = ""
                tl.go_desk()
            step.review = comment
            try:
                with (pdir / "PROJECT_LOG.md").open("a", encoding="utf-8") as f:
                    f.write(f"\n## 步骤 {idx + 1}：{step.title}"
                            f"（第 {rnd} 轮验收：{verdict}）\n"
                            f"{summaries}\n\n**队长验收**：{comment}\n")
            except Exception:
                pass
            w.dirty = True

            if verdict == "pass":
                w.log(f"👑 验收通过 ✅（步骤 {idx + 1}"
                      + (f"，第 {rnd} 轮" if rnd > 1 else "") + f"）：{comment[:90]}")
                tl.say(comment[:48] or "这步验收通过！", 9)
                return
            # 打回返工
            w.log(f"👑 验收不通过 🔁（步骤 {idx + 1}）：{comment[:90]}")
            tl.say("这个质量不行，打回重做！", 8)
            redo = []
            for r in rework:
                assign = next((a for a in step.assigns if a.role == r["role"]), None)
                if not assign:
                    continue
                assign.status = "pending"
                assign.rework_note = str(r["instruction"])[:300]
                assign.rework_round = rnd
                redo.append(assign)
                m = w.by_id[assign.role]
                w.add_link(LEADER_ID, assign.role, "整改意见")
                w.log(f"🔁 打回 → {m.spec['emoji']} {m.spec['name']}："
                      f"「{assign.rework_note[:60]}」（第 {rnd} 轮返工）")
            if not redo:
                return
            await asyncio.gather(
                *(self._run_passign(proj, step, a, pdir) for a in redo))
            if any(a.status != "done" for a in redo):
                return              # 返工执行失败，交给上层判定

    async def _accept_task(self, task):
        """TL 拆解任务的交付终验；不合格打回对应成员返工，最多 MAX_REWORK 轮。"""
        w = self.world
        tl = w.by_id[LEADER_ID]
        for rnd in range(1, MAX_REWORK + 2):
            final_round = rnd > MAX_REWORK
            summaries = "\n".join(
                f"- {w.by_id[s.role].spec['name']}「{s.title}」（{s.status}）：{s.summary[:100]}"
                for s in task.subtasks)
            rework_note = (f"注意：已返工 {rnd - 1} 次，达到上限，"
                           "只能输出 pass，并在 comment 中记录遗留问题。"
                           if final_round else
                           (f"这是第 {rnd - 1} 次返工后的复验。" if rnd > 1 else ""))
            async with self.locks[LEADER_ID]:
                tl.state = "meeting"
                tl.go(*MEETING_CENTER)
                tl.activity = "🧐 交付终验中"
                tl.stream_tag = f"任务#{task.id} 终验"
                task_dir = self.ws_root.parent / task.workdir
                verdict, comment, rework = await self._tl_verdict(
                    TASK_ACCEPT_PROMPT.format(title=task.title,
                                              summaries=summaries,
                                              rework_note=rework_note,
                                              workdir=str(task_dir.resolve())),
                    cwd=task_dir)
                tl.state = "idle"
                tl.activity = ""
                tl.go_desk()
            w.dirty = True
            if verdict == "pass":
                w.log(f"👑 终验通过 ✅「{task.title}」"
                      + (f"（第 {rnd} 轮）" if rnd > 1 else "") + f"：{comment[:90]}")
                tl.say(comment[:48] or "终验通过，可以交付！", 9)
                return
            w.log(f"👑 终验不通过 🔁「{task.title}」：{comment[:90]}")
            tl.say("还不能交付，打回整改！", 8)
            redo = []
            for r in rework:
                sub = next((s for s in reversed(task.subtasks)
                            if s.role == r["role"]), None)
                if not sub:
                    continue
                sub.status = "pending"
                sub.rework_note = str(r["instruction"])[:300]
                sub.rework_round = rnd
                redo.append(sub)
                m = w.by_id[sub.role]
                w.add_link(LEADER_ID, sub.role, "整改意见")
                w.log(f"🔁 打回 → {m.spec['emoji']} {m.spec['name']}："
                      f"「{sub.rework_note[:60]}」（第 {rnd} 轮返工）")
            if not redo:
                return
            await asyncio.gather(*(self._run_subtask(task, s) for s in redo))
            if any(s.status != "done" for s in redo):
                return

    # ── SDK 调用 ─────────────────────────────────────────
    def _opts(self, agent, cwd, allow_tools, force_cwd=False, read_only=False):
        """组装会话配置：应用该分身的个人配置（目录/skills/rules/模型/上下文/Bash）。
        force_cwd=True 时（项目模式）忽略个人专属目录，保证项目文件聚合。
        read_only=True 时（验收模式）放开读文件工具，但禁止写操作与 Bash。"""
        cfg = agent_config.get(agent.id)
        # 专属工作目录优先于任务目录（项目模式除外）
        if allow_tools and not force_cwd:
            custom_wd = agent_config.resolve_workdir(agent.id, self.ws_root.parent)
            if custom_wd:
                cwd = custom_wd
        # 附加规则拼进系统提示词（人工 rules + 自进化 learned）
        sys = agent.spec.get("sys") or ""
        if cfg["rules"]:
            sys = (sys + "\n\n【附加规则（必须遵守）】\n" + cfg["rules"]).strip()
        if cfg.get("learned"):
            sys = (sys + "\n\n【经验规则（历史复盘沉淀，必须遵守）】\n"
                   + "\n".join(f"- {r}" for r in cfg["learned"])).strip()
        # Skill 配置：["all"] = 全部；空 = 不启用
        skills = None
        if cfg["skills"]:
            skills = "all" if "all" in cfg["skills"] else cfg["skills"]
        # 上下文窗口透传给 CLI
        extra = {}
        if cfg.get("context"):
            extra["context-window"] = str(cfg["context"])
        return QoderAgentOptions(
            auth=qodercli_auth(),
            cwd=str(cwd),
            system_prompt=sys or None,
            tools=None if allow_tools else [],
            disallowed_tools=(list(READONLY_DISALLOWED) if read_only
                              else ([] if cfg["allow_bash"] else ["Bash"])),
            permission_mode="bypassPermissions",
            allow_dangerously_skip_permissions=True,
            model=cfg["model"] or None,
            skills=skills,
            extra_args=extra,
            max_turns=(15 if read_only else (30 if allow_tools else 1)),
        )

    async def _query_text(self, prompt, agent, timeout):
        """纯文本问答（TL 拆解/验收/澄清用，无工具），带流式日志。"""
        chunks = []
        agent.push_stream("info", f"▶ 思考会话：{prompt.strip().splitlines()[0][:60]}")

        async def consume():
            self._gate_note(agent)
            async with self.gate:      # 全局并发闸门
                opts = self._opts(agent, self.ws_root, allow_tools=False)
                async for msg in query(prompt=prompt, options=opts):
                    n = type(msg).__name__
                    if n == "AssistantMessage":
                        for b in getattr(msg, "content", []):
                            if hasattr(b, "text") and b.text.strip():
                                chunks.append(b.text)
                                agent.push_stream("text", b.text.strip())
                    elif n == "ResultMessage":
                        agent.push_stream("result",
                                          f"■ 会话结束（{getattr(msg, 'subtype', '?')}）")

        try:
            await asyncio.wait_for(consume(), timeout)
        except Exception as e:
            agent.push_stream("error", f"✖ {type(e).__name__}: {str(e)[:120]}")
            raise
        return "".join(chunks)

    async def _query_review(self, prompt, agent, cwd, timeout):
        """带只读文件工具的验收会话：TL 真实打开交付目录检查产物后再裁决。"""
        chunks = []
        agent.push_stream(
            "info", f"▶ 验收会话（可查阅文件）：{prompt.strip().splitlines()[0][:50]}")

        async def consume():
            self._gate_note(agent)
            async with self.gate:      # 全局并发闸门
                opts = self._opts(agent, cwd, allow_tools=True,
                                  force_cwd=True, read_only=True)
                async for msg in query(prompt=prompt, options=opts):
                    n = type(msg).__name__
                    if n == "AssistantMessage":
                        for b in getattr(msg, "content", []):
                            bn = type(b).__name__
                            if bn == "TextBlock" and b.text.strip():
                                chunks.append(b.text)
                                agent.push_stream("text", b.text.strip())
                            elif bn == "ToolUseBlock":
                                name = getattr(b, "name", "tool")
                                inp = getattr(b, "input", None) or {}
                                fname = ""
                                for k, v in inp.items():
                                    if "path" in k.lower() and isinstance(v, str):
                                        fname = os.path.basename(v)
                                        break
                                agent.activity = f"🔍 查阅 {fname}".strip()
                                agent.push_stream("tool", f"{name} {fname}".strip())
                                self.world.dirty = True
                    elif n == "ResultMessage":
                        agent.push_stream(
                            "result", f"■ 验收会话结束（{getattr(msg, 'subtype', '?')}）")

        try:
            await asyncio.wait_for(consume(), timeout)
        except Exception as e:
            agent.push_stream("error", f"✖ {type(e).__name__}: {str(e)[:120]}")
            raise
        return "".join(chunks)

    async def _execute(self, prompt, agent, sub, cwd, timeout, force_cwd=False):
        """带文件工具的真实执行，实时回传活动与流式日志。"""
        w = self.world
        last_text = ""
        agent.push_stream("info",
                          f"▶ 执行会话：{getattr(sub, 'title', '') or prompt.strip().splitlines()[0][:50]}"
                          f"（cwd: {pathlib.Path(cwd).name}）")

        async def consume():
            nonlocal last_text
            self._gate_note(agent)
            async with self.gate:      # 全局并发闸门
                opts = self._opts(agent, cwd, allow_tools=True, force_cwd=force_cwd)
                async for msg in query(prompt=prompt, options=opts):
                    n = type(msg).__name__
                    if n == "AssistantMessage":
                        for b in getattr(msg, "content", []):
                            bn = type(b).__name__
                            if bn == "TextBlock" and b.text.strip():
                                last_text = b.text.strip()
                                agent.say(last_text[:48], 10)
                                agent.push_stream("text", last_text)
                            elif bn == "ToolUseBlock":
                                name = getattr(b, "name", "tool")
                                inp = getattr(b, "input", None) or {}
                                fname = ""
                                brief = ""
                                for k, v in inp.items():
                                    if "path" in k.lower() and isinstance(v, str):
                                        fname = os.path.basename(v)
                                        break
                                if not fname:      # 无路径参数时展示主要入参
                                    for k in ("command", "query", "pattern", "prompt"):
                                        if isinstance(inp.get(k), str):
                                            brief = inp[k][:60]
                                            break
                                if fname and name.lower() in WRITE_TOOLS \
                                        and fname not in sub.files:
                                    sub.files.append(fname)
                                agent.activity = f"🔧 {name} {fname}".strip()
                                agent.push_stream("tool", f"{name} {fname or brief}".strip())
                                w.dirty = True
                    elif n == "UserMessage":
                        # 工具执行结果回传（截断展示）
                        for b in getattr(msg, "content", []) or []:
                            if type(b).__name__ == "ToolResultBlock":
                                c = getattr(b, "content", "")
                                if isinstance(c, list):
                                    c = " ".join(getattr(x, "text", "") for x in c)
                                c = str(c).strip().replace("\n", " ")
                                if c:
                                    agent.push_stream("ret", f"↳ {c[:150]}")
                    elif n == "ResultMessage":
                        sb = getattr(msg, "subtype", "success")
                        agent.push_stream("result", f"■ 会话结束（{sb}）")
                        if sb != "success":
                            raise RuntimeError(f"会话结束异常：{sb}")

        exec_task = asyncio.ensure_future(consume())
        agent.exec_task = exec_task          # 供用户插话时中断
        try:
            await asyncio.wait_for(exec_task, timeout)
        except Exception as e:
            if not isinstance(e, asyncio.CancelledError):
                agent.push_stream("error", f"✖ {type(e).__name__}: {str(e)[:120]}")
            raise
        finally:
            agent.exec_task = None
        return last_text

    @staticmethod
    def _parse_json(text):
        text = text.strip()
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise ValueError(f"未找到 JSON：{text[:60]}")
        return json.loads(m.group(0))
