# Windows 桌面版执行进度

最后更新：2026-11-10（Asia/Shanghai）
当前阶段：M5 发布验收准备；M0–M4 主要实现已落盘
当前工作项：本轮实现与本机验证完成，保留外部发布验收；导入中断恢复已自动化验证
整体状态：可构建桌面候选包；未通过完整发布验收

## 当前事实
- 已实现统一隔离数据目录、版本/配置 schema、全 API 认证、Electron 沙箱与单实例、托盘、后端握手/重启/Job Object 清理、PyInstaller + Chromium + NSIS。
- 已实现 SQLite schema 0→1→2、升级前一致性备份和失败回滚、四平台注册/通用配置/DPAPI、辅助登录和抓取互斥/安全错误/有限重试、持久化提醒与首次引导/副本导入。
- 当前 Python 126 passed；前端类型检查/构建通过；IPC 2 tests；新增模块 Ruff F 通过；git diff --check 无空白错误。
- 实际 Electron 开发/打包窗口、单实例、父进程强杀、冻结后端和随包 Chromium 探针通过。截图 artifacts/packaged-platforms.png。
- 候选安装包 artifacts/Concourse Setup 0.1.0.exe；SHA-256 与大小见 TEST-REPORT.md / artifacts/verification.json。
- 全部运行测试使用临时 CONCOURSE_DATA_DIR；未运行真实平台账号，未修改个人数据库、凭据、浏览器配置或开机启动设置。
- 保留开工前已有的未跟踪 AGENTS.md 与 docs/desktop 文档；在 main 上工作，未创建分支、未修改 Git author、未 commit。

## 阶段状态
| 阶段 | 实现状态 | 验收状态 | 证据 |
|---|---|---|---|
| M0 基线与约定 | 已实现 | 本机验证通过 | 基线 85 tests；隔离路径/配置测试 |
| M1 安装与进程 | 已实现候选链路 | 本机打包/窗口/进程通过；干净安装未验收 | NSIS、冻结后端/浏览器、Job Object、单实例 |
| M2 迁移与配置 | 已实现 | 自动化及配置页面通过；真实账号未验收 | schema 0/1→2、回滚、DPAPI/配置、适配器扩展 |
| M3 登录与恢复 | 已实现主要路径 | 故障/互斥自动化通过；真实验证码未验收 | 超时/重试/取消/安全错误测试 |
| M4 提醒与常驻 | 已实现 | 可控时间测试通过；系统通知/休眠/自启动未验收 | 去重、撤销、补提醒、设置；未发送真实 toast |
| M5 导入与发布 | 已实现候选导入和包 | 副本/占用/冲突自动化通过；中断及干净安装未验收 | 源不变、重复导入拒绝、冻结运行时、校验值 |

## 下一步（下个 agent 从这里执行）
1. 导入发布日志与校验值保护已补齐，模拟中断回滚/重新导入通过；在独立环境做实际进程强杀、断电/磁盘失败验收，不要动真实用户数据。
2. 使用候选包在干净 Win11 x64 安装/卸载/覆盖升级，测试中文空格路径、离线、磁盘不足及运行中升级；不要用本机开发验证代替。
3. 解决 Win10 支持：当前 Playwright 官方文档只列 Win11+，独立测试或选择有明确支持证据的运行时后再发布。
4. 在用户提供的隔离测试账号中验证学者网/iTest 的登录、验证码、辅助登录完成/取消，以及 Moodle 私有订阅和 Codeforces 公共数据；禁止使用个人生产状态。
5. 实测通知展示/点击/系统拒绝、自启动、休眠/唤醒、注销；补充稳定空闲全进程树资源样本。当前指标只是启动后主进程+后端快照。
6. 修改后运行 uv sync --frozen --extra dev --extra desktop、Python 测试、前端 type-check/build、IPC tests，再用 .venv/Scripts/python.exe scripts/build_desktop.py 重建和生成新的哈希。

## 正在进行
本轮已完成交接检查，未遗留本轮应用或构建进程。下一项是独立环境故障注入及外部发布验收；不能仅凭候选包存在勾选整阶段验收。

## 验证记录
| 日期 | 环境 | 命令/操作 | 结果 | 限制 |
|---|---|---|---|---|
| 2026-11-10 | 当前仓库 | git status --short（落盘前） | 无输出 | 只证明工作区当时干净 |
| 2026-11-10 | 当前仓库 | 部分源码静态读取 | 完成 | 没有运行 Python 测试、前端检查或安装包 |

