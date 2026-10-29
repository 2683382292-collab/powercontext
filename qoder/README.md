# 个性化复习助手（基于 PowerContext HTTP API）

一个开箱即用的个性化复习助手：记录学习者的**薄弱知识点、错题、复习进度**并持久化到
PowerContext；**新开对话自动读取历史记忆、针对性出题复盘**；附带一个简单的**网页前端**。

- 全部代码位于本目录 `qoder/`，**不修改仓库任何原有源码**。
- 仅通过 **PowerContext HTTP API** 读写记忆（用 `httpx` 直连，不 import 仓库 Python 源码）。
- 默认工作在 **Basic memory（无模型）** 模式：显式写记忆 + 全文检索回忆，无需任何大模型 API Key。

---

## 1. 整体架构

```
浏览器 (static/index.html + app.js + style.css)
        │  fetch /api/*  (JSON, 同源)
        ▼
复习助手后端  qoder/app/main.py  (FastAPI + uvicorn, 默认 127.0.0.1:8080)
   ├─ review_service.py     业务编排：开新会话→读历史→靶向出题；判分→写回记忆
   ├─ memory_repository.py  领域 ↔ PowerContext 记忆映射（kind 分类、序列化/反序列化）
   ├─ question_bank.py      内置题库 + 诊断/靶向选题（确定性，无需模型）
   ├─ powercontext_client.py httpx 异步客户端，直连 PowerContext HTTP API
   ├─ schemas.py            领域模型与 API 数据契约（pydantic）
   └─ config.py             读取环境变量 / qoder/.env
        │  HTTP (httpx)
        ▼
PowerContext Server  (默认 127.0.0.1:8000, SQLite)
   Scope = 每个学习者一个（idempotency_key 幂等创建）
   Memory kind: weak_point（薄弱知识点）/ wrong_question（错题）/ review_progress（复习进度）
```

### 数据流（对应功能需求）

1. **记录**（需求 1）：每次作答调用 `POST /api/answer` →
   - 答错：向 PowerContext 追加一条 `wrong_question`（错题事件）；
   - 无论对错：追加/刷新该知识点的 `weak_point` 快照（`attempts`、`misses` 计数）；
   - 追加/刷新本轮会话的 `review_progress`（`total`、`correct`、`weak_kps`）。
2. **新对话自动读历史 + 针对性出题**（需求 2）：`POST /api/session/start` →
   - `POST /v1/memory/entries/list` 枚举该 Scope 的活跃记忆并按 `kind` 解析；
   - 依据薄弱知识点（按 `misses` 排序）与最近错题**靶向选题**；无历史则出**诊断题**；
   - 额外调用 `POST /v1/context/prepare` 生成一段"助手回忆"叙述展示给学习者。
3. **网页前端**（需求 3）：单页 HTML/CSS/JS，支持开始复习、答题判分与解析、
   查看薄弱知识点 / 错题本 / 复习进度总览。

### 后端无状态设计

后端不保存会话状态：题目随 `session/start` 下发，答题时前端回传题目上下文，
所有持久状态都在 PowerContext。因此可随时重启后端、或在任意"新对话"中恢复学习者历史。

### 记忆数据模型（存入 Atomic Memory 的 `text` 字段，JSON 序列化）

| kind | 触发时机 | 主要字段 |
| --- | --- | --- |
| `wrong_question` | 答错时追加一条 | `question_id, subject, kp, prompt, options, correct_index, given_index, ts` |
| `weak_point` | 每次作答追加新快照 | `subject, kp, attempts, misses, last_ts, sample_question_id` |
| `review_progress` | 每次作答追加新快照 | `session_id, subject, total, correct, ts, weak_kps` |

> PowerContext 记忆是**追加式**的：读取时对 `weak_point` 取每个 `(subject,kp)` 的最新快照，
> 对 `review_progress` 取每个 `session_id` 的最新快照，`wrong_question` 构成错题本证据流。

### 用到的 PowerContext HTTP 接口

| 方法 & 路径 | 用途 |
| --- | --- |
| `GET /health/live` | 存活探针（前端连接状态） |
| `POST /v1/scopes` | 幂等创建学习者 Scope（`idempotency_key = <前缀>-<learner_id>`） |
| `POST /v1/memory/remember` | 显式写入一条记忆（无需模型） |
| `POST /v1/memory/entries/list` | 分页枚举记忆条目（读历史的主路径） |
| `POST /v1/memory/search` | 关键词检索活跃记忆 |
| `POST /v1/context/prepare` | 生成有界的叙述式回忆 |
| `POST /v1/sources/content` | 可选：把原始作答作为 Source 证据留存 |

---

## 2. 目录结构

