const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('ROBOT_DESKTOP', {
  request: request => ipcRenderer.invoke('embuddies:runtime', request),
  runtimeStatus: () => ipcRenderer.invoke('embuddies:runtime-status'),
  retryRuntime: () => ipcRenderer.invoke('embuddies:runtime-retry'),
});
