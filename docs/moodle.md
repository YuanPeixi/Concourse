# Moodle 日历订阅

Concourse 通过 Moodle 的私人 ICS 日历订阅获取日程。你只需在浏览器中手动完成
学校 SSO 登录以获取订阅地址；同步程序无需 SSO 密码或浏览器 Cookie。

## 1. 在 Moodle 中获取订阅地址

1. 在浏览器中登录学校 Moodle，打开“日历 / Calendar”。
2. 找到“导出日历 / Export calendar”（入口位置可能因学校主题不同而变化）。
3. 事件类型选择“所有事件 / All events”。
4. 时间范围选择“最近及未来 60 天 / Recent and next 60 days”。
5. 点击“获取日历 URL / Get calendar URL”，复制生成的完整地址。

应获取订阅 URL，而不是只下载一次 `.ics` 文件。完整地址包含 `userid` 和
`authtoken`，所选范围对应的末尾参数为：

```text
preset_what=all&preset_time=recentupcoming
```

注意 `recentupcoming` 末尾的 `g`。使用“本月”会得到 `monthnow`，其时间窗口较小。
订阅只覆盖所选时间窗口，并不表示账户中的全部历史日程；反复读取订阅即可持续刷新。

日历 URL 本身是私人访问凭据，请不要把它提交进仓库、截图、日志或命令历史。
此文档不需要也不应包含你的真实订阅地址。

## 2. 在 Concourse 中保存订阅

以下命令均在项目根目录执行：

```shell
uv sync --extra dev
uv run python -m fetchers.moodle --save-url
```

看到下面的提示后，再粘贴完整地址并按回车：

```text
Moodle calendar URL (hidden):
```

输入时不显示字符，这是正常现象。粘贴时不用加引号，也不要把地址直接跟在
`--save-url` 后面；这个参数不接受命令行位置参数。再次执行同一命令可覆盖旧订阅。

Windows 下复用项目现有的 DPAPI 存储，凭据绑定当前 Windows 用户。
定时服务必须以保存订阅的同一 Windows 用户身份运行；换账户或换机后需要重新保存。
非 Windows 平台的现有凭据实现回退到权限为 600 的明文文件。

服务器部署也可在本机 `.env` 或进程环境中配置 `MOODLE_CALENDAR_URL`。
此配置优先于加密存储；更新已保存订阅却仍使用旧地址时，检查这两个位置。
不要将完整地址放入 `config.yaml`。

## 3. 检查订阅并首次同步

先进行只读检查：

```shell
uv run python -m fetchers.moodle --check
```

成功时输出 JSON，包含 `platform`、`event_count`、`earliest_end_utc` 和
`latest_end_utc`。事件数取决于实际订阅，允许为零；没有事件时两个时间字段为 `null`。
时间是 UTC，例如 `16:00:00+00:00` 对应次日北京时间 `00:00`。

`--check` 不写入数据库。确认正常后，执行首次入库：

```shell
uv run concourse fetch moodle
```

在 Concourse 页面查看 Moodle 平台的日程。重复同步会按 ICS UID 更新原有条目，
不会重复创建。课程名称末尾的 `-` 加 32 位十六进制标识会自动去除，原始分类保留在
`raw_payload.categories`；普通课程编号如 `课程-2026` 不受影响。

## 4. 启用定时同步

添加模块或修改配置后，重启正在运行的后端。在“抓取器”页面启用 Moodle。
当前默认配置每小时同步一次，也可在该页面修改 cron。保存地址或手动运行一次
`fetch` 本身不会自动启用定时任务。

升级解析逻辑后，也应重启后端，使后续定时任务加载新代码；需要立即更新已有日程时，
再次执行 `uv run concourse fetch moodle`。

## 常见问题

| 现象 | 处理方法 |
|---|---|
| `unrecognized arguments: https://...` | 单独执行 `--save-url`，等隐藏输入提示出现后再粘贴地址。 |
| `HTTP 303` | 模块拒绝跳转。先检查是否复制完整地址，以及 `recentupcoming` 拼写；本项目对接时，少了末尾 `g` 的地址会跳回日历导出页。仍失败则在浏览器重新获取 URL，并确认校园网访问条件。303 本身不证明需要交出 SSO 凭据。 |
| 其他 HTTP 或网络错误 | 在本机检查订阅是否仍有效、网络是否可达，再重新保存；不要公开完整地址排错。 |
| `Invalid Moodle ICS calendar` | 服务返回的不是可解析日历，或事件日期无效；重新获取“日历 URL”，确认复制的不是登录页或普通日历页面地址。 |
| `--check` 成功，但页面没有日程 | 检查是否执行了 `uv run concourse fetch moodle`；只读检查不入库。订阅当前窗口也可能确实没有事件。 |
| 没有自动刷新 | 确认后端已重启且 Moodle 抓取器已启用，并查看抓取器页面的最近运行结果。 |
| 更换订阅后仍读旧地址 | 检查 `.env` 和进程环境中的 `MOODLE_CALENDAR_URL`，它们优先于加密存储。修改环境配置后重启后端。 |

删除本机已保存的订阅：

```shell
uv run concourse creds forget moodle
```

此命令不会清除 `.env` 或进程环境变量，也不会在 Moodle 服务端撤销该 URL。

## 数据范围与限制

- 导入标题、说明、课程分类、地点以及订阅提供的链接；缺少链接时不虚构作业地址。
- 保留时间精度；带时区时间转换为 UTC，无时区时间及全天日期默认按 Asia/Shanghai 解释。
  全天事件的 DTEND 是排他的，会转换为最后一天末尾；精确的零点截止保持原时间。
- 日历不提供作业提交状态，不会自动标记“已提交”；手动“已完成”状态受现有入库逻辑保护。
- 不因事件移出订阅窗口、空订阅或请求失败而删除已有日程。取消事件跳过，但不删除之前导入的条目。
- 保留 RRULE，当前不展开重复实例；独立 RECURRENCE-ID 实例使用独立来源标识。
- 若需要提交状态或更完整的活动详情，可另行补充浏览器脚本。

Moodle 官方日历导出说明：<https://docs.moodle.org/en/Calendar_export>。
