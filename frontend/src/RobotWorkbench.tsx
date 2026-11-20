import { useEffect, useRef, useState } from 'react';

type Json = Record<string, any>;
declare global {
  interface Window { ROBOT_DESKTOP?: { request: (request: Json) => Promise<any>; runtimeStatus: () => Promise<Json>; retryRuntime: () => Promise<any> } }
}
async function request(path: string, body?: Json) {
  if (!window.ROBOT_DESKTOP) throw new Error('请从桌面应用打开机器人工作台');
  return window.ROBOT_DESKTOP.request({ method: body === undefined ? 'GET' : 'POST', path, body });
}
const statuses: Record<string, string> = { idle: '尚未开始', initializing: '正在创建场景', interpreting: '正在理解目标', thinking: '模型思考中', executing: '正在执行', stopping: '正在停止', stopped: '已停止', completed: '目标已验证', awaiting_user: '等待补充说明', needs_attention: '需要处理', closed: '场景已结束', ready: '就绪', interrupted: '服务中断', failed: '失败', running: '执行中', cancelled: '已取消' };
const effects: Record<string, string> = { verified: '证据支持', failed: '未达到目标', unknown: '证据不足' };
const executionStatuses: Record<string, string> = { running: '执行中', completed: '已执行', failed: '执行失败', cancelled: '已取消' };
const presets: Record<string, string> = { cube_lifting: '抓起红色方块并保持抬起，不要释放。', cube_stack: '把红色方块放到绿色方块上，释放后保持空夹爪。', cube_restack: '将绿色方块从红色方块上移开，再将红色方块放到绿色方块上，最后空夹爪。' };

