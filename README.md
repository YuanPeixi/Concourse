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

在 Moodle 日历导出页面选择“所有事件”和“最近及未来 60 天”，获取日历 URL。
此 URL 含私人访问令牌，请勿提交到源码或日志。无需提供 SSO 密码或浏览器 Cookie。

```powershell
uv sync --extra dev
uv run python -m fetchers.moodle --save-url
uv run python -m fetchers.moodle --check
uv run concourse fetch moodle
```

`--save-url` 隐藏输入，复用 Windows DPAPI 凭据存储。非 Windows 的凭据存储会回退到
权限为 600 的明文文件；也可通过本机 `.env` 或环境变量 `MOODLE_CALENDAR_URL` 配置，
该变量优先于加密存储。`--check` 只显示数量和 UTC 时间范围，不写数据库或显示 URL。
`uv run concourse creds forget moodle` 可移除已保存的订阅（不会清除环境变量）。

新增 Fetcher 默认停用。保存订阅后，在“抓取器”页面启用 Moodle，默认每小时同步；
或用 `uv run concourse fetch moodle` 手动同步。无需经过 Agent 或逐条执行 add。

按 ICS UID 新增或更新事件，复用现有手动完成状态保护；不因事件移出滚动时间窗口、
订阅为空或请求失败而删除已有日程。保留标题、说明、课程分类及原始时间精度，
带时区时间转为 UTC，无时区时间及全天日期按 Asia/Shanghai 解释。
全天事件的 DTEND 是排他的，转换为最后一天末尾；不把零点截止改为前一天 23:59。
重定向（例如跳转到 SSO）视为错误，需检查订阅或校园网连接。

ICS 不提供作业提交状态，不会自动标记“已提交”；取消事件跳过但不删除已导入条目。
RRULE 会保留，但当前不展开重复实例；独立 RECURRENCE-ID 实例使用独立来源标识。
日历缺少 URL 时不虚构作业链接。需要提交状态或更完整详情时，再补充浏览器脚本。
