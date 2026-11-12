import struct

from perfzy.capture.android import _decode_screencap


def test_modern_raw_android_screencap_is_decoded_without_png():
    pixels = bytes((255, 0, 0, 255, 0, 255, 0, 255))
    payload = struct.pack("<IIII", 2, 1, 1, 1) + pixels

    image = _decode_screencap(payload)
    try:
        assert image is not None
        assert image.mode == "RGB"
        assert image.size == (2, 1)
        assert list(image.getdata()) == [(255, 0, 0), (0, 255, 0)]
    finally:
        if image is not None:
            image.close()


def test_truncated_or_unreasonable_raw_screencap_is_rejected():
    assert _decode_screencap(b"short") is None
    assert _decode_screencap(struct.pack("<III", 999999, 2, 1)) is None
