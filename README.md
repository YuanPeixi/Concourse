# Concourse

多平台截止事项聚合服务。各平台独立 Fetcher 抓取作业/考试/比赛截止时间，FastAPI 后端汇聚存储，Vue 3 前端提供仪表板和时间轴两种视图，CLI 支持纯键盘操作和 Agent 自然语言添加。

---

## 快速开始

```bash
# 1. 安装 Python 依赖
uv sync --extra dev

# 2. 复制环境变量模板
cp .env.example .env   # 填入 AGENT_TOKEN 和 ANTHROPIC_API_KEY

# 3. 保存 scholat 凭据（首次）
uv run python -m fetchers.scholat --save-credentials

# 4. 启动后端（含调度器）
uv run uvicorn backend.main:app --reload

# 5. 启动前端（新终端）
cd frontend
npm install
npm run dev   # → http://localhost:5173
```

---

## 项目结构

```
shared/
  models.py        ScheduleEventCreate / Event / StagedEvent / content hash
  credentials.py   纯 ctypes DPAPI，load/save/forget(platform)
  config.py        pydantic-settings 加载 .env + config.yaml

fetchers/
  _browser.py      共享 Playwright 启动器（msedge→chrome→chromium→bundled）
  base.py          Fetcher Protocol（runtime_checkable）
  codeforces.py    纯 HTTP 参考实现（无需登录）
  scholat.py       Playwright 参考实现（parse 纯函数可离线测试）
  moodle.py        私人 ICS 日历订阅（无需 SSO 凭据）

backend/
  db.py            SQLModel async engine + init_db()
  repository.py    upsert_event、list_events、staged CRUD
  scheduler.py     APScheduler cron，按 config.yaml 自动注册 fetcher
  main.py          FastAPI 应用，lifespan 启动调度器
  services/
    agent.py       Anthropic tool-use 循环，action 白名单 dispatch

concourse/
  cli.py           argparse CLI：list / add / staged / accept / dismiss / fetch / creds

frontend/src/
  stores/events.ts  Pinia store：fetch/poll /events + /staged，urgency 分类
  router/           /dashboard 和 /timeline 路由
  views/
    DashboardView.vue  Fluent 磨砂卡片，平台 tab，暂存区，分组事项列表
    TimelineView.vue   时间轴（now−1d 到 now+30d），逐事项 lane，实时倒计时
  components/
    TitleBar.vue      顶部导航
    EventRow.vue      事项行（带紧急度左边框）
    StagedPanel.vue   暂存区 accept/dismiss 面板

tests/
  test_cli_time.py        parse_shorthand 单元测试
  test_scholat_parser.py  parse_homework_list/detail 离线测试

frontend-samples/    6 个独立 HTML 风格样例（浏览器直接打开对比）
config.yaml          调度周期、fetcher 参数、agent 白名单（非敏感）
.env.example         环境变量模板
```

---

## 架构要点

### 数据模型

`ScheduleEventCreate` 是 fetcher 输出契约，`Event` 是 SQLite 持久化形态（naive UTC datetime），`StagedEvent` 是 Agent/CLI 写操作的暂存区。唯一键 `(platform, source_id)`，upsert 通过 `content_hash`（sha256）检测变化。

**各平台不跨平台去重** — 同一物理会议在 Outlook 和 Google 各是一条独立记录，聚合视图并排显示。

### Fetcher 契约

```python
class MyFetcher:
    name = "myplatform"
    async def fetch(self, since=None) -> list[ScheduleEventCreate]: ...
```

新增平台 = 一个文件 + `config.yaml` 一条配置。

- **HTTP fetcher**：参考 `fetchers/codeforces.py`，纯 `httpx`，无浏览器
- **Playwright fetcher**：参考 `fetchers/scholat.py`；`parse_*` 是接收 HTML 字符串的纯函数，可离线单测；`_browser.launch_context` 共享，msedge → chrome → chromium → bundled 依次回退

