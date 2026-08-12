import logging

from bot import build_application
from core.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# CHỈ dùng file này để chạy polling lúc dev cục bộ. Production (Docker/render.yaml) chạy
# qua web.py bằng webhook (xem supervisord.conf). python-telegram-bot's run_polling() tự
# gọi delete_webhook() trước khi polling — nếu ai lỡ chạy `python main.py` với .env trỏ
# vào TELEGRAM_TOKEN của production, nó sẽ ÂM THẦM XOÁ webhook đang chạy thật, làm bot
# production ngừng nhận update cho tới khi ai đó set lại webhook. Chặn bằng cách từ chối
# chạy nếu WEBHOOK_BASE_URL (chỉ set ở env production) đang có mặt.
_POLLING_WEBHOOK_CONFLICT_MSG = (
    "WEBHOOK_BASE_URL đang được set — .env này trông giống cấu hình production (webhook), "
    "không phải dev. Chạy `python main.py` (polling) sẽ tự xoá webhook đang hoạt động. "
    "Nếu đây thực sự là môi trường dev, xoá biến WEBHOOK_BASE_URL khỏi .env trước."
)


def main() -> None:
    settings.validate()
    if settings.webhook_base_url:
        raise RuntimeError(_POLLING_WEBHOOK_CONFLICT_MSG)
    build_application().run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
