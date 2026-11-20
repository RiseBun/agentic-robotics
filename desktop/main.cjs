const { app, BrowserWindow, ipcMain, protocol, safeStorage } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { RuntimeSupervisor } = require('./runtime-supervisor.cjs');

// A portable double-click must use the same encryption profile as the launcher.
if (!app.commandLine.hasSwitch('user-data-dir')) {
  const profile = path.join(process.env.LOCALAPPDATA || app.getPath('appData'), 'embuddies', 'profile');
  fs.mkdirSync(profile, { recursive: true });
  app.setPath('userData', profile);
}
const bundleRoot = process.env.EMBUDDIES_BUNDLE_ROOT || path.resolve(__dirname, '..');
const runtimeRoot = path.join(bundleRoot, 'runtime');
const uiRoot = path.join(bundleRoot, 'frontend', 'dist');
const dataRoot = process.env.EMBUDDIES_DATA_DIR || path.join(process.env.LOCALAPPDATA || app.getPath('userData'), 'embuddies', 'data');
// Preserve the previous safeStorage master key and existing user data.
const browserProfile = path.join(app.getPath('userData'), 'browser-profile');
fs.mkdirSync(browserProfile, { recursive: true });
const localState = path.join(browserProfile, 'Local State');
if (!fs.existsSync(localState)) {
  for (const source of [path.join(dataRoot, 'browser-profile', 'Local State'), path.join(app.getPath('userData'), 'Local State')]) {
    if (fs.existsSync(source)) { fs.copyFileSync(source, localState, fs.constants.COPYFILE_EXCL); break; }
  }
}
app.setPath('sessionData', browserProfile);
protocol.registerSchemesAsPrivileged([{ scheme: 'embuddies', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);
let window, supervisor, quitting = false;
const log = message => {
  const directory = path.join(dataRoot, 'logs');
  fs.mkdirSync(directory, { recursive: true });
  fs.appendFileSync(path.join(directory, 'robot-host.log'), new Date().toISOString() + ' ' + message + '\n');
};
function trusted(event) {
  if (event.sender !== window?.webContents || !event.senderFrame?.url.startsWith('embuddies://workbench/')) throw new Error('Untrusted renderer');
}
app.whenReady().then(async () => {
  protocol.handle('embuddies', request => {
    const url = new URL(request.url);
    if (url.hostname !== 'workbench') return new Response('', { status: 403 });
    const file = path.resolve(uiRoot, '.' + (url.pathname === '/' ? '/index.html' : decodeURIComponent(url.pathname)));
    if (!file.startsWith(uiRoot + path.sep)) return new Response('', { status: 403 });
    const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream';
    try { return new Response(fs.readFileSync(file), { headers: { 'Content-Type': mime } }); }
    catch { return new Response('', { status: 404 }); }
  });
  window = new BrowserWindow({ width: 1340, height: 900, minWidth: 900, minHeight: 640,
    title: 'embuddies · 机器人任务', backgroundColor: '#edf2f7',
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true,
      nodeIntegration: false, sandbox: true, webSecurity: true } });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, url) => { if (!url.startsWith('embuddies://workbench/')) event.preventDefault(); });
  supervisor = new RuntimeSupervisor(path.join(runtimeRoot, 'utility-entry.mjs'), {
    EMBUDDIES_DATA_DIR: dataRoot, EMBUDDIES_ENGINE: 'robot'
  }, async (operation, values) => {
    if (operation !== 'crypto' || !safeStorage.isEncryptionAvailable()) throw new Error('宿主仅提供加密存储');
    if (values.action === 'encrypt') return safeStorage.encryptString(values.value).toString('base64');
    if (values.action === 'decrypt') return safeStorage.decryptString(Buffer.from(values.value, 'base64'));
    throw new Error('非法加密操作');
  }, log);
  ipcMain.handle('embuddies:runtime', async (event, request, id) => {
    trusted(event);
    return supervisor.request(request, detail => { if (!event.sender.isDestroyed()) event.sender.send('embuddies:runtime-event', id, detail); });
  });
  ipcMain.handle('embuddies:runtime-status', event => { trusted(event); return { state: supervisor.state, detail: supervisor.detail }; });
  ipcMain.handle('embuddies:runtime-retry', event => { trusted(event); return supervisor.retry(); });
  supervisor.on('state', status => { if (!window.isDestroyed()) window.webContents.send('embuddies:host-state', status); });
  await Promise.all([window.loadURL('embuddies://workbench/'), supervisor.start()]);
}).catch(error => { log(error.message); app.quit(); });
app.on('window-all-closed', () => app.quit());
app.on('before-quit', event => {
  if (quitting || !supervisor) return;
  event.preventDefault();
  quitting = true;
  supervisor.stop().finally(() => app.quit());
});