```
qoder/
├── README.md                 # 本文件
├── requirements.txt          # 依赖清单
├── .env.example              # 环境变量样例（复制为 .env 按需修改）
├── pytest.ini                # 测试配置（rootdir=qoder，与仓库根互不干扰）
├── run.py                    # 复习助手启动入口
├── app/
│   ├── __init__.py
│   ├── config.py             # 配置加载（env / .env）
│   ├── powercontext_client.py# PowerContext HTTP 客户端（httpx）
│   ├── schemas.py            # 领域模型 + API 契约（pydantic）
│   ├── question_bank.py      # 内置题库 + 选题逻辑
│   ├── memory_repository.py  # 领域 ↔ 记忆映射、历史解析
│   ├── review_service.py     # 业务编排（开新会话/判分/总览）
│   └── main.py               # FastAPI 应用与路由、静态资源托管
├── static/
│   ├── index.html            # 单页前端
│   ├── app.js                # 前端逻辑（原生 JS，无构建）
│   └── style.css             # 样式
├── scripts/
│   └── smoke_test.py         # 端到端验证样例（对真实服务发请求）
└── tests/
    ├── conftest.py           # 内存版假 PowerContext（httpx.MockTransport）
    ├── test_review_service.py# 业务层测试
    └── test_api_flow.py      # HTTP 层端到端测试（FastAPI TestClient）
```

---

## 3. 依赖清单

见 [`requirements.txt`](requirements.txt)：

- 运行：`fastapi`、`uvicorn`、`httpx`、`pydantic`
- 测试：`pytest`（测试用 `asyncio.run` 驱动协程，无需额外插件）

> 提示：本仓库的 `uv` 开发环境（`dev` 组包含 `powercontext[cli,server]`）已自带
> `fastapi / uvicorn / httpx / pydantic`。若用 `uv run` 启动，通常**无需**再装 `requirements.txt`。

---

## 4. 启动步骤

以下命令面向 **Windows PowerShell**（用 `;` 分隔，不用 `&&`）。需要 Python 3.11+。

### 步骤 A：启动 PowerContext Server（终端 1，保持开启）

在**仓库根目录** `c:\Users\也许\powercontext` 下，二选一：

**方式 1 — 用仓库源码（推荐，当前就在仓库里）**
```powershell
# 首次准备环境（安装 CLI + Server 依赖）
uv sync --extra cli --extra server    # 等价于 make install
# 启动 Server（默认 127.0.0.1:8000，SQLite，显式记忆无需模型）
uv run powercontext server run
```

**方式 2 — 安装已发布包**
```powershell
uv tool install --force "powercontext[cli,server]==1.4.1"
powercontext server run
```

默认行为（无需任何环境变量）：绑定 `127.0.0.1:8000`、创建默认 Scope、在用户数据目录建 SQLite、
**支持显式 Memory 读写而无需推理服务**。`Ctrl-C` 干净退出，重启复用同一数据库。

验证 Server 就绪（新开一个终端）：
```powershell
curl.exe --fail --silent http://127.0.0.1:8000/health/live
# 或：uv run powercontext doctor ; uv run powercontext ready
```

> Windows 支持为 experimental。若 `uv` 未安装，参见 https://docs.astral.sh/uv/ 。

### 步骤 B：启动复习助手（终端 2）

```powershell
cd qoder
# 方式 1：复用仓库 uv 环境（已含 fastapi/uvicorn/httpx/pydantic）
uv run python run.py

# 方式 2：独立安装依赖后运行（任意 Python 3.11+ 环境）
python -m pip install -r requirements.txt
python run.py
```

启动后终端会打印地址，浏览器打开：**http://127.0.0.1:8080/**
（交互式 API 文档：http://127.0.0.1:8080/docs ）

### 步骤 C（可选）：自定义配置

```powershell
cd qoder
Copy-Item .env.example .env      # 然后按需编辑 .env
```

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `POWERCONTEXT_URL` | `http://127.0.0.1:8000` | PowerContext Server 地址 |
| `POWERCONTEXT_TOKEN` | 空 | Server 开启鉴权时填 Bearer 令牌 |
| `REVIEW_SCOPE_PREFIX` | `qoder-review` | 学习者 Scope 的 `idempotency_key` 前缀 |
| `REVIEW_HOST` / `REVIEW_PORT` | `127.0.0.1` / `8080` | 复习助手监听地址 |
| `REVIEW_SESSION_SIZE` | `5` | 每轮默认出题数 |

---

## 5. 验证测试样例

### 5.1 离线自动化测试（**无需**真实 Server，使用内存假 PowerContext）

覆盖"写记忆 → 读历史 → 靶向出题"的完整闭环，含业务层与 HTTP 层：

```powershell
cd qoder
python -m pytest -q          # 或：uv run pytest -q
```

预期：`9 passed`。（本机已验证通过。）