### Agent 安全

`POST /agent` 需 `Authorization: Bearer <AGENT_TOKEN>`。  
白名单 action 在 `config.yaml agent.allowed_actions` 声明。  
写操作（`draft_add_event`）只产 `StagedEvent` 提案，需人工 accept 才写入 `events`。

### CLI 时间简写

```
concourse add --title "提交报告" --end 1028+   # 当天 23:59 CST
concourse add --title "开始复习" --end 1028-   # 当天 00:00 CST
concourse add "明天下午三点组会 1小时"          # 自然语言，AI 解析后进暂存区
```

不带符号的 `mmdd` 格式被明确拒绝，避免歧义。

---

## 环境变量

| 变量 | 说明 |
|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./concourse.db`（默认）|
| `AGENT_TOKEN` | Agent 端点鉴权密钥 |
| `ANTHROPIC_API_KEY` | Claude API 密钥 |
| `MOODLE_CALENDAR_URL` | 可选：私人 Moodle 日历 URL；优先于加密存储，Windows 推荐使用 `--save-url` |

---

## 常用命令

```bash
# 后端
uv run uvicorn backend.main:app --reload     # 开发服务器
uv run pytest                                # 全量测试
uv run pytest tests/test_scholat_parser.py   # 单文件测试

# 手动触发 fetcher
uv run python -m fetchers.codeforces
uv run python -m fetchers.scholat --course-ids 3017 3042

# 凭据管理
concourse creds set scholat
concourse creds forget scholat

# 事项操作
concourse list
concourse list --platform scholat --status open
concourse staged
concourse accept 3
concourse dismiss 3

# 前端
cd frontend && npm run dev
cd frontend && npm run build
cd frontend && npm run type-check
```

---

## 部署（Windows Server + Tailscale）

服务以保存了 DPAPI 凭据的同一 Windows 用户身份运行。DPAPI 加密绑定用户账户，换机或换账户需重新执行 `--save-credentials`。Tailscale 内网访问，`AGENT_TOKEN` 使用长随机串即可，无需复杂 OAuth。


## Moodle 日历订阅（无需 SSO 凭据）

Moodle 通过私人 ICS 日历订阅接入，无需提供 SSO 密码或浏览器 Cookie。
请先在 Moodle 日历导出页面选择“所有事件”和“最近及未来 60 天”，获取完整日历 URL。

完整步骤见 [Moodle 接入指南](docs/moodle.md)，包括隐藏输入保存订阅、只读检查、首次入库、
定时同步，以及 `unrecognized arguments`、HTTP 303 等常见问题。


## Windows 桌面候选版

桌面入口与验收状态见 `docs/desktop/PROGRESS.md`；使用说明见 `docs/desktop/USAGE.md`，测试报告见 `docs/desktop/TEST-REPORT.md`。

构建：`uv sync --frozen --extra dev --extra desktop`、`npm ci`、`npm ci --prefix frontend`，然后 `.venv/Scripts/python.exe scripts/build_desktop.py`。输出在 `artifacts/`，包含 NSIS 安装包、SHA256SUMS.txt 与 verification.json。当前候选包不代表已经通过干净 Windows 安装与真实账号验收。

桌面开发运行：先构建前端，再 `npm run desktop`，默认使用隔离 `.desktop-data`。可用绝对路径环境变量 `CONCOURSE_DATA_DIR` 覆盖。配置、凭据、浏览器状态与数据库均在该数据目录；不再隐式加载源码根目录的 config.yaml 或 .env，旧数据应从首次引导导入。

单独 Web 调试需要显式配置后端 `AGENT_TOKEN` 与开发前端 `VITE_API_TOKEN`（相同的一次性测试值），并设置隔离数据目录。正式桌面只由主进程持有启动令牌，不将令牌交给页面或持久化。不要带 VITE_* 覆盖变量打发布包。
