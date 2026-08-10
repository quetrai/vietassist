# VietAssist

Trợ lý AI đa kênh cho Telegram và Zalo: chat nhanh (Groq/OpenRouter, có fallback), tra
cứu có grounding qua Google Search (giá sản phẩm, tin vĩ mô), chuyển ảnh thành prompt
bằng Gemini vision, phân tích cổ phiếu Việt Nam theo policy deterministic (không để LLM
tự bịa số liệu), ghi chú, nhắc nhở, theo dõi danh mục đầu tư, và tóm tắt nhóm chat Zalo.

## Mục lục

- [Kiến trúc](#kiến-trúc)
- [Cài đặt local (dev)](#cài-đặt-local-dev)
- [Cấu hình biến môi trường](#cấu-hình-biến-môi-trường)
- [Sử dụng — lệnh Telegram](#sử-dụng--lệnh-telegram)
- [Sử dụng — Zalo](#sử-dụng--zalo)
- [Deploy production](#deploy-production)
- [Kiểm tra / CI thủ công](#kiểm-tra--ci-thủ-công)
- [Vận hành & xử lý sự cố](#vận-hành--xử-lý-sự-cố)
- [Giới hạn đã biết](#giới-hạn-đã-biết)

## Kiến trúc

- **Telegram:** single owner — chỉ `TELEGRAM_OWNER_ID` được bot trả lời. Đây là trợ lý cá
  nhân, không phải bot công khai nhiều người dùng.
- **Zalo:** một tài khoản Zalo "B" trung tâm chạy qua [zca-js](https://github.com/RFS-ADRENO/zca-js)
  (thư mục `zalo-gateway/`, Node.js/TypeScript). Người dùng Zalo "A" phải được Telegram
  owner cấp quyền qua `/zalopair` trước khi bot trả lời họ. Chỉ A admin (`/zaloadmin`) mới
  dùng được tính năng quản lý nhóm/tổng kết.
- **Chat:** Groq là provider chính, tự động rơi về OpenRouter nếu Groq lỗi/timeout, và
  Gemini là tầng fallback cuối cùng nếu cả hai đều lỗi/chưa cấu hình (để bot vẫn trả lời
  được thay vì im lặng). Với `/stock sâu` (deep report) thứ tự đảo ngược: OpenRouter trước,
  Groq fallback, Gemini vẫn là tầng cuối. Model mặc định (`.env.example`) đều là model có
  free tier: Groq dùng `openai/gpt-oss-20b` (nhanh, hợp chat tần suất cao), OpenRouter dùng
  `nvidia/nemotron-3-super-120b-a12b:free` (context dài, hợp phân tích sâu), Gemini dùng
  `gemini-3.6-flash`. Các model cũ (`llama-3.3-70b-versatile`, `gemini-2.5-flash`) đang bị
  nhà cung cấp khai tử dần trong năm 2026 — kiểm tra lại định kỳ nếu thấy provider báo lỗi
  "model decommissioned".
- **`/gia`, `/vimo`:** Google Gemini + Google Search grounding — fail closed (nếu không
  xác minh được qua tìm kiếm thật, bot nói rõ chưa tra được, không tự suy đoán).
- **`/prompt` và gửi ảnh:** văn bản dùng Groq/OpenRouter; ảnh dùng Gemini vision để viết
  prompt tái tạo. Hoạt động trên cả Telegram (ảnh + caption) và Zalo (ảnh gửi trực tiếp
  cho Zalo B, không hỗ trợ trong group).
- **Chứng khoán:** dữ liệu giá lấy từ DNSE (nguồn chính, có retry), tự động rơi về
  `vnstock`/VCI nếu DNSE lỗi hoặc dữ liệu không hợp lệ. Nến hôm nay bị bỏ qua khi tính
  chỉ báo nếu thị trường chưa đóng cửa, để tín hiệu không đổi qua lại giữa phiên. Mọi
  quyết định/tín hiệu tính bằng code thuần (`stock/policy.py`, có thêm chặn thanh khoản
  thấp và trần rủi ro stop) — LLM chỉ được dùng để diễn giải kết quả đã tính sẵn, không
  bao giờ tự tính hay tự đưa ra khuyến nghị số liệu. Các ngưỡng trong `policy.py` là ước
  lượng thô, chưa qua backtest — tự điều chỉnh nếu cần.
- **Dữ liệu:** PostgreSQL (khuyến nghị Supabase), cô lập theo `user_id`. Session đăng nhập
  Zalo được mã hoá at-rest; `SETTINGS_ENC_KEY` bắt buộc khi bật Zalo.
- **Web server:** FastAPI (`web.py`) phục vụ webhook Telegram + endpoint cầu nối
  (`/bridge/...`) cho `zalo-gateway`. Trong Docker, `supervisord` chạy đồng thời tiến
  trình Python (`uvicorn`) và tiến trình Node (`zalo-gateway`).

```
Telegram ──webhook──▶ web.py (FastAPI) ──┬─▶ core/database.py (Postgres)
                                          ├─▶ ai/router.py (Groq/OpenRouter/Gemini)
                                          └─▶ stock/ (dữ liệu + policy)
Zalo B ──WebSocket──▶ zalo-gateway (Node) ──HTTP nội bộ──▶ web.py /bridge/events
```

## Cài đặt local (dev)

Yêu cầu: Python 3.12+, một database Postgres (Supabase free tier là đủ), và Node.js 18+
**chỉ nếu** bạn muốn chạy thử kênh Zalo (bỏ qua nếu chỉ dùng Telegram).

```bash
git clone <repo-url> vietassist
cd vietassist
cp .env.example .env          # rồi điền các biến bắt buộc, xem mục dưới
pip install -r requirements-dev.txt
python main.py                 # chạy Telegram bằng polling — chỉ dùng cho dev
```

Bot sẽ tự tạo bảng trong Postgres ở lần chạy đầu (`core/database.py::migrate()`), không
cần chạy migration thủ công. Mở Telegram, tìm bot theo token đã tạo, gửi `/start`.

### Cài đặt kênh Zalo (tuỳ chọn, chỉ khi cần)

```bash
cd zalo-gateway
npm ci
npm run build
cd ..
```

Zalo gateway chạy như tiến trình Node riêng, đọc cùng file `.env` (qua biến môi trường hệ
thống, không tự đọc `.env` — export biến hoặc dùng `dotenv-cli`/`env $(cat .env | xargs)`
khi chạy tay). Khi chạy qua Docker (mục Deploy), việc này tự động.

```bash
node zalo-gateway/dist/index.js
```

## Cấu hình biến môi trường

Xem đầy đủ ở `.env.example`. Các biến **bắt buộc** để bot chạy được:

| Biến | Ý nghĩa |
|---|---|
| `TELEGRAM_TOKEN` | Token bot, lấy từ [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_OWNER_ID` | Telegram user ID của bạn (số) — chỉ id này được bot trả lời. Lấy id qua [@userinfobot](https://t.me/userinfobot) |
| `DATABASE_URL` | Connection string Postgres, dạng `postgresql://user:pass@host:port/db` |
| `GROQ_API_KEY` | Ít nhất 1 trong `GROQ_API_KEY`/`OPENROUTER_API_KEY` cần có để `/chat` hoạt động |

Các biến theo tính năng:

| Biến | Ý nghĩa |
|---|---|
| `SETTINGS_ENC_KEY` | Bắt buộc khi `ZALO_ENABLED=true`. Sinh bằng `python -c "import secrets;print(secrets.token_urlsafe(32))"`. Dùng để mã hoá session cookie Zalo trong DB. |
| `GOOGLE_API_KEY` | Cần cho `/gia`, `/vimo`, gửi ảnh (`/prompt` ảnh, ảnh Zalo). Không có thì các tính năng này báo lỗi "chưa cấu hình". |

Các biến còn lại (`GROQ_MODEL`, `AI_TIMEOUT_SEC`, `CHAT_HISTORY_TURNS`, `*_MAX_CONCURRENCY`,
`STOCK_CACHE_TTL_SEC`...) đều có default hợp lý trong `core/config.py`, chỉ cần đổi nếu
bạn biết mình muốn gì. Biến liên quan Zalo (`ZALO_ENABLED`, `WEBHOOK_*`, `BRIDGE_SECRET`,
`ZALO_CONTROL_PORT`, `ZALO_DAILY_DIGEST_HOUR`) chỉ cần khi deploy webhook — xem mục Deploy.

## Sử dụng — lệnh Telegram

Gửi `/start` để bot in ra danh sách lệnh hiện hành trực tiếp trong chat (danh sách này
luôn khớp với code vì được sinh tự động từ `services/commands.py`, không lệch tay).

Tóm tắt các nhóm lệnh:

- **Chat tự do:** gõ bất kỳ câu gì không phải lệnh → trả lời qua Groq (fallback OpenRouter).
- **Tra cứu có grounding:** `/gia <sản phẩm>`, `/vimo <câu hỏi vĩ mô/tin tức>`.
- **Ảnh → prompt:** gửi ảnh trực tiếp (Telegram/Zalo) để Gemini vision đọc framing, pose,
  outfit, lighting, camera/lens và photographic finish rồi viết prompt tiếng Anh tự chứa.
  Có thể kèm caption như `giữ mặt tôi`, `cô gái 20`, hoặc yêu cầu đổi bối cảnh. `/prompt
  <mô tả>` dùng cùng bộ rule để tạo prompt từ text.
- **Chứng khoán:** `/stock <MÃ> [sâu]` (phân tích), `/quote <MÃ>` (tra nhanh giá) — mẹo:
  gõ đúng 3 chữ cái (vd `FPT`) không cần gõ `/quote`.
- **Danh mục:** `/muavao <MÃ> <KL> <giá>`, `/banra <MÃ> <KL>`, `/xoadanhmuc <MÃ>`, `/danhmuc`.
- **Ghi chú:** `/ghichu <nội dung>`, `/dsghichu`, `/xoaghichu <id>`.
- **Nhắc nhở:** `/nhac <30p|2h|1ngay|HH:MM> <nội dung>`, `/dsnhac`, `/xoanhac <id>`.
- **Quản trị Zalo** (chỉ Telegram owner dùng được): `/zalopair <id_zalo> [tên]` cấp quyền
  user, `/zaloadmin <id_zalo> [tên]` cấp quyền admin (được dùng tính năng nhóm),
  `/zalokhoa`/`/zalomokhoa <id_zalo>` khoá/mở khoá, `/zaloxoa <id_zalo>` gỡ quyền,
  `/zalodanhsach` xem danh sách.
- **Đăng nhập Zalo B:** `/zalologin` — gateway sinh mã QR, bot gửi ảnh QR ngay trong chat
  Telegram, quét bằng app Zalo của tài khoản B trong vài phút trước khi mã hết hạn.
- **Knowledge base:** `/kbreindex` — tính lại embedding sau khi sửa `knowledge/*.md`, để
  câu trả lời chat dùng được kiến thức mới nhất (RAG).
- **Định dạng tin nhắn:** `services/tg_format.py` tự convert markdown-lite mà AI hay trả về
  (`**bold**`, `*italic*`/`_italic_`, `` `code` ``, link, gạch đầu dòng) sang HTML Telegram
  hỗ trợ, áp dụng cho chat tự do và kết quả lệnh (`/stock`, `/gia`, `/vimo`...). Tự chia
  đoạn nếu vượt 4096 ký tự (giới hạn 1 tin nhắn Telegram) và tự rơi về plain text nếu HTML
  bị lệch thẻ thay vì lỗi im lặng.

## Sử dụng — Zalo

Zalo hoạt động qua 1 tài khoản trung tâm (Zalo "B") mà bot đăng nhập và điều khiển; người
dùng thật (Zalo "A") nhắn tin cho tài khoản B đó, không phải bot có Zalo riêng của từng
người.

**Thiết lập lần đầu:**
1. Đặt `ZALO_ENABLED=true` và deploy (xem mục Deploy).
2. Trên Telegram, gửi `/zalologin` — chờ vài giây, bot gửi ảnh QR trong chat.
3. Mở app Zalo trên điện thoại của tài khoản B → quét mã QR đó (giống đăng nhập Zalo Web).
4. Bot báo "Đăng nhập Zalo B thành công" — từ giờ container restart không cần quét lại
   (session được lưu, mã hoá nếu có `SETTINGS_ENC_KEY`).

**Cấp quyền cho người dùng:** người dùng Zalo A nhắn bất kỳ gì cho Zalo B lần đầu sẽ nhận
thông báo "chưa được cấp quyền"; đồng thời Telegram owner nhận thông báo kèm sẵn lệnh
`/zalopair <id> để pair`. Dùng `/zaloadmin <id>` thay vì `/zalopair` nếu muốn người đó
dùng được tính năng quản lý nhóm.

**Dùng trong chat 1-1 với Zalo B:** toàn bộ lệnh ở mục "Sử dụng — lệnh Telegram" phía trên
đều dùng được (gõ y hệt, có dấu `/`), trừ các lệnh quản trị Zalo/Telegram-only
(`/zalopair`, `/zaloadmin`, `/zalologin`...). Ngoài ra:
- **Gửi ảnh trực tiếp cho Zalo B** (không cần gõ `/prompt`) → bot tự nhận diện, phân tích
  ảnh bằng Gemini vision và trả lời prompt tái tạo. Gõ kèm caption cùng ảnh nếu muốn định
  hướng thêm (vd "chụp lại phong cách anime"); không có caption thì dùng hướng dẫn mặc
  định "phân tích ảnh và viết prompt tái tạo chi tiết". Tính năng này **chỉ hoạt động ở
  chat 1-1**, ảnh gửi trong group bị bỏ qua có chủ đích.

**Dùng trong group** (chỉ A admin, sau khi admin `/themnhom` bật allowlist cho group đó):
- `/themnhom <group_id> [alias]` — bật ghi log tin nhắn nhóm (bắt buộc trước khi tổng
  kết được). **Lưu ý riêng tư:** từ lúc bật, tin nhắn của *mọi* thành viên trong nhóm đó
  bị lưu lại, kể cả người chưa từng pair với bot — hãy đảm bảo các thành viên biết và
  đồng ý trước khi bật.
- `/xoanhom <group_id|alias>` — tắt lại.
- `/nhom` — xem danh sách nhóm đã bật.
- `/tongket <group_id|alias> [24h|7d]` — tóm tắt nội dung nhóm trong khoảng thời gian đó
  bằng AI (chỉ dựa trên tin nhắn đã lưu, không tự thêm dữ kiện ngoài).
- Ngoài các lệnh trên, group chỉ log tin nhắn (phục vụ `/tongket`), **không** trả lời chat
  tự do trong group — tránh bot trả lời ồn ào giữa cuộc trò chuyện của người khác.

**Digest hằng ngày:** nếu `ZALO_ENABLED=true`, mỗi ngày vào giờ `ZALO_DAILY_DIGEST_HOUR`
(giờ Việt Nam), A admin nhận tóm tắt tự động cho các nhóm đã bật.

## Deploy production

Repo có sẵn `Dockerfile` + `render.yaml` cho [Render](https://render.com) (free plan) —
1 container chạy cả `uvicorn` (web.py) lẫn `zalo-gateway` (Node) qua `supervisord`.

```bash
git push   # nếu đã connect repo với Render, deploy tự động theo render.yaml
```

Việc cần làm thủ công trên Render dashboard (không nằm trong `render.yaml` vì là secret):
1. Tạo service từ repo, Render tự đọc `render.yaml`.
2. Điền các env var được đánh dấu `sync: false`: `TELEGRAM_TOKEN`, `TELEGRAM_OWNER_ID`,
   `DATABASE_URL`, `SETTINGS_ENC_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`,
   `GOOGLE_API_KEY`, `WEBHOOK_SECRET` (tự đặt 1 chuỗi ngẫu nhiên), `WEBHOOK_BASE_URL`
   (URL Render cấp cho service, dạng `https://<tên>.onrender.com`), `BRIDGE_SECRET` (tự
   đặt 1 chuỗi ngẫu nhiên khác — dùng để `zalo-gateway` xác thực với `web.py`).
3. Nếu dùng Zalo, đổi `ZALO_ENABLED` thành `true` trong `render.yaml` hoặc override trên
   dashboard, rồi làm theo mục "Sử dụng — Zalo" ở trên (`/zalologin` qua Telegram).

**Health check:** `GET /` là liveness. `GET /ready` kiểm tra process Telegram và PostgreSQL;
đặt Render health check vào `/ready` để deployment chỉ được xem là ready khi DB đã hoạt động.

**Giới hạn Render free plan:** service ngủ sau ~15 phút không có traffic. Vòng lặp nhắc
nhở (`reminder_loop`) và digest hằng ngày (`daily_digest_loop`) chỉ chạy khi process còn
sống — nếu bot không có traffic đều đặn, nhắc nhở có thể trễ hoặc không bắn đúng giờ. Cân
nhắc nâng plan trả phí nếu tính năng nhắc nhở là thiết yếu.

## Kiểm tra / CI thủ công

Chưa có CI tự động (không có `.github/workflows/`) — chạy tay trước khi commit:

```bash
ruff check .                        # lint Python
ruff format --check .               # format Python
pytest -q                           # test Python (không cần DB thật, toàn bộ dùng mock)

cd zalo-gateway
npx tsc -p tsconfig.json --noEmit   # typecheck TypeScript
npm run build                       # build thử, đảm bảo không lỗi compile
```

## Vận hành & xử lý sự cố

- **Bot Telegram không trả lời:** kiểm tra `TELEGRAM_TOKEN` đúng chưa, `TELEGRAM_OWNER_ID`
  có khớp id Telegram thật của bạn không (chỉ id này được trả lời), và log service trên
  Render/server có báo lỗi webhook không (`set_webhook` thất bại thường do
  `WEBHOOK_BASE_URL` sai hoặc chưa đúng domain HTTPS).
- **Zalo B bị đăng xuất bất ngờ:** dùng lại `/zalologin` để quét QR lại — session cũ hết
  hạn là bình thường với tài khoản Zalo Web, không phải bug.
- **Muốn dev polling Telegram cục bộ nhưng có `.env` production:** `main.py` sẽ tự chặn
  và báo lỗi nếu phát hiện `WEBHOOK_BASE_URL` đã set, để tránh vô tình xoá webhook đang
  chạy thật (`run_polling()` của thư viện tự xoá webhook trước khi polling).
- **Chat trả lời sai/cũ sau khi sửa `knowledge/*.md`:** chạy `/kbreindex` trên Telegram để
  tính lại embedding — sửa file không tự động reindex.
- **Muốn xoá dữ liệu cũ thủ công:** `processed_events` (30 ngày) và `zalo_group_messages`
  (90 ngày) được tự dọn hằng ngày (`services/maintenance.py`), không cần can thiệp tay
  trừ khi cần đổi ngưỡng retention (sửa hằng số trong file đó).

## Giới hạn đã biết

Ghi ra để không ai bất ngờ khi gặp phải — đây là những đánh đổi có chủ đích cho quy mô cá
nhân/nhóm nhỏ, không phải oversight:

- **Chỉ 1 Telegram owner** — không phải bot nhiều người dùng độc lập trên Telegram. Zalo
  hỗ trợ nhiều người dùng nhưng qua 1 tài khoản B trung tâm, không phải multi-tenant thật.
- **Không có rate limiting** ở tầng ứng dụng — chấp nhận được vì chỉ owner + người được
  pair mới dùng được, nhưng nếu pair nhầm người không tin tưởng, họ có thể tốn quota AI
  không giới hạn.
- **Không theo dõi chi phí AI theo user/ngày** — nếu chi phí Groq/OpenRouter/Gemini tăng
  bất thường, hiện không có cách tra nhanh do ai/lệnh nào gây ra.
- **Lock/cache trong RAM** (`services/locks.py`, `stock/market.py`) không chia sẻ được nếu
  scale ngang nhiều instance — chỉ đúng khi chạy 1 instance (đúng với Render free plan
  hiện tại).
- **Không có CI tự động** — lint/test phải chạy tay trước khi deploy (xem mục phía trên).

## CI

GitHub Actions validates Python lint/format/tests and the Zalo gateway typecheck/build on every push and pull request.
