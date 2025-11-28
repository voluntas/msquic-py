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
    Publish,
    PublishDone,
    ParameterType,
    TrackNamespace,
    Location,
    ErrorCode,
    TrackStatusCode,
    StreamType,
    FilterType,
    GroupOrder,
    Fetch,
    FetchOk,
    FetchCancel,
    TrackStatus,
    PublishNamespace,
    PublishNamespaceDone,
    PublishNamespaceCancel,
    SubscribeNamespace,
    UnsubscribeNamespace,
    SubscribeUpdate,
    decode_control_message,
)
from moqt.session import (
    MoqtSession,
    SessionState,
    Role,
    SubscriptionFilter,
    TrackInfo,
    SUPPORTED_VERSIONS,
    CURRENT_VERSION,
)


# Varint のテスト


def test_varint_encode_1byte():
    """1 バイト varint のエンコード"""
    assert encode_varint(0) == b"\x00"
    assert encode_varint(1) == b"\x01"
    assert encode_varint(63) == b"\x3f"


def test_varint_encode_2bytes():
    """2 バイト varint のエンコード"""
    assert encode_varint(64) == b"\x40\x40"
    assert encode_varint(16383) == b"\x7f\xff"


def test_varint_encode_4bytes():
    """4 バイト varint のエンコード"""
    assert encode_varint(16384) == b"\x80\x00\x40\x00"
    assert encode_varint(1073741823) == b"\xbf\xff\xff\xff"


def test_varint_encode_8bytes():
    """8 バイト varint のエンコード"""
    assert encode_varint(1073741824) == b"\xc0\x00\x00\x00\x40\x00\x00\x00"


def test_varint_decode_1byte():
    """1 バイト varint のデコード"""
    assert decode_varint(b"\x00") == (0, 1)
    assert decode_varint(b"\x01") == (1, 1)
    assert decode_varint(b"\x3f") == (63, 1)


def test_varint_decode_2bytes():
    """2 バイト varint のデコード"""
    assert decode_varint(b"\x40\x40") == (64, 2)
    assert decode_varint(b"\x7f\xff") == (16383, 2)


def test_varint_decode_4bytes():
    """4 バイト varint のデコード"""
    assert decode_varint(b"\x80\x00\x40\x00") == (16384, 4)
    assert decode_varint(b"\xbf\xff\xff\xff") == (1073741823, 4)


def test_varint_decode_8bytes():
    """8 バイト varint のデコード"""
    assert decode_varint(b"\xc0\x00\x00\x00\x40\x00\x00\x00") == (1073741824, 8)


def test_varint_roundtrip():
    """エンコード/デコードのラウンドトリップ"""
    test_values = [0, 1, 63, 64, 16383, 16384, 1073741823, 1073741824]
    for value in test_values:
        encoded = encode_varint(value)
        decoded, _ = decode_varint(encoded)
        assert decoded == value


def test_varint_size():
    """varint サイズの計算"""
    assert varint_size(0) == 1
    assert varint_size(63) == 1
    assert varint_size(64) == 2
    assert varint_size(16383) == 2
    assert varint_size(16384) == 4
    assert varint_size(1073741823) == 4
    assert varint_size(1073741824) == 8


def test_varint_negative_value_error():
    """負の値でエラー"""
    with pytest.raises(ValueError):
        encode_varint(-1)


def test_varint_too_large_value_error():
    """大きすぎる値でエラー"""
    with pytest.raises(ValueError):
        encode_varint(2**62)


# Location のテスト


def test_location_encode_decode():
    """エンコード/デコードのテスト"""
    loc = Location(group=10, object=20)
    encoded = loc.encode()
    decoded, consumed = Location.decode(encoded)
    assert decoded.group == 10
    assert decoded.object == 20


# TrackNamespace のテスト


def test_track_namespace_encode_decode_empty():
    """空の namespace"""
    ns = TrackNamespace(tuple=[])
    encoded = ns.encode()
    decoded, consumed = TrackNamespace.decode(encoded)
    assert decoded.tuple == []


