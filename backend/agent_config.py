# -*- coding: utf-8 -*-
"""
每个分身的独立配置（文件存储 data/agents_config.json）：
- workdir    专属工作目录（空 = 使用任务默认目录 workspace/task-N）
- skills     可使用的 Skill 名单（["all"] = 全部启用）
- rules      附加规则（追加到系统提示词）
- model      指定模型（空 = 默认）
- allow_bash 是否允许执行 shell 命令
"""
import asyncio
import json
import pathlib
import re

DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
CONFIG_FILE = DATA_DIR / "agents_config.json"

DEFAULT_CFG = {
    "workdir": "",
    "skills": [],
    "rules": "",
    "model": "",
    "context": "",       # 上下文窗口大小（如 400000，空 = 默认）
    "allow_bash": False,
    "learned": [],       # 自进化沉淀的规则（复盘自动追加，可在设置页删除）
}

MAX_LEARNED = 8

_configs: dict = {}
_skills_cache: list | None = None
_models_cache: list | None = None


def load():
    global _configs
    if CONFIG_FILE.exists():
        try:
            _configs = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            _configs = {}
    return _configs


def get(agent_id: str) -> dict:
    cfg = dict(DEFAULT_CFG)
    cfg["skills"] = []
    cfg["learned"] = []          # 避免共享 DEFAULT_CFG 中的可变列表
    cfg.update(_configs.get(agent_id, {}))
    return cfg


# ── 全局配置（qodercli 会话全局并发数等）───────────────────
DEFAULT_GLOBAL = {"max_concurrency": 2}


def get_global() -> dict:
    g = dict(DEFAULT_GLOBAL)
    g.update(_configs.get("_global", {}))
    return g


def save_global(cfg: dict) -> dict:
    try:
        n = int(cfg.get("max_concurrency", DEFAULT_GLOBAL["max_concurrency"]))
    except (TypeError, ValueError):
        n = DEFAULT_GLOBAL["max_concurrency"]
    _configs["_global"] = {"max_concurrency": max(1, min(8, n))}
    _flush()
    return dict(_configs["_global"])

def get_all() -> dict:
    return {aid: get(aid) for aid in set(_configs) if not aid.startswith("_")}


def save(agent_id: str, cfg: dict) -> dict:
    """校验并保存单个分身的配置。"""
    existing = _configs.get(agent_id, {})
    clean = {
        "workdir": str(cfg.get("workdir", "")).strip(),
        "skills": [s.strip() for s in cfg.get("skills", []) if str(s).strip()][:30],
        "rules": str(cfg.get("rules", "")).strip()[:2000],
        "model": str(cfg.get("model", "")).strip()[:60],
        "context": str(cfg.get("context", "")).strip()[:12],
        "allow_bash": bool(cfg.get("allow_bash", False)),
        # learned 未随请求提交时保留已有的（兼容脚本调用）
        "learned": ([str(x).strip()[:200] for x in cfg["learned"] if str(x).strip()]
                    if "learned" in cfg else existing.get("learned", []))[:MAX_LEARNED],
    }
    _configs[agent_id] = clean
    _flush()
    return clean


def add_learned(agent_id: str, rule: str) -> bool:
    """自进化：追加一条自学规则（去重、上限 MAX_LEARNED，FIFO 淘汰）。"""
    rule = str(rule).strip()[:200]
    if not rule:
        return False
    cfg = _configs.setdefault(agent_id, get(agent_id))
    learned = cfg.setdefault("learned", [])
    if rule in learned:
        return False
    learned.append(rule)
    if len(learned) > MAX_LEARNED:
        del learned[0]
    _flush()
    return True


def _flush():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_configs, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(CONFIG_FILE)


def resolve_workdir(agent_id: str, root: pathlib.Path) -> pathlib.Path | None:
    """解析专属工作目录：支持 ~ 与相对路径（相对项目根），自动创建。"""
    wd = get(agent_id)["workdir"]
    if not wd:
        return None
    p = pathlib.Path(wd).expanduser()
    if not p.is_absolute():
        p = root / p
    try:
        p.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        return None


# ── 自定义 Agent 管理 ────────────────────────────────────
# 存储在 _configs["_custom_agents"] 列表中，每个元素与 PERSONAS 格式一致

BUILTIN_IDS = {"tl", "dev", "qa", "reviewer"}

# 扩展工位位置（左下区域空闲位置）
_EXTRA_DESKS = [
    (0.50, 0.44),
    (0.16, 0.72),
    (0.40, 0.72),
    (0.50, 0.72),
]


def get_custom_agents() -> list[dict]:
    """返回所有自定义 Agent 的 spec 列表。"""
    return list(_configs.get("_custom_agents", []))


def _next_desk(existing_desks: list[tuple]) -> tuple:
    """从预定义扩展工位中取第一个未被占用的。"""
    used = {tuple(d) for d in existing_desks}
    for d in _EXTRA_DESKS:
        if d not in used:
            return d
    # 全部用完：在现有基础上往下偏移
    return (0.50, 0.44 + 0.28 * (len(existing_desks) - 3))


