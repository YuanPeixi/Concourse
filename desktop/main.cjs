const { app, BrowserWindow, ipcMain, Menu, Tray, nativeImage, shell, dialog, session, Notification, powerMonitor } = require('electron')
const { spawn } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const { createInterface } = require('node:readline')
const path = require('node:path')
const fs = require('node:fs')
const { pathToFileURL } = require('node:url')
const { validateRequest, externalUrl } = require('./policy.cjs')
const version = require('../package.json').version
const root = path.resolve(__dirname, '..')
const dataDir = process.env.CONCOURSE_DATA_DIR || (app.isPackaged
  ? path.join(process.env.LOCALAPPDATA, 'Concourse') : path.join(root, '.desktop-data'))
if (!path.isAbsolute(dataDir)) throw new Error('Data directory must be absolute')
app.setPath('userData', path.join(dataDir, 'electron'))
const preferencesPath = path.join(dataDir, 'desktop-settings.json')
let preferences = { closeToTray: true, autoStart: false, initialized: false }
try { preferences = { ...preferences, ...JSON.parse(fs.readFileSync(preferencesPath, 'utf8')) } } catch {}
function savePreferences() {
  fs.mkdirSync(dataDir, { recursive: true })
  fs.writeFileSync(preferencesPath + '.tmp', JSON.stringify(preferences))
  fs.renameSync(preferencesPath + '.tmp', preferencesPath)
  if (app.isPackaged && !process.env.CONCOURSE_DATA_DIR) app.setLoginItemSettings({ openAtLogin: preferences.autoStart, args: ['--hidden'] })
}
let initialization = {}
let suspended = false
let win, tray, child, connection, quitting = false, healthTimer, notificationTimer, failures = 0, restarting = false
const page = pathToFileURL(path.join(root, 'frontend', 'dist', 'index.html')).href

function trusted(event) {
  return win && event.sender === win.webContents && event.senderFrame === win.webContents.mainFrame &&
    event.senderFrame.url.split('#')[0] === page
}

function startBackend() {
  return new Promise((resolve, reject) => {
    const token = randomBytes(32).toString('hex')
    const executable = app.isPackaged
      ? path.join(process.resourcesPath, 'backend', 'concourse-backend.exe')
      : path.join(root, '.venv', 'Scripts', 'python.exe')
    const args = app.isPackaged ? [] : ['-m', 'backend.desktop']
    const env = { ...process.env, PYTHONIOENCODING: 'utf-8' }
    delete env.CONCOURSE_DESKTOP_TOKEN
    if (app.isPackaged) env.PLAYWRIGHT_BROWSERS_PATH = path.join(process.resourcesPath, 'browsers')
    const proc = spawn(executable, args, { cwd: app.isPackaged ? process.resourcesPath : root,
      env, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] })
    child = proc
    let ready = false, settled = false
    const timer = setTimeout(() => fail(new Error('Backend startup timed out')), 30000)
    function fail(error) {
      if (settled) return
      settled = true
      clearTimeout(timer)
      proc.kill()
      reject(error)
    }
    proc.on('error', () => fail(new Error('Cannot start backend')))
    proc.stdin.on('error', () => {})
    // Do not forward raw backend logs, which may contain platform data.
    proc.stderr.resume()
    proc.stdout.setEncoding('utf8')
    const lines = createInterface({ input: proc.stdout })
    lines.on('line', line => {
      if (settled) return
      try {
        const message = JSON.parse(line)
        if (message.type === 'import_warning') return
        if (message.type === 'fatal') {
          const error = new Error(message.code === 'IMPORT_RECOVERED'
            ? '中断的导入已安全回滚，请重新启动并选择导入。' : '后台配置或初始化失败，请检查配置或恢复备份。')
          error.permanent = true
          fail(error)
        } else if (message.type === 'ready' && message.version === version &&
                   Number.isInteger(message.port) && message.port > 0 && message.port < 65536) {
          ready = settled = true
          clearTimeout(timer)
          connection = { base: `http://127.0.0.1:${message.port}`, token }
          resolve()
        } else fail(new Error('Invalid backend handshake'))
      } catch { fail(new Error('Invalid backend handshake')) }
    })
    proc.on('exit', code => {
      clearTimeout(timer)
      if (child === proc) { child = undefined; connection = undefined }
      if (!ready) { const error = new Error('Backend exited before readiness'); error.permanent = code === 78; fail(error) }
      else if (!quitting && !restarting && !suspended) recover()
    })
    proc.stdin.write(JSON.stringify({ token, dataDir, version, ...initialization }) + '\n')
  })
}

