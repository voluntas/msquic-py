"""MOQT モジュールのテスト"""

import pytest

from moqt.varint import decode_varint, encode_varint, varint_size
from moqt.message import (
    ClientSetup,
    ServerSetup,
    Goaway,
    MaxRequestId,
    RequestsBlocked,
    RequestOk,
    RequestError,
    Subscribe,
    SubscribeOk,
    Unsubscribe,
    Publish,
    PublishOk,
    PublishDone,
    Parameter,
    ParameterType,
    TrackNamespace,
    Location,
    decode_control_message,
)


class TestVarint:
    """varint エンコード/デコードのテスト"""

    def test_encode_1byte(self):
        """1 バイト varint のエンコード"""
        assert encode_varint(0) == b"\x00"
        assert encode_varint(1) == b"\x01"
        assert encode_varint(63) == b"\x3f"

    def test_encode_2bytes(self):
        """2 バイト varint のエンコード"""
        assert encode_varint(64) == b"\x40\x40"
        assert encode_varint(16383) == b"\x7f\xff"

    def test_encode_4bytes(self):
        """4 バイト varint のエンコード"""
        assert encode_varint(16384) == b"\x80\x00\x40\x00"
        assert encode_varint(1073741823) == b"\xbf\xff\xff\xff"

    def test_encode_8bytes(self):
        """8 バイト varint のエンコード"""
        assert encode_varint(1073741824) == b"\xc0\x00\x00\x00\x40\x00\x00\x00"

    def test_decode_1byte(self):
        """1 バイト varint のデコード"""
        assert decode_varint(b"\x00") == (0, 1)
        assert decode_varint(b"\x01") == (1, 1)
        assert decode_varint(b"\x3f") == (63, 1)

    def test_decode_2bytes(self):
        """2 バイト varint のデコード"""
        assert decode_varint(b"\x40\x40") == (64, 2)
        assert decode_varint(b"\x7f\xff") == (16383, 2)

    def test_decode_4bytes(self):
        """4 バイト varint のデコード"""
        assert decode_varint(b"\x80\x00\x40\x00") == (16384, 4)
        assert decode_varint(b"\xbf\xff\xff\xff") == (1073741823, 4)

    def test_decode_8bytes(self):
        """8 バイト varint のデコード"""
        assert decode_varint(b"\xc0\x00\x00\x00\x40\x00\x00\x00") == (1073741824, 8)

    def test_roundtrip(self):
        """エンコード/デコードのラウンドトリップ"""
        test_values = [0, 1, 63, 64, 16383, 16384, 1073741823, 1073741824]
        for value in test_values:
            encoded = encode_varint(value)
            decoded, _ = decode_varint(encoded)
            assert decoded == value

    def test_varint_size(self):
        """varint サイズの計算"""
        assert varint_size(0) == 1
        assert varint_size(63) == 1
        assert varint_size(64) == 2
        assert varint_size(16383) == 2
        assert varint_size(16384) == 4
        assert varint_size(1073741823) == 4
        assert varint_size(1073741824) == 8

    def test_negative_value_error(self):
        """負の値でエラー"""
        with pytest.raises(ValueError):
            encode_varint(-1)

    def test_too_large_value_error(self):
        """大きすぎる値でエラー"""
        with pytest.raises(ValueError):
            encode_varint(2**62)


class TestLocation:
    """Location のテスト"""

    def test_encode_decode(self):
        """エンコード/デコードのテスト"""
        loc = Location(group=10, object=20)
        encoded = loc.encode()
        decoded, consumed = Location.decode(encoded)
        assert decoded.group == 10
        assert decoded.object == 20


class TestTrackNamespace:
    """TrackNamespace のテスト"""

    def test_encode_decode_empty(self):
        """空の namespace"""
        ns = TrackNamespace(tuple=[])
        encoded = ns.encode()
        decoded, consumed = TrackNamespace.decode(encoded)
        assert decoded.tuple == []

    def test_encode_decode_single(self):
        """単一要素の namespace"""
        ns = TrackNamespace(tuple=[b"test"])
        encoded = ns.encode()
        decoded, consumed = TrackNamespace.decode(encoded)
        assert decoded.tuple == [b"test"]

    def test_encode_decode_multiple(self):
        """複数要素の namespace"""
        ns = TrackNamespace(tuple=[b"foo", b"bar", b"baz"])
        encoded = ns.encode()
        decoded, consumed = TrackNamespace.decode(encoded)
        assert decoded.tuple == [b"foo", b"bar", b"baz"]


