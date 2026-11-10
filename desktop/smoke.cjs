// Development-only smoke runner; excluded from packaged files.
const { app } = require('electron')
const assert = require('node:assert/strict')
let verified = false
const timer = setTimeout(() => { console.error('Desktop smoke timed out'); app.exit(1) }, 45000)
app.on('browser-window-created', (_, window) => {
  window.webContents.once('did-finish-load', async () => {
    try {
      const result = await window.webContents.executeJavaScript(`(async () => {
        const health = await window.concourse.request('/health', 'GET');
        const platforms = await window.concourse.request('/fetchers', 'GET');
        let rejected = false;
        try { await window.concourse.request('https://example.com', 'GET') } catch { rejected = true }
        return { health, platforms, rejected, node: typeof require, title: document.title };
      })()`)
      assert.equal(result.health.status, 200)
      assert.equal(JSON.parse(result.platforms.body).length, 4)
      assert.equal(result.rejected, true)
      assert.equal(result.node, 'undefined')
      await window.webContents.executeJavaScript("location.hash = '#/fetchers'")
      await new Promise(resolve => setTimeout(resolve, 1500))
      await window.webContents.executeJavaScript("document.querySelector('.platform-config summary').click()")
      await new Promise(resolve => setTimeout(resolve, 300))
      const fs = require('node:fs')
      const path = require('node:path')
      const output = path.join(__dirname, '..', 'artifacts')
      fs.mkdirSync(output, { recursive: true })
      fs.writeFileSync(path.join(output, 'desktop-platforms.png'), (await window.webContents.capturePage()).toPNG())
      window.close()
      assert.equal(window.isDestroyed(), false)
      assert.equal(window.isVisible(), false)
      verified = true
      console.log('DESKTOP_SMOKE_PASS: page, preload, authenticated IPC, route rejection, sandbox, tray close')
      clearTimeout(timer)
      app.quit()
    } catch (error) { console.error(error.message); clearTimeout(timer); app.quit() }
  })
})
app.on('will-quit', () => { if (!verified) process.exitCode = 1 })
require('./main.cjs')