## 决策记录
- 2026-11-10：采用六阶段实施，最低认证和进程边界进入首个安装验证阶段。
- 2026-11-10：数据迁移基础先于新增配置和提醒表，旧项目导入界面最后完成。
- 2026-11-10：导入按“业务数据尚未初始化”判断；已有旧凭据单独处理。
- 2026-11-10：提醒允许投递确认崩溃窗口内偶发重复；不承诺严格一次投递。
- 2026-11-10：不自动提交，不修改 Git author；不创建含 codex 的分支。

## 阻塞与待验证边界
- 无已确认干净 Win10/11 测试机或隔离真实平台账号；相关验收未执行。
- 当前 Playwright 1.64.1 官方系统要求不包含 Win10；支持目标未缩减，但发布阻塞未解除。
- 导入发布日志与模拟中断回滚已测试；真实强杀/断电及磁盘故障未验收。
- 系统通知、自启动、休眠/注销、运行中升级、磁盘故障未实际验证。辅助登录的真实验证码识别与选择器变化仍需实测。
- 本包未签名；npm audit 有 6 个 moderate，来自 electron-builder 构建链 minimatch，未盲目降级或强制升级。
- 本次测试和下载临时目录位于系统 Temp/concourse-*；构建产物在 build/、dist/、artifacts/，均已忽略。安装包约 336 MB，展开约 997 MB。
- 未自动 commit；Git author 未改。

## 工作日志（追加，避免重复整篇计划）
- 2026-11-10：创建 AGENTS.md、START.md、PLAN.md、PROGRESS.md。业务实现未开始。

## 检查点模板
每个有意义的工作单元后更新上方状态，并在日志中追加：
- 时间与工作项：
- 已实现内容及涉及文件：
- 验证命令与实际结果：
- 未验证边界/失败原因：
- 新决策或阻塞：
- 下一条可直接执行的动作：

## 最终交接要求
更新阶段表与 PLAN.md 勾选项；列出未完成验收、数据风险、运行中进程和临时产物位置（如有）。
不要自动 commit。提醒用户提交，并提供建议标题：
feat: 将 Concourse 封装为可扩展的 Windows 桌面助手

- 2026-11-10 开工检查：git status --short 显示 AGENTS.md 与 docs/desktop/ 为已有未跟踪文件，分支 main；保留已有内容。实际代码仍使用源码 config.yaml、源码 .browser-data 和相对数据库路径。尚未运行测试。

- 2026-11-10 M0 基线完成：仅复制源码和测试至临时目录 concourse-baseline-vlnxysox，空配置且 LOCALAPPDATA/APPDATA 隔离；使用 .venv/Scripts/python.exe -m pytest -q，85 passed (3.85s)。npm.cmd run type-check --prefix frontend 与 npm.cmd run build --prefix frontend 均通过。未运行真实平台抓取。开始统一 shared/paths.py、版本来源和配置 schema；测试隔离将落入 tests/conftest.py。

- 2026-11-10 M0 路径单元已实现：新增 shared/paths.py、shared/version.py；配置、凭据、浏览器、数据库统一数据根目录；不再隐式读取源码配置/.env。新增 tests/conftest.py 自动隔离、路径与配置边界测试；RUNTIME.md 记录版本/schema/发布白名单。尚未执行改动后测试；下一步立即运行测试。

- 2026-11-10 M0 验证完成：.venv/Scripts/python.exe -m pytest -q，94 passed (1.72s)。Python 3.12.13、Node 24.18.0、Playwright 1.64.1。M1 开始，涉及 backend/desktop.py、backend/main.py、desktop/、frontend/src/api.ts、打包脚本。普通沙箱 npm view 因 only-if-cached 失败，改用授权网络请求核对版本；安装与真实浏览器验收尚未执行。

- 2026-11-10 M1 第一实现单元落盘：backend/desktop.py 专用 stdin/stdout 启动协议、原子绑定动态回环端口、版本检查；Windows Job Object kill-on-close；全 API 认证；Electron 单实例/沙箱 preload/IPC 白名单/导航限制/托盘/健康检查/有限退避重启；统一 frontend/src/api.ts。已核对官方 Electron 安全文档、Playwright 打包文档，npm 官方注册表返回并选定 Electron 45.2.3、electron-builder 26.18.0。尚未运行本单元验证，下一步运行测试并安装构建依赖。