async function stopBackend() {
  connection = undefined
  const proc = child
  if (!proc || proc.exitCode !== null) return
  await new Promise(resolve => {
    const timer = setTimeout(() => proc.kill(), 10000)
    proc.once('exit', () => { clearTimeout(timer); resolve() })
    proc.stdin.end('shutdown\n')
  })
}

async function recover() {
  if (quitting || restarting || suspended) return
  restarting = true
  try {
    await stopBackend()
    while (!quitting && failures < 3) {
      await new Promise(resolve => setTimeout(resolve, 1000 * 2 ** failures++))
      try { await startBackend(); return } catch (error) { if (error.permanent) break }
    }
    if (!quitting) { dialog.showErrorBox('Concourse', '后台服务无法启动，请退出后重试。配置或数据错误需先恢复备份。'); app.quit() }
  } finally { restarting = false }
}

let delivering = false
async function backendRequest(route, method = 'GET', body) {
  const active = connection
  if (!active) throw new Error('Backend unavailable')
  return fetch(active.base + route, { method, body: body === undefined ? undefined : JSON.stringify(body),
    headers: { Authorization: `Bearer ${active.token}`, 'Content-Type': 'application/json' },
    signal: AbortSignal.timeout(15000) })
}
async function deliverNotifications() {
  if (delivering || quitting || !connection || !Notification.isSupported()) return
  delivering = true
  try {
    const response = await backendRequest('/notifications/pending')
    if (!response.ok) return
    const notices = await response.json()
    for (const item of notices) {
      // Revalidate immediately before handing the message to the OS.
      const fresh = await backendRequest('/notifications/pending')
      const current = (await fresh.json()).find(x => x.event_id === item.event_id && x.target_at === item.target_at)
      if (!current) continue
      const notice = new Notification({ title: 'Concourse 日程提醒', body: current.title })
      notice.on('click', () => {
        showWindow()
        if (win) win.webContents.executeJavaScript(`location.hash = '#/timeline?event=${current.event_id}'`)
      })
      notice.show()
      await backendRequest('/notifications/ack', 'POST', current.delivery_ids)
    }
  } catch { /* Keep pending on OS/network failure; retry next poll. */ }
  finally { delivering = false }
}

function showWindow() { if (win) { win.show(); if (win.isMinimized()) win.restore(); win.focus() } }

