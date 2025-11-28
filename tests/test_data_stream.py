"""Data Stream のテスト"""

from moqt.data_stream import (
    DatagramType,
    ObjectDatagram,
    ObjectStatus,
    SubgroupHeader,
    SubgroupHeaderType,
    SubgroupObject,
    FetchHeader,
    FetchObject,
    ObjectExtensions,
    decode_datagram,
    decode_subgroup_header,
    decode_subgroup_object,
    decode_fetch_header,
    decode_fetch_object,
)


# ObjectExtensions のテスト


def test_object_extensions_encode_decode_empty():
    """空の拡張"""
    ext = ObjectExtensions(headers={})
    encoded = ext.encode()
    decoded, consumed = ObjectExtensions.decode(encoded)
    assert decoded.headers == {}
    assert consumed == len(encoded)


def test_object_extensions_encode_decode_single_even_type():
    """偶数タイプ (varint 値) の拡張"""
    # Capture Timestamp (ID: 2)
    ext = ObjectExtensions(headers={2: b"\x80\x00\x01\x00"})
    encoded = ext.encode()
    decoded, consumed = ObjectExtensions.decode(encoded)
    assert 2 in decoded.headers
    assert decoded.headers[2] == b"\x80\x00\x01\x00"


def test_object_extensions_encode_decode_single_odd_type():
    """奇数タイプ (Length プレフィックス付き) の拡張"""
    # Video Config (ID: 13)
    config_data = b"\x01\x02\x03\x04\x05"
    ext = ObjectExtensions(headers={13: config_data})
    encoded = ext.encode()
    decoded, consumed = ObjectExtensions.decode(encoded)
    assert 13 in decoded.headers
    assert decoded.headers[13] == config_data


def test_object_extensions_encode_decode_multiple():
    """複数の拡張"""
    ext = ObjectExtensions(
        headers={
            2: b"\x40\x64",
            4: b"\x10",
            6: b"\x3F",
        }
    )
    encoded = ext.encode()
    decoded, consumed = ObjectExtensions.decode(encoded)
    assert len(decoded.headers) == 3
    assert 2 in decoded.headers
    assert decoded.headers[2] == b"\x40\x64"
    assert 4 in decoded.headers
    assert decoded.headers[4] == b"\x10"
    assert 6 in decoded.headers
    assert decoded.headers[6] == b"\x3F"


# ObjectDatagram のテスト


def test_object_datagram_encode_decode_basic():
    """基本的な OBJECT_DATAGRAM"""
    datagram = ObjectDatagram(
        datagram_type=DatagramType.OBJECT_ID_PRIORITY_PAYLOAD,
        track_alias=1,
        group_id=10,
        object_id=5,
        publisher_priority=128,
        payload=b"hello",
    )
    encoded = datagram.encode()
    decoded, consumed = decode_datagram(encoded)

    assert isinstance(decoded, ObjectDatagram)
    assert decoded.track_alias == 1
    assert decoded.group_id == 10
    assert decoded.object_id == 5
    assert decoded.publisher_priority == 128
    assert decoded.payload == b"hello"


def test_object_datagram_encode_decode_no_object_id():
    """Object ID なしの OBJECT_DATAGRAM"""
    datagram = ObjectDatagram(
        datagram_type=DatagramType.NO_OBJECT_ID_PRIORITY_PAYLOAD,
        track_alias=2,
        group_id=20,
        object_id=0,
        publisher_priority=64,
        payload=b"test",
    )
    encoded = datagram.encode()
    decoded, consumed = decode_datagram(encoded)

    assert decoded.track_alias == 2
    assert decoded.group_id == 20
    assert decoded.object_id == 0
    assert decoded.publisher_priority == 64
    assert decoded.payload == b"test"


def test_object_datagram_encode_decode_with_extensions():
    """Extension 付き OBJECT_DATAGRAM"""
    ext = ObjectExtensions(headers={2: b"\x40\x64"})
    datagram = ObjectDatagram(
        datagram_type=DatagramType.OBJECT_ID_EXTENSIONS_PRIORITY_PAYLOAD,
        track_alias=3,
        group_id=30,
        object_id=15,
        publisher_priority=200,
        extensions=ext,
        payload=b"data",
    )
    encoded = datagram.encode()
    decoded, consumed = decode_datagram(encoded)

    assert decoded.extensions is not None
    assert 2 in decoded.extensions.headers
    assert decoded.payload == b"data"


def test_object_datagram_encode_decode_end_of_group():
    """End of Group の OBJECT_DATAGRAM"""
    datagram = ObjectDatagram(
        datagram_type=DatagramType.OBJECT_ID_PRIORITY_PAYLOAD_END_OF_GROUP,
        track_alias=4,
        group_id=40,
        object_id=100,
        publisher_priority=255,
        end_of_group=True,
        payload=b"last",
    )
    encoded = datagram.encode()
    decoded, consumed = decode_datagram(encoded)

    assert decoded.end_of_group is True
    assert decoded.payload == b"last"