- 2026-11-10 M1 首轮验证：Python 96 passed (1.77s)，node --test desktop/policy.test.cjs 2 passed，前端 type-check/build 通过。npm install 正在运行（会话 61719）；桌面进程与安装包尚未验证。开始新增子进程握手/鉴权/退出/强杀测试和 PyInstaller 构建入口。

- 2026-11-10 M1 进程验证：pytest -q tests/test_desktop_process.py 3 passed (1.71s)，真实动态端口/认证/首次全停用/EOF 退出/版本永久失败及 Windows Job Object 强杀后子进程清理均通过。npm install 完成，Electron 45.2.3、builder 26.18.0 已安装；审计报告 6 moderate，待分析。构建入口 scripts/build_desktop.py、desktop/backend.spec 已实现，尚未构建。下一步 Electron 冒烟及后端 onedir/Chromium 构建。

- 2026-11-10 构建依赖已就绪：官方 PyPI 查询后锁定 PyInstaller 6.22.3，uv sync --extra dev --extra desktop 成功，uv.lock 已更新。桌面模式强制使用锁定的 Playwright Chromium，避免回落到开发机 Edge/Chrome。Electron 冒烟正在下载首次运行所需二进制。开始执行完整构建，仍未视为安装验收。

- 2026-11-10 官方兼容性核对发现：Playwright 当前 Python 文档只列 Windows 11+/Server 2019+/WSL，未承诺 Win10。保留用户 Win10 目标，但将其列为独立发布阻塞，不能凭开发机结果宣称支持；Electron 文档列 Win10+。npm audit 的 6 moderate 来自构建链 minimatch（非运行依赖），暂不自动跨大版本降级。补齐所有调度任务取消/等待及同一事件循环重复点击互斥。

- 2026-11-10 检查点：任务清理调整后全量 Python 99 passed (3.30s)，主进程 node --check 通过。Electron/Chromium 下载仍在进行（会话 38266/57056）。为不等待网络空转，开始独立的 M2 数据库迁移单元：backend/migrations.py、backend/db.py、迁移测试及恢复说明；M1 整阶段仍未验收。

- 2026-11-10 M2 迁移单元已实现：严格识别旧结构、user_version 顺序迁移、一致性备份、显式事务回滚、拒绝未来版本；新增恢复说明及 5 个迁移场景测试。尚未执行，下一步运行迁移与全量回归。

- 2026-11-10 M2 迁移验证：完整 Python 104 passed (3.61s)。开始平台注册/配置接口与现有页面通用表单，涉及 shared/platforms.py、backend/main.py、scheduler、credentials、frontend 配置组件。发现 Scholat 构造器内置课程 ID，必须移除以保证新安装无个人配置；新增启用前校验。应用 ui-ux-pro-max 技能保留现有样式并完善表单标签/错误反馈。

- 2026-11-10 M2 后端配置已实现：显式平台注册、字段描述、独立类型校验、启用前必要配置校验、DPAPI 原子保存/替换/清除、响应仅返回保存状态、Moodle 连接检查；移除 Scholat 默认课程 ID。新增配置与测试适配器测试，尚未执行；继续通用 Vue 表单并验证。

- 2026-11-10 M2 配置单元验证：Python 107 passed (4.12s)、前端 type-check/build、IPC 2 tests 全部通过。通用 Vue 表单已接入服务端字段描述，明文秘密不回传、不写前端存储；真实四平台连接尚未验证。新增测试适配器无需修改通用调度与页面即可出现。Electron/Chromium 下载尚未完成，窗口视觉验收未执行。

- 2026-11-10 M3 开始：shared/errors.py、backend/scheduler.py、backend/login.py、适配器错误边界及登录控制 UI。目标为统一安全错误分类、平台总超时/有限重试、禁止失效凭据定时重试、辅助登录与抓取共享互斥、取消/完成/超时释放浏览器。真实验证码和账号交互仍需要外部验收。

- 2026-11-10 M3 首单元已实现：存储/API/调度日志统一固定安全错误、登录/验证/网络/解析分类；180 秒平台超时、网络最多两次尝试、定时任务跳过失效凭据；辅助登录显式打开、3 分钟超时、完成校验/取消、共享平台锁和浏览器目录。界面新增登录控制，未执行本单元验证。

- 2026-11-10 M3 首轮回归：Python 107 passed (4.03s)、前端类型检查与构建通过。这只是既有回归；新登录/重试/取消边界测试尚待补齐，不能据此标记 M3 验收完成。下一步新增故障与并发测试，继续观察下载。

