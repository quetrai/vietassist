import {createServer} from "node:http";
import {timingSafeEqual} from "node:crypto";
import {ThreadType, Zalo, type Credentials} from "zca-js";
import {
  DEFAULT_ZALO_MAX_LEN,
  markdownToZalo,
  splitFormattedMessage,
  type FormattedZaloMessage,
  type ZaloStyle,
} from "./format.js";

const bridgeUrl = process.env.BRIDGE_URL ?? "http://127.0.0.1:10000/bridge/events";
const sessionUrl = process.env.ZALO_SESSION_URL ?? "http://127.0.0.1:10000/bridge/zalo-session";
const qrUrl = process.env.ZALO_QR_URL ?? "http://127.0.0.1:10000/bridge/zalo-qr";
const loginResultUrl =
  process.env.ZALO_LOGIN_RESULT_URL ?? "http://127.0.0.1:10000/bridge/zalo-login-result";
const bridgeSecret = process.env.BRIDGE_SECRET ?? "";
const defaultUserAgent = process.env.ZALO_USER_AGENT ?? "Mozilla/5.0";
const controlPort = Number(process.env.ZALO_CONTROL_PORT ?? "9901");
if (!Number.isInteger(controlPort) || controlPort < 1024 || controlPort > 65535) {
  throw new Error("ZALO_CONTROL_PORT không hợp lệ");
}
const MAX_SEND_BODY_BYTES = 200_000;
const seen = new Set<string>();
const pending = new Set<string>();

if (!bridgeSecret) throw new Error("Thiếu BRIDGE_SECRET");
const threadQueues = new Map<string, Promise<void>>();

function enqueueForThread(threadId: string, task: () => Promise<void>): void {
  const previous = threadQueues.get(threadId) ?? Promise.resolve();
  let next: Promise<void>;
  next = previous
    .catch(() => undefined)
    .then(task)
    .finally(() => {
      if (threadQueues.get(threadId) === next) threadQueues.delete(threadId);
    });
  threadQueues.set(threadId, next);
}

function remember(id: string): boolean {
  if (seen.has(id) || pending.has(id)) return false;
  pending.add(id);
  return true;
}

function markProcessed(id: string): void {
  pending.delete(id);
  seen.add(id);
  if (seen.size > 5000) seen.delete(seen.values().next().value!);
}

function markFailed(id: string): void {
  pending.delete(id);
}

async function postJson(url: string, payload: Record<string, unknown>): Promise<unknown> {
  const response = await fetch(url, {
    method: "POST",
    headers: {"content-type": "application/json", "x-bridge-secret": bridgeSecret},
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(20000),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status} khi POST ${url}`);
  return response.json();
}

async function bridge(payload: Record<string, unknown>): Promise<string[]> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      const response = await fetch(bridgeUrl, {
        method: "POST",
        headers: {"content-type": "application/json", "x-bridge-secret": bridgeSecret},
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(90000),
      });
      if (response.ok) {
        const result = (await response.json()) as {messages?: string[]};
        return result.messages ?? [];
      }
      if (![502, 503, 504].includes(response.status)) {
        throw new Error(`Bridge HTTP ${response.status}`);
      }
      lastError = new Error(`Bridge HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, attempt * 1000));
  }
  throw lastError instanceof Error ? lastError : new Error("Bridge request failed");
}

