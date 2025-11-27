"""QUIC Variable-Length Integer エンコーディング

RFC 9000 Section 16 に基づく実装
"""

from __future__ import annotations


def encode_varint(value: int) -> bytes:
    """整数を QUIC varint 形式にエンコードする

    Args:
        value: エンコードする整数 (0 から 2^62-1)

    Returns:
        エンコードされたバイト列

    Raises:
        ValueError: 値が範囲外の場合
    """
    if value < 0:
        raise ValueError(f"varint は負の値をエンコードできません: {value}")

    if value < 0x40:
        # 1 バイト (6 ビット)
        return bytes([value])
    elif value < 0x4000:
        # 2 バイト (14 ビット)
        return bytes([0x40 | (value >> 8), value & 0xFF])
    elif value < 0x40000000:
        # 4 バイト (30 ビット)
        return bytes(
            [
                0x80 | (value >> 24),
                (value >> 16) & 0xFF,
                (value >> 8) & 0xFF,
                value & 0xFF,
            ]
        )
    elif value < 0x4000000000000000:
        # 8 バイト (62 ビット)
        return bytes(
            [
                0xC0 | (value >> 56),
                (value >> 48) & 0xFF,
                (value >> 40) & 0xFF,
                (value >> 32) & 0xFF,
                (value >> 24) & 0xFF,
                (value >> 16) & 0xFF,
                (value >> 8) & 0xFF,
                value & 0xFF,
            ]
        )
    else:
        raise ValueError(f"varint は 2^62-1 より大きい値をエンコードできません: {value}")


def decode_varint(data: bytes | memoryview, offset: int = 0) -> tuple[int, int]:
    """QUIC varint 形式からデコードする

    Args:
        data: デコードするバイト列
        offset: 開始オフセット

    Returns:
        (デコードされた値, 消費したバイト数) のタプル

    Raises:
        ValueError: データが不足している場合
    """
    if offset >= len(data):
        raise ValueError("varint をデコードするためのデータが不足しています")

    first_byte = data[offset]
    prefix = first_byte >> 6

    if prefix == 0:
        # 1 バイト
        return first_byte, 1
    elif prefix == 1:
        # 2 バイト
        if offset + 2 > len(data):
            raise ValueError("2 バイト varint をデコードするためのデータが不足しています")
        value = ((first_byte & 0x3F) << 8) | data[offset + 1]
        return value, 2
    elif prefix == 2:
        # 4 バイト
        if offset + 4 > len(data):
            raise ValueError("4 バイト varint をデコードするためのデータが不足しています")
        value = (
            ((first_byte & 0x3F) << 24)
            | (data[offset + 1] << 16)
            | (data[offset + 2] << 8)
            | data[offset + 3]
        )
        return value, 4
    else:
        # 8 バイト
        if offset + 8 > len(data):
            raise ValueError("8 バイト varint をデコードするためのデータが不足しています")
        value = (
            ((first_byte & 0x3F) << 56)
            | (data[offset + 1] << 48)
            | (data[offset + 2] << 40)
            | (data[offset + 3] << 32)
            | (data[offset + 4] << 24)
            | (data[offset + 5] << 16)
            | (data[offset + 6] << 8)
            | data[offset + 7]
        )
        return value, 8


def varint_size(value: int) -> int:
    """varint のエンコードに必要なバイト数を返す

    Args:
        value: エンコードする整数

    Returns:
        必要なバイト数
    """
    if value < 0x40:
        return 1
    elif value < 0x4000:
        return 2
    elif value < 0x40000000:
        return 4
    elif value < 0x4000000000000000:
        return 8
    else:
        raise ValueError(f"varint は 2^62-1 より大きい値をエンコードできません: {value}")
