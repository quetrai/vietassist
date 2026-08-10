import dataclasses

from core import crypto


def _with_key(monkeypatch, key: str) -> None:
    monkeypatch.setattr(
        crypto, "settings", dataclasses.replace(crypto.settings, settings_enc_key=key)
    )
    monkeypatch.setattr(crypto, "_fernet", None)


def test_encrypt_decrypt_roundtrip(monkeypatch):
    _with_key(monkeypatch, "test-secret-key")
    ciphertext = crypto.encrypt("cookie-du-lieu-nhay-cam")
    assert ciphertext != "cookie-du-lieu-nhay-cam"
    assert crypto.decrypt(ciphertext) == "cookie-du-lieu-nhay-cam"


def test_encrypt_without_key_passes_through(monkeypatch):
    _with_key(monkeypatch, "")
    assert crypto.encrypt("plain") == "plain"
    assert crypto.decrypt("plain") == "plain"


def test_decrypt_backward_compatible_with_legacy_plaintext(monkeypatch):
    # Dữ liệu lưu trước khi SETTINGS_ENC_KEY được bật (plaintext) không được vỡ khi bật
    # mã hoá sau đó — decrypt() phải trả nguyên văn nếu giá trị không phải ciphertext hợp lệ.
    _with_key(monkeypatch, "test-secret-key")
    assert crypto.decrypt("gia-tri-cu-chua-ma-hoa") == "gia-tri-cu-chua-ma-hoa"


def test_different_keys_derive_different_ciphertext(monkeypatch):
    _with_key(monkeypatch, "key-one")
    first = crypto.encrypt("giong-nhau")

    _with_key(monkeypatch, "key-two")
    second = crypto.encrypt("giong-nhau")

    assert first != second
    assert crypto.decrypt(first) != "giong-nhau"  # sai key -> không giải mã được -> passthrough