def test_track_namespace_encode_decode_single():
    """単一要素の namespace"""
    ns = TrackNamespace(tuple=[b"test"])
    encoded = ns.encode()
    decoded, consumed = TrackNamespace.decode(encoded)
    assert decoded.tuple == [b"test"]


def test_track_namespace_encode_decode_multiple():
    """複数要素の namespace"""
    ns = TrackNamespace(tuple=[b"foo", b"bar", b"baz"])
    encoded = ns.encode()
    decoded, consumed = TrackNamespace.decode(encoded)
    assert decoded.tuple == [b"foo", b"bar", b"baz"]


# ClientSetup のテスト


def test_client_setup_encode_decode_empty():
    """パラメータなしの CLIENT_SETUP"""
    setup = ClientSetup()
    encoded = setup.encode()
    decoded, consumed = decode_control_message(encoded)
    assert isinstance(decoded, ClientSetup)
    assert len(decoded.parameters) == 0


def test_client_setup_encode_decode_with_path():
    """PATH パラメータ付きの CLIENT_SETUP"""
    setup = ClientSetup()
    setup.set_path("/test/path")
    encoded = setup.encode()
    decoded, consumed = decode_control_message(encoded)
    assert isinstance(decoded, ClientSetup)
    path_param = decoded.get_parameter(ParameterType.PATH)
    assert path_param is not None
    assert path_param.value == b"/test/path"


def test_client_setup_encode_decode_with_max_request_id():
    """MAX_REQUEST_ID パラメータ付きの CLIENT_SETUP"""
    setup = ClientSetup()
    setup.set_max_request_id(100)
    encoded = setup.encode()
    decoded, consumed = decode_control_message(encoded)
    assert isinstance(decoded, ClientSetup)
    max_id_param = decoded.get_parameter(ParameterType.MAX_REQUEST_ID)
    assert max_id_param is not None


# ServerSetup のテスト


def test_server_setup_encode_decode_empty():
    """パラメータなしの SERVER_SETUP"""
    setup = ServerSetup()
    encoded = setup.encode()
    decoded, consumed = decode_control_message(encoded)
    assert isinstance(decoded, ServerSetup)
    assert len(decoded.parameters) == 0


def test_server_setup_encode_decode_with_max_request_id():
    """MAX_REQUEST_ID パラメータ付きの SERVER_SETUP"""
    setup = ServerSetup()
    setup.set_max_request_id(200)
    encoded = setup.encode()
    decoded, consumed = decode_control_message(encoded)
    assert isinstance(decoded, ServerSetup)


# Goaway のテスト


def test_goaway_encode_decode_empty_uri():
    """空の URI"""
    goaway = Goaway()
    encoded = goaway.encode()
    decoded, consumed = decode_control_message(encoded)
    assert isinstance(decoded, Goaway)
    assert decoded.new_session_uri == ""


def test_goaway_encode_decode_with_uri():
    """URI 付き"""
    goaway = Goaway(new_session_uri="moqt://example.com/new")
    encoded = goaway.encode()
    decoded, consumed = decode_control_message(encoded)
    assert isinstance(decoded, Goaway)
    assert decoded.new_session_uri == "moqt://example.com/new"


# MaxRequestId のテスト


def test_max_request_id_encode_decode():
    """エンコード/デコード"""
    msg = MaxRequestId(request_id=1000)
    encoded = msg.encode()
    decoded, consumed = decode_control_message(encoded)
    assert isinstance(decoded, MaxRequestId)
    assert decoded.request_id == 1000


# RequestOk のテスト


def test_request_ok_encode_decode():
    """エンコード/デコード"""
    msg = RequestOk(request_id=42)
    encoded = msg.encode()
    decoded, consumed = decode_control_message(encoded)
    assert isinstance(decoded, RequestOk)
    assert decoded.request_id == 42


# RequestError のテスト


