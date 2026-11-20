import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { RobotWorkbench } from './RobotWorkbench';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
test.each([false, true])('holding monitor distinguishes requested hold from authoritative confirmation: %s', async confirmed => {
  Element.prototype.scrollIntoView = vi.fn();
  window.ROBOT_DESKTOP = { request: async request => {
    if (request.path === '/robot/settings') return { channels: [] };
    return { status: 'thinking', messages: [], events: [], history: [],
      episode: { status: 'ready', runtime_alert: { code: 'PUBLIC_HOLDING_LOSS', hold_confirmed: confirmed } },
      verification: { status: 'unknown' } };
  }, runtimeStatus: async () => ({ state: 'ready' }), retryRuntime: async () => ({}) };
  render(<RobotWorkbench />);
  expect(await screen.findByText(confirmed ? /控制器已确认保持/ : /正在等待控制器确认保持/)).toBeTruthy();
  if (!confirmed) expect(screen.queryByText(/控制器已确认保持/)).toBeNull();
  expect(screen.getByText('证据不足')).toBeTruthy();
});
test('robot page removes generic tools and sends structured task without self-reporting success', async () => {
  Element.prototype.scrollIntoView = vi.fn();
  const calls: any[] = [];
  window.ROBOT_DESKTOP = { request: async request => {
    calls.push(request);
    if (request.path === '/robot/settings') return { activeChannel: 'x', runtimeURL: 'http://localhost:8120', channels: [{ id: 'x', label: '模型渠道', model: 'vision' }] };
    if (request.path === '/robot/start') return { accepted: true };
    return { status: 'idle', messages: [], events: [], history: [], verification: { status: 'unknown' } };
  }, runtimeStatus: async () => ({ state: 'ready' }), retryRuntime: async () => ({}) };
  render(<RobotWorkbench />);
  await screen.findByRole('button', { name: '开始任务' });
  expect(screen.queryByText('SSH')).toBeNull();
  expect(screen.queryByText('资源商店')).toBeNull();
  expect(screen.getByText('证据不足')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '开始任务' }));
  await waitFor(() => expect(calls.some(r => r.path === '/robot/start')).toBe(true));
  const start = calls.find(r => r.path === '/robot/start');
  expect(start.body.task).toBe('cube_lifting');
  expect(start.body.text).toContain('红色方块');
});
test('stop remains available during execution and unknown stays distinct', async () => {
  Element.prototype.scrollIntoView = vi.fn();
  const calls: string[] = [];
  window.ROBOT_DESKTOP = { request: async request => {
    calls.push(request.path);
    if (request.path === '/robot/settings') return { activeChannel: 'x', runtimeURL: 'http://localhost:8120', channels: [{ id: 'x', label: '渠道', model: 'vision' }] };
    return { task_id: 't', status: 'executing', messages: [], events: [], history: [], episode: { status: 'ready', active_segment: 's', segments: { s: { execution: 'running' } } }, verification: { status: 'unknown' } };
  }, runtimeStatus: async () => ({ state: 'ready' }), retryRuntime: async () => ({}) };
  render(<RobotWorkbench />);
  await waitFor(() => expect((screen.getByRole('button', { name: '停止' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '停止' }));
  await waitFor(() => expect(calls.includes('/robot/stop')).toBe(true));
  expect(screen.getByText('证据不足')).toBeTruthy();
});

test('verified intermediate stage does not appear as a verified final task', async () => {
  Element.prototype.scrollIntoView = vi.fn();
  window.ROBOT_DESKTOP = { request: async request => {
    if (request.path === '/robot/settings') return { activeChannel: 'x', channels: [{ id: 'x', label: '渠道', model: 'vision' }] };
    return { status: 'thinking', messages: [], events: [], verification: { status: 'failed' }, history: [{
      code: 'lift()', subgoal: '抬起', execution: 'completed', effect: 'failed', stage_verification: { status: 'verified' }
    }] };
  }, runtimeStatus: async () => ({ state: 'ready' }), retryRuntime: async () => ({}) };
  render(<RobotWorkbench />);
  expect(await screen.findByText('阶段验证：证据支持 · 最终目标：未达到目标')).toBeTruthy();
  expect(screen.queryByText('目标已验证')).toBeNull();
});
