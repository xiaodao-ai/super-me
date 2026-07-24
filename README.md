# 🫧 超级个体 · Super Me

> 一个「真实干活」的多 Agent 协作可视化沙盒：把一支研发小队搬进 3D 办公室，你派需求，队长真实拆解、成员真实产出代码文件，全程在画面里可视化流转。

由 [Qoder Agent SDK](https://pypi.org/project/qoder-agent-sdk/) 驱动 —— 不是模拟动画，每个分身都会发起真实的 LLM 会话，在本地目录里读写真实文件。

---

## ✨ 特性

- **真实执行**：任务由 `qoder_agent_sdk` 驱动真实 LLM 会话，成员在 `workspace/` / `projects/` 里产出可运行的真实文件，非模拟。
- **TL 智能拆解**：把需求发给「队长桑」，它会真实拆解为子任务并按阶段（开发 → 测试 → 评审）分批调度。
- **项目督导模式**：复杂需求可立项，队长规划多步骤计划、逐步验收、不达标打回返工，直到结项。
- **验收看真凭实据**：验收时以磁盘实测（`ls`/文件字节数）为最高判据，成员自述与证据冲突时以证据为准，杜绝「嘴上说做了、盘上没东西」。
- **项目追加需求**：已完成的项目可随时追加新需求，队长在既有产出之上规划新增步骤继续推进，不重复造轮子。
- **需求澄清 / 执行插话**：开工前队长会就关键问题向你提问；执行中可随时插话纠正，会中断当前会话并按你的话重跑。
- **自进化**：任务/项目复盘后自动为成员沉淀可复用规则，写入各自配置。
- **健壮的 JSON 解析**：拆解/规划/验收的 LLM 输出先本地修复，失败再交由 LLM 重排为严格 JSON，最大限度避免因格式问题中断。
- **执行日志留痕**：项目 / 任务 / LLM / 分身四类日志分门别类落盘到 `backend/logs/`，随时回溯完整时间线与原始 prompt/response。
- **3D 可视化**：WebGPU 渲染的俯视办公室，实时展示分身移动、气泡、任务流转动画与协作动态（前端限帧降 CPU）。
- **可配置**：每个分身可独立设置工作目录、可用 Skill、附加规则、指定模型、是否允许执行 Shell 命令。
- **定时任务 & 自定义 Agent**：支持给分身配置周期性任务（含畸形数据容错），也支持新增自定义角色。
- **本地持久化**：世界状态（任务/事件/完成数）落盘到 `backend/data/state.json`，重启自动恢复。

---

## 🧩 分身阵容

| Emoji | 名称 | 角色 | 职责 |
|------|--------|------|------|
| 👑 | 队长桑 | TL · 技术负责人 | 拆解需求、规划项目、追加规划、验收督导、复盘沉淀 |
| 💻 | 全栈君 | 开发 · 全栈工程师 | 前后端一肩挑，产出真实代码文件 |
| 🐞 | 测试喵 | 测试 · 质量工程师 | 编写测试用例/验收清单，产出 `TEST_REPORT.md` |
| 🕵️ | 检查官 | 检查员 · 代码评审官 | 从正确性/可读性/风险评审，产出 `REVIEW.md` |

> 还可在界面中「➕ Agent」新增自定义角色。

---

## 🏗 架构

```
super-me/
├── backend/                # Python + aiohttp 服务端
│   ├── server.py           # 入口：静态托管 + /ws 广播 + REST API
│   ├── world.py            # 世界状态模型（Agent / Task / Project / 事件）
│   ├── runner.py           # 任务编排器：驱动 SDK 真实执行、拆解、验收、返工、追加
│   ├── personas.py         # 分身人设 + 各类 LLM 提示词模板（拆解/规划/追加/验收/JSON 修复）
│   ├── agent_config.py     # 每个分身的配置（目录/Skill/规则/模型/并发）
│   ├── project_memory.py   # 项目级记忆的读写
│   ├── run_log.py          # 执行日志：项目/任务/LLM/分身四类日志落盘 logs/
│   ├── scheduler.py        # 定时任务调度（含脏数据容错）
│   ├── storage.py          # 状态落盘 data/state.json
│   └── smoke_*.py          # SDK / 计划 / 模型的冒烟自测脚本
├── frontend/               # WebGPU 前端（无构建，原生 ESM）
│   ├── index.html          # 页面结构
│   ├── main.js / ui.js     # 交互与 WebSocket 快照渲染
│   ├── scene3d.js / renderer3d.js  # 3D 办公室场景与渲染
│   └── style.css
├── requirements.txt
└── README.md
```

**数据流**：前端通过 `/ws` 每秒接收 ~10Hz 的世界快照；发布任务走 `POST /api/task` 或 `/api/project`，`runner` 用 SDK 执行并把工具调用/文本输出实时回传到世界，前端渲染为气泡、活动、事件流与流转动画。

---

## 📦 环境要求

- **Python 3.10+**（macOS 已验证；使用了 `pathlib.is_relative_to` 等 3.9+ 特性）
- **[Qoder](https://qoder.com) 客户端 / `qodercli` 命令**：分身执行依赖 `qodercli_auth` 完成鉴权
  - 若系统无 `qodercli`，Skill 列表将为空，且真实任务会因鉴权失败而报错
- **支持 WebGPU 的浏览器**：最新版 Chrome / Edge / Safari
- 「用编辑器打开」功能依赖 `qoder` 命令；「在 Finder 打开」为 macOS 特性（`open`）

---

## 🚀 快速开始

### 1. 安装依赖

```bash
# 普通安装
python3 -m pip install -r requirements.txt

# 国内镜像（推荐，更快）
python3 -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
```

### 2. 完成 Qoder 鉴权

确保本机已安装 Qoder 并登录（提供 `qodercli` 命令与账号鉴权），否则分身无法真实执行任务。

### 3. 启动服务

```bash
cd backend
python3 server.py
```

看到以下输出即成功：

```
🌟 super-me [REAL MODE] running at http://localhost:8787
```

### 4. 打开浏览器

访问 **http://localhost:8787** ，用支持 WebGPU 的浏览器打开。

---

## 🎮 使用方式

- **派任务**：选接收人 + 填任务内容。发给「队长桑」会先拆解再分派，产出在 `workspace/task-N/`。
- **发项目**：填项目名/描述/文件夹名，选参与成员，队长规划多步骤并督导推进，产出在 `projects/<folder>/shared/`。
- **追加需求**：对已完成的项目继续提需求，队长在既有产出上规划新增步骤推进。
- **澄清问答**：队长弹出问题时选择候选项作答，或跳过让队长自行拍板。
- **执行插话**：点开分身终端，在执行中输入纠正内容立即生效。
- **配置分身**：点分身设置专属目录、Skill、规则、模型、并发与定时任务。
- **并发闸门**：顶栏 🚦 调整同时运行的会话上限（全局即时生效）。

---

## 🔌 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/ws` | WebSocket，接收 ~10Hz 世界快照 |
| POST | `/api/task` | 发布任务 `{assignee, title}` |
| POST | `/api/task/{tid}/followup` | 对已完成/失败任务追问 |
| POST | `/api/project` | 立项 `{title, desc, folder, members}` |
| POST | `/api/project/{pid}/cancel` | 终止进行中的项目 |
| POST | `/api/project/{pid}/delete` | 删除项目（可选删除产出文件） |
| POST | `/api/project/{pid}/retry` | 从失败步骤重试项目 |
| POST | `/api/project/{pid}/extend` | 对已完成项目追加新需求 `{message}` |
| GET/POST/DELETE | `/api/project/{pid}/memory` | 项目记忆增删查 |
| POST | `/api/answer` | 回答队长澄清 `{kind, id, answers}` |
| POST | `/api/interject` | 执行中插话纠正 `{aid, hint}` |
| GET | `/api/stream/{aid}` | 拉取某分身的会话流式日志 |
| GET/POST | `/api/config[/{aid}]` | 读取/保存分身或全局配置 |
| GET | `/api/skills` | 本机可用 Skill 列表（`?refresh=1` 强制重扫，按需加载） |
| GET/POST/DELETE | `/api/agents` | 自定义 Agent 列表/新增/删除 |
| GET/POST/PUT/DELETE | `/api/schedules` | 定时任务管理 |

服务默认监听 `127.0.0.1:8787`。

---

## 💾 数据与产出

- **状态持久化**：`backend/data/state.json`（任务/项目/事件/完成数），重启自动恢复；中断的任务会被标记为失败。
- **配置**：`backend/data/agents_config.json`。
- **执行日志**：`backend/logs/`，按类别分目录：
  - `projects/project-<id>.log` — 每个项目的完整时间线
  - `tasks/task-<id>.log` — 每个任务的完整时间线
  - `llm/llm-YYYYMMDD.log` — 当天所有 LLM 调用的原始 prompt/response
  - `agents/<agent>-YYYYMMDD.log` — 每个分身当天的动作流
  - `system/system-YYYYMMDD.log` — 无归属的系统事件兜底

  日志默认保留 14 天，超期自动清理；写入异常被吞掉，绝不影响主执行流程。
- **任务产出**：`workspace/task-N/`。
- **项目产出**：`projects/<folder>/shared/`（公共交付）、`projects/<folder>/members/<role>/`（个人草稿）。

以上目录均已在 `.gitignore` 中忽略，不会误提交。

---

## 🧪 冒烟自测

`backend/` 下提供了几个独立脚本用于快速验证 SDK 连通性：

```bash
cd backend
python3 smoke_sdk.py      # 验证 SDK 基本会话
python3 smoke_plan.py     # 验证项目规划提示词
python3 smoke_models.py   # 列出账号可用模型
```

---

## ❓ 常见问题

- **打开页面提示不支持 WebGPU**：请升级到最新版 Chrome / Edge / Safari。
- **任务一直失败 / 鉴权错误**：确认已安装 Qoder 并登录，`qodercli` 命令可用。
- **Skill 列表为空**：说明本机没有 `qodercli` 或未安装任何 Skill；可在设置页点「刷新」按需重扫。
- **想排查执行细节**：直接翻阅 `backend/logs/` 下对应项目/任务/LLM 的日志文件。
- **端口被占用**：`server.py` 中 `web.run_app(..., port=8787)` 可自行修改端口。

---

## 📄 License

如需开源发布，建议补充 LICENSE 文件（例如 MIT）。