class TestClientSetup:
    """CLIENT_SETUP メッセージのテスト"""

    def test_encode_decode_empty(self):
        """パラメータなしの CLIENT_SETUP"""
        setup = ClientSetup()
        encoded = setup.encode()
        decoded, consumed = decode_control_message(encoded)
        assert isinstance(decoded, ClientSetup)
        assert len(decoded.parameters) == 0

    def test_encode_decode_with_path(self):
        """PATH パラメータ付きの CLIENT_SETUP"""
        setup = ClientSetup()
        setup.set_path("/test/path")
        encoded = setup.encode()
        decoded, consumed = decode_control_message(encoded)
        assert isinstance(decoded, ClientSetup)
        path_param = decoded.get_parameter(ParameterType.PATH)
        assert path_param is not None
        assert path_param.value == b"/test/path"

    def test_encode_decode_with_max_request_id(self):
        """MAX_REQUEST_ID パラメータ付きの CLIENT_SETUP"""
        setup = ClientSetup()
        setup.set_max_request_id(100)
        encoded = setup.encode()
        decoded, consumed = decode_control_message(encoded)
        assert isinstance(decoded, ClientSetup)
        max_id_param = decoded.get_parameter(ParameterType.MAX_REQUEST_ID)
        assert max_id_param is not None


class TestServerSetup:
    """SERVER_SETUP メッセージのテスト"""

    def test_encode_decode_empty(self):
        """パラメータなしの SERVER_SETUP"""
        setup = ServerSetup()
        encoded = setup.encode()
        decoded, consumed = decode_control_message(encoded)
        assert isinstance(decoded, ServerSetup)
        assert len(decoded.parameters) == 0

    def test_encode_decode_with_max_request_id(self):
        """MAX_REQUEST_ID パラメータ付きの SERVER_SETUP"""
        setup = ServerSetup()
        setup.set_max_request_id(200)
        encoded = setup.encode()
        decoded, consumed = decode_control_message(encoded)
        assert isinstance(decoded, ServerSetup)


class TestGoaway:
    """GOAWAY メッセージのテスト"""

    def test_encode_decode_empty_uri(self):
        """空の URI"""
        goaway = Goaway()
        encoded = goaway.encode()
        decoded, consumed = decode_control_message(encoded)
        assert isinstance(decoded, Goaway)
        assert decoded.new_session_uri == ""

    def test_encode_decode_with_uri(self):
        """URI 付き"""
        goaway = Goaway(new_session_uri="moqt://example.com/new")
        encoded = goaway.encode()
        decoded, consumed = decode_control_message(encoded)
        assert isinstance(decoded, Goaway)
        assert decoded.new_session_uri == "moqt://example.com/new"


class TestMaxRequestId:
    """MAX_REQUEST_ID メッセージのテスト"""

    def test_encode_decode(self):
        """エンコード/デコード"""
        msg = MaxRequestId(request_id=1000)
        encoded = msg.encode()
        decoded, consumed = decode_control_message(encoded)
        assert isinstance(decoded, MaxRequestId)
        assert decoded.request_id == 1000


class TestRequestOk:
    """REQUEST_OK メッセージのテスト"""

    def test_encode_decode(self):
        """エンコード/デコード"""
        msg = RequestOk(request_id=42)
        encoded = msg.encode()
        decoded, consumed = decode_control_message(encoded)
        assert isinstance(decoded, RequestOk)
        assert decoded.request_id == 42


class TestRequestError:
    """REQUEST_ERROR メッセージのテスト"""

    def test_encode_decode(self):
        """エンコード/デコード"""
        msg = RequestError(request_id=42, error_code=1, reason_phrase="Test error")
        encoded = msg.encode()
        decoded, consumed = decode_control_message(encoded)
        assert isinstance(decoded, RequestError)
        assert decoded.request_id == 42
        assert decoded.error_code == 1
        assert decoded.reason_phrase == "Test error"


class TestSubscribe:
    """SUBSCRIBE メッセージのテスト"""

    def test_encode_decode(self):
        """エンコード/デコード"""
        msg = Subscribe(
            request_id=10,
            track_alias=1,
            track_namespace=TrackNamespace(tuple=[b"live", b"stream"]),
            track_name=b"video",
        )
        encoded = msg.encode()
        decoded, consumed = decode_control_message(encoded)
        assert isinstance(decoded, Subscribe)
        assert decoded.request_id == 10
        assert decoded.track_alias == 1
        assert decoded.track_namespace.tuple == [b"live", b"stream"]
        assert decoded.track_name == b"video"


class TestPublish:
    """PUBLISH メッセージのテスト"""

    def test_encode_decode(self):
        """エンコード/デコード"""
        msg = Publish(
            request_id=20,
            track_alias=2,
            track_namespace=TrackNamespace(tuple=[b"broadcast"]),
            track_name=b"audio",
        )
        encoded = msg.encode()
        decoded, consumed = decode_control_message(encoded)
        assert isinstance(decoded, Publish)
        assert decoded.request_id == 20
        assert decoded.track_alias == 2
        assert decoded.track_namespace.tuple == [b"broadcast"]
        assert decoded.track_name == b"audio"


class TestPublishDone:
    """PUBLISH_DONE メッセージのテスト"""

    def test_encode_decode(self):
        """エンコード/デコード"""
        msg = PublishDone(request_id=30, status_code=0, reason_phrase="Completed")
        encoded = msg.encode()
        decoded, consumed = decode_control_message(encoded)
        assert isinstance(decoded, PublishDone)
        assert decoded.request_id == 30
        assert decoded.status_code == 0
        assert decoded.reason_phrase == "Completed"
