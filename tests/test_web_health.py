import logging

import web


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access", level=logging.INFO, pathname=__file__, lineno=1,
        msg=message, args=(), exc_info=None,
    )


def test_log_filter_hides_health_check_hits():
    log_filter = web._HealthCheckLogFilter()
    quiet_messages = [
        '127.0.0.1:0 - "GET / HTTP/1.1" 200',
        '127.0.0.1:0 - "HEAD / HTTP/1.1" 200',
        '127.0.0.1:0 - "GET /health HTTP/1.1" 200',
        '127.0.0.1:0 - "HEAD /health HTTP/1.1" 200',
        '127.0.0.1:0 - "GET /ready HTTP/1.1" 200',
    ]
    for message in quiet_messages:
        assert log_filter.filter(_record(message)) is False, message


def test_log_filter_keeps_other_requests_visible():
    log_filter = web._HealthCheckLogFilter()
    kept_messages = [
        '127.0.0.1:0 - "POST /webhook HTTP/1.1" 200',
        '127.0.0.1:0 - "POST /bridge/events HTTP/1.1" 200',
        '127.0.0.1:0 - "GET /bridge/zalo-session HTTP/1.1" 403',
    ]
    for message in kept_messages:
        assert log_filter.filter(_record(message)) is True, message


async def test_health_endpoint_is_cheap_and_public():
    result = await web.health()
    assert result == {"status": "ok"}


async def test_readiness_endpoint_requires_telegram_initialized(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(web, "telegram", None)
    try:
        await web.readiness()
        raise AssertionError("phải raise HTTPException khi telegram chưa sẵn sàng")
    except HTTPException as exc:
        assert exc.status_code == 503
