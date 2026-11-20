// Persist only bounded diagnostics, never provider response bodies or credentials.
export function safeModelDetail(value, secrets = []) {
  let text = String(value || "");
  for (const secret of secrets.filter(Boolean))
    text = text.replaceAll(String(secret), "[redacted]");
  return text
    .replace(/Bearer\s+[^\s,;"']+/gi, "Bearer [redacted]")
    .replace(/sk-[a-zA-Z0-9_-]+/g, "[redacted]")
    .replace(/([?&](?:key|api_key|token)=)[^\s&#"']+/gi, "$1[redacted]")
    .slice(0, 500);
}

function modelError(message, fields = {}, cause) {
  return Object.assign(new Error(message, cause ? { cause } : undefined), {
    code: fields.code || "MODEL_ERROR",
    category: fields.category || "unknown",
    phase: fields.phase || "request",
    retryable: fields.retryable === true,
    action: fields.action || "检查模型渠道配置后重试",
    ...fields,
  });
}

function nestedCode(error) {
  let current = error;
  for (let depth = 0; current && depth < 5; depth += 1) {
    if (current.code) return String(current.code).toUpperCase();
    current = current.cause;
  }
  return "";
}

function retryAfterMs(value) {
  if (!value) return undefined;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) return Math.round(seconds * 1000);
  const date = Date.parse(value);
  return Number.isFinite(date) ? Math.max(0, date - Date.now()) : undefined;
}

export function connectionFailure(error, { phase = "connect" } = {}) {
  const transportCode = nestedCode(error);
  const source = `${transportCode} ${error?.name || ""} ${error?.message || ""}`;
  let category = "connection_failed";
  let label = "无法连接模型服务";
  let action = "检查网络、代理和模型接口地址后重试";

  if (/ENOTFOUND|EAI_AGAIN|DNS/i.test(source)) {
    category = "dns_failed";
    label = "模型服务域名解析失败";
    action = "检查接口域名、DNS 和代理设置";
  } else if (/ECONNREFUSED/i.test(source)) {
    category = "connection_refused";
    label = "模型服务拒绝连接";
    action = "确认接口地址、端口及本地代理是否正在运行";
  } else if (/CERT|TLS|SSL|SELF_SIGNED|UNABLE_TO_VERIFY/i.test(source)) {
    category = "tls_failed";
    label = "模型服务安全连接失败";
    action = "检查系统时间、证书、HTTPS 代理及服务端证书链";
  } else if (/ETIMEDOUT|TIMEOUT|TimeoutError/i.test(source)) {
    category = "connection_timeout";
    label = "连接模型服务超时";
    action = "检查网络和代理，或稍后重试";
  } else if (/ECONNRESET|EPIPE|UND_ERR_SOCKET|terminated|socket/i.test(source)) {
    category = "connection_reset";
    label = phase === "stream" ? "模型响应连接被中途重置" : "模型服务连接被重置";
    action = "检查网络、代理或网关超时设置后重试";
  }

  return modelError(
    `${label}${transportCode ? `（${safeModelDetail(transportCode)}）` : ""}；已保存执行记录，可继续对话`,
    {
      code: "MODEL_CONNECTION_ERROR",
      category,
      phase,
      transportCode: safeModelDetail(transportCode),
      retryable: true,
      action,
    },
    error,
  );
}

export function streamFailure(error, { requestId } = {}) {
  if (error?.code?.startsWith("MODEL_")) return error;
  const transportCode = nestedCode(error);
  if (transportCode || /fetch|socket|network|terminated/i.test(error?.message || ""))
    return connectionFailure(error, { phase: "stream" });
  return modelError(
    "模型响应流在完成前结束：未收到协议结束事件；可能是网关协议不兼容、网关超时或连接提前关闭；已保存执行记录，可继续对话",
    {
      code: "MODEL_STREAM_INTERRUPTED",
      category: "stream_interrupted",
      phase: "stream",
      requestId: safeModelDetail(requestId),
      retryable: true,
      action: "重试；若稳定复现，请确认模型网关完整支持所选 API 的流式结束事件",
    },
    error,
  );
}

export function protocolFailure(message, { phase = "parse", requestId } = {}) {
  return modelError(
    `${message}；已保存执行记录，可继续对话`,
    {
      code: "MODEL_PROTOCOL_ERROR",
      category: "protocol_error",
      phase,
      requestId: safeModelDetail(requestId),
      retryable: false,
      action: "检查渠道 API 类型与模型网关协议是否匹配",
    },
  );
}

export function providerFailure(payload = {}, options = {}) {
  const { status, requestId } = options;
  if (!payload || typeof payload !== "object") payload = {};
  const response = payload.response || payload;
  const detail = response.error || payload.error || response.incomplete_details || {};
  const providerCode = String(detail.code || detail.reason || payload.code || "");
  const normalized = providerCode.toLowerCase();
  const message = safeModelDetail(
    typeof detail === "string" ? detail : detail.message || payload.message,
  );

  let category = "provider_error";
  let label = "模型服务返回失败";
  let action = "根据服务商错误信息检查配置后重试";
  let retryable = !status || status === 408 || status === 429 || status >= 500;

  if (/rate_limit|too_many_requests|tokens_per_minute|requests_per_minute/.test(normalized) || status === 429) {
    category = "rate_limited";
    label = "模型请求速率受限";
    action = "等待限流窗口恢复，缩短上下文，或切换模型、区域和服务通道";
    retryable = true;
  }
  if (/insufficient_quota|quota_exceeded|billing|credit/.test(normalized)) {
    category = "quota_exhausted";
    label = "模型额度不足";
    action = "检查账户余额、订阅和区域配额";
    retryable = false;
  } else if (/context_length|max_context|context_window/.test(normalized)) {
    category = "context_too_long";
    label = "模型上下文超限";
    action = "新建对话或压缩历史、日志和工具输出后重试";
    retryable = false;
  } else if (/auth|api_key|invalid_key/.test(normalized) || status === 401) {
    category = "authentication_failed";
    label = "模型认证失败";
    action = "检查 API Key 是否正确、有效且属于当前服务地址";
    retryable = false;
  } else if (/permission|forbidden|access_denied/.test(normalized) || status === 403) {
    category = "permission_denied";
    label = "模型访问权限不足";
    action = "检查 API Key 权限、模型授权、区域和组织设置";
    retryable = false;
  } else if (/model_not_found|deployment_not_found|not_found/.test(normalized) || status === 404) {
    category = "model_not_found";
    label = "模型或接口不存在";
    action = "检查模型名称、部署名称、API 类型和接口地址";
    retryable = false;
  } else if (/content_filter|safety|moderation/.test(normalized)) {
    category = "content_filtered";
    label = "模型安全策略拒绝了请求";
    action = "调整请求内容，或检查服务商的内容安全策略";
    retryable = false;
  } else if (/max_output_tokens|length/.test(normalized)) {
    category = "output_limit";
    label = "模型输出达到上限";
    action = "要求模型分段输出，或提高允许的输出上限";
    retryable = false;
  } else if (status >= 500 || /server_error|overloaded|unavailable/.test(normalized)) {
    category = "provider_unavailable";
    label = "模型服务端暂时异常";
    action = "稍后重试，或切换模型、区域和服务通道";
    retryable = true;
  } else if (status === 408 || /timeout/.test(normalized)) {
    category = "provider_timeout";
    label = "模型服务端处理超时";
    action = "稍后重试，缩短上下文，或检查网关超时设置";
    retryable = true;
  } else if (response.status === "incomplete" || payload.type === "response.incomplete") {
    category = "incomplete_response";
    label = "模型响应未完成";
    action = "根据服务商返回的 incomplete 原因调整上下文或输出长度后重试";
    retryable = false;
  }

  const retryDelay = retryAfterMs(options.retryAfter);
  return modelError(
    `${label}${providerCode ? `（${safeModelDetail(providerCode)}）` : status ? `（HTTP ${status}）` : ""}${message ? `：${message}` : ""}；已保存执行记录，可继续对话`,
    {
      code: "MODEL_PROVIDER_ERROR",
      category,
      phase: "provider",
      providerCode: safeModelDetail(providerCode),
      httpStatus: status,
      requestId: safeModelDetail(requestId || response.id),
      retryAfterMs: retryDelay,
      retryable,
      action,
    },
  );
}

export function modelDiagnostic(error, secrets = []) {
  return {
    code: safeModelDetail(error.code || "MODEL_UNKNOWN_ERROR", secrets),
    category: safeModelDetail(error.category || "unknown", secrets),
    phase: safeModelDetail(error.phase || "request", secrets),
    providerCode: safeModelDetail(error.providerCode, secrets),
    transportCode: safeModelDetail(error.transportCode, secrets),
    httpStatus: error.httpStatus,
    requestId: safeModelDetail(error.requestId, secrets),
    retryAfterMs: Number.isFinite(error.retryAfterMs) ? error.retryAfterMs : undefined,
    message: safeModelDetail(error.message, secrets),
    action: safeModelDetail(error.action || "检查诊断信息后重试", secrets),
    retryable: error.retryable === true,
    occurredAt: new Date().toISOString(),
  };
}