### 5.2 端到端冒烟测试（需要 A、B 两个服务都在运行）

```powershell
cd qoder
python scripts/smoke_test.py
# 如地址不同：python scripts/smoke_test.py --assistant http://127.0.0.1:8080 --powercontext http://127.0.0.1:8000
```

脚本逐步打印 `[PASS]/[FAIL]`：PowerContext 存活 → 助手健康 → 首次诊断出题 →
答错写入记忆 → 总览读回 → **新会话自动读历史并靶向薄弱点**。全部通过退出码为 0。

### 5.3 网页手动验证（对应三大功能需求）

1. 打开 http://127.0.0.1:8080/ ，顶部状态应显示"PowerContext 已连接"。
2. 填写**学习者 ID**（如 `xiaoming`），选择学科（如 Python），点【开始新一轮复习】。
   - 首次为**诊断题**（题目标签"诊断题"），"助手已读取的历史记忆"提示尚无记录。
3. 故意**答错**几题并提交：
   - 立即看到正确答案高亮、解析、以及"已记录薄弱点「xxx」，累计错 N 次"。
4. 点【查看学习总览】：薄弱知识点、错题本、复习进度均已从 PowerContext 读回。
5. **关键验证（需求 2）**：再次点【开始新一轮复习】（相当于"新开对话"）：
   - 顶部出现"已从 PowerContext 载入你在「Python」的历史记忆……本轮将重点复盘这些薄弱知识点：…"；
   - 题目标签变为"错题重练 / 靶向薄弱点"，即根据历史记忆针对性出题；
   - 展开"PowerContext 叙述式回忆"可见 `context/prepare` 返回的内容。

### 5.4 直接对 PowerContext HTTP API 的验证样例

不经过复习助手，直接验证底层记忆读写（PowerShell）。先取一个 `scope_id`：

```powershell
$base = "http://127.0.0.1:8000"
# 1) 幂等创建 Scope
$scope = (Invoke-RestMethod -Method Post -Uri "$base/v1/scopes" -ContentType "application/json" `
  -Body '{"title":"review-demo","summary":"demo scope","idempotency_key":"qoder-review-demo"}').scope_id
Write-Output "scope_id=$scope"

# 2) 显式写入一条记忆（薄弱知识点）
Invoke-RestMethod -Method Post -Uri "$base/v1/memory/remember" -ContentType "application/json" `
  -Body (@{ scope_id=$scope; kind="weak_point"; text="列表推导式：容易忽略 if 过滤条件" } | ConvertTo-Json)

# 3) 搜索记忆
Invoke-RestMethod -Method Post -Uri "$base/v1/memory/search" -ContentType "application/json" `
  -Body (@{ scope_id=$scope; query="列表推导式"; limit=5 } | ConvertTo-Json)

# 4) 生成有界回忆
Invoke-RestMethod -Method Post -Uri "$base/v1/context/prepare" -ContentType "application/json" `
  -Body (@{ scope_id=$scope; query="列表推导式 薄弱点"; max_bytes=4000 } | ConvertTo-Json)
```

等价 `curl`（Bash）样例见仓库文档 `docs/en/docs/develop/api-quickstart.md`。

---

## 6. 设计与约束说明

- **只用 HTTP API**：`app/powercontext_client.py` 用 `httpx` 调用 `/v1/*`，与仓库源码零耦合，
  也可指向远程 PowerContext Server。
- **不改仓库源码**：新增文件全部在 `qoder/`；`qoder/pytest.ini` 使测试的 rootdir 落在 `qoder`，
  与仓库根 `pyproject.toml` 的 pytest 配置互不干扰。
- **无模型可运行**：默认 Basic memory 模式即可完整跑通记录/读取/出题；题库与选题为确定性逻辑。
- **可扩展**：
  - 题库：在 `app/question_bank.py` 的 `_BANK` 中按同结构追加题目/学科即可被自动纳入选题。
  - 接入大模型：把 `review_service` 的选题/解析替换为模型调用，并用 `context/prepare` 的回忆作为上下文，
    即可从"确定性出题"升级为"生成式复盘"，而记忆读写路径不变。

---

## 7. 常见问题

- **前端显示"PowerContext 未就绪"**：确认终端 1 的 `powercontext server run` 仍在运行，且
  `qoder/.env` 的 `POWERCONTEXT_URL` 与实际地址端口一致。
- **端口占用**：修改 `qoder/.env` 的 `REVIEW_PORT`（助手）或用 `--port` 启动 PowerContext。
- **`ModuleNotFoundError: app`**：请在 `qoder` 目录下运行 `python run.py` / `python -m pytest`，
  以保证 `qoder` 在导入路径上。
- **换机器/换学习者**：`learner_id` 决定 Scope（数据隔离）；相同 `learner_id` 在新对话中会自动恢复历史。
