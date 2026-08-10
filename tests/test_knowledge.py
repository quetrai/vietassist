from core import knowledge


def test_chunk_file_splits_by_heading():
    text = "# Tiêu đề\nĐoạn 1.\n\n## Mục con\nĐoạn 2."
    chunks = knowledge.chunk_file(text)
    headings = [h for h, _ in chunks]
    assert "Tiêu đề" in headings
    assert "Mục con" in headings
    assert any("Đoạn 1" in c for _, c in chunks)
    assert any("Đoạn 2" in c for _, c in chunks)


def test_chunk_file_keeps_preamble_before_first_heading():
    text = "Mở đầu chưa có heading.\n\n# Tiêu đề\nNội dung."
    chunks = knowledge.chunk_file(text)
    assert chunks[0][0] == ""
    assert "Mở đầu" in chunks[0][1]


def test_chunk_file_no_heading_at_all():
    text = "Chỉ là văn bản thuần, không có heading nào."
    chunks = knowledge.chunk_file(text)
    assert len(chunks) == 1
    assert chunks[0][0] == ""
    assert chunks[0][1] == text


def test_chunk_file_splits_long_section_with_overlap():
    long_content = "x" * (knowledge.CHUNK_MAX_CHARS * 2 + 100)
    text = f"# Rất dài\n{long_content}"
    chunks = knowledge.chunk_file(text)
    assert len(chunks) >= 3
    for heading, content in chunks:
        assert heading == "Rất dài"
        assert len(content) <= knowledge.CHUNK_MAX_CHARS

    # Có overlap: ký tự cuối của chunk trước phải xuất hiện lại ở đầu chunk sau
    joined = "".join(content for _, content in chunks)
    assert len(joined) >= len(long_content)


def test_chunk_file_skips_empty_sections():
    text = "# A\n\n# B\nNội dung B."
    chunks = knowledge.chunk_file(text)
    # Section "A" rỗng -> bị bỏ qua, chỉ còn section "B"
    assert all(content.strip() for _, content in chunks)
    assert any(h == "B" for h, _ in chunks)
