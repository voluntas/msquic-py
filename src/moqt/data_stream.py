"""MOQT Data Streams and Datagrams

draft-ietf-moq-transport-15 Section 10 に基づく実装
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import ClassVar

from .varint import decode_varint, encode_varint


class ObjectStatus(IntEnum):
    """Object Status"""

    NORMAL = 0x0
    OBJECT_DOES_NOT_EXIST = 0x1
    END_OF_GROUP = 0x3
    END_OF_TRACK = 0x4


class DatagramType(IntEnum):
    """OBJECT_DATAGRAM Type"""

    # Priority Present, Payload
    OBJECT_ID_PRIORITY_PAYLOAD = 0x00
    OBJECT_ID_EXTENSIONS_PRIORITY_PAYLOAD = 0x01
    OBJECT_ID_PRIORITY_PAYLOAD_END_OF_GROUP = 0x02
    OBJECT_ID_EXTENSIONS_PRIORITY_PAYLOAD_END_OF_GROUP = 0x03
    NO_OBJECT_ID_PRIORITY_PAYLOAD = 0x04
    NO_OBJECT_ID_EXTENSIONS_PRIORITY_PAYLOAD = 0x05
    NO_OBJECT_ID_PRIORITY_PAYLOAD_END_OF_GROUP = 0x06
    NO_OBJECT_ID_EXTENSIONS_PRIORITY_PAYLOAD_END_OF_GROUP = 0x07

    # Priority Present, Status
    OBJECT_ID_PRIORITY_STATUS = 0x20
    OBJECT_ID_EXTENSIONS_PRIORITY_STATUS = 0x21
    NO_OBJECT_ID_PRIORITY_STATUS = 0x24
    NO_OBJECT_ID_EXTENSIONS_PRIORITY_STATUS = 0x25

    # No Priority, Payload
    OBJECT_ID_NO_PRIORITY_PAYLOAD = 0x08
    OBJECT_ID_EXTENSIONS_NO_PRIORITY_PAYLOAD = 0x09
    OBJECT_ID_NO_PRIORITY_PAYLOAD_END_OF_GROUP = 0x0A
    OBJECT_ID_EXTENSIONS_NO_PRIORITY_PAYLOAD_END_OF_GROUP = 0x0B
    NO_OBJECT_ID_NO_PRIORITY_PAYLOAD = 0x0C
    NO_OBJECT_ID_EXTENSIONS_NO_PRIORITY_PAYLOAD = 0x0D
    NO_OBJECT_ID_NO_PRIORITY_PAYLOAD_END_OF_GROUP = 0x0E
    NO_OBJECT_ID_EXTENSIONS_NO_PRIORITY_PAYLOAD_END_OF_GROUP = 0x0F

    # No Priority, Status
    OBJECT_ID_NO_PRIORITY_STATUS = 0x28
    OBJECT_ID_EXTENSIONS_NO_PRIORITY_STATUS = 0x29
    NO_OBJECT_ID_NO_PRIORITY_STATUS = 0x2C
    NO_OBJECT_ID_EXTENSIONS_NO_PRIORITY_STATUS = 0x2D

    def has_object_id(self) -> bool:
        """Object ID フィールドがあるか"""
        return self.value not in (0x04, 0x05, 0x06, 0x07, 0x24, 0x25, 0x0C, 0x0D, 0x0E, 0x0F, 0x2C, 0x2D)

    def has_extensions(self) -> bool:
        """Extensions フィールドがあるか"""
        return (self.value & 0x01) == 0x01

    def has_priority(self) -> bool:
        """Priority フィールドがあるか"""
        return self.value < 0x08 or (0x20 <= self.value <= 0x25)

    def is_end_of_group(self) -> bool:
        """End of Group かどうか"""
        return self.value in (0x02, 0x03, 0x06, 0x07, 0x0A, 0x0B, 0x0E, 0x0F)

    def has_status(self) -> bool:
        """Status フィールドがあるか (Payload ではなく)"""
        return self.value in (0x20, 0x21, 0x24, 0x25, 0x28, 0x29, 0x2C, 0x2D)


class SubgroupHeaderType(IntEnum):
    """SUBGROUP_HEADER Type"""

    # Priority Present
    SUBGROUP_ID_ZERO_NO_EXT_PRIORITY = 0x10
    SUBGROUP_ID_ZERO_EXT_PRIORITY = 0x11
    SUBGROUP_ID_FIRST_OBJECT_NO_EXT_PRIORITY = 0x12
    SUBGROUP_ID_FIRST_OBJECT_EXT_PRIORITY = 0x13
    SUBGROUP_ID_PRESENT_NO_EXT_PRIORITY = 0x14
    SUBGROUP_ID_PRESENT_EXT_PRIORITY = 0x15

    # End of Group, Priority Present
    SUBGROUP_ID_ZERO_NO_EXT_END_PRIORITY = 0x18
    SUBGROUP_ID_ZERO_EXT_END_PRIORITY = 0x19
    SUBGROUP_ID_FIRST_OBJECT_NO_EXT_END_PRIORITY = 0x1A
    SUBGROUP_ID_FIRST_OBJECT_EXT_END_PRIORITY = 0x1B
    SUBGROUP_ID_PRESENT_NO_EXT_END_PRIORITY = 0x1C
    SUBGROUP_ID_PRESENT_EXT_END_PRIORITY = 0x1D

    # No Priority
    SUBGROUP_ID_ZERO_NO_EXT_NO_PRIORITY = 0x30
    SUBGROUP_ID_ZERO_EXT_NO_PRIORITY = 0x31
    SUBGROUP_ID_FIRST_OBJECT_NO_EXT_NO_PRIORITY = 0x32
    SUBGROUP_ID_FIRST_OBJECT_EXT_NO_PRIORITY = 0x33
    SUBGROUP_ID_PRESENT_NO_EXT_NO_PRIORITY = 0x34
    SUBGROUP_ID_PRESENT_EXT_NO_PRIORITY = 0x35

    # End of Group, No Priority
    SUBGROUP_ID_ZERO_NO_EXT_END_NO_PRIORITY = 0x38
    SUBGROUP_ID_ZERO_EXT_END_NO_PRIORITY = 0x39
    SUBGROUP_ID_FIRST_OBJECT_NO_EXT_END_NO_PRIORITY = 0x3A
    SUBGROUP_ID_FIRST_OBJECT_EXT_END_NO_PRIORITY = 0x3B
    SUBGROUP_ID_PRESENT_NO_EXT_END_NO_PRIORITY = 0x3C
    SUBGROUP_ID_PRESENT_EXT_END_NO_PRIORITY = 0x3D

    def subgroup_id_mode(self) -> str:
        """Subgroup ID の決定方法を返す"""
        low_nibble = self.value & 0x0F
        if low_nibble in (0x00, 0x01, 0x08, 0x09):
            return "zero"
        elif low_nibble in (0x02, 0x03, 0x0A, 0x0B):
            return "first_object"
        else:
            return "present"

    def has_extensions(self) -> bool:
        """Extensions Present か"""
        return (self.value & 0x01) == 0x01

    def has_priority(self) -> bool:
        """Priority Present か"""
        return self.value < 0x30

    def contains_end_of_group(self) -> bool:
        """End of Group を含むか"""
        low_nibble = self.value & 0x0F
        return low_nibble >= 0x08


class FetchSerializationFlags(IntEnum):
    """Fetch Object Serialization Flags"""

    SUBGROUP_ZERO = 0x00
    SUBGROUP_PRIOR = 0x01
    SUBGROUP_PRIOR_PLUS_ONE = 0x02
    SUBGROUP_PRESENT = 0x03
    OBJECT_ID_PRESENT = 0x04
    GROUP_ID_PRESENT = 0x08
    PRIORITY_PRESENT = 0x10
    EXTENSIONS_PRESENT = 0x20


@dataclass
class ObjectExtensions:
    """Object Extension Headers"""

    headers: dict[int, bytes] = field(default_factory=dict)

    def encode(self) -> bytes:
        """Extensions をエンコードする"""
        if not self.headers:
            return encode_varint(0)

        # 各ヘッダーをエンコード
        headers_data = b""
        for header_type, value in self.headers.items():
            headers_data += encode_varint(header_type)
            if header_type % 2 == 1:
                # 奇数型は Length プレフィックス付き
                headers_data += encode_varint(len(value))
            headers_data += value

        return encode_varint(len(headers_data)) + headers_data

    @classmethod
    def decode(cls, data: bytes, offset: int = 0) -> tuple[ObjectExtensions, int]:
        """Extensions をデコードする"""
        length, consumed = decode_varint(data, offset)
        total_consumed = consumed

        headers = {}
        end_offset = offset + total_consumed + length
        current_offset = offset + total_consumed

        while current_offset < end_offset:
            header_type, consumed = decode_varint(data, current_offset)
            current_offset += consumed

            if header_type % 2 == 1:
                # 奇数型は Length プレフィックス付き
                value_length, consumed = decode_varint(data, current_offset)
                current_offset += consumed
                value = bytes(data[current_offset : current_offset + value_length])
                current_offset += value_length
            else:
                # 偶数型は varint 値 (生のバイト列を保持)
                start_offset = current_offset
                _, consumed = decode_varint(data, current_offset)
                value = bytes(data[start_offset : start_offset + consumed])
                current_offset += consumed

            headers[header_type] = value

        total_consumed = current_offset - offset
        return cls(headers=headers), total_consumed


@dataclass
class ObjectDatagram:
    """OBJECT_DATAGRAM"""

    datagram_type: DatagramType
    track_alias: int
    group_id: int
    object_id: int = 0
    publisher_priority: int | None = None
    extensions: ObjectExtensions | None = None
    object_status: ObjectStatus | None = None
    end_of_group: bool = False
    payload: bytes = b""

    def encode(self) -> bytes:
        """OBJECT_DATAGRAM をエンコードする"""
        result = encode_varint(self.datagram_type.value)
        result += encode_varint(self.track_alias)
        result += encode_varint(self.group_id)

        if self.datagram_type.has_object_id():
            result += encode_varint(self.object_id)

        if self.datagram_type.has_priority():
            result += bytes([self.publisher_priority or 0])

        if self.datagram_type.has_extensions():
            if self.extensions:
                result += self.extensions.encode()
            else:
                raise ValueError("Extensions Present だが extensions が None")

        if self.datagram_type.has_status():
            result += encode_varint(self.object_status or ObjectStatus.NORMAL)
        else:
            result += self.payload

        return result


@dataclass
class SubgroupHeader:
    """SUBGROUP_HEADER"""

    header_type: SubgroupHeaderType
    track_alias: int
    group_id: int
    subgroup_id: int | None = None
    publisher_priority: int | None = None

    def encode(self) -> bytes:
        """SUBGROUP_HEADER をエンコードする"""
        result = encode_varint(self.header_type.value)
        result += encode_varint(self.track_alias)
        result += encode_varint(self.group_id)

        if self.header_type.subgroup_id_mode() == "present":
            if self.subgroup_id is None:
                raise ValueError("Subgroup ID が必要です")
            result += encode_varint(self.subgroup_id)

        if self.header_type.has_priority():
            result += bytes([self.publisher_priority or 0])

        return result


@dataclass
class SubgroupObject:
    """Subgroup Stream の Object"""

    object_id_delta: int = 0
    extensions: ObjectExtensions | None = None
    payload_length: int = 0
    object_status: ObjectStatus | None = None
    payload: bytes = b""

    def encode(self, extensions_present: bool) -> bytes:
        """Subgroup Object をエンコードする"""
        result = encode_varint(self.object_id_delta)

        if extensions_present:
            if self.extensions:
                result += self.extensions.encode()
            else:
                result += encode_varint(0)

        result += encode_varint(self.payload_length)

        if self.payload_length == 0 and self.object_status is not None:
            result += encode_varint(self.object_status)
        else:
            result += self.payload

        return result


@dataclass
class FetchHeader:
    """FETCH_HEADER"""

    STREAM_TYPE: ClassVar[int] = 0x05
    request_id: int

    def encode(self) -> bytes:
        """FETCH_HEADER をエンコードする"""
        result = encode_varint(self.STREAM_TYPE)
        result += encode_varint(self.request_id)
        return result


@dataclass
class FetchObject:
    """Fetch Stream の Object"""

    serialization_flags: int = 0
    group_id: int = 0
    subgroup_id: int = 0
    object_id: int = 0
    publisher_priority: int | None = None
    extensions: ObjectExtensions | None = None
    payload_length: int = 0
    object_status: ObjectStatus | None = None
    payload: bytes = b""

    def encode(self) -> bytes:
        """Fetch Object をエンコードする"""
        result = bytes([self.serialization_flags])

        if self.serialization_flags & FetchSerializationFlags.GROUP_ID_PRESENT:
            result += encode_varint(self.group_id)

        # Subgroup ID のエンコード
        subgroup_mode = self.serialization_flags & 0x03
        if subgroup_mode == 0x03:
            # Subgroup ID Present
            result += encode_varint(self.subgroup_id)

        if self.serialization_flags & FetchSerializationFlags.OBJECT_ID_PRESENT:
            result += encode_varint(self.object_id)

        if self.serialization_flags & FetchSerializationFlags.PRIORITY_PRESENT:
            result += bytes([self.publisher_priority or 0])

        if self.serialization_flags & FetchSerializationFlags.EXTENSIONS_PRESENT:
            if self.extensions:
                result += self.extensions.encode()
            else:
                result += encode_varint(0)

        result += encode_varint(self.payload_length)

        if self.payload_length == 0 and self.object_status is not None:
            result += encode_varint(self.object_status)
        else:
            result += self.payload

        return result


def decode_datagram(data: bytes, offset: int = 0) -> tuple[ObjectDatagram, int]:
    """OBJECT_DATAGRAM をデコードする"""
    datagram_type_val, consumed = decode_varint(data, offset)
    total_consumed = consumed

    datagram_type = DatagramType(datagram_type_val)

    track_alias, consumed = decode_varint(data, offset + total_consumed)
    total_consumed += consumed

    group_id, consumed = decode_varint(data, offset + total_consumed)
    total_consumed += consumed

    object_id = 0
    if datagram_type.has_object_id():
        object_id, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed

    publisher_priority = None
    if datagram_type.has_priority():
        publisher_priority = data[offset + total_consumed]
        total_consumed += 1

    extensions = None
    if datagram_type.has_extensions():
        extensions, consumed = ObjectExtensions.decode(data, offset + total_consumed)
        total_consumed += consumed

    object_status = None
    payload = b""

    if datagram_type.has_status():
        status_val, consumed = decode_varint(data, offset + total_consumed)
        object_status = ObjectStatus(status_val)
        total_consumed += consumed
    else:
        # 残りが全て payload
        payload = bytes(data[offset + total_consumed :])
        total_consumed = len(data) - offset

    return (
        ObjectDatagram(
            datagram_type=datagram_type,
            track_alias=track_alias,
            group_id=group_id,
            object_id=object_id,
            publisher_priority=publisher_priority,
            extensions=extensions,
            object_status=object_status,
            end_of_group=datagram_type.is_end_of_group(),
            payload=payload,
        ),
        total_consumed,
    )


def decode_subgroup_header(data: bytes, offset: int = 0) -> tuple[SubgroupHeader, int]:
    """SUBGROUP_HEADER をデコードする"""
    header_type_val, consumed = decode_varint(data, offset)
    total_consumed = consumed

    header_type = SubgroupHeaderType(header_type_val)

    track_alias, consumed = decode_varint(data, offset + total_consumed)
    total_consumed += consumed

    group_id, consumed = decode_varint(data, offset + total_consumed)
    total_consumed += consumed

    subgroup_id = None
    mode = header_type.subgroup_id_mode()
    if mode == "zero":
        subgroup_id = 0
    elif mode == "present":
        subgroup_id, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
    # mode == "first_object" の場合は最初のオブジェクトの Object ID で決まる

    publisher_priority = None
    if header_type.has_priority():
        publisher_priority = data[offset + total_consumed]
        total_consumed += 1

    return (
        SubgroupHeader(
            header_type=header_type,
            track_alias=track_alias,
            group_id=group_id,
            subgroup_id=subgroup_id,
            publisher_priority=publisher_priority,
        ),
        total_consumed,
    )


def decode_subgroup_object(
    data: bytes, extensions_present: bool, offset: int = 0
) -> tuple[SubgroupObject, int]:
    """Subgroup Object をデコードする"""
    object_id_delta, consumed = decode_varint(data, offset)
    total_consumed = consumed

    extensions = None
    if extensions_present:
        extensions, consumed = ObjectExtensions.decode(data, offset + total_consumed)
        total_consumed += consumed

    payload_length, consumed = decode_varint(data, offset + total_consumed)
    total_consumed += consumed

    object_status = None
    payload = b""

    if payload_length == 0:
        # Status のみ
        status_val, consumed = decode_varint(data, offset + total_consumed)
        object_status = ObjectStatus(status_val)
        total_consumed += consumed
    else:
        payload = bytes(data[offset + total_consumed : offset + total_consumed + payload_length])
        total_consumed += payload_length

    return (
        SubgroupObject(
            object_id_delta=object_id_delta,
            extensions=extensions,
            payload_length=payload_length,
            object_status=object_status,
            payload=payload,
        ),
        total_consumed,
    )


def decode_fetch_header(data: bytes, offset: int = 0) -> tuple[FetchHeader, int]:
    """FETCH_HEADER をデコードする"""
    stream_type, consumed = decode_varint(data, offset)
    total_consumed = consumed

    if stream_type != FetchHeader.STREAM_TYPE:
        raise ValueError(f"不正な FETCH_HEADER Type: {stream_type}")

    request_id, consumed = decode_varint(data, offset + total_consumed)
    total_consumed += consumed

    return FetchHeader(request_id=request_id), total_consumed


def decode_fetch_object(
    data: bytes,
    first_object: bool,
    offset: int = 0,
    prior_group_id: int = 0,
    prior_subgroup_id: int = 0,
    prior_object_id: int = 0,
    prior_priority: int = 0,
) -> tuple[FetchObject, int]:
    """Fetch Object をデコードする"""
    serialization_flags = data[offset]
    total_consumed = 1

    # Group ID
    if serialization_flags & FetchSerializationFlags.GROUP_ID_PRESENT:
        group_id, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
    else:
        if first_object:
            raise ValueError("最初のオブジェクトで Group ID がない")
        group_id = prior_group_id

    # Subgroup ID
    subgroup_mode = serialization_flags & 0x03
    if subgroup_mode == 0x00:
        # Subgroup ID is zero
        subgroup_id = 0
    elif subgroup_mode == 0x01:
        # Prior Subgroup ID
        if first_object:
            raise ValueError("最初のオブジェクトで prior Subgroup ID を参照")
        subgroup_id = prior_subgroup_id
    elif subgroup_mode == 0x02:
        # Prior Subgroup ID + 1
        if first_object:
            raise ValueError("最初のオブジェクトで prior Subgroup ID + 1 を参照")
        subgroup_id = prior_subgroup_id + 1
    else:
        # Subgroup ID Present
        subgroup_id, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed

    # Object ID
    if serialization_flags & FetchSerializationFlags.OBJECT_ID_PRESENT:
        object_id, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
    else:
        if first_object:
            raise ValueError("最初のオブジェクトで Object ID がない")
        object_id = prior_object_id + 1

    # Priority
    if serialization_flags & FetchSerializationFlags.PRIORITY_PRESENT:
        publisher_priority = data[offset + total_consumed]
        total_consumed += 1
    else:
        if first_object:
            raise ValueError("最初のオブジェクトで Priority がない")
        publisher_priority = prior_priority

    # Extensions
    extensions = None
    if serialization_flags & FetchSerializationFlags.EXTENSIONS_PRESENT:
        extensions, consumed = ObjectExtensions.decode(data, offset + total_consumed)
        total_consumed += consumed

    # Payload Length
    payload_length, consumed = decode_varint(data, offset + total_consumed)
    total_consumed += consumed

    # Status or Payload
    object_status = None
    payload = b""

    if payload_length == 0:
        status_val, consumed = decode_varint(data, offset + total_consumed)
        object_status = ObjectStatus(status_val)
        total_consumed += consumed
    else:
        payload = bytes(data[offset + total_consumed : offset + total_consumed + payload_length])
        total_consumed += payload_length

    return (
        FetchObject(
            serialization_flags=serialization_flags,
            group_id=group_id,
            subgroup_id=subgroup_id,
            object_id=object_id,
            publisher_priority=publisher_priority,
            extensions=extensions,
            payload_length=payload_length,
            object_status=object_status,
            payload=payload,
        ),
        total_consumed,
    )
