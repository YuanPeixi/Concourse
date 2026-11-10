const { contextBridge, ipcRenderer } = require('electron')
contextBridge.exposeInMainWorld('concourse', Object.freeze({
  request: (path, method, body) => ipcRenderer.invoke('concourse:request', path, method, body),
}))
