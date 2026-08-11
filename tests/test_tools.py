import pytest

from services import tools


@pytest.mark.asyncio
async def test_note_tool(monkeypatch):
    async def add_note(user_id, content):
        assert user_id == "u1"
        assert content == "mua HPG"
        return 12

    monkeypatch.setattr(tools.database, "add_note", add_note)
    result = await tools.maybe_run("u1", "ghi chú mua HPG")
    assert result.handled is True
    assert result.text == "Đã lưu ghi chú #12."


@pytest.mark.asyncio
async def test_unknown_text_is_not_routed():
    result = await tools.maybe_run("u1", "hôm nay thị trường thế nào")
    assert result.handled is False
