// ─── 超级个体 · DOM 覆盖层（真实模式）────────────────────
// 成员卡、真实时钟、事件流、任务详情、任务发布、场景内浮动标签

const STATE_ICON = { working: "🔥", idle: "🟢", meeting: "🧠" };
const STATE_NAME = { working: "执行中", idle: "待命中", meeting: "拆解中" };
const ST_ICON = { pending: "⬜", running: "⏳", done: "✅", failed: "❌" };
const TASK_ST = {
  queued: ["排队中", "#b09ad0"], clarifying: ["审题中", "#9a7fe0"],
  waiting: ["等你确认", "#e0608a"], splitting: ["拆解中", "#9a7fe0"],
  running: ["执行中", "#e09a4a"], done: ["已完成", "#3aa876"],
  failed: ["失败", "#e0608a"],
};

const rgba = (c, a) =>
  `rgba(${(c[0] * 255) | 0},${(c[1] * 255) | 0},${(c[2] * 255) | 0},${a})`;
const esc = (s) => String(s).replace(/[&<>"]/g,
  (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));

export class UI {
  constructor(onSelect) {
    this.onSelect = onSelect;
    this.overlay = document.getElementById("overlay");
    this.roster = document.getElementById("roster");
    this.eventList = document.getElementById("eventList");
    this.detail = document.getElementById("detailCard");
    this.detailHead = document.getElementById("detailHead");
    this.detailTodos = document.getElementById("detailTodos");
    this.selected = null;
    this.lastSnap = null;
    this.chips = new Map();
    this.tags = new Map();
    this.zoneTags = new Map();
    this.orbLabels = [];
    this.lastEventSig = "";
    document.getElementById("detailClose").onclick = () => this.select(null);
    this.#initPublish();
    this.#initSettings();
    this.#initAsk();
    this.#initTerm();
    this.#initEvtFilters();
    this.#initAgentMgr();
    this.projDetailId = null;
    this.taskDetailId = null;
    document.getElementById("projDetailClose").onclick = () => this.closeProjDetail();
    document.getElementById("projDetailBackdrop").onclick = () => this.closeProjDetail();
    this.#initGate();
    // 全局委托：点击文件链接在 Finder 中定位
    document.addEventListener("click", (e) => {
      const el = e.target.closest(".fLink[data-fpath]");
      if (!el) return;
      e.preventDefault();
      fetch("/api/open-file", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: el.dataset.fpath }),
      }).catch(() => {});
    });
  }

  // ── 全局并发控制 ─────────────────────────────────────
  #initGate() {
    const sel = document.getElementById("gateSel");
    fetch("/api/config").then((r) => r.json()).then((d) => {
      if (d.global) sel.value = String(d.global.max_concurrency);
    }).catch(() => {});
    sel.onchange = async () => {
      try {
        const d = await (await fetch("/api/config/global", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ max_concurrency: +sel.value }),
        })).json();
        if (d.ok) sel.value = String(d.config.max_concurrency);
      } catch { /* 下次快照会纠正显示 */ }
    };
  }

  // ── Agent 管理弹窗 ─────────────────────────────────────
  #initAgentMgr() {
    const card = document.getElementById("agentMgrCard");
    const backdrop = document.getElementById("agentMgrBackdrop");
    const close = () => { card.classList.add("hidden"); backdrop.classList.add("hidden"); };
    document.getElementById("agentMgrClose").onclick = close;
    backdrop.onclick = close;
    document.getElementById("manageAgentsBtn").onclick = () => {
      card.classList.remove("hidden"); backdrop.classList.remove("hidden");
      this.#refreshAgentMgr();
    };
    document.getElementById("agentNewBtn").onclick = () => this.#addAgent();
  }

  async #refreshAgentMgr() {
    const list = document.getElementById("agentMgrList");
    list.innerHTML = '<p style="font-size:11px;color:var(--ink-soft)">加载中…</p>';
    try {
      const d = await (await fetch("/api/agents")).json();
      const customs = (d.agents || []).filter((a) => a.custom);
      if (!customs.length) {
        list.innerHTML = '<p style="font-size:12px;color:var(--ink-soft)">还没有自定义 Agent，下方添加一个吧～</p>';
        return;
      }
      list.innerHTML = customs.map((a) => `
        <div class="agentItem">
          <span>${a.emoji} <b>${esc(a.name)}</b>（${esc(a.role)}）</span>
          <button class="miniBtn danger" data-id="${esc(a.id)}">删除</button>
        </div>`).join("");
      list.querySelectorAll("button").forEach((btn) => {
        btn.onclick = async () => {
          if (!confirm(`确定删除 Agent「${btn.parentElement.querySelector("b").textContent}」？`)) return;
          btn.disabled = true;
          try {
            await fetch(`/api/agents/${btn.dataset.id}`, { method: "DELETE" });
            this.#refreshAgentMgr();
            this.pubReady = false;
          } catch { /* 忽略 */ }
        };
      });
    } catch {
      list.innerHTML = '<p style="color:#e0608a;font-size:12px">加载失败</p>';
    }
  }

  async #addAgent() {
    const msg = document.getElementById("agentNewMsg");
    const id = document.getElementById("agentNewId").value.trim();
    const name = document.getElementById("agentNewName").value.trim();
    const emoji = document.getElementById("agentNewEmoji").value.trim();
    const role = document.getElementById("agentNewRole").value.trim();
    const sys = document.getElementById("agentNewSys").value.trim();
    if (!id || !name) { msg.textContent = "请至少填写 id 和名称"; msg.style.color = "#e0608a"; return; }
    msg.textContent = "添加中…"; msg.style.color = "var(--ink-soft)";
    try {
      const d = await (await fetch("/api/agents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, name, emoji, role, sys }),
      })).json();
      if (d.ok) {
        msg.textContent = `✅ ${d.agent.emoji} ${d.agent.name} 已加入团队！`;
        msg.style.color = "#3aa876";
        document.getElementById("agentNewId").value = "";
        document.getElementById("agentNewName").value = "";
        document.getElementById("agentNewEmoji").value = "";
        document.getElementById("agentNewRole").value = "";
        document.getElementById("agentNewSys").value = "";
        this.#refreshAgentMgr();
        this.pubReady = false;
      } else {
        msg.textContent = d.error || "添加失败"; msg.style.color = "#e0608a";
      }
    } catch {
      msg.textContent = "网络出错"; msg.style.color = "#e0608a";
    }
  }

  // ── 事件流过滤 ───────────────────────────────────────
  #initEvtFilters() {
    this.evtFilter = "all";
    const box = document.getElementById("evtFilters");
    box.querySelectorAll("button").forEach((btn) => {
      btn.onclick = () => {
        this.evtFilter = btn.dataset.f;
        box.querySelectorAll("button").forEach((b) =>
          b.classList.toggle("on", b === btn));
        this.#applyEvtFilter();
      };
    });
  }

  static #evtCat(text) {
    if (text.startsWith("✅") || text.startsWith("🎉") || text.startsWith("🏁")) return "done";
    if (text.startsWith("👑")) return "accept";
    if (text.startsWith("🔁")) return "rework";
    if (/^[📮📨🗂🧩📍🔧🙋🙆⏭📋]/u.test(text)) return "flow";
    return "other";
  }

  #applyEvtFilter() {
    const f = this.evtFilter;
    for (const li of this.eventList.children) {
      li.style.display = (f === "all" || li.dataset.cat === f) ? "" : "none";
    }
    this.eventList.scrollTop = this.eventList.scrollHeight;
  }

  // ── 实时执行终端（qodercli 流式输出）──────────────────
  #initTerm() {
    this.termCard = document.getElementById("termCard");
    this.termBackdrop = document.getElementById("termBackdrop");
    this.termHead = document.getElementById("termHead");
    this.termBody = document.getElementById("termBody");
    // 会话流长行：复制按钮 + 点击展开/收起（委托，只绑一次）
    this.termBody.addEventListener("click", async (e) => {
      const copyBtn = e.target.closest(".tCopy");
      if (copyBtn) {
        const txtEl = copyBtn.closest(".tLine")?.querySelector(".tTxt");
        if (txtEl) {
          try {
            await navigator.clipboard.writeText(txtEl.textContent);
            copyBtn.textContent = "✓";
            setTimeout(() => { copyBtn.textContent = "⧉"; }, 1200);
          } catch { copyBtn.textContent = "✗"; }
        }
        return;   // 不触发展开/收起
      }
      const el = e.target.closest(".tTxt.clip, .tTxt.open");
      if (!el) return;
      el.classList.toggle("open");
      el.classList.toggle("clip");
    });
    this.termFollow = document.getElementById("termFollowCk");
    this.termFilter = document.getElementById("termFilter");
    this.termId = null;
    this.termSeq = 0;
    this.termTimer = null;
    this.termTags = new Set();
    this.termFilter.onchange = () => this.#applyTermFilter();
    const close = () => this.closeTerminal();
    document.getElementById("termClose").onclick = close;
    this.termBackdrop.onclick = close;

    // 插话纠正
    this.interjectInput = document.getElementById("interjectInput");
    const interjectBtn = document.getElementById("interjectBtn");
    const send = async () => {
      const hint = this.interjectInput.value.trim();
      if (!hint || !this.termId) return;
      interjectBtn.disabled = true;
      try {
        const res = await fetch("/api/interject", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ aid: this.termId, hint }),
        });
        const d = await res.json();
        if (d.ok) this.interjectInput.value = "";
        this.interjectInput.placeholder = d.ok
          ? `✅ ${d.msg}` : `❌ ${d.error || "发送失败"}`;
        setTimeout(() => {
          this.interjectInput.placeholder =
            "💬 插话纠正：执行中会立刻中断并按你的话调整…";
        }, 3000);
      } catch { /* 忽略 */ }
      interjectBtn.disabled = false;
    };
    interjectBtn.onclick = send;
    this.interjectInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") send();
    });
  }

  openTerminal(id) {
    const a = this.lastSnap?.agents.find((x) => x.id === id);
    if (!a) return;
    this.termId = id;
    this.termSeq = 0;
    this.termTags = new Set();
    this.termFilter.innerHTML = '<option value="">全部会话</option>';
    this.termBody.innerHTML =
      '<p style="color:#6c6293;font-size:11px">连接会话流…</p>';
    this.termCard.classList.remove("hidden");
    this.termBackdrop.classList.remove("hidden");
    this.#renderTermHead(a);
    clearInterval(this.termTimer);
    const poll = async () => {
      if (!this.termId) return;
      try {
        const res = await fetch(`/api/stream/${this.termId}?since=${this.termSeq}`);
        const d = await res.json();
        if (!d.ok || this.termId !== id) return;
        const cur = this.lastSnap?.agents.find((x) => x.id === id);
        if (cur) this.#renderTermHead(cur);
        if (d.items.length) {
          if (this.termSeq === 0) this.termBody.innerHTML = "";
          for (const it of d.items) {
            const div = document.createElement("div");
            div.className = `tLine ${it.k}`;
            div.dataset.tag = it.tag || "";
            const chip = it.tag
              ? `<i class="tTag" style="color:${UI.#tagColor(it.tag)}">${esc(it.tag)}</i>`
              : "";
            const long = (it.txt || "").length > 160 || (it.txt || "").includes("\n");
            const cls = long ? "tTxt clip" : "tTxt";
            const copyBtn = long ? `<button class="tCopy" title="复制完整内容">⧉</button>` : "";
            div.innerHTML = `<time>${it.t}</time>${chip}<span class="${cls}" title="${long ? '点击展开/收起' : ''}">${esc(it.txt)}</span>${copyBtn}`;
            this.termBody.appendChild(div);
            if (it.tag) this.#addTermTag(it.tag);
          }
          while (this.termBody.children.length > 500) {
            this.termBody.removeChild(this.termBody.firstChild);
          }
          this.termSeq = d.seq;
          this.#applyTermFilter(true);
        } else if (this.termSeq === 0 && !d.seq) {
          this.termBody.innerHTML =
            '<p style="color:#6c6293;font-size:11px">这个分身还没有执行过会话，派个任务试试～</p>';
        }
      } catch { /* 下一轮重试 */ }
    };
    poll();
    this.termTimer = setInterval(poll, 700);
  }

  // 会话标签 → 稳定配色（哈希取色板）
  static #tagColor(tag) {
    const palette = ["#ffd166", "#7ce3b1", "#8ecbff", "#f5a3d0",
                     "#c4a8ff", "#ffb28a", "#7fe0e8", "#d3e07f"];
    let h = 0;
    for (const ch of tag) h = (h * 31 + ch.codePointAt(0)) >>> 0;
    return palette[h % palette.length];
  }

  #addTermTag(tag) {
    if (this.termTags.has(tag)) return;
    this.termTags.add(tag);
    const opt = document.createElement("option");
    opt.value = tag;
    opt.textContent = tag;
    this.termFilter.appendChild(opt);
  }

  #applyTermFilter(keepScroll = false) {
    const f = this.termFilter.value;
    for (const line of this.termBody.children) {
      if (line.classList?.contains("tLine")) {
        line.style.display = !f || line.dataset.tag === f ? "" : "none";
      }
    }
    if (this.termFollow.checked || !keepScroll) {
      this.termBody.scrollTop = this.termBody.scrollHeight;
    }
  }

  closeTerminal() {
    this.termCard.classList.add("hidden");
    this.termBackdrop.classList.add("hidden");
    this.termId = null;
    clearInterval(this.termTimer);
  }

  #renderTermHead(a) {
    const live = a.state !== "idle";
    this.termHead.innerHTML = `
      <div class="face" style="background:${rgba(a.color, 0.3)}">${a.emoji}</div>
      <div>${a.name} 的执行终端
        <i>${esc(a.activity || (live ? "会话进行中" : "空闲，显示最近会话记录"))}</i></div>
      <span class="live ${live ? "" : "idle"}">${live ? "● LIVE" : "○ idle"}</span>`;
  }

  // ── 澄清确认弹窗 ─────────────────────────────────────
  #initAsk() {
    this.askCard = document.getElementById("askCard");
    this.askBackdrop = document.getElementById("askBackdrop");
    this.askHead = document.getElementById("askHead");
    this.askBody = document.getElementById("askBody");
    this.askMsg = document.getElementById("askMsg");
    this.askTarget = null;        // {kind, id}
    this.askDismissed = new Set();  // 用户暂时关掉的确认（点背景关闭）
    this.askBackdrop.onclick = () => {
      if (this.askTarget) this.askDismissed.add(this.askTarget.key);
      this.#hideAsk();
    };
    document.getElementById("askSkip").onclick = () => this.#submitAsk(true);
    document.getElementById("askSubmit").onclick = () => this.#submitAsk(false);
  }

  #hideAsk() {
    this.askCard.classList.add("hidden");
    this.askBackdrop.classList.add("hidden");
    this.askTarget = null;
  }

  // 快照里发现 waiting 的任务/项目 → 弹出确认卡
  #checkAsk(snap) {
    if (this.askTarget) {                     // 已在展示：确认对象是否仍在等待
      const pool = this.askTarget.kind === "task" ? snap.tasks : snap.projects;
      const cur = (pool || []).find((o) => o.id === this.askTarget.id);
      if (!cur || cur.status !== "waiting") this.#hideAsk();
      return;
    }
    const waiting = []
      .concat((snap.tasks || []).map((t) => ({ kind: "task", o: t })))
      .concat((snap.projects || []).map((p) => ({ kind: "project", o: p })))
      .filter((x) => x.o.status === "waiting"
        && (x.o.questions || []).some((q) => !("answer" in q)))
      .filter((x) => !this.askDismissed.has(`${x.kind}:${x.o.id}`));
    if (!waiting.length) return;
    const { kind, o } = waiting[0];
    // 只展示还没回答过的问题（成员执行中求助会往已答的澄清后面追加）
    const pending = (o.questions || []).filter((q) => !("answer" in q));
    const fromMember = pending.some((q) => q.src === "member");
    this.askTarget = { kind, id: o.id, key: `${kind}:${o.id}` };
    this.askMsg.textContent = "";
    this.askHead.innerHTML = fromMember ? `
      <div class="face">🙋</div>
      <div><b>执行中遇到问题，队长也拿不准</b>
        <i>${kind === "task" ? "任务" : "项目"}「${esc(o.title)}」 · 你的答案会转给执行成员</i></div>` : `
      <div class="face">👑</div>
      <div><b>队长桑有几个问题想确认</b>
        <i>${kind === "task" ? "任务" : "项目"}「${esc(o.title)}」 · 回答后按你的意思执行</i></div>`;
    this.askBody.innerHTML = pending.map((q, i) => `
      <div class="askQ" data-i="${i}">
        <div class="qText">${i + 1}. ${esc(q.q)}</div>
        <div class="qOpts">${(q.options || []).map((op) =>
          `<button class="qOpt" data-v="${esc(op)}">${esc(op)}</button>`).join("")}
        </div>
        <input class="qFree" type="text" maxlength="120"
               placeholder="或者自由输入你的回答…" />
      </div>`).join("");
    // 选项点击：单选高亮，填入自由输入框
    this.askBody.querySelectorAll(".askQ").forEach((qEl) => {
      const free = qEl.querySelector(".qFree");
      qEl.querySelectorAll(".qOpt").forEach((btn) => {
        btn.onclick = () => {
          qEl.querySelectorAll(".qOpt").forEach((b) => b.classList.remove("sel"));
          btn.classList.add("sel");
          free.value = btn.dataset.v;
        };
      });
    });
    this.askCard.classList.remove("hidden");
    this.askBackdrop.classList.remove("hidden");
  }

  async #submitAsk(skip) {
    if (!this.askTarget) return;
    const answers = skip ? []
      : [...this.askBody.querySelectorAll(".askQ .qFree")].map((i) => i.value.trim());
    if (!skip && answers.every((a) => !a)) {
      this.askMsg.textContent = "一个都没回答哦，可以点左边跳过～";
      this.askMsg.style.color = "#e0608a";
      return;
    }
    try {
      const res = await fetch("/api/answer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: this.askTarget.kind,
                               id: this.askTarget.id, answers }),
      });
      const data = await res.json();
      if (data.ok) this.#hideAsk();
      else {
        this.askMsg.textContent = data.error || "提交失败";
        this.askMsg.style.color = "#e0608a";
      }
    } catch {
      this.askMsg.textContent = "网络出错了…";
      this.askMsg.style.color = "#e0608a";
    }
  }

  // ── 分身设置页 ───────────────────────────────────────
  #initSettings() {
    this.setCard = document.getElementById("settingsCard");
    this.setBackdrop = document.getElementById("settingsBackdrop");
    this.setHead = document.getElementById("settingsHead");
    this.setWorkdir = document.getElementById("setWorkdir");
    this.setSkillAll = document.getElementById("setSkillAll");
    this.setSkills = document.getElementById("setSkills");
    this.setSkillRefresh = document.getElementById("setSkillRefresh");
    this.setRules = document.getElementById("setRules");
    this.setModel = document.getElementById("setModel");
    this.setCtx = document.getElementById("setCtx");
    this.setBash = document.getElementById("setBash");
    this.setSave = document.getElementById("setSave");
    this.setMsg = document.getElementById("setMsg");
    this.settingsId = null;
    this.modelsCache = [];
    const close = () => {
      this.setCard.classList.add("hidden");
      this.setBackdrop.classList.add("hidden");
      this.settingsId = null;
    };
    document.getElementById("settingsClose").onclick = close;
    this.setBackdrop.onclick = close;
    this.setSkillAll.onchange = () =>
      this.setSkills.classList.toggle("disabled", this.setSkillAll.checked);
    if (this.setSkillRefresh)
      this.setSkillRefresh.onclick = () => this.#loadSkills(true);
    this.setSave.onclick = () => this.#saveSettings();
    // 模型切换 → 联动可选上下文窗口
    this.setModel.onchange = () => this.#fillCtxOptions("");

    // 目录选择器
    this.dirPicker = document.getElementById("dirPicker");
    this.dirList = document.getElementById("dirList");
    this.dirCurrent = document.getElementById("dirCurrent");
    this.dirShortcuts = document.getElementById("dirShortcuts");
    document.getElementById("dirBrowseBtn").onclick = () => {
      const show = this.dirPicker.classList.toggle("hidden");
      if (!show) this.#browse(this.setWorkdir.value.trim() || "");
    };
    document.getElementById("dirUp").onclick = () => {
      if (this.dirParent) this.#browse(this.dirParent);
    };
    document.getElementById("dirPick").onclick = () => {
      this.setWorkdir.value = this.dirPath || "";
      this.dirPicker.classList.add("hidden");
    };
  }

  async #browse(path) {
    this.dirList.innerHTML =
      '<p style="font-size:11px;color:var(--ink-soft)">加载中…</p>';
    try {
      const res = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
      const d = await res.json();
      this.dirPath = d.path;
      this.dirParent = d.parent;
      this.dirCurrent.textContent = `📍 ${d.path}`;
      this.dirShortcuts.innerHTML = "";
      for (const s of d.shortcuts || []) {
        const b = document.createElement("button");
        b.className = "miniBtn";
        b.textContent = s.label;
        b.onclick = () => this.#browse(s.path);
        this.dirShortcuts.appendChild(b);
      }
      this.dirList.innerHTML = d.dirs.length
        ? d.dirs.map((n) => `<div class="dirItem" data-n="${esc(n)}">📁 ${esc(n)}</div>`).join("")
        : '<p style="font-size:11px;color:var(--ink-soft)">（没有子目录）</p>';
      this.dirList.querySelectorAll(".dirItem").forEach((el) => {
        el.onclick = () => this.#browse(`${this.dirPath}/${el.dataset.n}`);
      });
    } catch {
      this.dirList.innerHTML =
        '<p style="font-size:11px;color:#e0608a">目录加载失败</p>';
    }
  }

  #renderLearned() {
    const box = document.getElementById("learnedList");
    const list = this.curLearned || [];
    box.innerHTML = list.length
      ? list.map((r, i) => `
        <div class="learnedItem">
          <span>${esc(r)}</span>
          <button data-i="${i}" title="删除">✕</button>
        </div>`).join("")
      : '<p class="learnedEmpty">还没有沉淀，任务中返工/被你纠正后队长复盘会自动学习</p>';
    box.querySelectorAll("button").forEach((btn) => {
      btn.onclick = () => {
        this.curLearned.splice(+btn.dataset.i, 1);
        this.#renderLearned();
      };
    });
  }

  #fillCtxOptions(saved) {
    const m = this.modelsCache.find((x) => x.id === this.setModel.value);
    const ctxs = (m && m.contexts) || [];
    const fmt = (n) => n >= 1000000 ? `${n / 1000000}M` : `${(n / 1000) | 0}K`;
    this.setCtx.innerHTML = '<option value="">默认</option>' + ctxs.map((c) =>
      `<option value="${c}" ${String(c) === String(saved) ? "selected" : ""}>${fmt(c)} tokens</option>`
    ).join("");
    this.setCtx.disabled = !ctxs.length;
  }

  async openSettings(id) {
    const a = this.lastSnap?.agents.find((x) => x.id === id);
    if (!a) return;
    this.settingsId = id;
    this.setMsg.textContent = "";
    this.setHead.innerHTML = `
      <div class="face" style="background:${rgba(a.color, 0.35)}">${a.emoji}</div>
      <div><b>${a.name} 的设置</b><i>${a.role} · 配置仅影响这个分身</i></div>`;
    this.setCard.classList.remove("hidden");
    this.setBackdrop.classList.remove("hidden");
    this.setSkills.innerHTML =
      '<p style="font-size:12px;color:var(--ink-soft)">加载中…</p>';
    try {
      const res = await fetch("/api/config");
      const data = await res.json();
      const cfg = data.agents[id] || {};
      if (this.settingsId !== id) return;   // 加载期间切换了目标
      this.setWorkdir.value = cfg.workdir || "";
      this.setRules.value = cfg.rules || "";
      this.setBash.checked = !!cfg.allow_bash;
      this.curLearned = [...(cfg.learned || [])];
      this.#renderLearned();
      // 模型下拉：默认 + 账号可用模型（回显已保存的选择）
      const opts = ['<option value="">默认（Auto）</option>'].concat(
        (data.models || []).map((m) => `
          <option value="${esc(m.id)}" ${cfg.model === m.id ? "selected" : ""}>
            ${esc(m.name)}${m.desc ? ` — ${esc(m.desc)}` : ""}
          </option>`));
      // 已保存的模型不在列表里（如已下线）也要能回显
      if (cfg.model && !(data.models || []).some((m) => m.id === cfg.model)) {
        opts.push(`<option value="${esc(cfg.model)}" selected>${esc(cfg.model)}（已失效？）</option>`);
      }
      this.setModel.innerHTML = opts.join("");
      this.modelsCache = data.models || [];
      this.#fillCtxOptions(cfg.context || "");
      const all = (cfg.skills || []).includes("all");
      this.setSkillAll.checked = all;
      this.setSkills.classList.toggle("disabled", all);
      this.curCfg = cfg;                 // 供 Skill 按需加载时回显已选
      this.#loadSkills();                // 非阻塞：配置已就绪，Skill 单独异步拉取（不再拖慢配置显示）
    } catch {
      this.setSkills.innerHTML =
        '<p style="font-size:12px;color:#e0608a">配置加载失败</p>';
    }
    // 加载该 Agent 的定时任务
    this.#loadCronJobs(id);
    document.getElementById("cronAddBtn").onclick = () => this.#addCronJob(id);
  }

  #renderSkills(skills, checkedNames) {
    const checked = new Set(checkedNames || []);
    this.setSkills.innerHTML = (skills && skills.length)
      ? skills.map((s) => `
        <label class="skillItem">
          <input type="checkbox" value="${esc(s.name)}"
            ${checked.has(s.name) ? "checked" : ""} />
          <span>${esc(s.name)}</span><small>${esc(s.desc || "")}</small>
        </label>`).join("")
      : '<p style="font-size:12px;color:var(--ink-soft)">本机没有发现已安装的 Skill（点“刷新”重新扫描）</p>';
  }

  async #loadSkills(refresh = false) {
    const id = this.settingsId;
    if (!id) return;
    // 记住当前勾选（含用户临时改动）；首次加载则回显配置里已选的
    const prev = [...this.setSkills.querySelectorAll("input[type=checkbox]:checked")]
      .map((i) => i.value);
    const checkedNames = prev.length
      ? prev
      : ((this.curCfg && this.curCfg.skills) || []);
    this.setSkills.innerHTML =
      `<p style="font-size:12px;color:var(--ink-soft)">${refresh ? "刷新中…" : "加载中…"}</p>`;
    if (this.setSkillRefresh) this.setSkillRefresh.disabled = true;
    try {
      const r = await fetch(`/api/skills${refresh ? "?refresh=1" : ""}`);
      const d = await r.json();
      if (this.settingsId !== id) return;      // 期间切换了目标分身
      this.#renderSkills(d.skills || [], checkedNames);
    } catch {
      if (this.settingsId === id)
        this.setSkills.innerHTML =
          '<p style="font-size:12px;color:#e0608a">Skill 加载失败，点“刷新”重试</p>';
    } finally {
      if (this.setSkillRefresh) this.setSkillRefresh.disabled = false;
    }
  }

  async #saveSettings() {
    if (!this.settingsId) return;
    // Skill 列表可能尚未异步加载完；此时保留原有选择，避免误存为空清掉配置
    let skills;
    if (this.setSkillAll.checked) {
      skills = ["all"];
    } else if (this.setSkills.querySelector("input[type=checkbox]")) {
      skills = [...this.setSkills.querySelectorAll("input:checked")].map((i) => i.value);
    } else {
      skills = (this.curCfg && this.curCfg.skills) || [];
    }
    this.setSave.disabled = true;
    this.setMsg.textContent = "保存中…";
    this.setMsg.style.color = "var(--ink-soft)";
    try {
      const res = await fetch(`/api/config/${this.settingsId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          workdir: this.setWorkdir.value.trim(),
          skills,
          rules: this.setRules.value.trim(),
          model: this.setModel.value.trim(),
          context: this.setCtx.value,
          allow_bash: this.setBash.checked,
          learned: this.curLearned || [],
        }),
      });
      const data = await res.json();
      if (data.ok) {
        this.setMsg.textContent = "已保存，下次执行任务生效 ✅";
        this.setMsg.style.color = "#3aa876";
      } else {
        this.setMsg.textContent = data.error || "保存失败";
        this.setMsg.style.color = "#e0608a";
      }
    } catch {
      this.setMsg.textContent = "网络出错了…";
      this.setMsg.style.color = "#e0608a";
    }
    this.setSave.disabled = false;
  }

  // ── 定时任务管理（在设置弹窗内）─────────────────
  async #loadCronJobs(aid) {
    const list = document.getElementById("setCronList");
    list.innerHTML = '<p style="font-size:11px;color:var(--ink-soft)">加载中…</p>';
    try {
      const d = await (await fetch(`/api/schedules?agent=${aid}`)).json();
      const jobs = d.jobs || [];
      if (!jobs.length) {
        list.innerHTML = '<p style="font-size:12px;color:var(--ink-soft)">暂无定时任务</p>';
        return;
      }
      list.innerHTML = jobs.map((j) => {
        const wins = (j.windows || []).map((w) => `${w.start}-${w.end}`).join(", ") || "全天";
        return `
        <div class="cronItem ${j.enabled ? '' : 'disabled'}">
          <div class="cronInfo">
            <b>每 ${j.interval} 分钟</b><span class="cronWin">${esc(wins)}</span>
            <p class="cronTxt">${esc(j.prompt)}</p>
          </div>
          <div class="cronActs">
            <button class="miniBtn" data-toggle="${j.id}">${j.enabled ? '❘❘' : '▶'}</button>
            <button class="miniBtn danger" data-del="${j.id}">✕</button>
          </div>
        </div>`;
      }).join("");
      list.querySelectorAll("[data-toggle]").forEach((btn) => {
        btn.onclick = async () => {
          await fetch(`/api/schedules/${btn.dataset.toggle}/toggle`, { method: "POST" });
          this.#loadCronJobs(aid);
        };
      });
      list.querySelectorAll("[data-del]").forEach((btn) => {
        btn.onclick = async () => {
          if (!confirm("确定删除这个定时任务？")) return;
          await fetch(`/api/schedules/${btn.dataset.del}`, { method: "DELETE" });
          this.#loadCronJobs(aid);
        };
      });
    } catch {
      list.innerHTML = '<p style="color:#e0608a;font-size:12px">加载失败</p>';
    }
  }

  async #addCronJob(aid) {
    const msg = document.getElementById("cronMsg");
    const interval = parseInt(document.getElementById("cronInterval").value) || 5;
    const prompt = document.getElementById("cronPrompt").value.trim();
    const winsRaw = document.getElementById("cronWindows").value.trim();
    if (!prompt) { msg.textContent = "请填写执行指令"; msg.style.color = "#e0608a"; return; }
    const windows = winsRaw ? winsRaw.split(/[,，]/).map((s) => {
      const [start, end] = s.trim().split("-").map((x) => x.trim());
      return { start: start || "", end: end || "" };
    }).filter((w) => w.start && w.end) : [];
    msg.textContent = "添加中…"; msg.style.color = "var(--ink-soft)";
    try {
      const d = await (await fetch("/api/schedules", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent: aid, interval, windows, prompt }),
      })).json();
      if (d.ok) {
        msg.textContent = "✅ 已添加"; msg.style.color = "#3aa876";
        document.getElementById("cronPrompt").value = "";
        document.getElementById("cronWindows").value = "";
        this.#loadCronJobs(aid);
      } else {
        msg.textContent = d.error || "添加失败"; msg.style.color = "#e0608a";
      }
    } catch {
      msg.textContent = "网络出错"; msg.style.color = "#e0608a";
    }
  }

  // ── 任务发布 ─────────────────────────────────────────
  #initPublish() {
    this.pubSelect = document.getElementById("pubAssignee");
    this.pubInput = document.getElementById("pubTitle");
    this.pubBtn = document.getElementById("pubBtn");
    this.pubMsg = document.getElementById("pubMsg");
    this.pubReady = false;
    // 派任务 / 发项目 标签切换
    const tabTask = document.getElementById("tabTask");
    const tabProj = document.getElementById("tabProj");
    const taskForm = document.getElementById("taskForm");
    const projForm = document.getElementById("projForm");
    const switchTab = (proj) => {
      tabTask.classList.toggle("on", !proj);
      tabProj.classList.toggle("on", proj);
      taskForm.classList.toggle("hidden", proj);
      projForm.classList.toggle("hidden", !proj);
      this.#syncDetailPos();
    };
    tabTask.onclick = () => switchTab(false);
    tabProj.onclick = () => switchTab(true);

    const submit = async () => {
      const title = this.pubInput.value.trim();
      if (!title) { this.#pubFeedback("先写点任务内容嘛～", false); return; }
      this.pubBtn.disabled = true;
      try {
        const res = await fetch("/api/task", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ assignee: this.pubSelect.value, title }),
        });
        const data = await res.json();
        if (data.ok) {
          this.pubInput.value = "";
          this.#pubFeedback(`已发给 ${data.assignee}，真实执行中 ✨`, true);
        } else {
          this.#pubFeedback(data.error || "发布失败", false);
        }
      } catch {
        this.#pubFeedback("网络出错了…", false);
      }
      this.pubBtn.disabled = false;
    };
    this.pubBtn.onclick = submit;
    this.pubInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") submit();
    });

    // 立项
    const projTitle = document.getElementById("projTitle");
    const projFolder = document.getElementById("projFolder");
    const projDesc = document.getElementById("projDesc");
    const projBtn = document.getElementById("projBtn");
    const slugify = (s) =>
      s.replace(/[^\w\u4e00-\u9fff-]+/gu, "-").replace(/^-+|-+$/g, "").slice(0, 40);
    // 输入标题时联动建议文件夹名（用户手动改过就不再联动）
    let folderTouched = false;
    projFolder.oninput = () => { folderTouched = !!projFolder.value.trim(); };
    projTitle.oninput = () => {
      if (!folderTouched) projFolder.value = slugify(projTitle.value.trim());
    };
    projBtn.onclick = async () => {
      const title = projTitle.value.trim();
      if (!title) { this.#pubFeedback("先给项目起个名字～", false); return; }
      const folder = slugify(projFolder.value.trim());
      const members = [...document.querySelectorAll("#projMembers input:checked")]
        .map((i) => i.value);
      projBtn.disabled = true;
      try {
        const res = await fetch("/api/project", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title, desc: projDesc.value.trim(), folder, members }),
        });
        const data = await res.json();
        if (data.ok) {
          projTitle.value = ""; projFolder.value = ""; projDesc.value = "";
          folderTouched = false;
          this.#pubFeedback(
            data.folder
              ? `已立项！目录 projects/${data.folder} 🗂` : "已立项！队长桑开始规划 🗂",
            true);
        } else {
          this.#pubFeedback(data.error || "立项失败", false);
        }
      } catch {
        this.#pubFeedback("网络出错了…", false);
      }
      projBtn.disabled = false;
    };
  }

  #pubFeedback(text, ok) {
    this.pubMsg.textContent = text;
    this.pubMsg.style.color = ok ? "#3aa876" : "#e0608a";
    clearTimeout(this._pubTimer);
    this._pubTimer = setTimeout(() => { this.pubMsg.textContent = ""; }, 3200);
  }

  #fillPublishOptions(agents) {
    if (this.pubReady) return;
    this.pubReady = true;
    for (const a of agents) {
      const opt = document.createElement("option");
      opt.value = a.id;
      opt.textContent = a.id === "tl"
        ? `${a.emoji} ${a.name}（TL · 真实拆解）`
        : `${a.emoji} ${a.name}（直接指派）`;
      this.pubSelect.appendChild(opt);
    }
    // 填充项目成员选择区
    const box = document.getElementById("projMembers");
    box.innerHTML = agents.filter((a) => a.id !== "tl").map((a) => `
      <label class="memberCk">
        <input type="checkbox" value="${esc(a.id)}" checked />
        <span>${a.emoji} ${esc(a.name)}</span>
      </label>`).join("");
  }

  // 详情卡贴在发布卡上方（发布卡高度随页签变化）
  #syncDetailPos() {
    const pub = document.getElementById("publishCard");
    this.detail.style.bottom = `${pub.offsetHeight + 28}px`;
  }

  select(id) {
    this.selected = id;
    this.detail.classList.toggle("hidden", !id);
    for (const [aid, chip] of this.chips) {
      chip.classList.toggle("selected", aid === this.selected);
    }
    if (id) this.#syncDetailPos();
    if (id && this.lastSnap) this.#renderDetail(id);
  }

  // ── 每次快照更新（10Hz）──────────────────────────────
  onSnapshot(snap) {
    this.lastSnap = snap;
    document.getElementById("dayLabel").textContent = snap.date;
    document.getElementById("clockLabel").textContent = snap.clock;
    const pill = document.getElementById("phasePill");
    const hasWaiting =
      (snap.tasks || []).some((t) => t.status === "waiting") ||
      (snap.projects || []).some((p) => p.status === "waiting");
    if (hasWaiting) {
      pill.textContent = "🙋 有确认待处理";
      pill.classList.add("alert");
      pill.onclick = () => {          // 弹窗被关掉后可从这里重新打开
        this.askDismissed.clear();
        this.#hideAsk();
        this.#checkAsk(this.lastSnap);
      };
    } else {
      pill.classList.remove("alert");
      pill.onclick = null;
      pill.textContent =
        snap.running > 0 ? `🔥 ${snap.running} 个任务执行中` : "🟢 全员待命";
    }

    this.#fillPublishOptions(snap.agents);
    // 全局并发实时占用
    if (snap.gate) {
      const live = document.getElementById("gateLive");
      const txt = `${snap.gate.active}/${snap.gate.limit}`;
      if (live.textContent !== txt) live.textContent = txt;
      live.classList.toggle("busy", snap.gate.active >= snap.gate.limit);
    }
    for (const a of snap.agents) this.#upsertChip(a);
    this.#updateEvents(snap.events);
    this.#renderProjects(snap);
    this.#renderDetailPopup();
    this.#checkAsk(snap);
    if (this.selected) this.#renderDetail(this.selected);
  }

  // ── 项目/任务看板 ─────────────────────────────────────
  #renderProjects(snap) {
    const projects = snap.projects || [];
    const tasks = snap.tasks || [];
    const board = document.getElementById("projBoard");
    const RUN_P = ("clarifying waiting planning running").split(" ");
    const RUN_T = ("queued clarifying waiting splitting running").split(" ");
    const sig = JSON.stringify([
      projects.map((p) => [p.id, p.status, p.stepIndex,
        p.steps.map((s) => s.status + s.review.length).join()]),
      tasks.map((t) => [t.id, t.status,
        t.subtasks.map((s) => s.status).join()]),
    ]);
    if (sig === this._projSig) return;
    this._projSig = sig;
    const PS = {
      clarifying: ["🤔 审题中", "#9a7fe0"], waiting: ["🙋 等你确认", "#e0608a"],
      planning: ["🧠 规划中", "#9a7fe0"], running: ["🔥 推进中", "#e09a4a"],
      done: ["🏁 已结项", "#3aa876"], failed: ["⚠️ 受阻", "#e0608a"],
      canceled: ["🛑 已终止", "#8b8b9e"],
    };

    // 进行中的项目：完整卡片；历史项目：折叠成单行（最多 3 条）
    const active = projects.filter((p) => RUN_P.includes(p.status));
    const history = projects.filter((p) => !RUN_P.includes(p.status)).slice(0, 3);
    let html = active.map((p) => {
      const [label, color] = PS[p.status] || [p.status, "#999"];
      const total = p.steps.length || 1;
      const doneN = p.steps.filter((s) => s.status === "done").length;
      const cur = p.steps[p.stepIndex];
      const curLine =
        p.status === "clarifying" ? "🤔 队长桑正在审题…"
        : p.status === "waiting" ? "🙋 队长桑有疑问，等待你的确认"
        : p.status === "planning" ? "👑 队长桑正在拆解规划…"
        : cur
          ? `步骤 ${p.stepIndex + 1}/${total}：${esc(cur.title)}（${cur.assigns.map((a) => a.role).join("+")}）`
          : `${total} 个步骤全部完成`;
      const lastReview = [...p.steps].reverse().find((s) => s.review);
      return `
      <div class="projCard" data-pid="${p.id}" title="点击查看项目详情">
        <div class="pTitle">🗂 ${esc(p.title)}
          <span class="pill" style="background:${color}">${label}</span></div>
        <div class="pStep">${curLine}</div>
        <div class="pBar"><i style="width:${(doneN / total) * 100}%"></i></div>
        ${lastReview ? `<div class="pReview">👑 ${esc(lastReview.review)}</div>` : ""}
      </div>`;
    }).join("");
    html += history.map((p) => {
      const [label, color] = PS[p.status] || [p.status, "#999"];
      return `<div class="projMini" data-pid="${p.id}"
                   title="点击查看项目详情${p.error ? ` · ${esc(p.error)}` : ""}">
        <span class="pill" style="background:${color}">${label}</span>
        <span class="mTitle">🗂 ${esc(p.title)}</span>
        <small>${p.steps.length} 步</small></div>`;
    }).join("");

    // 进行中的普通任务（可点开详情）
    const activeTasks = tasks.filter((t) => RUN_T.includes(t.status));
    if (activeTasks.length) {
      html += `<div class="taskSectionTitle">🎯 进行中任务</div>` +
        activeTasks.map((t) => {
          const [label, color] = TASK_ST[t.status] || [t.status, "#999"];
          const doneN = t.subtasks.filter((s) => s.status === "done").length;
          const who = t.subtasks.find((s) => s.status === "running");
          return `<div class="projMini" data-tid="${t.id}"
                       title="点击查看任务详情">
            <span class="pill" style="background:${color}">${label}</span>
            <span class="mTitle">📮 ${esc(t.title)}</span>
            <small>${t.subtasks.length ? `${doneN}/${t.subtasks.length}` : ""}${
              who ? ` · ${who.role}` : ""}</small></div>`;
        }).join("");
    }
    // 最近完成/失败的任务（最多 3 条，可查看产出文件）
    const histTasks = tasks
      .filter((t) => ["done", "failed"].includes(t.status)).slice(0, 3);
    if (histTasks.length) {
      html += `<div class="taskSectionTitle">📦 最近交付</div>` +
        histTasks.map((t) => {
          const [label, color] = TASK_ST[t.status] || [t.status, "#999"];
          const files = t.subtasks.reduce((n, s) => n + (s.files || []).length, 0);
          return `<div class="projMini" data-tid="${t.id}"
                       title="点击查看任务详情与产出">
            <span class="pill" style="background:${color}">${label}</span>
            <span class="mTitle">📮 ${esc(t.title)}</span>
            <small>${files ? `📄${files}` : ""}</small></div>`;
        }).join("");
    }
    board.innerHTML = html;
    board.querySelectorAll("[data-pid]").forEach((el) => {
      el.onclick = () => this.openProjDetail(+el.dataset.pid);
    });
    board.querySelectorAll("[data-tid]").forEach((el) => {
      el.onclick = () => this.openTaskDetail(+el.dataset.tid);
    });
  }

  // ── 详情看板（项目 / 任务共用弹窗）─────────────────────
  openProjDetail(pid) {
    this.projDetailId = pid;
    this.taskDetailId = null;
    this._projDetailSig = "";
    document.getElementById("projDetailCard").classList.remove("hidden");
    document.getElementById("projDetailBackdrop").classList.remove("hidden");
    this.#renderDetailPopup();
  }

  openTaskDetail(tid) {
    this.taskDetailId = tid;
    this.projDetailId = null;
    this._projDetailSig = "";
    document.getElementById("projDetailCard").classList.remove("hidden");
    document.getElementById("projDetailBackdrop").classList.remove("hidden");
    this.#renderDetailPopup();
  }

  closeProjDetail() {
    this.projDetailId = null;
    this.taskDetailId = null;
    document.getElementById("projDetailCard").classList.add("hidden");
    document.getElementById("projDetailBackdrop").classList.add("hidden");
  }

  #renderDetailPopup() {
    if (this.taskDetailId) this.#renderTaskDetail();
    else if (this.projDetailId) this.#renderProjDetail();
  }

  // 目录按钮：调用后端在 Finder 中打开（限产出目录）
  #bindOpenDir(container) {
    container.querySelectorAll(".openDirBtn").forEach((btn) => {
      btn.onclick = async () => {
        btn.disabled = true;
        try {
          const d = await (await fetch("/api/open-dir", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path: btn.dataset.path }),
          })).json();
          if (!d.ok) alert(d.error || "打开失败");
        } catch { alert("网络出错了…"); }
        btn.disabled = false;
      };
    });
  }

  // 编辑器按钮：用 Qoder/VS Code 打开项目目录
  #bindOpenEditor(container) {
    container.querySelectorAll(".editorBtn").forEach((btn) => {
      btn.onclick = async () => {
        btn.disabled = true;
        try {
          const d = await (await fetch("/api/open-editor", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path: btn.dataset.path }),
          })).json();
          if (!d.ok) alert(d.error || "打开失败");
        } catch { alert("网络出错了…"); }
        btn.disabled = false;
      };
    });
  }

  // ── 任务详情：子任务进展 + 产出文件 ────────────────────
  #renderTaskDetail() {
    if (!this.taskDetailId || !this.lastSnap) return;
    const t = (this.lastSnap.tasks || [])
      .find((x) => x.id === this.taskDetailId);
    if (!t) return;
    const sig = JSON.stringify(t);
    if (sig === this._projDetailSig) return;   // 无变化不重绘
    this._projDetailSig = sig;

    const AS = { pending: "⬜", running: "⏳", done: "✅", failed: "❌" };
    const [label, color] = TASK_ST[t.status] || [t.status, "#999"];
    const agentOf = (role) =>
      this.lastSnap.agents.find((a) => a.id === role) || { emoji: "👤", name: role };
    const assignee = t.assignee === "tl" ? "队长拆解" : `直接指派`;

    const head = document.getElementById("projDetailHead");
    head.innerHTML = `
      <b>📮 ${esc(t.title)}</b>
      <span class="pill" style="background:${color}">${label}</span>
      ${t.workdir ? `<button class="miniBtn openDirBtn" data-path="${esc(t.workdir)}" title="在 Finder 中打开">📁 任务目录</button>` : ""}`;
    this.#bindOpenDir(head);

    let html = `<p class="pdDesc">${assignee} · 发布于 ${
      new Date(t.created * 1000).toLocaleTimeString("zh-CN", { hour12: false })}${
      t.finished ? ` · 完成于 ${new Date(t.finished * 1000).toLocaleTimeString("zh-CN", { hour12: false })}` : ""}</p>`;
    if (t.error) html += `<p class="pdErr">⚠️ ${esc(t.error)}</p>`;

    const answered = (t.questions || []).filter((q) => "answer" in q);
    if (answered.length) {
      html += `<div class="pdSection">🙋 澄清与回答</div>` + answered.map((q) => `
        <div class="pdQa"><b>Q：${esc(q.q)}</b><span>💬 ${esc(q.answer || "（跳过，队长自行决定）")}</span></div>`).join("");
    }

    if (t.subtasks.length) {
      const doneN = t.subtasks.filter((s) => s.status === "done").length;
      html += `<div class="pdSection">🧩 子任务进展（${doneN}/${t.subtasks.length}）</div>`;
      html += t.subtasks.map((s) => {
        const a = agentOf(s.role);
        const files = (s.files || []).map((f) => t.workdir
          ? `<span class="fLink" data-fpath="${esc(t.workdir)}/${esc(f)}">${esc(f)}</span>`
          : esc(f)).join("、");
        const dur = s.started && s.finished
          ? ` · 耗时 ${Math.max(1, Math.round((s.finished - s.started) / 60))} 分钟` : "";
        return `
        <div class="pdStep ${s.status}${s.status === "running" ? " cur" : ""}">
          <div class="pdStepTitle">${AS[s.status] || "⬜"} ${esc(s.title)}</div>
          <div class="pdAssign">
            <span class="pdWho">${a.emoji} ${esc(a.name)}${dur}</span>
            ${s.brief ? `<span class="pdBrief">${esc(s.brief)}</span>` : ""}
            ${s.summary ? `<span class="pdSum">${esc(s.summary)}</span>` : ""}
            ${files ? `<span class="pdFiles">📄 ${files}</span>` : ""}
          </div>
        </div>`;
      }).join("");
    } else if (t.status === "splitting" || t.status === "clarifying") {
      html += `<p class="pdDesc">队长桑正在处理…</p>`;
    } else if (!t.subtasks.length) {
      html += `<p class="pdDesc">暂无子任务记录</p>`;
    }
    // 已完成/失败的任务：追问输入框（原班人马在原目录继续）
    if (["done", "failed"].includes(t.status)) {
      html += `
      <div class="followupRow">
        <input class="followupInput" type="text" maxlength="300"
               placeholder="💬 追问：基于已有产出继续，如「把标题改成红色」…" />
        <button class="followupBtn">发送</button>
      </div>`;
    }
    const body = document.getElementById("projDetailBody");
    body.innerHTML = html;
    const fuInput = body.querySelector(".followupInput");
    const fuBtn = body.querySelector(".followupBtn");
    if (fuBtn) {
      const send = async () => {
        const message = fuInput.value.trim();
        if (!message) return;
        fuBtn.disabled = true;
        fuBtn.textContent = "发送中…";
        try {
          const d = await (await fetch(`/api/task/${t.id}/followup`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message }),
          })).json();
          if (d.ok) {
            fuInput.value = "";
            this._projDetailSig = "";   // 强制重绘（状态变 running）
          } else {
            alert(d.error || "发送失败");
          }
        } catch { alert("网络出错了…"); }
        fuBtn.disabled = false;
        fuBtn.textContent = "发送";
      };
      fuBtn.onclick = send;
      fuInput.onkeydown = (e) => { if (e.key === "Enter") send(); };
    }
  }

  #renderProjDetail() {
    if (!this.projDetailId || !this.lastSnap) return;
    const p = (this.lastSnap.projects || [])
      .find((x) => x.id === this.projDetailId);
    if (!p) return;
    const sig = JSON.stringify(p);
    if (sig === this._projDetailSig) return;   // 无变化不重绘（保留滚动位置）
    this._projDetailSig = sig;

    const PS = {
      clarifying: ["🤔 审题中", "#9a7fe0"], waiting: ["🙋 等你确认", "#e0608a"],
      planning: ["🧠 规划中", "#9a7fe0"], running: ["🔥 推进中", "#e09a4a"],
      done: ["🏁 已结项", "#3aa876"], failed: ["⚠️ 受阻", "#e0608a"],
      canceled: ["🛑 已终止", "#8b8b9e"],
    };
    const AS = { pending: "⬜", running: "⏳", done: "✅", failed: "❌" };
    const [label, color] = PS[p.status] || [p.status, "#999"];
    const agentOf = (role) =>
      this.lastSnap.agents.find((a) => a.id === role) || { emoji: "👤", name: role };

    const active = ["clarifying", "waiting", "planning", "running"]
      .includes(p.status);
    const head = document.getElementById("projDetailHead");
    head.innerHTML = `
      <b>🗂 ${esc(p.title)}</b>
      <span class="pill" style="background:${color}">${label}</span>
      ${p.dir ? `<button class="miniBtn openDirBtn" data-path="${esc(p.dir)}" title="在 Finder 中打开项目目录">📁 Finder</button>` : ""}
      ${p.dir ? `<button class="miniBtn editorBtn" data-path="${esc(p.dir)}" title="用 Qoder 打开项目">💻 Qoder</button>` : ""}
      ${active ? '<button id="pdCancel" class="miniBtn danger">🛑 终止</button>' : ""}
      ${p.status === "failed" ? '<button id="pdRetry" class="miniBtn">🔄 重试</button>' : ""}
      <button id="pdDelete" class="miniBtn danger" title="删除项目">🗑 删除</button>`;
    this.#bindOpenDir(head);
    this.#bindOpenEditor(head);
    const cancelBtn = head.querySelector("#pdCancel");
    if (cancelBtn) {
      cancelBtn.onclick = async () => {
        if (!confirm(`确定终止项目「${p.title}」？\n正在执行的会话会被立即中断，已产出的文件会保留。`)) return;
        cancelBtn.disabled = true;
        try {
          const d = await (await fetch(`/api/project/${p.id}/cancel`,
                                       { method: "POST" })).json();
          if (!d.ok) alert(d.error || "终止失败");
        } catch { alert("网络出错了…"); }
      };
    }
    const deleteBtn = head.querySelector("#pdDelete");
    if (deleteBtn) {
      deleteBtn.onclick = async () => {
        const withFiles = confirm(
          `确定删除项目「${p.title}」？\n\n点「确定」= 删除项目并删除所有文件\n点「取消」= 不删除`);
        if (!withFiles && !confirm(`仅从列表中移除项目「${p.title}」？（保留文件）`)) return;
        deleteBtn.disabled = true;
        try {
          const d = await (await fetch(`/api/project/${p.id}/delete`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ delete_files: withFiles }),
          })).json();
          if (d.ok) this.closeProjDetail();
          else alert(d.error || "删除失败");
        } catch { alert("网络出错了…"); }
        deleteBtn.disabled = false;
      };
    }
    const retryBtn = head.querySelector("#pdRetry");
    if (retryBtn) {
      retryBtn.onclick = async () => {
        retryBtn.disabled = true;
        retryBtn.textContent = "重试中…";
        try {
          const d = await (await fetch(`/api/project/${p.id}/retry`,
                                       { method: "POST" })).json();
          if (!d.ok) alert(d.error || "重试失败");
          else this._projDetailSig = "";  // 强制重绘
        } catch { alert("网络出错了…"); }
        retryBtn.disabled = false;
        retryBtn.textContent = "🔄 重试";
      };
    }

    let html = "";
    if (p.desc) html += `<p class="pdDesc">${esc(p.desc)}</p>`;
    if (p.error) html += `<p class="pdErr">⚠️ ${esc(p.error)}</p>`;
    const answered = (p.questions || []).filter((q) => "answer" in q);
    if (answered.length) {
      html += `<div class="pdSection">🙋 需求澄清</div>` + answered.map((q) => `
        <div class="pdQa"><b>Q：${esc(q.q)}</b><span>💬 ${esc(q.answer || "（跳过，队长自行决定）")}</span></div>`).join("");
    }
    html += `<div class="pdSection">📍 步骤进展（${
      p.steps.filter((s) => s.status === "done").length}/${p.steps.length}）</div>`;
    html += p.steps.map((s, i) => {
      const cur = i === p.stepIndex && s.status !== "done";
      return `
      <div class="pdStep ${s.status}${cur ? " cur" : ""}">
        <div class="pdStepTitle">${AS[s.status] || "⬜"} 步骤 ${i + 1}：${esc(s.title)}</div>
        ${s.assigns.map((g) => {
          const a = agentOf(g.role);
          const files = (g.files || []).map((f) => p.dir
            ? `<span class="fLink" data-fpath="${esc(p.dir)}/shared/${esc(f)}">${esc(f)}</span>`
            : esc(f)).join("、");
          return `
          <div class="pdAssign">
            <span class="pdWho">${a.emoji} ${esc(a.name)} ${AS[g.status] || ""}</span>
            ${g.summary ? `<span class="pdSum">${esc(g.summary)}</span>` : ""}
            ${files ? `<span class="pdFiles">📄 ${files}</span>` : ""}
          </div>`;
        }).join("")}
        ${s.review ? `<div class="pdReview">👑 队长验收：${esc(s.review)}</div>` : ""}
      </div>`;
    }).join("") || `<p class="pdDesc">队长还没规划出步骤…</p>`;
    // 失败的项目：显示重试输入框（可携带补充指示）
    if (p.status === "failed") {
      html += `
      <div class="followupRow">
        <input class="followupInput" type="text" maxlength="300"
               placeholder="💬 补充指示后重试，或直接点发送从断点继续…" />
        <button class="followupBtn">🔄 重试</button>
      </div>`;
    }
    // 已完成的项目：追加需求（队长会规划新增步骤继续做）
    if (p.status === "done") {
      html += `
      <div class="followupRow">
        <input class="followupInput extendInput" type="text" maxlength="800"
               placeholder="➕ 追加新需求，队长会拆解新步骤继续做…" />
        <button class="followupBtn extendBtn">➕ 追加</button>
      </div>`;
    }
    const body = document.getElementById("projDetailBody");
    body.innerHTML = html;
    // 绑定重试输入框事件
    const retryInput = body.querySelector(".followupInput");
    const retryFuBtn = body.querySelector(".followupBtn");
    if (retryFuBtn && p.status === "failed") {
      const sendRetry = async () => {
        const message = retryInput.value.trim();
        retryFuBtn.disabled = true;
        retryFuBtn.textContent = "重试中…";
        try {
          const d = await (await fetch(`/api/project/${p.id}/retry`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message }),
          })).json();
          if (d.ok) {
            retryInput.value = "";
            this._projDetailSig = "";  // 强制重绘
          } else {
            alert(d.error || "重试失败");
          }
        } catch { alert("网络出错了…"); }
        retryFuBtn.disabled = false;
        retryFuBtn.textContent = "🔄 重试";
      };
      retryFuBtn.onclick = sendRetry;
      retryInput.onkeydown = (e) => { if (e.key === "Enter") sendRetry(); };
    }
    // 已完成项目：追加需求 → 队长重新拆解继续做
    const extInput = body.querySelector(".extendInput");
    const extBtn = body.querySelector(".extendBtn");
    if (extBtn && p.status === "done") {
      const sendExtend = async () => {
        const message = extInput.value.trim();
        if (!message) { extInput.focus(); return; }
        extBtn.disabled = true;
        extBtn.textContent = "规划中…";
        try {
          const d = await (await fetch(`/api/project/${p.id}/extend`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message }),
          })).json();
          if (d.ok) {
            extInput.value = "";
            this._projDetailSig = "";  // 强制重绘
          } else {
            alert(d.error || "追加失败");
          }
        } catch { alert("网络出错了…"); }
        extBtn.disabled = false;
        extBtn.textContent = "➕ 追加";
      };
      extBtn.onclick = sendExtend;
      extInput.onkeydown = (e) => { if (e.key === "Enter") sendExtend(); };
    }
  }

  #upsertChip(a) {
    let chip = this.chips.get(a.id);
    if (!chip) {
      chip = document.createElement("div");
      chip.className = "chip";
      chip.innerHTML = `
        <div class="face"></div>
        <div class="meta"><b></b><i></i></div>
        <span class="stateIco"></span>
        <button class="term" title="实时执行终端">📟</button>
        <button class="gear" title="设置">⚙️</button>`;
      chip.onclick = () => this.onSelect(a.id);
      chip.querySelector(".gear").onclick = (e) => {
        e.stopPropagation();
        this.openSettings(a.id);
      };
      chip.querySelector(".term").onclick = (e) => {
        e.stopPropagation();
        this.openTerminal(a.id);
      };
      this.roster.appendChild(chip);
      this.chips.set(a.id, chip);
      chip.querySelector(".face").style.background = rgba(a.color, 0.35);
      chip.querySelector(".face").textContent = a.emoji;
      chip.querySelector("b").textContent = a.name;
    }
    const sub = a.current ? `「${a.current.title}」`
              : a.activity || `已交付 ${a.doneCount} 项`;
    chip.querySelector("i").textContent =
      `${STATE_NAME[a.state] || a.state} · ${sub}`;
    chip.querySelector(".stateIco").textContent = STATE_ICON[a.state] || "";
    chip.classList.toggle("busy", a.state !== "idle");
  }

  #updateEvents(events) {
    if (!events.length) return;
    const last = events[events.length - 1];
    const sig = `${last.t}|${last.text}`;
    if (sig === this.lastEventSig) return;
    let start = 0;
    if (this.lastEventSig) {
      for (let i = events.length - 1; i >= 0; i--) {
        if (`${events[i].t}|${events[i].text}` === this.lastEventSig) {
          start = i + 1;
          break;
        }
      }
    }
    for (let i = start; i < events.length; i++) {
      const e = events[i];
      const li = document.createElement("li");
      li.dataset.cat = UI.#evtCat(e.text);
      li.innerHTML = `<time>${e.t}</time>${esc(e.text)}`;
      if (this.evtFilter !== "all" && li.dataset.cat !== this.evtFilter) {
        li.style.display = "none";
      }
      this.eventList.appendChild(li);
    }
    while (this.eventList.children.length > 80) {
      this.eventList.removeChild(this.eventList.firstChild);
    }
    this.eventList.scrollTop = this.eventList.scrollHeight;
    this.lastEventSig = sig;
  }

  // ── 成员详情：其真实子任务清单 ─────────────────────────
  #renderDetail(id) {
    const snap = this.lastSnap;
    const a = snap.agents.find((x) => x.id === id);
    if (!a) return;
    this.detailHead.innerHTML = `
      <div class="face" style="background:${rgba(a.color, 0.35)}">${a.emoji}</div>
      <div><b>${a.name}</b><i>${a.role} · ${a.zone}</i></div>
      <span class="badge">${STATE_ICON[a.state] || ""} ${STATE_NAME[a.state] || ""}</span>
      <button class="miniBtn dTerm" title="实时执行终端">📟</button>
      <button class="miniBtn dGear" title="设置">⚙️</button>`;
    this.detailHead.querySelector(".dTerm").onclick = () => this.openTerminal(id);
    this.detailHead.querySelector(".dGear").onclick = () => this.openSettings(id);

    // 从任务 + 项目里收集该成员的历史（倒序=最新在前）
    const mine = [];
    for (const t of snap.tasks) {
      if (id === "tl" && t.assignee === "tl") {
        mine.push({ title: `拆解「${t.title}」`, status:
          t.status === "splitting" ? "running"
          : t.status === "failed" && !t.subtasks.length ? "failed" : "done",
          summary: t.error || `拆成 ${t.subtasks.length} 个子任务`, files: [] });
      }
      for (const s of t.subtasks) {
        if (s.role === id) mine.push({ ...s, dir: t.workdir });
      }
    }
    for (const p of snap.projects || []) {
      for (const st of p.steps) {
        for (const g of st.assigns) {
          if (g.role === id) {
            mine.push({ title: `[项目] ${st.title}`, status: g.status,
                        summary: g.summary, files: g.files,
                        dir: p.dir ? `${p.dir}/shared` : "" });
          }
        }
      }
    }
    const fileLink = (dir, f) => dir
      ? `<span class="fLink" data-fpath="${esc(dir)}/${esc(f)}">${esc(f)}</span>`
      : esc(f);
    const rows = mine.slice(0, 12).map((s) => `
      <div class="todoItem ${s.status === "done" ? "done" : ""}">
        <div class="row"><span class="st">${ST_ICON[s.status] || "⬜"}</span>
          <span>${esc(s.title)}</span></div>
        ${s.summary ? `<p class="subSummary">${esc(s.summary)}</p>` : ""}
        ${s.files && s.files.length
          ? `<p class="subFiles">📁 ${s.files.map((f) => fileLink(s.dir, f)).join("、")}</p>` : ""}
      </div>`).join("");
    this.detailTodos.innerHTML = `
      <p style="font-size:12px;color:var(--ink-soft);margin:4px 0 10px">
        ${a.activity ? `⚙️ ${esc(a.activity)} ・ ` : ""}已交付 ${a.doneCount} 项
      </p>` + (rows || '<p style="font-size:12px;color:var(--ink-soft)">还没有接到过任务，去左下角发一个吧～</p>');
  }

  // ── 每帧更新（rAF）：3D 投影后的浮动元素 ───────────────
  frame3d(labels, linkLabels, zoneLabels, hud) {
    // 交付计数电子屏数字
    if (!this.deliverBadge) {
      this.deliverBadge = document.createElement("div");
      this.deliverBadge.className = "deliverBadge";
      this.overlay.appendChild(this.deliverBadge);
    }
    if (hud) {
      this.deliverBadge.style.display = hud.visible ? "" : "none";
      if (this.deliverBadge.textContent !== hud.text) {
        this.deliverBadge.textContent = hud.text;
      }
      this.deliverBadge.style.left = `${hud.x}px`;
      this.deliverBadge.style.top = `${hud.y}px`;
    }

    for (const a of labels) {
      let t = this.tags.get(a.id);
      if (!t) {
        t = {
          name: document.createElement("div"),
          bubble: document.createElement("div"),
          emote: document.createElement("div"),
        };
        t.name.className = "nameTag";
        t.bubble.className = "bubble";
        t.emote.className = "emote";
        t.bubble.style.display = "none";
        this.overlay.append(t.name, t.bubble, t.emote);
        this.tags.set(a.id, t);
      }
      const show = a.visible;
      t.name.style.display = show ? "" : "none";
      if (show) {
        t.name.innerHTML = `<em>${a.emoji}</em>${a.name}`;
        t.name.style.borderColor = rgba(a.color, 0.9);
        t.name.style.left = `${a.sx}px`;
        t.name.style.top = `${a.sy}px`;
      }

      if (a.bubble && show) {
        if (t.bubble.textContent !== a.bubble) t.bubble.textContent = a.bubble;
        t.bubble.style.display = "";
        t.bubble.style.left = `${a.bx}px`;
        t.bubble.style.top = `${a.by - 6}px`;
      } else {
        t.bubble.style.display = "none";
      }

      const emo = a.state === "working" ? "⚙️" : a.state === "meeting" ? "🧠" : "";
      t.emote.textContent = emo;
      t.emote.style.display = emo && show ? "" : "none";
      t.emote.style.left = `${a.bx + 30}px`;
      t.emote.style.top = `${a.by + 10}px`;
    }

    for (const z of zoneLabels) {
      let el = this.zoneTags.get(z.label);
      if (!el) {
        el = document.createElement("div");
        el.className = "zoneTag";
        el.textContent = z.label;
        this.overlay.appendChild(el);
        this.zoneTags.set(z.label, el);
      }
      el.style.display = z.visible ? "" : "none";
      el.style.left = `${z.x}px`;
      el.style.top = `${z.y}px`;
    }

    while (this.orbLabels.length < linkLabels.length) {
      const el = document.createElement("div");
      el.className = "orbLabel";
      this.overlay.appendChild(el);
      this.orbLabels.push(el);
    }
    this.orbLabels.forEach((el, i) => {
      const l = linkLabels[i];
      if (!l || !l.visible) { el.style.display = "none"; return; }
      el.style.display = "";
      el.textContent = `📦 ${l.label}`;
      el.style.left = `${l.x}px`;
      el.style.top = `${l.y}px`;
    });
  }
}