export function RobotWorkbench() {
  const [state, setState] = useState<Json>({ status: 'idle', messages: [], events: [], history: [] });
  const [settings, setSettings] = useState<Json | null>(null);
  const [openSettings, setOpenSettings] = useState(false);
  const [task, setTask] = useState('cube_lifting');
  const [text, setText] = useState(presets.cube_lifting);
  const [images, setImages] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
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
  useEffect(() => {
    let alive = true;
    async function refresh() {
      if (inflight.current) return;
      inflight.current = true;
      try {
        const next = await request('/robot/state');
        if (alive) setState(next);
        if (!settings) {
          const value = await request('/robot/settings');
          if (alive) { setSettings(value); setChannel(value.activeChannel); setRuntimeURL(value.runtimeURL); setModel(value.channels.find((c: Json) => c.id === value.activeChannel)?.model || ''); }
        }
      } catch (e) { if (alive) setError((e as Error).message); }
      finally { inflight.current = false; }
    }
    void refresh();
    const timer = setInterval(refresh, 700);
    return () => { alive = false; clearInterval(timer); };
  }, [settings]);
  useEffect(() => { messagesEnd.current?.scrollIntoView({ block: 'end' }); }, [state.messages?.length]);
  async function act(fn: () => Promise<any>) {
    setBusy(true); setError('');
    try { await fn(); setState(await request('/robot/state')); }
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
  const goalText = goal ? [goal.holding && `持有${objectLabel(goal.holding)}`, goal.on && `${objectLabel(goal.on[0])}在${objectLabel(goal.on[1])}上`, goal.empty_gripper && '空夹爪'].filter(Boolean).join('；') : '';
  const active = state.episode?.segments?.[state.episode?.active_segment];
  const last = state.history?.at(-1);
  const holdingAlert = state.episode?.runtime_alert?.code === 'PUBLIC_HOLDING_LOSS' ? state.episode.runtime_alert : null;
  return <div className="robot-app">
    <header className="topbar"><div className="wordmark">embuddies <span>机器人工作台</span></div><div className="header-actions"><span className={`connection ${state.connection_error ? 'offline' : ''}`}>{state.connection_error ? '连接中断' : state.episode ? '场景已连接' : '等待任务'}</span><button onClick={() => { if (!openSettings) chooseChannel(channel); setOpenSettings(!openSettings); }}>设置</button></div></header>
    {error && <div role="alert" className="alert">{error}<button onClick={() => setError('')} aria-label="关闭错误">×</button></div>}
    {state.connection_error && <div role="alert" className="alert">{state.connection_error}。执行状态请以 Runtime 重新连接后的记录为准。</div>}
    <main className="workspace">
      <section className="conversation" aria-label="目标与对话">
        <div className="panel-heading"><h1>告诉机器人要做什么</h1><span>{statuses[state.status] || state.status}</span></div>
        <div className="messages">
          {!state.messages?.length && <div className="empty-conversation"><h2>从一个明确目标开始</h2><p>选择场景，描述方块的目标位置或持有状态。机器人会观察现场，分段执行，并检查每一步的结果。</p></div>}
          {state.messages?.map((message: Json, i: number) => <article key={i} className={`message ${message.role}`}><div className="speaker">{message.role === 'user' ? '你' : message.role === 'assistant' ? '机器人' : '运行提示'}</div><p>{message.text}</p>{message.images?.map((image: string, j: number) => <img className="attachment" src={image} alt={`输入图片 ${j + 1}`} key={j} />)}</article>)}
          <div ref={messagesEnd} />
        </div>
        <div className="composer" onPaste={event => { const files = event.clipboardData.files; if (files.length) { event.preventDefault(); void addImages(files); } }}>
          {!continuing && <label className="scenario">实验场景<select aria-label="实验场景" value={task} onChange={event => { setTask(event.target.value); setText(presets[event.target.value]); }}><option value="cube_lifting">抬起方块</option><option value="cube_stack">堆叠方块</option><option value="cube_restack">重新堆叠</option></select></label>}
          <textarea aria-label="任务目标或新指令" placeholder={continuing ? '补充说明或改变目标；发送后先中断当前动作' : '例如：抓起红色方块并保持抬起'} value={text} onChange={event => setText(event.target.value)} />
          {images.length > 0 && <div className="attachments">{images.map((src, i) => <button key={i} onClick={() => setImages(images.filter((_, index) => index !== i))} aria-label={`移除图片 ${i + 1}`}><img src={src} alt={`待发送图片 ${i + 1}`} /></button>)}</div>}
          <div className="composer-actions"><label className="attach">附加图片<input type="file" accept="image/png,image/jpeg,image/webp" multiple onChange={event => { void addImages(event.target.files); event.target.value = ''; }} /></label><div><button className="stop" disabled={!state.task_id} onClick={() => void act(() => request('/robot/stop', {}))}>停止</button><button className="primary" disabled={busy || !text.trim() || ['stopping', 'initializing'].includes(state.status)} onClick={() => void submit()}>{continuing ? '发送指令' : '开始任务'}</button></div></div>
        </div>
      </section>
      <section className="scene-panel" aria-label="场景与执行证据">
        <div className="panel-heading"><h2>当前场景</h2><span>{state.observation?.captured_at ? new Date(state.observation.captured_at * 1000).toLocaleTimeString() : '暂无观测'}</span></div>
        {goalText && <p className="goal-text">最终目标：{goalText}</p>}
        {holdingAlert && <p role="status" className="alert">持有证据发生异常。{holdingAlert.hold_confirmed ? '控制器已确认保持，需要根据新观测重新规划。' : '正在等待控制器确认保持。'}</p>}
        <div className="scene">{state.image ? <img src={state.image} alt="Runtime 最新场景观测" /> : <div className="scene-empty"><svg viewBox="0 0 160 110" aria-hidden="true"><path d="M20 90h120M65 90V65l30-20-15-25M95 45l18 7 8-15M72 20h16M60 65h10" /><circle cx="65" cy="65" r="7" /><circle cx="95" cy="45" r="6" /><rect x="115" y="73" width="17" height="17" /></svg><p>任务开始后显示现场图像</p></div>}</div>
        <div className="execution-summary"><div><span>当前子目标</span><strong>{state.subgoal || '等待目标'}</strong></div><div><span>代码执行</span><strong>{executionStatuses[active?.execution || last?.execution] || (state.episode ? '空闲' : '未开始')}</strong></div><div><span>目标验证</span><strong className={`effect ${state.verification?.status || 'unknown'}`}>{effects[state.verification?.status || 'unknown']}</strong></div></div>
        {state.remaining_steps?.length > 0 && <ol className="remaining">{state.remaining_steps.map((step: string, i: number) => <li key={i}>{step}</li>)}</ol>}
        {used && <div className="budgets"><span>模型 {used.decisions}/12</span><span>接口 {used.calls}/40</span><span>运动步 {used.motion_steps}/1500</span><span>耗时 {Math.round(used.wall_seconds)}s</span><span>等待步 {used.idle_steps}</span></div>}
        <details className="records"><summary>代码与执行记录 <span>{state.events?.length || 0} 条事件</span></summary>{state.history?.filter((h: Json) => h.code).map((h: Json, i: number) => <div className="segment" key={i}><p>{h.subgoal} · {executionStatuses[h.execution] || h.execution}</p><p>阶段验证：{effects[h.stage_verification?.status || 'unknown']} · 最终目标：{effects[h.effect || 'unknown']}</p><pre>{h.code}</pre>{h.error && <p className="error-text">{h.error}</p>}{h.feedback && <details><summary>失败与未知证据</summary><pre>{JSON.stringify(h.feedback, null, 2)}</pre></details>}</div>)}<ol className="timeline">{state.events?.map((event: Json) => <li key={event.id}><time>{new Date(event.time * 1000).toLocaleTimeString()}</time><span>{event.kind}</span><details><summary>证据</summary><pre>{JSON.stringify(event, null, 2)}</pre></details></li>)}</ol></details>
        <footer className="scene-actions"><button disabled={!state.task_id || busy} onClick={() => void exportRecords()}>导出任务记录</button><button disabled={!hasEpisode || busy} onClick={() => void act(() => request('/robot/close', {}))}>结束场景</button></footer>
      </section>
    </main>
    {openSettings && <div className="modal-backdrop"><section className="settings" role="dialog" aria-modal="true" aria-label="模型与 CaP 连接设置"><div className="panel-heading"><h2>模型与 CaP 连接</h2><button aria-label="关闭设置" onClick={() => setOpenSettings(false)}>×</button></div><label>模型渠道<select value={channel} onChange={event => chooseChannel(event.target.value)}>{settings?.channels.map((c: Json) => <option value={c.id} key={c.id}>{c.label}</option>)}{channel && !settings?.channels.some((c: Json) => c.id === channel) && <option value={channel}>新渠道</option>}<option value="__new__">新增自定义渠道</option></select></label>{!settings?.channels.find((c: Json) => c.id === channel)?.builtin && <><label>渠道名称<input value={channelLabel} onChange={event => setChannelLabel(event.target.value)} /></label><label>模型接口地址<input value={baseURL} placeholder="https://provider.example/v1" onChange={event => setBaseURL(event.target.value)} /></label><label>接口协议<select value={protocol} onChange={event => setProtocol(event.target.value)}><option value="openai-completions">OpenAI Completions</option><option value="openai-responses">OpenAI Responses</option></select></label></>}<label>模型名称<input value={model} onChange={event => setModel(event.target.value)} /></label><label>模型密钥<input type="password" autoComplete="off" value={key} placeholder="留空保留已有密钥" onChange={event => setKey(event.target.value)} /></label><label>CaP 服务地址<input value={runtimeURL} onChange={event => setRuntimeURL(event.target.value)} /></label><label>CaP 访问令牌<input type="password" autoComplete="off" value={token} placeholder="留空保留已有令牌" onChange={event => setToken(event.target.value)} /></label><p>模型密钥保存在本机加密存储。修改连接前请结束当前场景。</p><button className="primary" disabled={busy || hasEpisode} onClick={() => void saveSettings()}>保存设置</button></section></div>}
  </div>;
}