if (!app.requestSingleInstanceLock()) app.quit()
else {
  app.on('second-instance', showWindow)
  app.on('activate', showWindow)
  app.on('before-quit', event => {
    if (quitting) return
    event.preventDefault(); quitting = true; clearInterval(healthTimer); clearInterval(notificationTimer)
    stopBackend().finally(() => app.quit())
  })
  app.whenReady().then(async () => {
    session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false))
    session.defaultSession.setPermissionCheckHandler(() => false)
    session.defaultSession.webRequest.onHeadersReceived((details, callback) => callback({
      responseHeaders: { ...details.responseHeaders, 'Content-Security-Policy': [
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; object-src 'none'; base-uri 'none'",
      ] },
    }))
    ipcMain.handle('concourse:request', async (event, route, method, body) => {
      if (!trusted(event)) throw new Error('Untrusted sender')
      validateRequest(route, method, body)
      const active = connection
      if (!active) throw new Error('Backend unavailable')
      const response = await fetch(active.base + route, { method, body,
        headers: { Authorization: `Bearer ${active.token}`, 'Content-Type': 'application/json' },
        redirect: 'error', signal: AbortSignal.timeout(15000) })
      return { status: response.status, body: await response.text() }
    })
    if (app.isPackaged && !fs.existsSync(path.join(dataDir, 'initialized.json')) &&
        !fs.existsSync(path.join(dataDir, 'concourse.db'))) {
      const choice = await dialog.showMessageBox({ type: 'question', title: '欢迎使用 Concourse',
        message: '选择全新开始或从旧项目导入', detail: '导入前请完全退出旧服务。导入只处理副本，不修改源数据。',
        buttons: ['全新开始', '导入旧项目', '退出'], cancelId: 2, defaultId: 0 })
      if (choice.response === 2) { app.quit(); return }
      initialization = { initialize: true }
      if (choice.response === 1) {
        const selected = await dialog.showOpenDialog({ title: '选择旧项目目录', properties: ['openDirectory'] })
        if (selected.canceled) { app.quit(); return }
        initialization.importDir = selected.filePaths[0]
      }
      const startup = await dialog.showMessageBox({ type: 'question', title: '后台运行设置',
        message: '开机后静默启动 Concourse？', detail: '关闭窗口默认进入托盘，后台继续抓取与提醒；完全退出后停止。可在托盘菜单中调整。',
        buttons: ['启用开机静默启动', '暂不开启'], defaultId: 0, cancelId: 1 })
      preferences.autoStart = startup.response === 0
      const closing = await dialog.showMessageBox({ type: 'question', title: '关闭窗口行为',
        message: '关闭窗口后是否继续在后台运行？', buttons: ['进入托盘继续运行', '完全退出'], defaultId: 0, cancelId: 1 })
      preferences.closeToTray = closing.response === 0
    }
    try { await startBackend(); initialization = {}; if (app.isPackaged) { preferences.initialized = true; savePreferences() } } catch (error) {
      if (!error.permanent) await recover()
      else { dialog.showErrorBox('Concourse', error.message); app.quit(); return }
    }
    if (quitting || !connection) return
    win = new BrowserWindow({ width: 1180, height: 780, minWidth: 800, minHeight: 540,
      title: 'Concourse', show: false,
      webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true,
        nodeIntegration: false, sandbox: true, webSecurity: true } })
    win.webContents.setWindowOpenHandler(({ url }) => { if (externalUrl(url)) shell.openExternal(url); return { action: 'deny' } })
    win.webContents.on('will-navigate', (event, url) => { event.preventDefault(); if (externalUrl(url)) shell.openExternal(url) })
    win.webContents.on('will-attach-webview', event => event.preventDefault())
    win.on('close', event => { if (!quitting) { event.preventDefault(); if (preferences.closeToTray) win.hide(); else app.quit() } })
    win.once('ready-to-show', () => { if (!process.argv.includes('--hidden')) showWindow() })
    await win.loadFile(path.join(root, 'frontend', 'dist', 'index.html'))
    const icon = nativeImage.createFromBuffer(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==', 'base64')).resize({ width: 16, height: 16 })
    tray = new Tray(icon)
    tray.setToolTip('Concourse — 关闭窗口后继续运行')
    tray.setContextMenu(Menu.buildFromTemplate([{ label: '打开 Concourse', click: showWindow },
      { label: '关闭窗口后继续运行', type: 'checkbox', checked: preferences.closeToTray, click: item => { preferences.closeToTray = item.checked; savePreferences() } },
      { label: '开机静默启动', type: 'checkbox', checked: preferences.autoStart, enabled: app.isPackaged && !process.env.CONCOURSE_DATA_DIR, click: item => { preferences.autoStart = item.checked; savePreferences() } },
      { type: 'separator' }, { label: '完全退出', click: () => app.quit() }]))
    tray.on('double-click', showWindow)
    await backendRequest('/notifications/pending') // Establish first-run baseline before catch-up.
    backendRequest('/runtime/wake', 'POST').catch(() => {})
    powerMonitor.on('resume', async () => { suspended = false; if (!connection) await recover(); backendRequest('/runtime/wake', 'POST').catch(() => {}) })
    powerMonitor.on('suspend', () => { suspended = true; stopBackend() })
    notificationTimer = setInterval(deliverNotifications, 30000)
    healthTimer = setInterval(async () => {
      if (!connection || restarting || quitting || suspended) return
      try {
        const res = await fetch(connection.base + '/health', { headers: { Authorization: `Bearer ${connection.token}` }, signal: AbortSignal.timeout(5000) })
        if (!res.ok || (await res.json()).version !== version) throw new Error('Unhealthy')
      } catch { recover() }
    }, 15000)
  }).catch(() => { dialog.showErrorBox('Concourse', '桌面启动失败。'); app.quit() })
}
