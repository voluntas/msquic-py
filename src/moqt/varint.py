"""QUIC Variable-Length Integer エンコーディング

RFC 9000 Section 16 に基づく実装
C++ バインディング (msquic) を使用
"""

from __future__ import annotations

from msquic import (
    decode_varint as _decode_varint,
    encode_varint as _encode_varint,
    varint_size as _varint_size,
)


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
    try:
        return bytes(_encode_varint(value))
    except OverflowError as e:
        raise ValueError(f"varint は 2^62-1 より大きい値をエンコードできません: {value}") from e


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
    if isinstance(data, memoryview):
        data = bytes(data)
    try:
        return _decode_varint(data, offset)
    except (RuntimeError, IndexError) as e:
        raise ValueError(str(e)) from e


def varint_size(value: int) -> int:
    """varint のエンコードに必要なバイト数を返す

    Args:
        value: エンコードする整数

    Returns:
        必要なバイト数
    """
    if value < 0:
        raise ValueError(f"varint は負の値をエンコードできません: {value}")
    try:
        return _varint_size(value)
    except OverflowError as e:
        raise ValueError(f"varint は 2^62-1 より大きい値をエンコードできません: {value}") from e
