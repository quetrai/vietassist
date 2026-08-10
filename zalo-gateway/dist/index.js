import { createServer } from "node:http";
import { timingSafeEqual } from "node:crypto";
import { ThreadType, Zalo } from "zca-js";
import { DEFAULT_ZALO_MAX_LEN, markdownToZalo, splitFormattedMessage, } from "./format.js";
const bridgeUrl = process.env.BRIDGE_URL ?? "http://127.0.0.1:10000/bridge/events";
const sessionUrl = process.env.ZALO_SESSION_URL ?? "http://127.0.0.1:10000/bridge/zalo-session";
const qrUrl = process.env.ZALO_QR_URL ?? "http://127.0.0.1:10000/bridge/zalo-qr";
const loginResultUrl = process.env.ZALO_LOGIN_RESULT_URL ?? "http://127.0.0.1:10000/bridge/zalo-login-result";
const bridgeSecret = process.env.BRIDGE_SECRET ?? "";
const defaultUserAgent = process.env.ZALO_USER_AGENT ?? "Mozilla/5.0";
const controlPort = Number(process.env.ZALO_CONTROL_PORT ?? "9901");
if (!Number.isInteger(controlPort) || controlPort < 1024 || controlPort > 65535) {
    throw new Error("ZALO_CONTROL_PORT không hợp lệ");
}
const MAX_SEND_BODY_BYTES = 200_000;
const seen = new Set();
const pending = new Set();
if (!bridgeSecret)
    throw new Error("Thiếu BRIDGE_SECRET");
