import { useEffect, useRef, useState } from 'react';

type Json = Record<string, any>;
declare global {
  interface Window { ROBOT_DESKTOP?: { request: (request: Json) => Promise<any>; runtimeStatus: () => Promise<Json>; retryRuntime: () => Promise<any> } }
}
async function request(path: string, body?: Json) {
  if (window.ROBOT_DESKTOP) return window.ROBOT_DESKTOP.request({ method: body === undefined ? 'GET' : 'POST', path, body });
  const response = await fetch('/api'+path, { method: body === undefined ? 'GET' : 'POST',
    ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || '工作台请求失败');
  return value;
}
const statuses: Record<string, string> = { idle: '尚未开始', initializing: '正在创建场景', interpreting: '正在理解目标', thinking: '模型思考中', executing: '正在执行', stopping: '正在停止', stopped: '已停止', completed: '目标已验证', awaiting_user: '等待补充说明', needs_attention: '需要处理', closed: '场景已结束', ready: '就绪', interrupted: '服务中断', failed: '失败', running: '执行中', cancelled: '已取消' };
const effects: Record<string, string> = { verified: '证据支持', failed: '未达到目标', unknown: '证据不足' };
const executionStatuses: Record<string, string> = { running: '执行中', completed: '已执行', failed: '执行失败', cancelled: '已取消' };
const presets: Record<string, string> = { tabletop: '把蓝色方块放到灰色支撑台上，最后保持空夹爪。', cube_lifting: '抓起红色方块并保持抬起，不要释放。', cube_stack: '把红色方块放到绿色方块上，释放后保持空夹爪。', cube_restack: '将绿色方块从红色方块上移开，再将红色方块放到绿色方块上，最后空夹爪。' };

export function RobotWorkbench() {
  const [state, setState] = useState<Json>({ status: 'idle', messages: [], events: [], history: [] });
  const [settings, setSettings] = useState<Json | null>(null);
  const [openSettings, setOpenSettings] = useState(false);
  const [task, setTask] = useState('cube_lifting');
  const [text, setText] = useState(presets.cube_lifting);
  const [images, setImages] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState({ at: 0, rtt: Infinity });
  const [clock, setClock] = useState(performance.now());
  const [channel, setChannel] = useState('');
  const [model, setModel] = useState('');
  const [key, setKey] = useState('');
  const [baseURL, setBaseURL] = useState('');
  const [protocol, setProtocol] = useState('openai-completions');
  const [channelLabel, setChannelLabel] = useState('');
  const [runtimeURL, setRuntimeURL] = useState('');
  const [token, setToken] = useState('');
  const messagesEnd = useRef<HTMLDivElement>(null);
  const inflight = useRef(false);
  const refreshError = useRef('');
  useEffect(() => {
    let alive = true;
    async function refresh() {
      if (inflight.current) return;
      inflight.current = true;
      try {
        const started = performance.now();
        const next = await request('/robot/state');
        if (alive) { setState(next); setReceipt({ at: performance.now(), rtt: (performance.now()-started)/1000 });
          const previousRefreshError = refreshError.current;
          setError(previous => previous === previousRefreshError ? '' : previous); refreshError.current = ''; }
        if (!settings) {
          const value = await request('/robot/settings');
          if (alive) { setSettings(value); setChannel(value.activeChannel); setRuntimeURL(value.runtimeURL); setModel(value.channels.find((c: Json) => c.id === value.activeChannel)?.model || ''); }
        }
      } catch (e) { if (alive) { refreshError.current = (e as Error).message; setError(refreshError.current); } }
      finally { inflight.current = false; }
    }
    void refresh();
    const timer = setInterval(() => { setClock(performance.now()); void refresh(); }, 700);
    return () => { alive = false; clearInterval(timer); };
  }, [settings]);
  useEffect(() => { messagesEnd.current?.scrollIntoView({ block: 'end' }); }, [state.messages?.length]);
  async function act(fn: () => Promise<any>) {
    setBusy(true); setError('');
    try { await fn(); const started = performance.now(); setState(await request('/robot/state')); setReceipt({ at: performance.now(), rtt: (performance.now()-started)/1000 }); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const hasEpisode = state.episode && !['closed', 'interrupted'].includes(state.episode.status);
  const continuing = hasEpisode || (state.task_id && ['awaiting_user', 'needs_attention'].includes(state.status));
  async function submit() {
    await act(async () => {
      await request(continuing ? '/robot/input' : '/robot/start', { text, images, task, seed: 0 });
      setText(''); setImages([]);
    });
  }
  async function addImages(files: FileList | null) {
    if (!files) return;
    try {
      const incoming: string[] = [];
      for (const file of Array.from(files)) {
        if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 6_000_000) throw new Error('请选择小于 6 MB 的 PNG、JPEG 或 WebP 图片');
        incoming.push(await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsDataURL(file); }));
      }
      if (images.length + incoming.length > 4) throw new Error('每次最多附加 4 张图片');
      setImages(previous => [...previous, ...incoming]);
    } catch (e) { setError((e as Error).message); }
  }
  async function saveSettings() {
    await act(async () => {
      const patch: Json = { id: channel, model };
      if (!settings?.channels.find((c: Json) => c.id === channel)?.builtin) {
        patch.label = channelLabel;
        patch.baseURL = baseURL;
        patch.api = protocol;
        patch.models = [...new Set([...(settings?.channels.find((c: Json) => c.id === channel)?.models || []).map((m: Json | string) => typeof m === 'string' ? m : m.id), model])];
      }
      if (key) patch.apiKey = key;
      const body: Json = { runtimeURL, model: { activeChannel: channel, channelPatch: patch } };
      if (token) body.runtimeToken = token;
      setSettings(await request('/robot/settings', body)); setKey(''); setToken(''); setOpenSettings(false);
    });
  }
  function chooseChannel(id: string) {
    const selected = settings?.channels.find((c: Json) => c.id === id);
    setChannel(id === '__new__' ? 'custom-' + Date.now().toString(36) : id);
    setModel(selected?.model || '');
    setBaseURL(selected?.baseURL || '');
    setProtocol(selected?.api || 'openai-completions');
    setChannelLabel(selected?.label || '');
    setKey('');
  }
  async function exportRecords() {
    await act(async () => {
      const value = await request('/robot/export');
      const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
      const a = document.createElement('a'); a.href = url; a.download = `robot-task-${state.task_id}.json`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }
  const used = state.episode?.used;
  const goal = state.episode?.goal;
  const objectLabel = (name: string) => ({ 'red cube': '红色方块', 'green cube': '绿色方块', table: '桌面' }[name] || name);
  const goalText = goal?.all ? goal.all.map((c: Json) => c.predicate === 'empty_gripper' ? '空夹爪' : `${c.predicate}(${[c.object,c.support,c.region].filter(Boolean).join(', ')})`).join('；') : goal ? [goal.holding && `持有${objectLabel(goal.holding)}`, goal.on && `${objectLabel(goal.on[0])}在${objectLabel(goal.on[1])}上`, goal.empty_gripper && '空夹爪'].filter(Boolean).join('；') : '';
  const active = state.episode?.segments?.[state.episode?.active_segment];
  const last = state.history?.at(-1);
  const holdingAlert = state.episode?.runtime_alert?.code === 'PUBLIC_HOLDING_LOSS' ? state.episode.runtime_alert : null;
  const system = [state.episode?.system, state.observation?.system]
    .filter(value => value && state.episode?.id && value.episode_id === state.episode.id && value.revision === state.episode.revision)
    .sort((a, b) => (b.world?.source?.control_step ?? -1)-(a.world?.source?.control_step ?? -1))[0];
  const systemFresh = system?.world?.fresh && !state.connection_error && state.episode?.status === 'ready'
    && Number.isFinite(system.world.age_seconds) && system.world.age_seconds >= 0
    && system.world.age_seconds+receipt.rtt+Math.max(0, clock-receipt.at)/1000 <= 1;
  const currentVerification = systemFresh ? system.world.facts?.goal?.status || 'unknown' : 'unknown';
  const taskStatus = state.status === 'completed' && currentVerification !== 'verified'
    ? '完成时已验证；当前证据待更新' : statuses[state.status] || state.status;
  const nodeStatus = (value: string) => ({ completed: '已执行（历史验收通过）', ready: '可执行', blocked: '条件不足', satisfied: '证据已支持',
    not_required: '无需执行', running: '执行中', awaiting_verification: '等待放置验收', unknown: '需重新观测' }[value] || value);
  const taskPhase = (value: string) => ({ ready: '选择下一节点', blocked: '等待条件或协助', running: '等待当前尝试',
    awaiting_user: '等待用户指令',
    holding: '等待控制器确认保持', verified: '可请求最终验收', program_rejected: '需要修正程序',
    evidence_insufficient: '需要取证或授权恢复', recovery_ready: '可选择授权恢复',
    awaiting_verification: '等待放置验收', effect_failed: '需要恢复或重新规划', execution_failed: '需要恢复或重新规划',
    cancelled: '当前尝试已取消', budget_exhausted: '预算耗尽', interface_unavailable: '需要检查接口或协助',
    state_unavailable: '等待新观测', unavailable: 'Runtime 不可用' }[value] || value);
  return <div className="robot-app">
    <header className="topbar"><div className="wordmark">embuddies <span>机器人工作台</span></div><div className="header-actions"><span className={`connection ${state.connection_error ? 'offline' : ''}`}>{state.connection_error ? '连接中断' : state.episode ? '场景已连接' : '等待任务'}</span><button onClick={() => { if (!openSettings) chooseChannel(channel); setOpenSettings(!openSettings); }}>设置</button></div></header>
    {error && <div role="alert" className="alert">{error}<button onClick={() => setError('')} aria-label="关闭错误">×</button></div>}
    {state.connection_error && <div role="alert" className="alert">{state.connection_error}。执行状态请以 Runtime 重新连接后的记录为准。</div>}
    <main className="workspace">
      <section className="conversation" aria-label="目标与对话">
        <div className="panel-heading"><h1>告诉机器人要做什么</h1><span>{taskStatus}</span></div>
        <div className="messages">
          {!state.messages?.length && <div className="empty-conversation"><h2>从一个明确目标开始</h2><p>选择场景，描述方块的目标位置或持有状态。机器人会观察现场，分段执行，并检查每一步的结果。</p></div>}
          {state.messages?.map((message: Json, i: number) => <article key={i} className={`message ${message.role}`}><div className="speaker">{message.role === 'user' ? '你' : message.role === 'assistant' ? '机器人' : '运行提示'}</div><p>{message.text}</p>{message.images?.map((image: string, j: number) => <img className="attachment" src={image} alt={`输入图片 ${j + 1}`} key={j} />)}</article>)}
          <div ref={messagesEnd} />
        </div>
        <div className="composer" onPaste={event => { const files = event.clipboardData.files; if (files.length) { event.preventDefault(); void addImages(files); } }}>
          {!continuing && <label className="scenario">实验场景<select aria-label="实验场景" value={task} onChange={event => { setTask(event.target.value); setText(presets[event.target.value]); }}><option value="cube_lifting">抬起方块</option><option value="cube_stack">堆叠方块</option><option value="cube_restack">重新堆叠</option>{settings?.experimentalModes?.includes("tabletop") && <option value="tabletop">通用桌面（实验）</option>}</select></label>}
          <textarea aria-label="任务目标或新指令" placeholder={continuing ? '补充说明或改变目标；发送后先中断当前动作' : '例如：抓起红色方块并保持抬起'} value={text} onChange={event => setText(event.target.value)} />
          {images.length > 0 && <div className="attachments">{images.map((src, i) => <button key={i} onClick={() => setImages(images.filter((_, index) => index !== i))} aria-label={`移除图片 ${i + 1}`}><img src={src} alt={`待发送图片 ${i + 1}`} /></button>)}</div>}
          <div className="composer-actions"><label className="attach">附加图片<input type="file" accept="image/png,image/jpeg,image/webp" multiple onChange={event => { void addImages(event.target.files); event.target.value = ''; }} /></label><div><button className="stop" disabled={!state.task_id} onClick={() => void act(() => request('/robot/stop', {}))}>停止</button><button className="primary" disabled={busy || !text.trim() || ['stopping', 'initializing'].includes(state.status)} onClick={() => void submit()}>{continuing ? '发送指令' : '开始任务'}</button></div></div>
        </div>
      </section>
      <section className="scene-panel" aria-label="场景与执行证据">
        <div className="panel-heading"><h2>当前场景</h2><span>{state.observation?.captured_at ? new Date(state.observation.captured_at * 1000).toLocaleTimeString() : '暂无观测'}</span></div>
        {goalText && <p className="goal-text">最终目标：{goalText}</p>}
        {holdingAlert && <p role="status" className="alert">持有证据发生异常。{holdingAlert.hold_confirmed ? '控制器已确认保持，需要根据新观测重新规划。' : '正在等待控制器确认保持。'}</p>}
        {system && <details className="records" aria-label="Runtime 任务状态"><summary>任务与证据状态 · 目标版本 {system.goal_version}</summary><p>{systemFresh ? '当前公开观测' : '证据已过期或场景不可用，需重新观测'}</p>{system.task?.execution && <p>任务推进：{systemFresh ? taskPhase(system.task.execution.phase) : '等待新观测'}</p>}<ol>{system.task?.nodes?.map((node: Json) => <li key={node.id}>{node.title} · {nodeStatus(systemFresh ? node.status : 'unknown')}</li>)}</ol><p>执行器：{system.executor?.mode === 'skill' ? '注册技能' : system.executor?.mode === 'cap_program' ? 'CaP 程序' : '空闲'}</p></details>}
        {system?.contract_version === 2 && <details className="records" aria-label="通用任务缺失证据"><summary>通用任务 · 能力与缺失证据</summary><p>对象位置来自当前公开观测；历史执行通过不代表当前状态仍成立。</p><ul>{Object.entries(system.world?.facts || {}).filter(([key, value]) => key !== 'goal' && (value as Json).status === 'unknown').slice(0, 12).map(([key, value]) => <li key={key}>{key}：{(value as Json).result?.reason || '缺少公开证据'}</li>)}</ul><p>计划版本：{system.task?.plan_version}；相机：{system.description?.sensors?.map((sensor: Json) => sensor.id).join('、')}</p></details>}

        <div className="scene">{state.image ? <img src={state.image} alt="Runtime 最新场景观测" /> : <div className="scene-empty"><svg viewBox="0 0 160 110" aria-hidden="true"><path d="M20 90h120M65 90V65l30-20-15-25M95 45l18 7 8-15M72 20h16M60 65h10" /><circle cx="65" cy="65" r="7" /><circle cx="95" cy="45" r="6" /><rect x="115" y="73" width="17" height="17" /></svg><p>任务开始后显示现场图像</p></div>}</div>
        <div className="execution-summary"><div><span>当前子目标</span><strong>{state.subgoal || '等待目标'}</strong></div><div><span>动作执行</span><strong>{executionStatuses[active?.execution || last?.execution] || (state.episode ? '空闲' : '未开始')}</strong></div><div><span>当前目标验证</span><strong className={`effect ${currentVerification}`}>{effects[currentVerification]}</strong></div></div>
        {state.remaining_steps?.length > 0 && <details className="records"><summary>模型建议的后续步骤</summary><ol className="remaining">{state.remaining_steps.map((step: string, i: number) => <li key={i}>{step}</li>)}</ol></details>}
        {used && <div className="budgets"><span>模型 {used.decisions}/{state.episode?.limits?.decisions ?? 12}</span><span>接口 {used.calls}/{state.episode?.limits?.calls ?? 40}</span><span>运动步 {used.motion_steps}/{state.episode?.limits?.motion_steps ?? 1500}</span><span>耗时 {Math.round(used.wall_seconds)}s</span><span>等待步 {used.idle_steps}</span></div>}
        <details className="records"><summary>代码与执行记录 <span>{state.events?.length || 0} 条事件</span></summary>{state.history?.filter((h: Json) => h.code).map((h: Json, i: number) => <div className="segment" key={i}><p>{h.subgoal} · {executionStatuses[h.execution] || h.execution}</p><p>阶段验证：{effects[h.stage_verification?.status || 'unknown']} · 最终目标：{effects[h.effect || 'unknown']}</p>{h.task_binding && <p>任务节点 {h.task_binding.node_id} · 节点验证：{effects[h.node_verification?.status || 'unknown']}</p>}<pre>{h.code}</pre>{h.error && <p className="error-text">{h.error}</p>}{h.feedback && <details><summary>失败与未知证据</summary><pre>{JSON.stringify(h.feedback, null, 2)}</pre></details>}</div>)}<ol className="timeline">{state.events?.map((event: Json) => <li key={event.id}><time>{new Date(event.time * 1000).toLocaleTimeString()}</time><span>{event.kind}</span><details><summary>证据</summary><pre>{JSON.stringify(event, null, 2)}</pre></details></li>)}</ol></details>
        {settings?.deployment && <details className="records" aria-label="运行版本"><summary>运行版本与部署状态</summary><p>Runtime：{settings.deployment.runtime === 'current' ? '与磁盘源码一致' : settings.deployment.runtime === 'restart_required' ? '磁盘源码已更新，运行服务尚未重启' : '运行版本未知'}</p><p>工作台：{settings.deployment.workbench === 'current' ? '与磁盘源码一致' : settings.deployment.workbench === 'restart_required' ? '磁盘源码已更新，工作台尚未重启' : '运行版本未知'}</p><p>正在运行的 Runtime：{settings.deployment.runningRuntime?.slice(0, 12) || '未知'}；磁盘源码：{settings.deployment.installedRuntime?.slice(0, 12) || '未知'}</p></details>}
        <footer className="scene-actions"><button disabled={!state.task_id || busy} onClick={() => void exportRecords()}>导出任务记录</button><button disabled={!hasEpisode || busy} onClick={() => void act(() => request('/robot/close', {}))}>结束场景</button></footer>
      </section>
    </main>
    {openSettings && <div className="modal-backdrop"><section className="settings" role="dialog" aria-modal="true" aria-label="模型与 CaP 连接设置"><div className="panel-heading"><h2>模型与 CaP 连接</h2><button aria-label="关闭设置" onClick={() => setOpenSettings(false)}>×</button></div><label>模型渠道<select value={channel} onChange={event => chooseChannel(event.target.value)}>{settings?.channels.map((c: Json) => <option value={c.id} key={c.id}>{c.label}</option>)}{channel && !settings?.channels.some((c: Json) => c.id === channel) && <option value={channel}>新渠道</option>}<option value="__new__">新增自定义渠道</option></select></label>{!settings?.channels.find((c: Json) => c.id === channel)?.builtin && <><label>渠道名称<input value={channelLabel} onChange={event => setChannelLabel(event.target.value)} /></label><label>模型接口地址<input value={baseURL} placeholder="https://provider.example/v1" onChange={event => setBaseURL(event.target.value)} /></label><label>接口协议<select value={protocol} onChange={event => setProtocol(event.target.value)}><option value="openai-completions">OpenAI Completions</option><option value="openai-responses">OpenAI Responses</option></select></label></>}<label>模型名称<input value={model} onChange={event => setModel(event.target.value)} /></label><label>模型密钥<input type="password" autoComplete="off" value={key} placeholder="留空保留已有密钥" onChange={event => setKey(event.target.value)} /></label><label>CaP 服务地址<input value={runtimeURL} onChange={event => setRuntimeURL(event.target.value)} /></label><label>CaP 访问令牌<input type="password" autoComplete="off" value={token} placeholder="留空保留已有令牌" onChange={event => setToken(event.target.value)} /></label><p>模型密钥保存在本机加密存储。修改连接前请结束当前场景。</p><button className="primary" disabled={busy || hasEpisode} onClick={() => void saveSettings()}>保存设置</button></section></div>}
  </div>;
}
