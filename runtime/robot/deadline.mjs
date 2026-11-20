// Bound the whole operation, including response-body parsing. An uncooperative
// provider may ignore AbortSignal; its late value must never release the wait.
export async function boundedOperation(operation, { signal, milliseconds, label = 'Operation' }) {
  const controller = new AbortController();
  const combined = signal ? AbortSignal.any([signal, controller.signal]) : controller.signal;
  const timeout = new Error(`${label} timed out`);
  timeout.code = 'OPERATION_TIMEOUT';
  let rejectAbort;
  const aborted = new Promise((_resolve, reject) => { rejectAbort = reject; });
  const onAbort = () => rejectAbort(combined.reason || new DOMException('Aborted', 'AbortError'));
  combined.addEventListener('abort', onAbort, { once: true });
  const timer = setTimeout(() => controller.abort(timeout), Math.max(1, Math.floor(milliseconds)));
  try {
    combined.throwIfAborted();
    return await Promise.race([Promise.resolve().then(() => { combined.throwIfAborted(); return operation(combined); }), aborted]);
  } finally {
    clearTimeout(timer);
    combined.removeEventListener('abort', onAbort);
  }
}
