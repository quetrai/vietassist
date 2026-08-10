import pytest

from ai.providers.google import _image_part


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("x.jpg", b"\xff\xd8\xfffake"),
        ("x.png", b"\x89PNG\r\n\x1a\nfake"),
        ("x.webp", b"RIFFxxxxWEBPfake"),
        ("x.gif", b"GIF89afake"),
    ],
)
def test_image_part_accepts_supported_magic_bytes(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    part = _image_part(str(path))
    assert part is not None


def test_image_part_rejects_renamed_non_image(tmp_path):
    path = tmp_path / "evil.jpg"
    path.write_bytes(b"not an image")
    with pytest.raises(Exception, match="không khớp định dạng"):
        _image_part(str(path))


def test_image_part_rejects_unsupported_extension(tmp_path):
    path = tmp_path / "image.bmp"
    path.write_bytes(b"BM" + b"x" * 20)
    with pytest.raises(Exception, match="không được hỗ trợ"):
        _image_part(str(path))
