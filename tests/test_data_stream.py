"""Data Stream のテスト"""

import pytest

from moqt.data_stream import (
    DatagramType,
    ObjectDatagram,
    ObjectStatus,
    SubgroupHeader,
    SubgroupHeaderType,
    SubgroupObject,
    FetchHeader,
    FetchObject,
    FetchSerializationFlags,
    ObjectExtensions,
    decode_datagram,
    decode_subgroup_header,
    decode_subgroup_object,
    decode_fetch_header,
    decode_fetch_object,
)


class TestObjectExtensions:
    """Object Extension Headers のテスト"""

    def test_encode_decode_empty(self):
        """空の拡張"""
        ext = ObjectExtensions(headers={})
        encoded = ext.encode()
        decoded, consumed = ObjectExtensions.decode(encoded)
        assert decoded.headers == {}
        assert consumed == len(encoded)

    def test_encode_decode_single_even_type(self):
        """偶数タイプ (varint 値) の拡張"""
        # Capture Timestamp (ID: 2)
        ext = ObjectExtensions(headers={2: b"\x80\x00\x01\x00"})
        encoded = ext.encode()
        decoded, consumed = ObjectExtensions.decode(encoded)
        assert 2 in decoded.headers
        assert decoded.headers[2] == b"\x80\x00\x01\x00"

    def test_encode_decode_single_odd_type(self):
        """奇数タイプ (Length プレフィックス付き) の拡張"""
        # Video Config (ID: 13)
        config_data = b"\x01\x02\x03\x04\x05"
        ext = ObjectExtensions(headers={13: config_data})
        encoded = ext.encode()
        decoded, consumed = ObjectExtensions.decode(encoded)
        assert 13 in decoded.headers
        assert decoded.headers[13] == config_data

    def test_encode_decode_multiple(self):
        """複数の拡張"""
        # 偶数タイプは varint 値として格納
        # 0x40, 0x64 = 2 バイト varint (値: 100)
        # 0x10 = 1 バイト varint (値: 16)
        # 0x3F = 1 バイト varint (値: 63)
        ext = ObjectExtensions(
            headers={
                2: b"\x40\x64",
                # Capture Timestamp (2 バイト varint)
                4: b"\x10",
                # Video Frame Marking (1 バイト varint)
                6: b"\x3F",
                # Audio Level (1 バイト varint)
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


class TestObjectDatagram:
    """OBJECT_DATAGRAM のテスト"""

    def test_encode_decode_basic(self):
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

    def test_encode_decode_no_object_id(self):
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

    def test_encode_decode_with_extensions(self):
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

    def test_encode_decode_end_of_group(self):
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

    def test_encode_decode_status_only(self):
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


class TestSubgroupHeader:
    """SUBGROUP_HEADER のテスト"""

    def test_encode_decode_type_0x10(self):
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

    def test_encode_decode_type_0x14(self):
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

    def test_encode_decode_no_priority(self):
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


class TestSubgroupObject:
    """Subgroup Object のテスト"""

    def test_encode_decode_basic(self):
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

    def test_encode_decode_with_delta(self):
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

    def test_encode_decode_with_extensions(self):
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

    def test_encode_decode_status_only(self):
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


class TestFetchHeader:
    """FETCH_HEADER のテスト"""

    def test_encode_decode(self):
        """FETCH_HEADER のエンコード/デコード"""
        header = FetchHeader(request_id=42)
        encoded = header.encode()
        decoded, consumed = decode_fetch_header(encoded)

        assert decoded.request_id == 42


class TestFetchObject:
    """Fetch Object のテスト"""

    def test_encode_decode_basic(self):
        """基本的な Fetch Object"""
        # 0x1F = GROUP_ID_PRESENT (0x08) | PRIORITY_PRESENT (0x10) | OBJECT_ID_PRESENT (0x04) | SUBGROUP_PRESENT (0x03)
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

    def test_encode_decode_with_extensions(self):
        """Extension 付き Fetch Object"""
        # 偶数タイプは varint 値 (0x20 = 1 バイト varint、値 32)
        ext = ObjectExtensions(headers={6: b"\x20"})
        # 0x3F = GROUP_ID_PRESENT | PRIORITY_PRESENT | OBJECT_ID_PRESENT | SUBGROUP_PRESENT | EXTENSIONS_PRESENT
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

    def test_decode_with_prior_values(self):
        """前のオブジェクトの値を参照する Fetch Object"""
        # 2 番目のオブジェクト (前のオブジェクトの値を継承)
        # 0x01 = SUBGROUP_PRIOR (Subgroup ID は前のオブジェクトと同じ)
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

        # 前の値を渡してデコード
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


class TestDatagramTypes:
    """各 DatagramType のテスト"""

    def test_all_datagram_types_roundtrip(self):
        """全ての Datagram Type のラウンドトリップテスト"""
        # Payload 系のタイプ
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


class TestSubgroupHeaderTypes:
    """各 SubgroupHeaderType のテスト"""

    def test_subgroup_id_from_first_object(self):
        """Subgroup ID が First Object ID の場合"""
        header = SubgroupHeader(
            header_type=SubgroupHeaderType.SUBGROUP_ID_FIRST_OBJECT_NO_EXT_PRIORITY,
            track_alias=1,
            group_id=10,
            subgroup_id=None,
            # 最初の Object ID で決まる
            publisher_priority=128,
        )
        encoded = header.encode()
        decoded, consumed = decode_subgroup_header(encoded)

        assert decoded.track_alias == 1
        assert decoded.group_id == 10
        # Subgroup ID は最初のオブジェクトの Object ID から決まる
        assert decoded.subgroup_id is None