def test_object_datagram_encode_decode_status_only():
    """Status のみの OBJECT_DATAGRAM (Object Does Not Exist)"""
    datagram = ObjectDatagram(
        datagram_type=DatagramType.OBJECT_ID_PRIORITY_STATUS,
        track_alias=5,
        group_id=50,
        object_id=25,
        publisher_priority=128,
        object_status=ObjectStatus.OBJECT_DOES_NOT_EXIST,
    )
    encoded = datagram.encode()
    decoded, consumed = decode_datagram(encoded)

    assert decoded.object_status == ObjectStatus.OBJECT_DOES_NOT_EXIST
    assert decoded.payload == b""


# SubgroupHeader のテスト


def test_subgroup_header_encode_decode_type_0x10():
    """Type 0x10: Subgroup ID = 0, No Extensions, No End of Group"""
    header = SubgroupHeader(
        header_type=SubgroupHeaderType.SUBGROUP_ID_ZERO_NO_EXT_PRIORITY,
        track_alias=1,
        group_id=10,
        subgroup_id=0,
        publisher_priority=128,
    )
    encoded = header.encode()
    decoded, consumed = decode_subgroup_header(encoded)

    assert decoded.track_alias == 1
    assert decoded.group_id == 10
    assert decoded.subgroup_id == 0
    assert decoded.publisher_priority == 128


def test_subgroup_header_encode_decode_type_0x14():
    """Type 0x14: Subgroup ID Present"""
    header = SubgroupHeader(
        header_type=SubgroupHeaderType.SUBGROUP_ID_PRESENT_NO_EXT_PRIORITY,
        track_alias=2,
        group_id=20,
        subgroup_id=5,
        publisher_priority=64,
    )
    encoded = header.encode()
    decoded, consumed = decode_subgroup_header(encoded)

    assert decoded.track_alias == 2
    assert decoded.group_id == 20
    assert decoded.subgroup_id == 5
    assert decoded.publisher_priority == 64


def test_subgroup_header_encode_decode_no_priority():
    """Priority なしの SUBGROUP_HEADER"""
    header = SubgroupHeader(
        header_type=SubgroupHeaderType.SUBGROUP_ID_ZERO_NO_EXT_NO_PRIORITY,
        track_alias=3,
        group_id=30,
        subgroup_id=0,
        publisher_priority=None,
    )
    encoded = header.encode()
    decoded, consumed = decode_subgroup_header(encoded)

    assert decoded.track_alias == 3
    assert decoded.group_id == 30
    assert decoded.publisher_priority is None


# SubgroupObject のテスト


def test_subgroup_object_encode_decode_basic():
    """基本的な Subgroup Object"""
    obj = SubgroupObject(
        object_id_delta=0,
        payload_length=5,
        payload=b"hello",
    )
    encoded = obj.encode(extensions_present=False)
    decoded, consumed = decode_subgroup_object(encoded, extensions_present=False)

    assert decoded.object_id_delta == 0
    assert decoded.payload == b"hello"


def test_subgroup_object_encode_decode_with_delta():
    """Object ID Delta が 0 より大きい場合"""
    obj = SubgroupObject(
        object_id_delta=5,
        payload_length=4,
        payload=b"test",
    )
    encoded = obj.encode(extensions_present=False)
    decoded, consumed = decode_subgroup_object(encoded, extensions_present=False)

    assert decoded.object_id_delta == 5
    assert decoded.payload == b"test"


def test_subgroup_object_encode_decode_with_extensions():
    """Extension 付き Subgroup Object"""
    ext = ObjectExtensions(headers={4: b"\x20"})
    obj = SubgroupObject(
        object_id_delta=1,
        extensions=ext,
        payload_length=3,
        payload=b"abc",
    )
    encoded = obj.encode(extensions_present=True)
    decoded, consumed = decode_subgroup_object(encoded, extensions_present=True)

    assert decoded.extensions is not None
    assert 4 in decoded.extensions.headers
    assert decoded.payload == b"abc"


def test_subgroup_object_encode_decode_status_only():
    """Status のみ (ペイロードなし)"""
    obj = SubgroupObject(
        object_id_delta=2,
        payload_length=0,
        object_status=ObjectStatus.END_OF_GROUP,
    )
    encoded = obj.encode(extensions_present=False)
    decoded, consumed = decode_subgroup_object(encoded, extensions_present=False)

    assert decoded.object_status == ObjectStatus.END_OF_GROUP
    assert decoded.payload == b""


# FetchHeader のテスト