async function fetchSavedSession(): Promise<Credentials | null> {
  const maxAttempts = 15;
  const retryDelayMs = 2000;
  let lastError: unknown;

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const response = await fetch(sessionUrl, {
        headers: {"x-bridge-secret": bridgeSecret},
        signal: AbortSignal.timeout(5000),
      });

      if (response.ok) {
        const data = (await response.json()) as {
          cookie_json?: string;
          imei?: string;
          user_agent?: string;
        };
        if (!data.cookie_json || !data.imei) return null;

        let cookie: unknown;
        try {
          cookie = JSON.parse(data.cookie_json);
        } catch {
          throw new Error("Zalo session cookie is invalid JSON");
        }

        return {
          cookie,
          imei: data.imei,
          userAgent: data.user_agent || defaultUserAgent,
        };
      }

      if (![502, 503, 504].includes(response.status)) return null;
      lastError = new Error(`Backend HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }

    if (attempt < maxAttempts) {
      console.warn(
        `[zalo] backend chưa sẵn sàng, retry ${attempt}/${maxAttempts - 1} sau ${retryDelayMs}ms`,
      );
      await new Promise((resolve) => setTimeout(resolve, retryDelayMs));
    }
  }

  throw lastError instanceof Error ? lastError : new Error("Backend unavailable");
}

interface IncomingMessage {
  isSelf?: boolean;
  type: ThreadType;
  threadId: string | number;
  data?: {
    msgType?: string;
    content?: unknown;
    uidFrom?: string | number;
    msgId?: string | number;
    cliMsgId?: string | number;
    dName?: string;
  };
}

interface PhotoContent {
  href?: string;
  description?: string;
}

interface ZaloApi {
  listener: {
    on(event: "message", handler: (message: IncomingMessage) => void): void;
    start(): void;
  };
  getOwnId(): string | number;
  getGroupInfo(groupId: string | string[]): Promise<{
    gridInfoMap: Record<string, {name?: string}>;
  }>;
  sendMessage(payload: {msg: string; styles?: ZaloStyle[]}, threadId: string, type: ThreadType): Promise<unknown>;
}

// Cache tên nhóm Zalo theo threadId, tránh gọi getGroupInfo() lại cho mỗi tin
// nhắn (rate limit + tốn thời gian). Bot chỉ cần tên nhóm để hiển thị thân
// thiện ở /nhom bên Python, không cần realtime tuyệt đối - refresh theo TTL
// là đủ, vẫn bắt được khi ai đó đổi tên nhóm sau một thời gian.
const _GROUP_NAME_TTL_MS = 6 * 60 * 60 * 1000; // 6 giờ
const groupNameCache = new Map<string, {name: string; fetchedAt: number}>();

async function resolveGroupName(api: ZaloApi, threadId: string): Promise<string> {
  const cached = groupNameCache.get(threadId);
  const now = Date.now();
  if (cached && now - cached.fetchedAt < _GROUP_NAME_TTL_MS) return cached.name;
  try {
    const info = await api.getGroupInfo(threadId);
    const name = info?.gridInfoMap?.[threadId]?.name ?? "";
    groupNameCache.set(threadId, {name, fetchedAt: now});
    return name;
  } catch (error) {
    console.error("[zalo] getGroupInfo thất bại", error);
    // Giữ giá trị cache cũ (nếu có) thay vì xoá, để lần sau vẫn còn thứ gì đó
    // để hiển thị thay vì trắng tên; không cache lỗi để lần sau thử lại sớm.
    return cached?.name ?? "";
  }
}

async function sendMessageWithRetry(
  api: ZaloApi,
  text: string,
  threadId: string,
  type: ThreadType,
): Promise<void> {
  const formatted = markdownToZalo(text);
  const chunks = splitFormattedMessage(formatted, DEFAULT_ZALO_MAX_LEN);
  for (const chunk of chunks) {
    await sendFormattedMessage(api, chunk, threadId, type);
  }
}

async function sendFormattedMessage(
  api: ZaloApi,
  formatted: FormattedZaloMessage,
  threadId: string,
  type: ThreadType,
): Promise<void> {
  // Do not blindly retry sendMessage. A timeout/reset can happen after Zalo
  // accepted the message, which would make a retry duplicate user-visible
  // content. Exactly-once delivery is not exposed by zca-js, so the safe
  // default is one send attempt and explicit failure propagation.
  await api.sendMessage(
    {msg: formatted.text, styles: formatted.styles},
    threadId,
    type,
  );
}

function attachListener(api: ZaloApi): void {
  api.listener.on("message", (message) => {
    if (message.isSelf) return;
    const isGroup = message.type === ThreadType.Group;
    const threadId = String(message.threadId);
    const senderId = String(message.data?.uidFrom ?? "");
    const messageId = String(message.data?.msgId ?? message.data?.cliMsgId ?? "");
    const rawContent = message.data?.content;

    let kind: "direct" | "group" | "image";
    let text = "";
    let imageUrl: string | null = null;

    if (typeof rawContent === "string") {
      text = rawContent.trim();
      if (!text) return;
      kind = isGroup ? "group" : "direct";
    } else if (!isGroup && message.data?.msgType === "chat.photo") {
      const photo = rawContent as PhotoContent | undefined;
      if (!photo?.href) return;
      kind = "image";
      text = photo.description ?? "";
      imageUrl = photo.href;
    } else {
      return;
    }
    if (!messageId || !remember(messageId)) return;

    enqueueForThread(threadId, async () => {
      try {
        const groupName = isGroup ? await resolveGroupName(api, threadId) : null;
        const replies = await bridge({
          event_id: `${isGroup ? "g" : "d"}:${threadId}:${messageId}`,
          kind,
          sender_id: senderId,
          sender_name: String(message.data?.dName ?? ""),
          text,
          image_url: imageUrl,
          group_id: isGroup ? threadId : null,
          group_name: groupName,
          message_id: messageId,
        });
        for (const reply of replies) {
          await sendMessageWithRetry(
            api,
            reply,
            threadId,
            isGroup ? ThreadType.Group : ThreadType.User,
          );
        }
        markProcessed(messageId);
      } catch (error) {
        markFailed(messageId);
        console.error("[zalo] event failed", error);
      }
    });
  });
  api.listener.start();
  console.log(`[zalo] listener started account=${String(api.getOwnId())}`);
}

async function loginWithSession(creds: Credentials): Promise<ZaloApi> {
  const zalo = new Zalo({selfListen: false, checkUpdate: false, logging: false});
  const api = (await zalo.login(creds)) as unknown as ZaloApi;
  attachListener(api);
  return api;
}

interface QrLoginEvent {
  data?: {
    image?: string;
    cookie?: unknown;
    imei?: string;
    userAgent?: string;
  };
}

async function loginWithQr(): Promise<ZaloApi> {
  const zalo = new Zalo({selfListen: false, checkUpdate: false, logging: false});
  const api = (await zalo.loginQR(undefined, (event: QrLoginEvent) => {
    void (async () => {
      try {
        if (event?.data?.image) {
          await postJson(qrUrl, {image_base64: event.data.image});
        }
        const cookie = event?.data?.cookie;
        const imei = event?.data?.imei;
        if (cookie && imei) {
          await postJson(sessionUrl, {
            cookie_json: JSON.stringify(cookie),
            imei,
            user_agent: event?.data?.userAgent || defaultUserAgent,
          });
        }
      } catch (error) {
        console.error("[zalo] lỗi khi xử lý sự kiện QR login", error);
      }
    })();
  })) as unknown as ZaloApi;
  attachListener(api);
  return api;
}

function secretMatches(provided: unknown): boolean {
  if (typeof provided !== "string") return false;
  const a = Buffer.from(provided);
  const b = Buffer.from(bridgeSecret);
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

function startControlServer(getApi: () => ZaloApi | null, triggerLogin: () => void): void {
  const server = createServer((req, res) => {
    if (!secretMatches(req.headers["x-bridge-secret"])) {
      res.writeHead(403).end();
      return;
    }
    if (req.method === "POST" && req.url === "/login/start") {
      triggerLogin();
      res.writeHead(200, {"content-type": "application/json"}).end(JSON.stringify({ok: true}));
      return;
    }
    if (req.method === "POST" && req.url === "/send") {
      const api = getApi();
      if (!api) {
        res.writeHead(409, {"content-type": "application/json"}).end(
          JSON.stringify({ok: false, error: "Zalo B chưa đăng nhập, dùng /zalologin trên Telegram trước."}),
        );
        return;
      }
      let body = "";
      let bodyBytes = 0;
      let rejected = false;
      req.on("data", (chunk: Buffer) => {
        if (rejected) return;
        bodyBytes += chunk.length;
        if (bodyBytes > MAX_SEND_BODY_BYTES) {
          rejected = true;
          res.writeHead(413, {"content-type": "application/json"}).end(
            JSON.stringify({ok: false, error: "Payload quá lớn"}),
          );
          req.destroy();
          return;
        }
        body += chunk;
      });
      req.on("end", () => {
        if (rejected) return;
        void (async () => {
          try {
            const {to, text} = JSON.parse(body) as {to?: string; text?: string};
            if (!to || !text) {
              res.writeHead(400).end();
              return;
            }
            await sendMessageWithRetry(api, text, to, ThreadType.User);
            res.writeHead(200, {"content-type": "application/json"}).end(JSON.stringify({ok: true}));
          } catch (error) {
            console.error("[zalo] control send failed", error);
            res.writeHead(500).end();
          }
        })();
      });
      return;
    }
    res.writeHead(404).end();
  });
  server.listen(controlPort, "127.0.0.1", () => {
    console.log(`[zalo] control server listening on 127.0.0.1:${controlPort}`);
  });
}

async function main(): Promise<void> {
  let api: ZaloApi | null = null;
  let loginInFlight = false;

  const triggerLogin = () => {
    if (loginInFlight || api) return;
    loginInFlight = true;
    void loginWithQr()
      .then(async (newApi) => {
        api = newApi;
        console.log("[zalo] đăng nhập QR thành công");
        await postJson(loginResultUrl, {ok: true}).catch(() => {});
      })
      .catch(async (error) => {
        console.error("[zalo] đăng nhập QR thất bại", error);
        await postJson(loginResultUrl, {ok: false, message: String(error)}).catch(() => {});
      })
      .finally(() => {
        loginInFlight = false;
      });
  };

  startControlServer(() => api, triggerLogin);

  const saved = await fetchSavedSession().catch((error) => {
    console.error("[zalo] không lấy được session đã lưu, sẽ chờ /zalologin", error);
    return null;
  });
  if (saved) {
    try {
      api = await loginWithSession(saved);
      console.log("[zalo] đăng nhập lại bằng session đã lưu — không cần quét QR");
    } catch (error) {
      console.error("[zalo] session đã lưu không dùng được nữa (có thể hết hạn)", error);
      api = null;
    }
  } else {
    console.log("[zalo] chưa có session nào — chờ lệnh /zalologin từ Telegram để sinh mã QR");
  }
}

void main();