def test_request_error_encode_decode():
    """エンコード/デコード"""
    msg = RequestError(request_id=42, error_code=1, reason_phrase="Test error")
    encoded = msg.encode()
    decoded, consumed = decode_control_message(encoded)
    assert isinstance(decoded, RequestError)
    assert decoded.request_id == 42
    assert decoded.error_code == 1
    assert decoded.reason_phrase == "Test error"


# Subscribe のテスト


def test_subscribe_encode_decode():
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


# Publish のテスト


def test_publish_encode_decode():
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


# PublishDone のテスト


def test_publish_done_encode_decode():
    """エンコード/デコード"""
    msg = PublishDone(request_id=30, status_code=0, reason_phrase="Completed")
    encoded = msg.encode()
    decoded, consumed = decode_control_message(encoded)
    assert isinstance(decoded, PublishDone)
    assert decoded.request_id == 30
    assert decoded.status_code == 0
    assert decoded.reason_phrase == "Completed"


# ErrorCode のテスト


def test_error_code_values():
    """エラーコードの値を確認"""
    assert ErrorCode.NO_ERROR == 0x0
    assert ErrorCode.INTERNAL_ERROR == 0x1
    assert ErrorCode.UNAUTHORIZED == 0x2
    assert ErrorCode.PROTOCOL_VIOLATION == 0x3
    assert ErrorCode.DUPLICATE_TRACK_ALIAS == 0x4
    assert ErrorCode.PARAMETER_LENGTH_MISMATCH == 0x5
    assert ErrorCode.TOO_MANY_SUBSCRIBERS == 0x6
    assert ErrorCode.GOAWAY_TIMEOUT == 0x10


# TrackStatusCode のテスト


def test_track_status_code_values():
    """ステータスコードの値を確認"""
    assert TrackStatusCode.IN_PROGRESS == 0x0
    assert TrackStatusCode.TRACK_DOES_NOT_EXIST == 0x1
    assert TrackStatusCode.NO_OBJECTS == 0x2
    assert TrackStatusCode.GROUP_DOES_NOT_EXIST == 0x3


# StreamType のテスト


def test_stream_type_values():
    """ストリームタイプの値を確認"""
    assert StreamType.CONTROL == 0x00
    assert StreamType.SUBGROUP == 0x04
    assert StreamType.FETCH == 0x05


# SubscriptionFilter のテスト


def test_subscription_filter_latest_group():
    """LATEST_GROUP フィルターのエンコード/デコード"""
    filter_obj = SubscriptionFilter(filter_type=FilterType.LATEST_GROUP)
    encoded = filter_obj.encode()
    decoded, consumed = SubscriptionFilter.decode(encoded)
    assert decoded.filter_type == FilterType.LATEST_GROUP
    assert decoded.start_group is None


def test_subscription_filter_latest_object():
    """LATEST_OBJECT フィルターのエンコード/デコード"""
    filter_obj = SubscriptionFilter(filter_type=FilterType.LATEST_OBJECT)
    encoded = filter_obj.encode()
    decoded, consumed = SubscriptionFilter.decode(encoded)
    assert decoded.filter_type == FilterType.LATEST_OBJECT


def test_subscription_filter_absolute_start():
    """ABSOLUTE_START フィルターのエンコード/デコード"""
    filter_obj = SubscriptionFilter(
        filter_type=FilterType.ABSOLUTE_START,
        start_group=10,
        start_object=5,
    )
    encoded = filter_obj.encode()
    decoded, consumed = SubscriptionFilter.decode(encoded)
    assert decoded.filter_type == FilterType.ABSOLUTE_START
    assert decoded.start_group == 10
    assert decoded.start_object == 5


def test_subscription_filter_absolute_range():
    """ABSOLUTE_RANGE フィルターのエンコード/デコード"""
    filter_obj = SubscriptionFilter(
        filter_type=FilterType.ABSOLUTE_RANGE,
        start_group=10,
        start_object=5,
        end_group=20,
        end_object=15,
    )
    encoded = filter_obj.encode()
    decoded, consumed = SubscriptionFilter.decode(encoded)
    assert decoded.filter_type == FilterType.ABSOLUTE_RANGE
    assert decoded.start_group == 10
    assert decoded.start_object == 5
    assert decoded.end_group == 20
    assert decoded.end_object == 15


