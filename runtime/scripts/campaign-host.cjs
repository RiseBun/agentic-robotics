// Headless engineering host: decrypt existing model configuration in an isolated
// Electron profile. No robot UI routes, no writes to the user's task/settings DB.
const { app, safeStorage } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const options = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const scratch = path.resolve(options.scratch);
const sourceData = path.join(process.env.LOCALAPPDATA, 'embuddies/data');
const sourceProfile = path.join(process.env.LOCALAPPDATA, 'embuddies/profile');
if (options.reuseEncryptedProfile) {
  for (const name of ['profile/browser-profile/Local State', 'data/config.json', 'data/secrets.json']) {
    if (!fs.existsSync(path.join(scratch, name))) throw new Error('Existing encrypted profile is incomplete: ' + name);
  }
} else {
  fs.mkdirSync(path.join(scratch, 'profile/browser-profile'), { recursive: true });
  for (const file of [path.join(sourceProfile, 'browser-profile/Local State'), path.join(sourceProfile, 'Local State'), path.join(sourceData, 'browser-profile/Local State')]) {
    if (fs.existsSync(file)) {
      fs.copyFileSync(file, path.join(scratch, 'profile/browser-profile/Local State'));
      break;
    }
  }
}
app.setPath('userData', path.join(scratch, 'profile'));
app.setPath('sessionData', path.join(scratch, 'profile/browser-profile'));
app.disableHardwareAcceleration();
app.whenReady().then(async () => {
  const { DatabaseSync } = require('node:sqlite');
  const isolated = path.join(scratch, 'data');
  fs.mkdirSync(isolated, { recursive: true });
  if (!options.reuseEncryptedProfile) {
    const original = new DatabaseSync(path.join(sourceData, 'embuddies.sqlite'), { readOnly: true });
    // Copy only encrypted configuration records, never user conversations/tasks.
    const values = original.prepare("SELECT key,value FROM records WHERE key IN ('config.json','secrets.json')").all();
    original.close();
    for (const row of values) fs.writeFileSync(path.join(isolated, row.key), row.value);
  }
  globalThis.embuddiesCrypto = async (action, value) => {
    if (action === 'decrypt') return safeStorage.decryptString(Buffer.from(value, 'base64'));
    if (action === 'encrypt') return safeStorage.encryptString(value).toString('base64');
    throw new Error('Unsupported crypto operation');
  };
  const runtimeRoot = path.join(options.sourceRoot, 'runtime');
  const { createConfigStore } = await import(pathToFileURL(path.join(runtimeRoot, 'config-store.mjs')).href);
  const { runCampaign, runPairedCampaign } = await import(pathToFileURL(path.join(__dirname, 'robot-campaign.mjs')).href);
  const config = createConfigStore(runtimeRoot, isolated);
  const runner = options.programRepairEpisodes
    ? (await import(pathToFileURL(path.join(__dirname, 'program-repair-model-diagnosis.mjs')).href)).runProgramRepairDiagnosis
    : options.retreatEpisodes
    ? (await import(pathToFileURL(path.join(__dirname, 'retreat-model-diagnosis.mjs')).href)).runRetreatModelDiagnosis
    : options.schedule ? runPairedCampaign : runCampaign;
  const result = await runner(options, () => config.read());
  fs.writeFileSync(path.join(options.output, 'host-result.json'), JSON.stringify(result, null, 2));
  app.exit(0);
}).catch(error => {
  fs.mkdirSync(options.output, { recursive: true });
  fs.writeFileSync(path.join(options.output, 'host-error.json'), JSON.stringify({ error: error.message, stack: error.stack }, null, 2));
  console.error(error.message);
  app.exit(1);
});
