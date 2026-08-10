import asyncio

import pytest

from services import maintenance


async def test_cleanup_loop_calls_cleanup_and_sleeps(monkeypatch):
    calls = []

    async def fake_cleanup():
        calls.append("cleanup")
        return {"processed_events": 3, "zalo_group_messages": 1}

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()  # dừng vòng lặp sau đúng 1 vòng để test không treo

    monkeypatch.setattr(maintenance.database, "cleanup_old_data", fake_cleanup)
    monkeypatch.setattr(maintenance.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await maintenance.cleanup_loop()

    assert calls == ["cleanup"]
    assert sleep_calls == [maintenance._INTERVAL_SEC]


async def test_cleanup_loop_survives_exception_and_keeps_looping(monkeypatch):
    async def fake_cleanup():
        raise RuntimeError("db lỗi tạm thời")

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(maintenance.database, "cleanup_old_data", fake_cleanup)
    monkeypatch.setattr(maintenance.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await maintenance.cleanup_loop()

    # Lỗi ở cleanup không được làm chết hẳn vòng lặp — phải vẫn đi tới sleep() để thử lại sau.
    assert sleep_calls == [maintenance._INTERVAL_SEC]