def test_subscription_filter_absolute_start_missing_fields():
    """ABSOLUTE_START で必須フィールドがない場合のエラー"""
    filter_obj = SubscriptionFilter(filter_type=FilterType.ABSOLUTE_START)
    with pytest.raises(ValueError):
        filter_obj.encode()


def test_subscription_filter_absolute_range_missing_fields():
    """ABSOLUTE_RANGE で必須フィールドがない場合のエラー"""
    filter_obj = SubscriptionFilter(
        filter_type=FilterType.ABSOLUTE_RANGE,
        start_group=10,
        start_object=5,
    )
    with pytest.raises(ValueError):
        filter_obj.encode()


# Fetch のテスト


def test_fetch_encode_decode():
    """エンコード/デコード"""
    msg = Fetch(
        request_id=100,
        track_namespace=TrackNamespace(tuple=[b"media", b"video"]),
        track_name=b"stream1",
        start=Location(group=0, object=0),
        end=Location(group=10, object=100),
    )
    encoded = msg.encode()
    decoded, consumed = decode_control_message(encoded)
    assert isinstance(decoded, Fetch)
    assert decoded.request_id == 100
    assert decoded.track_namespace.tuple == [b"media", b"video"]
    assert decoded.track_name == b"stream1"
    assert decoded.start.group == 0
    assert decoded.start.object == 0
    assert decoded.end.group == 10
    assert decoded.end.object == 100


# FetchOk のテスト


def test_fetch_ok_encode_decode():
    """エンコード/デコード"""
    msg = FetchOk(request_id=100)
    encoded = msg.encode()
    decoded, consumed = decode_control_message(encoded)
    assert isinstance(decoded, FetchOk)
    assert decoded.request_id == 100


# FetchCancel のテスト


def test_fetch_cancel_encode_decode():
    """エンコード/デコード"""
    msg = FetchCancel(request_id=100)
    encoded = msg.encode()
    decoded, consumed = decode_control_message(encoded)
    assert isinstance(decoded, FetchCancel)
    assert decoded.request_id == 100


# TrackStatus のテスト


def test_track_status_encode_decode():
    """エンコード/デコード"""
    msg = TrackStatus(
        request_id=50,
        track_namespace=TrackNamespace(tuple=[b"live"]),
        track_name=b"video",
    )
    encoded = msg.encode()
    decoded, consumed = decode_control_message(encoded)
    assert isinstance(decoded, TrackStatus)
    assert decoded.request_id == 50
    assert decoded.track_namespace.tuple == [b"live"]
    assert decoded.track_name == b"video"


# PublishNamespace のテスト


def test_publish_namespace_encode_decode():
    """エンコード/デコード"""
    msg = PublishNamespace(
        request_id=60,
        track_namespace=TrackNamespace(tuple=[b"media", b"streams"]),
    )
    encoded = msg.encode()
    decoded, consumed = decode_control_message(encoded)
    assert isinstance(decoded, PublishNamespace)
    assert decoded.request_id == 60
    assert decoded.track_namespace.tuple == [b"media", b"streams"]


# PublishNamespaceDone のテスト


def test_publish_namespace_done_encode_decode():
    """エンコード/デコード"""
    msg = PublishNamespaceDone(
        request_id=60,
        status_code=0,
        reason_phrase="Done",
    )
    encoded = msg.encode()
    decoded, consumed = decode_control_message(encoded)
    assert isinstance(decoded, PublishNamespaceDone)
    assert decoded.request_id == 60
    assert decoded.status_code == 0
    assert decoded.reason_phrase == "Done"


# PublishNamespaceCancel のテスト