- 2026-11-10 M3 专项验证：完整 Python 112 passed (3.94s)。新增网络最多两次、失效登录只尝试一次、超时关闭与释放锁、快速重复点击拒绝、退出取消、浏览器启动前取消释放预留状态测试。真实浏览器完成/取消及验证码尚未验收。复查迁移时补充主键和事项唯一约束验证；扩展测试适配器到真实调度成功链路。

- 2026-11-10 Electron 冒烟已实际通过：node_modules/.bin/electron desktop/smoke.cjs 输出 `DESKTOP_SMOKE_PASS`，覆盖页面加载、受限 preload、认证 IPC、恶意路由拒绝、renderer 无 require、关闭入托盘；预期的 Unsupported operation 被 smoke 用例捕获。浏览器安装目录 build/browsers 已有约 461 MB，主 Chromium 与 headless shell 正在/已下载；PyInstaller/NSIS 尚未完成。

- 2026-11-10 Electron 再次冒烟与实际截图完成（artifacts/desktop-platforms.png）；通用平台页面可显示，未发现水平溢出。截图仍未展开表单，待追加展开态检查。开始 M4 独立提醒单元：backend/reminders.py、schema v2、通知待投递/确认 API、Electron Notification 与唤醒。测试使用可控时间和临时库，不调用真实系统通知。

- 2026-11-10 M4 提醒首单元已实现：schema v2 设置/投递表、比赛考试开始/其他截止目标、全天本地 09:00、完成删除停用过滤、默认 24h/1h、24h 补提醒与同事项合并、投递前复查及确认、首次基线、启动/唤醒双并发补抓取。Electron Notification 接口接入；系统实际投递与休眠行为未验证。下一步可控时间测试及回归。

- 2026-11-10 M4 首轮既有回归 112 passed (4.16s)；新增可控时钟场景覆盖未确认重试、确认去重、完成/删除/停用/目标变更撤销、设置基线和 24h 同事项合并，接下来执行。

- 2026-11-10 M4 专项首次执行：7 failed / 113 passed；原因是当前 SQLModel 要求 ORM 写入带时区 datetime，新增提醒代码传了 naive UTC。已统一 ORM 写入 aware UTC、比较时规范化 naive UTC；立即重跑，不将失败结果标记通过。

- 2026-11-10 提醒修复后 120 passed (4.73s)；SQLModel 提示的 execute 弃用调用改为既有 exec 风格。构建方案调整：辅助登录与无头抓取共用完整 Chromium channel=chromium，安装命令 --no-shell，避免额外 118MB shell 下载和双运行时。停止本轮已核实的构建树 PID 91228，再重新构建；不触碰其他 Python/Node 服务。

- 2026-11-10 M4 设置交互已实现：现有平台页增加统一提醒提前量/关闭选项，托盘增加关闭窗口行为与开机静默启动选项（开发模式禁用真实自启动写入）。首次引导尚未实现，因此尚不默认注册自启动。构建已进入 PyInstaller 收集阶段。

- 2026-11-10 M4 设置回归：前端构建通过，Python 120 passed (4.55s)。PyInstaller onedir 已成功；electron-builder 已完成 win-unpacked，正在 NSIS 压缩，未配置代码签名。构建过程中源码有后续修改，本包仅构建链路探针，最终需要重建。开始 M5 首次选择/副本导入单元，不读取个人源目录；测试仅使用生成的旧库和假凭据。

- 2026-11-10 M5 首次选择/导入已实现：正式版启动前选择新建或旧目录，设置自启动/关闭行为；只读源库复制到临时区、迁移/完整性检查、保留启停、凭据冲突拒绝/同用户解密检查、浏览器状态尽力复制、数据库最后发布、重复导入拒绝。源服务静止目前仅保守检查 sidecar，尚未实现跨进程锁证明；发布前必须补齐。新增两项临时旧库测试，尚未执行。

- 2026-11-10 M5 首轮测试 2 failed / 120 passed：sqlite3 connection 上下文只提交/回滚不关闭句柄，导致 Windows 备份目录移动与清理失败。改为 contextlib.closing 显式关闭；导入复制改为 Windows CreateFileW share=0 独占只读句柄，源仍被占用则拒绝；不再仅凭 sidecar 判断。立即重跑。

- 2026-11-10 M5 修复验证：122 passed (4.78s)，独占只读复制与目录句柄释放通过。首次 NSIS 构建成功，探针安装包 336349249 bytes；非最终版本，必须重建。补齐通知点击通过独立事项接口打开详情/已删除提示、休眠停止后端与唤醒恢复；系统休眠仍未实测。

