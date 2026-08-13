import services.chat as chat


def test_exact_phrase_markers_still_match():
    assert chat._is_realtime_request("tin tức hôm nay")
    assert chat._is_realtime_request("giá vàng hôm nay")
    assert chat._is_realtime_request("what's the news today")


def test_news_topic_plus_freshness_word_anywhere_in_sentence():
    """Regression: 'tin tức về AI hôm nay' từng lọt qua vì _REALTIME_MARKERS chỉ so
    khớp cụm liền nhau ('tin tức hôm nay'), còn câu tự nhiên chèn thêm từ ở giữa
    ('về AI') thì không khớp — khiến bot rơi vào nhánh chat + knowledge base thay vì
    tìm kiếm thời gian thực."""
    assert chat._is_realtime_request("tin tức về AI hôm nay")
    assert chat._is_realtime_request("tin tức về thị trường chứng khoán mới nhất")
    assert chat._is_realtime_request("cho tôi bản tin công nghệ gần đây")
    assert chat._is_realtime_request("có news gì today không")


def test_explicit_lookup_intent_words_always_force_realtime():
    assert chat._is_realtime_request("tra cứu giúp anh vụ này")
    assert chat._is_realtime_request("tìm kiếm thông tin về công ty XYZ")
    assert chat._is_realtime_request("search cho tôi tin về Fed")


def test_plain_news_topic_without_freshness_does_not_force_realtime():
    """Câu chỉ nhắc 'tin tức' nhưng không có ý định thời gian thực/tra cứu (vd hỏi
    khái niệm) thì không cần ép qua web search."""
    assert not chat._is_realtime_request("tin tức là gì")


def test_greeting_is_not_realtime():
    assert not chat._is_realtime_request("xin chào")
    assert not chat._is_realtime_request("cảm ơn bạn nhé")