def test_publish_namespace_cancel_encode_decode():
    """エンコード/デコード"""
    msg = PublishNamespaceCancel(request_id=60)
    encoded = msg.encode()
    decoded, consumed = decode_control_message(encoded)
    assert isinstance(decoded, PublishNamespaceCancel)
    assert decoded.request_id == 60


# SubscribeNamespace のテスト


def test_subscribe_namespace_encode_decode():
    """エンコード/デコード"""
    msg = SubscribeNamespace(
        request_id=70,
        track_namespace_prefix=TrackNamespace(tuple=[b"media"]),
    )
    encoded = msg.encode()
    decoded, consumed = decode_control_message(encoded)
    assert isinstance(decoded, SubscribeNamespace)
    assert decoded.request_id == 70
    assert decoded.track_namespace_prefix.tuple == [b"media"]


# UnsubscribeNamespace のテスト


def test_unsubscribe_namespace_encode_decode():
    """エンコード/デコード"""
    msg = UnsubscribeNamespace(request_id=70)
    encoded = msg.encode()
    decoded, consumed = decode_control_message(encoded)
    assert isinstance(decoded, UnsubscribeNamespace)
    assert decoded.request_id == 70


# SubscribeUpdate のテスト


def test_subscribe_update_encode_decode():
    """エンコード/デコード"""
    msg = SubscribeUpdate(request_id=80)
    encoded = msg.encode()
    decoded, consumed = decode_control_message(encoded)
    assert isinstance(decoded, SubscribeUpdate)
    assert decoded.request_id == 80


# RequestsBlocked のテスト


def test_requests_blocked_encode_decode():
    """エンコード/デコード"""
    msg = RequestsBlocked(maximum_request_id=999)
    encoded = msg.encode()
    decoded, consumed = decode_control_message(encoded)
    assert isinstance(decoded, RequestsBlocked)
    assert decoded.maximum_request_id == 999


# MoqtSession のテスト


def test_moqt_session_client_init():
    """クライアントセッションの初期化"""
    session = MoqtSession(role=Role.CLIENT)
    assert session.role == Role.CLIENT
    assert session.state == SessionState.IDLE
    assert session.next_request_id == 0


def test_moqt_session_server_init():
    """サーバーセッションの初期化"""
    session = MoqtSession(role=Role.SERVER)
    assert session.role == Role.SERVER
    assert session.state == SessionState.IDLE
    assert session.next_request_id == 1


def test_moqt_session_allocate_request_id_client():
    """クライアントの Request ID 割り当て"""
    session = MoqtSession(role=Role.CLIENT)
    session.peer_max_request_id = 100
    assert session.allocate_request_id() == 0
    assert session.allocate_request_id() == 2
    assert session.allocate_request_id() == 4


def test_moqt_session_allocate_request_id_server():
    """サーバーの Request ID 割り当て"""
    session = MoqtSession(role=Role.SERVER)
    session.peer_max_request_id = 101
    assert session.allocate_request_id() == 1
    assert session.allocate_request_id() == 3
    assert session.allocate_request_id() == 5


def test_moqt_session_allocate_track_alias():
    """Track Alias の割り当て"""
    session = MoqtSession(role=Role.CLIENT)
    assert session.allocate_track_alias() == 0
    assert session.allocate_track_alias() == 1
    assert session.allocate_track_alias() == 2


def test_moqt_session_version_constants():
    """バージョン定数の確認"""
    assert CURRENT_VERSION == 0xFF000015
    assert 0xFF000015 in SUPPORTED_VERSIONS


def test_moqt_session_track_info():
    """TrackInfo の作成"""
    info = TrackInfo(
        request_id=10,
        track_alias=1,
        track_namespace=TrackNamespace(tuple=[b"media"]),
        track_name=b"video",
    )
    assert info.request_id == 10
    assert info.track_alias == 1
    assert info.subscription_filter is None
    assert info.group_order is None


# GroupOrder のテスト


def test_group_order_values():
    """グループ順序の値を確認"""
    assert GroupOrder.ASCENDING == 0x01
    assert GroupOrder.DESCENDING == 0x02