const threadQueues = new Map();
function enqueueForThread(threadId, task) {
    const previous = threadQueues.get(threadId) ?? Promise.resolve();
    let next;
    next = previous
        .catch(() => undefined)
        .then(task)
        .finally(() => {
        if (threadQueues.get(threadId) === next)
            threadQueues.delete(threadId);
    });
    threadQueues.set(threadId, next);
}
function remember(id) {
    if (seen.has(id) || pending.has(id))
        return false;
    pending.add(id);
    return true;
}
function markProcessed(id) {
    pending.delete(id);
    seen.add(id);
    if (seen.size > 5000)
        seen.delete(seen.values().next().value);
}
function markFailed(id) {
    pending.delete(id);
}
async function postJson(url, payload) {
    const response = await fetch(url, {
        method: "POST",
        headers: { "content-type": "application/json", "x-bridge-secret": bridgeSecret },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(20000),
    });
    if (!response.ok)
        throw new Error(`HTTP ${response.status} khi POST ${url}`);
    return response.json();
}
async function bridge(payload) {
    let lastError;
    for (let attempt = 1; attempt <= 3; attempt += 1) {
        try {
            const response = await fetch(bridgeUrl, {
                method: "POST",
                headers: { "content-type": "application/json", "x-bridge-secret": bridgeSecret },
                body: JSON.stringify(payload),
                signal: AbortSignal.timeout(90000),
            });
            if (response.ok) {
                const result = (await response.json());
                return result.messages ?? [];
            }
            if (![502, 503, 504].includes(response.status)) {
                throw new Error(`Bridge HTTP ${response.status}`);
            }
            lastError = new Error(`Bridge HTTP ${response.status}`);
        }
        catch (error) {
            lastError = error;
        }
        await new Promise((resolve) => setTimeout(resolve, attempt * 1000));
    }
    throw lastError instanceof Error ? lastError : new Error("Bridge request failed");
}
async function fetchSavedSession() {
    const response = await fetch(sessionUrl, {
        headers: { "x-bridge-secret": bridgeSecret },
        signal: AbortSignal.timeout(20000),
    });
    if (!response.ok)
        return null;
    const data = (await response.json());
    if (!data.cookie_json || !data.imei)
        return null;
    let cookie;
    try {
        cookie = JSON.parse(data.cookie_json);
    }
    catch {
        throw new Error("Zalo session cookie is invalid JSON");
    }
    return {
        cookie,
        imei: data.imei,
        userAgent: data.user_agent || defaultUserAgent,
    };
}
async function sendMessageWithRetry(api, text, threadId, type) {
    const formatted = markdownToZalo(text);
    const chunks = splitFormattedMessage(formatted, DEFAULT_ZALO_MAX_LEN);
    for (const chunk of chunks) {
        await sendFormattedMessage(api, chunk, threadId, type);
    }
}
async function sendFormattedMessage(api, formatted, threadId, type) {
    // Do not blindly retry sendMessage. A timeout/reset can happen after Zalo
    // accepted the message, which would make a retry duplicate user-visible
    // content. Exactly-once delivery is not exposed by zca-js, so the safe
    // default is one send attempt and explicit failure propagation.
    await api.sendMessage({ msg: formatted.text, styles: formatted.styles }, threadId, type);
}
function attachListener(api) {
    api.listener.on("message", (message) => {
        if (message.isSelf)
            return;
        const isGroup = message.type === ThreadType.Group;
        const threadId = String(message.threadId);
        const senderId = String(message.data?.uidFrom ?? "");
        const messageId = String(message.data?.msgId ?? message.data?.cliMsgId ?? "");
        const rawContent = message.data?.content;
        let kind;
        let text = "";
        let imageUrl = null;
        if (typeof rawContent === "string") {
            text = rawContent.trim();
            if (!text)
                return;
            kind = isGroup ? "group" : "direct";
        }
        else if (!isGroup && message.data?.msgType === "chat.photo") {
            const photo = rawContent;
            if (!photo?.href)
                return;
            kind = "image";
            text = photo.description ?? "";
            imageUrl = photo.href;
        }
        else {
            return;
        }
        if (!messageId || !remember(messageId))
            return;
        enqueueForThread(threadId, async () => {
            try {
                const replies = await bridge({
                    event_id: `${isGroup ? "g" : "d"}:${threadId}:${messageId}`,
                    kind,
                    sender_id: senderId,
                    sender_name: String(message.data?.dName ?? ""),
                    text,
                    image_url: imageUrl,
                    group_id: isGroup ? threadId : null,
                    message_id: messageId,
                });
                for (const reply of replies) {
                    await sendMessageWithRetry(api, reply, threadId, isGroup ? ThreadType.Group : ThreadType.User);
                }
                markProcessed(messageId);
            }
            catch (error) {
                markFailed(messageId);
                console.error("[zalo] event failed", error);
            }
        });
    });
    api.listener.start();
    console.log(`[zalo] listener started account=${String(api.getOwnId())}`);
}
async function loginWithSession(creds) {
    const zalo = new Zalo({ selfListen: false, checkUpdate: false, logging: false });
    const api = (await zalo.login(creds));
    attachListener(api);
    return api;
}
async function loginWithQr() {
    const zalo = new Zalo({ selfListen: false, checkUpdate: false, logging: false });
    const api = (await zalo.loginQR(undefined, (event) => {
        void (async () => {
            try {
                if (event?.data?.image) {
                    await postJson(qrUrl, { image_base64: event.data.image });
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
            }
            catch (error) {
                console.error("[zalo] lỗi khi xử lý sự kiện QR login", error);
            }
        })();
    }));
    attachListener(api);
    return api;
}
function secretMatches(provided) {
    if (typeof provided !== "string")
        return false;
    const a = Buffer.from(provided);
    const b = Buffer.from(bridgeSecret);
    if (a.length !== b.length)
        return false;
    return timingSafeEqual(a, b);
}
function startControlServer(getApi, triggerLogin) {
    const server = createServer((req, res) => {
        if (!secretMatches(req.headers["x-bridge-secret"])) {
            res.writeHead(403).end();
            return;
        }
        if (req.method === "POST" && req.url === "/login/start") {
            triggerLogin();
            res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({ ok: true }));
            return;
        }
        if (req.method === "POST" && req.url === "/send") {
            const api = getApi();
            if (!api) {
                res.writeHead(409, { "content-type": "application/json" }).end(JSON.stringify({ ok: false, error: "Zalo B chưa đăng nhập, dùng /zalologin trên Telegram trước." }));
                return;
            }
            let body = "";
            let bodyBytes = 0;
            let rejected = false;
            req.on("data", (chunk) => {
                if (rejected)
                    return;
                bodyBytes += chunk.length;
                if (bodyBytes > MAX_SEND_BODY_BYTES) {
                    rejected = true;
                    res.writeHead(413, { "content-type": "application/json" }).end(JSON.stringify({ ok: false, error: "Payload quá lớn" }));
                    req.destroy();
                    return;
                }
                body += chunk;
            });
            req.on("end", () => {
                if (rejected)
                    return;
                void (async () => {
                    try {
                        const { to, text } = JSON.parse(body);
                        if (!to || !text) {
                            res.writeHead(400).end();
                            return;
                        }
                        await sendMessageWithRetry(api, text, to, ThreadType.User);
                        res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({ ok: true }));
                    }
                    catch (error) {
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
async function main() {
    let api = null;
    let loginInFlight = false;
    const saved = await fetchSavedSession().catch((error) => {
        console.error("[zalo] không lấy được session đã lưu, sẽ chờ /zalologin", error);
        return null;
    });
    if (saved) {
        try {
            api = await loginWithSession(saved);
            console.log("[zalo] đăng nhập lại bằng session đã lưu — không cần quét QR");
        }
        catch (error) {
            console.error("[zalo] session đã lưu không dùng được nữa (có thể hết hạn)", error);
            api = null;
        }
    }
    else {
        console.log("[zalo] chưa có session nào — chờ lệnh /zalologin từ Telegram để sinh mã QR");
    }
    const triggerLogin = () => {
        if (loginInFlight || api)
            return;
        loginInFlight = true;
        void loginWithQr()
            .then(async (newApi) => {
            api = newApi;
            console.log("[zalo] đăng nhập QR thành công");
            await postJson(loginResultUrl, { ok: true }).catch(() => { });
        })
            .catch(async (error) => {
            console.error("[zalo] đăng nhập QR thất bại", error);
            await postJson(loginResultUrl, { ok: false, message: String(error) }).catch(() => { });
        })
            .finally(() => {
            loginInFlight = false;
        });
    };
    startControlServer(() => api, triggerLogin);
}
void main();
