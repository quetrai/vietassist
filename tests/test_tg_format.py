from unittest.mock import AsyncMock, MagicMock

from services.tg_format import (
    _split_raw_text,
    markdown_to_html,
    reply_rich,
    reply_rich_edit_first,
    send_rich,
)


def test_bold_and_italic_star():
    assert markdown_to_html("**đậm** và *nghiêng*") == "<b>đậm</b> và <i>nghiêng</i>"


def test_italic_underscore_ignores_snake_case():
    # foreign_net_vol không được biến thành italic vì "_" nằm giữa chữ/số.
    assert markdown_to_html("foreign_net_vol tăng") == "foreign_net_vol tăng"


def test_italic_underscore_matches_word_boundary():
    assert markdown_to_html("_nhấn mạnh_ ở đây") == "<i>nhấn mạnh</i> ở đây"


def test_code_span():
    assert markdown_to_html("dùng `auto_close`") == "dùng <code>auto_close</code>"


def test_code_span_not_affected_by_bold_italic():
    # Nội dung trong <code> giữ nguyên ký tự * _ , không bị nuốt nhầm thành bold/italic.
    assert markdown_to_html("`a*b_c`") == "<code>a*b_c</code>"


def test_link_markdown_to_anchor():
    result = markdown_to_html("xem [tại đây](https://example.com/x)")
    assert result == 'xem <a href="https://example.com/x">tại đây</a>'


def test_link_rejects_non_http_scheme():
    # javascript:/data: không khớp _LINK_RE nên giữ nguyên dạng markdown thô (an toàn).
    result = markdown_to_html("[click](javascript:alert(1))")
    assert "<a " not in result


def test_bullet_list_converted():
    result = markdown_to_html("- mục 1\n- mục 2")
    assert result == "• mục 1\n• mục 2"


def test_html_special_chars_escaped():
    assert markdown_to_html("A&B <script>") == "A&amp;B &lt;script&gt;"


def test_url_with_underscore_in_link_not_broken():
    # URL chứa "_" từng bị _ITALIC_RE bắt nhầm, phá mất href — kiểm tra không còn bug này.
    result = markdown_to_html("[xem](https://example.com/a_b_c)")
    assert result == '<a href="https://example.com/a_b_c">xem</a>'


def test_split_raw_text_keeps_short_text_whole():
    text = "câu ngắn"
    assert _split_raw_text(text, max_len=4096) == [text]


def test_split_raw_text_splits_long_text_at_paragraph_boundary():
    para1 = "a" * 3000
    para2 = "b" * 3000
    text = f"{para1}\n\n{para2}"
    chunks = _split_raw_text(text, max_len=4096)
    assert len(chunks) == 2
    assert chunks[0] == para1
    assert chunks[1] == para2


async def test_reply_rich_sends_html_by_default():
    message = MagicMock()
    message.reply_text = AsyncMock()
    await reply_rich(message, "**xin chào**")
    message.reply_text.assert_awaited_once_with("<b>xin chào</b>", parse_mode="HTML")


async def test_reply_rich_falls_back_to_plain_text_on_html_error():
    message = MagicMock()
    calls: list[tuple[str, str | None]] = []

    async def fake_reply_text(content, parse_mode=None):
        calls.append((content, parse_mode))
        if parse_mode == "HTML":
            raise ValueError("Can't parse entities")

    message.reply_text = fake_reply_text
    await reply_rich(message, "**xin chào**")
    assert calls[0] == ("<b>xin chào</b>", "HTML")
    assert calls[1] == ("xin chào", None)


async def test_reply_rich_splits_long_text_into_multiple_messages():
    message = MagicMock()
    message.reply_text = AsyncMock()
    long_text = "x" * 5000
    await reply_rich(message, long_text, max_len=4096)
    assert message.reply_text.await_count >= 2


async def test_reply_rich_edit_first_edits_first_chunk_then_replies_rest():
    status = MagicMock()
    status.edit_text = AsyncMock()
    status.reply_text = AsyncMock()
    long_text = "y" * 5000
    await reply_rich_edit_first(status, long_text, max_len=4096)
    status.edit_text.assert_awaited_once()
    status.reply_text.assert_awaited()


async def test_send_rich_calls_bot_send_message():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    await send_rich(bot, 12345, "**tin nhắn**")
    bot.send_message.assert_awaited_once_with(
        chat_id=12345, text="<b>tin nhắn</b>", parse_mode="HTML"
    )