- 2026-11-10 打包最终验证开始：新增 scripts/verify_bundle.py 扫描禁带数据、运行冻结后端/真实 Chromium 本地页面探针、生成 SHA-256 与体积/启动耗时。测试显式覆盖数据根目录，正式版在覆盖模式也不写用户开机启动注册项。准备重建全部最新源码并验证。

- 2026-11-10 最终静态检查：新增后端/构建模块 Ruff F 检查通过，git diff --check 无空白错误，IPC 2 tests 通过。普通只读沙箱的 Ruff 缓存/Node spawn 被权限限制，使用 --no-cache 和正常授权重跑成功，不属于业务失败。新增 v1→v2 备份升级及源数据库占用拒绝测试。

- 2026-11-10 最新完整构建成功：冻结后端就绪 11.086s，随包 Chromium 本地真实页面探针 2.596s；安装包 336354963 bytes，展开 997358737 bytes；SHA-256 7977056e482f342bd54202f4536dffd30e604752916adc3e981b6c93fc5be802。资源禁带扫描通过。Python 最终当前 124 passed (5.21s)。开发 Win11 结果不代表干净安装/真实账号/系统通知/休眠验收；开始验证实际 win-unpacked 窗口与强杀退出清理。

- 2026-11-10 打包窗口首轮测试工具失败：CDP 调试端口先于业务窗口就绪，脚本直接取 pages[0] 导致 IndexError；已结束本次测试父进程。修正测试等待窗口事件后重跑，不据此判定产品失败或验收通过。

- 2026-11-10 交接检查：实际 win-unpacked 窗口 2.128s 暖启动测试、四平台表单、认证 IPC、单实例第二实例退出、强杀 Electron 后后端退出均通过。主进程工作集 128012288 bytes、后端 94904320 bytes，仅启动后快照，不是全树空闲指标。最终 Python 124 passed (5.21s)；候选包与报告已落盘。未执行真实数据操作，未 commit。

- 2026-11-10 继续修复交接审查项：开始导入多文件发布日志与中断恢复。涉及 backend/importer.py、backend/desktop.py、tests/test_importer.py；在每次发布前持久化带校验值日志，启动恢复只处理日志内且内容未被更改的本轮副本，保留用户既有文件。完成后重建候选包和校验值。

- 2026-11-10 导入恢复单元验证：126 passed (5.86s)。每次发布前 fsync 日志记录副本校验值；模拟提交标记前中断可回滚并重试，用户改动副本时拒绝自动覆盖。启动自动回滚后要求重新选择导入，防止静默初始化新库。实际断电/磁盘故障仍未验收。

- 2026-11-10 最终交接：导入恢复修复后 Python 126 passed (5.84s)；完整重新构建及 verify_bundle.py 通过。最终安装包 336358603 bytes，展开 997361589 bytes，冻结后端 11.171s，Chromium 探针 2.081s；SHA-256 01b93eb7dfb207708451d40df6a797cb67a4a3c1cb62b27c792ba169b49c202f。最新指标覆盖此前候选包哈希。README/USAGE/RECOVERY/CHANGELOG/TEST-REPORT 均同步；未自动 commit，未改 Git author。


- 2026-11-10 提交准备（进行中）：用户反馈“验证成功”，并明确授权创建 fork 和提交，覆盖此前不自动提交的限制。反馈未列明验证环境和场景，因此保留原有未验证边界。涉及本轮桌面实现、测试和 docs/desktop 文档；保留原有未跟踪 AGENTS.md，不纳入提交。已核对全局配置来源及有效作者 ParaNoite，未修改身份。git diff --check 通过。聊天 fork 两次返回 execution-config-loading，尚未成功；继续完成不依赖 fork 的提交工作。

- 2026-11-10 提交检查与交接：已暂存 64 个实现/测试/文档文件；git diff --cached --check 通过，只有 LF/CRLF 提示。未重新执行运行时测试，沿用上轮 126 个 Python 测试及前端/IPC/打包验证记录和用户复核反馈。聊天 fork 共尝试三次，均被应用服务 execution-config-loading 阻塞，需应用完成配置加载后重试。下一动作：按用户授权使用现有作者在 main 提交，提交结果以 git log 为准；不推送，AGENTS.md 保留未跟踪。外部验收范围仍见 TEST-REPORT.md。
