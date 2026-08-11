import logging

from bot import build_application
from core.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

_POLLING_WEBHOOK_CONFLICT_MSG = (
    "WEBHOOK_BASE_URL đang được set. Xóa biến này trước khi chạy polling local."
)


def main() -> None:
    settings.validate()
    if settings.webhook_base_url:
        raise RuntimeError(_POLLING_WEBHOOK_CONFLICT_MSG)
    build_application().run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