def test_fetch_header_encode_decode():
    """FETCH_HEADER のエンコード/デコード"""
    header = FetchHeader(request_id=42)
    encoded = header.encode()
    decoded, consumed = decode_fetch_header(encoded)

    assert decoded.request_id == 42


# FetchObject のテスト


def test_fetch_object_encode_decode_basic():
    """基本的な Fetch Object"""
    obj = FetchObject(
        serialization_flags=0x1F,
        group_id=10,
        subgroup_id=5,
        object_id=3,
        publisher_priority=128,
        payload_length=4,
        payload=b"data",
    )
    encoded = obj.encode()
    decoded, consumed = decode_fetch_object(encoded, first_object=True)

    assert decoded.group_id == 10
    assert decoded.subgroup_id == 5
    assert decoded.object_id == 3
    assert decoded.publisher_priority == 128
    assert decoded.payload == b"data"


def test_fetch_object_encode_decode_with_extensions():
    """Extension 付き Fetch Object"""
    ext = ObjectExtensions(headers={6: b"\x20"})
    obj = FetchObject(
        serialization_flags=0x3F,
        group_id=20,
        subgroup_id=10,
        object_id=7,
        publisher_priority=64,
        extensions=ext,
        payload_length=5,
        payload=b"hello",
    )
    encoded = obj.encode()
    decoded, consumed = decode_fetch_object(encoded, first_object=True)

    assert decoded.extensions is not None
    assert 6 in decoded.extensions.headers
    assert decoded.payload == b"hello"


def test_fetch_object_decode_with_prior_values():
    """前のオブジェクトの値を参照する Fetch Object"""
    second = FetchObject(
        serialization_flags=0x01,
        group_id=100,
        subgroup_id=50,
        object_id=11,
        publisher_priority=200,
        payload_length=3,
        payload=b"def",
    )
    encoded = second.encode()

    decoded, consumed = decode_fetch_object(
        encoded,
        first_object=False,
        prior_group_id=100,
        prior_subgroup_id=50,
        prior_object_id=10,
        prior_priority=200,
    )

    assert decoded.group_id == 100
    assert decoded.subgroup_id == 50
    assert decoded.object_id == 11
    assert decoded.publisher_priority == 200


# DatagramType のテスト


def test_datagram_types_all_roundtrip():
    """全ての Datagram Type のラウンドトリップテスト"""
    payload_types = [
        DatagramType.OBJECT_ID_PRIORITY_PAYLOAD,
        DatagramType.OBJECT_ID_EXTENSIONS_PRIORITY_PAYLOAD,
        DatagramType.OBJECT_ID_PRIORITY_PAYLOAD_END_OF_GROUP,
        DatagramType.OBJECT_ID_EXTENSIONS_PRIORITY_PAYLOAD_END_OF_GROUP,
        DatagramType.NO_OBJECT_ID_PRIORITY_PAYLOAD,
        DatagramType.NO_OBJECT_ID_EXTENSIONS_PRIORITY_PAYLOAD,
        DatagramType.NO_OBJECT_ID_PRIORITY_PAYLOAD_END_OF_GROUP,
        DatagramType.NO_OBJECT_ID_EXTENSIONS_PRIORITY_PAYLOAD_END_OF_GROUP,
    ]

    for dtype in payload_types:
        has_object_id = dtype.has_object_id()
        has_extensions = dtype.has_extensions()
        has_priority = dtype.has_priority()
        is_end_of_group = dtype.is_end_of_group()

        ext = ObjectExtensions(headers={2: b"\x01"}) if has_extensions else None

        datagram = ObjectDatagram(
            datagram_type=dtype,
            track_alias=1,
            group_id=10,
            object_id=5 if has_object_id else 0,
            publisher_priority=128 if has_priority else None,
            extensions=ext,
            end_of_group=is_end_of_group,
            payload=b"test",
        )

        encoded = datagram.encode()
        decoded, _ = decode_datagram(encoded)

        assert decoded.track_alias == 1
        assert decoded.group_id == 10
        if has_object_id:
            assert decoded.object_id == 5
        if has_priority:
            assert decoded.publisher_priority == 128
        assert decoded.end_of_group == is_end_of_group
        assert decoded.payload == b"test"


# SubgroupHeaderType のテスト


def test_subgroup_header_types_subgroup_id_from_first_object():
    """Subgroup ID が First Object ID の場合"""
    header = SubgroupHeader(
        header_type=SubgroupHeaderType.SUBGROUP_ID_FIRST_OBJECT_NO_EXT_PRIORITY,
        track_alias=1,
        group_id=10,
        subgroup_id=None,
        publisher_priority=128,
    )
    encoded = header.encode()
    decoded, consumed = decode_subgroup_header(encoded)

    assert decoded.track_alias == 1
    assert decoded.group_id == 10
    assert decoded.subgroup_id is None