def add_custom_agent(spec: dict) -> dict | str:
    """新增自定义 Agent。返回完整 spec 或错误字符串。"""
    aid = re.sub(r"[^a-z0-9_-]", "", (spec.get("id") or "").strip().lower())[:20]
    if not aid:
        return "id 不能为空"
    if aid in BUILTIN_IDS:
        return f"id '{aid}' 与内置角色冲突"
    customs = _configs.setdefault("_custom_agents", [])
    if any(c["id"] == aid for c in customs):
        return f"id '{aid}' 已存在"
    if len(customs) >= 8:
        return "自定义 Agent 最多 8 个"
    name = (spec.get("name") or aid).strip()[:20]
    emoji = (spec.get("emoji") or "🤖").strip()[:4]
    role = (spec.get("role") or "自定义角色").strip()[:40]
    sys_prompt = (spec.get("sys") or f"你是「{name}」，{role}。在当前工作目录内完成任务，"
                  f"产出真实文件。完成后用 3 句话以内总结产出。").strip()[:500]
    color = spec.get("color") or [0.6, 0.6, 0.8]
    if isinstance(color, list) and len(color) == 3:
        color = [max(0, min(1, float(c))) for c in color]
    else:
        color = [0.6, 0.6, 0.8]
    # 工位自动分配
    from personas import PERSONAS
    existing_desks = [p["desk"] for p in PERSONAS] + [c["desk"] for c in customs]
    desk = spec.get("desk")
    if not desk or not isinstance(desk, (list, tuple)) or len(desk) != 2:
        desk = _next_desk(existing_desks)
    else:
        desk = (float(desk[0]), float(desk[1]))
    zone = (spec.get("zone") or f"{name}工位").strip()[:20]
    full = {
        "id": aid, "name": name, "role": role, "emoji": emoji,
        "color": color, "accessory": spec.get("accessory", 0),
        "desk": list(desk), "zone": zone, "sys": sys_prompt,
        "custom": True,
    }
    customs.append(full)
    _flush()
    return full


def remove_custom_agent(aid: str) -> bool:
    """删除自定义 Agent。内置角色不可删除。"""
    if aid in BUILTIN_IDS:
        return False
    customs = _configs.get("_custom_agents", [])
    before = len(customs)
    _configs["_custom_agents"] = [c for c in customs if c["id"] != aid]
    if len(_configs["_custom_agents"]) < before:
        _flush()
        return True
    return False


async def list_skills(refresh=False) -> list:
    """枚举本机已安装的 Skill（解析 `qodercli skills list`），带缓存。"""
    global _skills_cache
    if _skills_cache is not None and not refresh:
        return _skills_cache
    try:
        proc = await asyncio.create_subprocess_exec(
            "qodercli", "skills", "list",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        text = out.decode("utf-8", "ignore")
        skills = []
        cur = None
        for line in text.splitlines():
            m = re.match(r"^(\S+)\s+\[(Enabled|Disabled)\]", line.strip())
            if m:
                cur = {"name": m.group(1), "enabled": m.group(2) == "Enabled",
                       "desc": ""}
                skills.append(cur)
            elif cur is not None and not cur["desc"]:
                dm = re.match(r"^\s*Description:\s*(.+)", line)
                if dm:
                    cur["desc"] = dm.group(1).strip()[:80]
        _skills_cache = skills
    except Exception:
        _skills_cache = []
    return _skills_cache


async def list_models(refresh=False) -> list:
    """通过 qoder_agent_sdk 获取本账号可用模型列表，带缓存。"""
    global _models_cache
    if _models_cache is not None and not refresh:
        return _models_cache
    try:
        from qoder_agent_sdk import (
            QoderSDKClient, QoderAgentOptions, qodercli_auth,
        )
        opts = QoderAgentOptions(
            auth=qodercli_auth(),
            cwd=str(DATA_DIR.parent),
            tools=[], max_turns=1,
            # 给一个 stderr 回调：让内置 qodercli 的 stderr（如 git 探测的 fatal）
            # 被 PIPE 后吁掉，不再继承到控制台刷屏。
            stderr=lambda _line: None,
        )
        client = QoderSDKClient(options=opts)
        await asyncio.wait_for(client.connect(None), timeout=60)
        try:
            raw = await asyncio.wait_for(client.get_available_models(),
                                         timeout=30)
        finally:
            await client.disconnect()
        models = []
        for m in raw or []:
            if not isinstance(m, dict):
                continue
            mid = m.get("modelId") or m.get("value")
            if not mid or m.get("isEnabled") is False:
                continue
            models.append({
                "id": mid,
                "name": m.get("displayName") or mid,
                "desc": (m.get("description") or "")[:60],
                "contexts": m.get("availableContextWindows") or [],
            })
        _models_cache = models
    except Exception:
        _models_cache = []
    return _models_cache
