# 运行与构建约定

发行版本唯一来源：`shared/version.py`；Python 构建读取此文件，桌面构建脚本同步 package.json。配置 schema 独立为 1；数据库版本迁移在 M2 实现。

正式数据目录 `%LOCALAPPDATA%\Concourse`，包含 `concourse.db`、`config.yaml`、`credentials/*.dpapi`、`browser/<platform>`、`logs/`、`backups/`。`CONCOURSE_DATA_DIR` 必须为绝对路径；开发与测试必须设置。源码配置与 `.env` 不再隐式加载；旧数据通过后续导入流程迁移。现有个人文件不修改。

无配置时使用 schema 1 空 schedule/fetchers，新安装平台全部停用。数据库已保存的启停状态仍优先。保留开发用 DATABASE_URL 环境覆盖；桌面入口必须强制使用选定数据目录的 SQLite，并清除继承的业务秘密。

当前适配器按目录动态发现、类名约定加载；M2 替换为显式描述表。当前状态包含 running、ok/error 及最近运行时间。原前端 stores 和 DashboardView 使用 localhost:8000；M1 统一通过受限 IPC 请求后端。

发布白名单：Electron 主进程/preload、Vue dist、PyInstaller onedir 后端及锁定 Chromium。禁止根目录通配符；禁止 config.yaml、.env、concourse.db、.browser-data、credentials、日志、调试资源和测试数据。用户数据不属于安装包。正式打包规则及资源检查在 M1 实现。

验证：`.venv/Scripts/python.exe -m pytest -q` 会在导入应用前隔离所有数据目录；`npm.cmd run type-check --prefix frontend`；`npm.cmd run build --prefix frontend`。已有 Python 基线 85 passed，前端两项基线通过（2026-11-10）。


开发 Web 调试：同时为后端 `AGENT_TOKEN` 与 Vite `VITE_API_TOKEN` 指定一次性测试令牌，并设置隔离 `CONCOURSE_DATA_DIR`。该 Vite 变量仅用于开发构建，不得发布；正式桌面通过主进程 IPC 持有令牌，构建脚本拒绝 frontend/.env* 和 VITE_* 覆盖。建议优先 `npm run desktop` 测试桌面链路。

实际选型（2026-11-10）：Electron 45.2.3、electron-builder 26.18.0、Playwright 1.64.1/Chromium 156.0.8078.4 revision 1248、PyInstaller 6.22.3、Python 3.12.13、Node 24.18.0。官方 Electron 安全指南核对上下文隔离、禁用 Node 与沙箱；Playwright library 指南核对随包浏览器路径；Playwright intro 当前仅列 Win11+，Win10 兼容是未解除的发布条件。官方文档入口：electronjs.org/docs/latest/tutorial/security；playwright.dev/python/docs/library；playwright.dev/python/docs/intro；electron.build/nsis.html。
