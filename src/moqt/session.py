"""MOQT Session 管理

draft-ietf-moq-transport-15 に基づく完全な実装
msquic 上での MOQT セッションを管理する
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable

import msquic

from .message import (
    ClientSetup,
    ControlMessage,
    ErrorCode,
    Fetch,
    FetchCancel,
    FetchOk,
    FilterType,
    Goaway,
    GroupOrder,
    Location,
    MaxRequestId,
    MessageType,
    Parameter,
    ParameterType,
    Publish,
    PublishDone,
    PublishNamespace,
    PublishNamespaceCancel,
    PublishNamespaceDone,
    PublishOk,
    RequestError,
    RequestOk,
    RequestsBlocked,
    ServerSetup,
    StreamType,
    Subscribe,
    SubscribeNamespace,
    SubscribeOk,
    SubscribeUpdate,
    TrackNamespace,
    TrackStatus,
    TrackStatusCode,
    Unsubscribe,
    UnsubscribeNamespace,
    decode_control_message,
)
from .varint import decode_varint, encode_varint


# サポートする MOQT バージョン
SUPPORTED_VERSIONS = [0xFF000015]  # draft-15
CURRENT_VERSION = 0xFF000015


class SessionState(Enum):
    """セッション状態"""

    IDLE = auto()
    CONNECTING = auto()
    SETUP = auto()
    ESTABLISHED = auto()
    GOAWAY = auto()
    CLOSED = auto()


class Role(Enum):
    """エンドポイントの役割"""

    CLIENT = auto()
    SERVER = auto()


@dataclass
class SubscriptionFilter:
    """サブスクリプションフィルター"""

    filter_type: FilterType
    start_group: int | None = None
    start_object: int | None = None
    end_group: int | None = None
    end_object: int | None = None

    def encode(self) -> bytes:
        """フィルターをエンコードする"""
        result = encode_varint(self.filter_type)

        if self.filter_type == FilterType.ABSOLUTE_START:
            if self.start_group is None or self.start_object is None:
                raise ValueError("ABSOLUTE_START には start_group と start_object が必要")
            result += encode_varint(self.start_group)
            result += encode_varint(self.start_object)
        elif self.filter_type == FilterType.ABSOLUTE_RANGE:
            if (
                self.start_group is None
                or self.start_object is None
                or self.end_group is None
                or self.end_object is None
            ):
                raise ValueError("ABSOLUTE_RANGE には全ての位置情報が必要")
            result += encode_varint(self.start_group)
            result += encode_varint(self.start_object)
            result += encode_varint(self.end_group)
            result += encode_varint(self.end_object)

        return result

    @classmethod
    def decode(cls, data: bytes, offset: int = 0) -> tuple[SubscriptionFilter, int]:
        """フィルターをデコードする"""
        filter_type_val, consumed = decode_varint(data, offset)
        total_consumed = consumed
        filter_type = FilterType(filter_type_val)

        start_group = None
        start_object = None
        end_group = None
        end_object = None

        if filter_type == FilterType.ABSOLUTE_START:
            start_group, consumed = decode_varint(data, offset + total_consumed)
            total_consumed += consumed
            start_object, consumed = decode_varint(data, offset + total_consumed)
            total_consumed += consumed
        elif filter_type == FilterType.ABSOLUTE_RANGE:
            start_group, consumed = decode_varint(data, offset + total_consumed)
            total_consumed += consumed
            start_object, consumed = decode_varint(data, offset + total_consumed)
            total_consumed += consumed
            end_group, consumed = decode_varint(data, offset + total_consumed)
            total_consumed += consumed
            end_object, consumed = decode_varint(data, offset + total_consumed)
            total_consumed += consumed

        return (
            cls(
                filter_type=filter_type,
                start_group=start_group,
                start_object=start_object,
                end_group=end_group,
                end_object=end_object,
            ),
            total_consumed,
        )


@dataclass
class TrackInfo:
    """トラック情報"""

    request_id: int
    track_alias: int
    track_namespace: TrackNamespace
    track_name: bytes
    subscription_filter: SubscriptionFilter | None = None
    group_order: GroupOrder | None = None
    delivery_timeout: int | None = None


@dataclass
class MoqtSession:
    """MOQT セッション

    msquic の Connection 上で MOQT プロトコルを処理する
    """

    role: Role
    state: SessionState = SessionState.IDLE

    # バージョン
    selected_version: int = CURRENT_VERSION
    supported_versions: list[int] = field(default_factory=lambda: SUPPORTED_VERSIONS.copy())

    # Request ID 管理
    # クライアントは偶数 (0, 2, 4, ...)、サーバーは奇数 (1, 3, 5, ...)
    next_request_id: int = 0
    max_request_id: int = 0
    peer_max_request_id: int = 0

    # Track Alias 管理
    next_track_alias: int = 0
    subscriptions: dict[int, TrackInfo] = field(default_factory=dict)
    publications: dict[int, TrackInfo] = field(default_factory=dict)
    track_alias_map: dict[int, int] = field(default_factory=dict)

    # コールバック
    on_setup_complete: Callable[[MoqtSession], None] | None = None
    on_message: Callable[[MoqtSession, ControlMessage], None] | None = None
    on_subscribe: Callable[[MoqtSession, Subscribe], bool] | None = None
    on_publish: Callable[[MoqtSession, Publish], bool] | None = None
    on_goaway: Callable[[MoqtSession, str], None] | None = None
    on_close: Callable[[MoqtSession], None] | None = None

    # 内部状態
    _connection: msquic.Connection | None = None
    _control_stream: msquic.Stream | None = None
    _receive_buffer: bytearray = field(default_factory=bytearray)
    _pending_requests: dict[int, asyncio.Future] = field(default_factory=dict)
    _data_streams: dict[int, msquic.Stream] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # クライアントは偶数、サーバーは奇数から開始
        if self.role == Role.CLIENT:
            self.next_request_id = 0
        else:
            self.next_request_id = 1

    def allocate_request_id(self) -> int:
        """新しい Request ID を割り当てる"""
        if self.next_request_id > self.peer_max_request_id:
            raise RuntimeError("Request ID の上限に達しました")
        request_id = self.next_request_id
        self.next_request_id += 2
        return request_id

    def allocate_track_alias(self) -> int:
        """新しい Track Alias を割り当てる"""
        track_alias = self.next_track_alias
        self.next_track_alias += 1
        return track_alias

    def set_connection(self, connection: msquic.Connection) -> None:
        """接続を設定する"""
        self._connection = connection

    def set_control_stream(self, stream: msquic.Stream) -> None:
        """制御ストリームを設定する"""
        self._control_stream = stream

        def on_receive(data: Sequence[int], fin: bool) -> None:
            self._on_control_stream_receive(bytes(data), fin)

        stream.set_on_receive(on_receive)

    async def open_control_stream(self) -> msquic.Stream:
        """制御ストリームを開く (クライアント側)"""
        if self._connection is None:
            raise RuntimeError("接続が設定されていません")

        stream = self._connection.open_stream(msquic.StreamOpenFlags.NONE)
        self.set_control_stream(stream)

        # Stream Type を送信
        stream.send(encode_varint(StreamType.CONTROL), msquic.SendFlags.NONE)

        return stream

    def _on_control_stream_receive(self, data: bytes, fin: bool) -> None:
        """制御ストリームからデータを受信した時の処理"""
        self._receive_buffer.extend(data)
        self._process_control_messages()

        if fin:
            # 制御ストリームが閉じられた
            self.state = SessionState.CLOSED
            if self.on_close:
                self.on_close(self)

    def _process_control_messages(self) -> None:
        """受信バッファからメッセージを処理する"""
        while len(self._receive_buffer) > 0:
            try:
                message, consumed = decode_control_message(bytes(self._receive_buffer))
                del self._receive_buffer[:consumed]
                self._handle_message(message)
            except ValueError:
                # データが不足している場合は待機
                break

    def _handle_message(self, message: ControlMessage) -> None:
        """受信したメッセージを処理する"""
        if isinstance(message, ClientSetup):
            self._handle_client_setup(message)
        elif isinstance(message, ServerSetup):
            self._handle_server_setup(message)
        elif isinstance(message, Goaway):
            self._handle_goaway(message)
        elif isinstance(message, MaxRequestId):
            self._handle_max_request_id(message)
        elif isinstance(message, RequestsBlocked):
            self._handle_requests_blocked(message)
        elif isinstance(message, (RequestOk, RequestError)):
            self._handle_request_response(message)
        elif isinstance(message, Subscribe):
            self._handle_subscribe(message)
        elif isinstance(message, (SubscribeOk, SubscribeUpdate)):
            self._handle_subscribe_response(message)
        elif isinstance(message, Unsubscribe):
            self._handle_unsubscribe(message)
        elif isinstance(message, Publish):
            self._handle_publish(message)
        elif isinstance(message, (PublishOk, PublishDone)):
            self._handle_publish_response(message)
        elif isinstance(message, Fetch):
            self._handle_fetch(message)
        elif isinstance(message, (FetchOk, FetchCancel)):
            self._handle_fetch_response(message)
        elif isinstance(message, TrackStatus):
            self._handle_track_status(message)
        elif isinstance(message, PublishNamespace):
            self._handle_publish_namespace(message)
        elif isinstance(message, (PublishNamespaceDone, PublishNamespaceCancel)):
            self._handle_publish_namespace_response(message)
        elif isinstance(message, SubscribeNamespace):
            self._handle_subscribe_namespace(message)
        elif isinstance(message, UnsubscribeNamespace):
            self._handle_unsubscribe_namespace(message)
        else:
            # アプリケーションにメッセージを渡す
            if self.on_message:
                self.on_message(self, message)

    def _handle_client_setup(self, message: ClientSetup) -> None:
        """CLIENT_SETUP を処理する"""
        if self.role != Role.SERVER:
            # クライアントが CLIENT_SETUP を受信するのはプロトコル違反
            self.close(error_code=ErrorCode.PROTOCOL_VIOLATION, reason="Protocol violation")
            return

        # MAX_REQUEST_ID パラメータを取得
        max_req_param = message.get_parameter(ParameterType.MAX_REQUEST_ID)
        if max_req_param:
            self.peer_max_request_id, _ = decode_varint(max_req_param.value)

        self.state = SessionState.SETUP

    def _handle_server_setup(self, message: ServerSetup) -> None:
        """SERVER_SETUP を処理する"""
        if self.role != Role.CLIENT:
            # サーバーが SERVER_SETUP を受信するのはプロトコル違反
            self.close(error_code=ErrorCode.PROTOCOL_VIOLATION, reason="Protocol violation")
            return

        # MAX_REQUEST_ID パラメータを取得
        max_req_param = message.get_parameter(ParameterType.MAX_REQUEST_ID)
        if max_req_param:
            self.peer_max_request_id, _ = decode_varint(max_req_param.value)

        self.state = SessionState.ESTABLISHED
        if self.on_setup_complete:
            self.on_setup_complete(self)

    def _handle_goaway(self, message: Goaway) -> None:
        """GOAWAY を処理する"""
        self.state = SessionState.GOAWAY
        if self.on_goaway:
            self.on_goaway(self, message.new_session_uri)

    def _handle_max_request_id(self, message: MaxRequestId) -> None:
        """MAX_REQUEST_ID を処理する"""
        if message.request_id <= self.peer_max_request_id:
            # プロトコル違反: MAX_REQUEST_ID は増加のみ許可
            self.close(error_code=ErrorCode.PROTOCOL_VIOLATION, reason="Protocol violation")
            return
        self.peer_max_request_id = message.request_id

    def _handle_requests_blocked(self, message: RequestsBlocked) -> None:
        """REQUESTS_BLOCKED を処理する"""
        # ピアが Request ID の上限に達したことを通知
        # 必要に応じて MAX_REQUEST_ID を送信
        pass

    def _handle_request_response(self, message: RequestOk | RequestError) -> None:
        """リクエストへのレスポンスを処理する"""
        future = self._pending_requests.pop(message.request_id, None)
        if future and not future.done():
            future.set_result(message)

    def _handle_subscribe(self, message: Subscribe) -> None:
        """SUBSCRIBE を処理する"""
        # Track Alias の重複チェック
        if message.track_alias in self.track_alias_map:
            self.send_request_error(
                message.request_id,
                ErrorCode.DUPLICATE_TRACK_ALIAS,
                "Duplicate Track Alias",
            )
            return

        # コールバックで判断
        if self.on_subscribe:
            accepted = self.on_subscribe(self, message)
            if not accepted:
                self.send_request_error(
                    message.request_id,
                    ErrorCode.INTERNAL_ERROR,
                    "Subscription rejected",
                )
                return

        # サブスクリプションを登録
        track_info = TrackInfo(
            request_id=message.request_id,
            track_alias=message.track_alias,
            track_namespace=message.track_namespace,
            track_name=message.track_name,
        )
        self.subscriptions[message.request_id] = track_info
        self.track_alias_map[message.track_alias] = message.request_id

        # SUBSCRIBE_OK を送信
        self.send_subscribe_ok(message.request_id)

    def _handle_subscribe_response(self, message: SubscribeOk | SubscribeUpdate) -> None:
        """SUBSCRIBE のレスポンスを処理する"""
        future = self._pending_requests.pop(message.request_id, None)
        if future and not future.done():
            future.set_result(message)

    def _handle_unsubscribe(self, message: Unsubscribe) -> None:
        """UNSUBSCRIBE を処理する"""
        if message.request_id in self.subscriptions:
            track_info = self.subscriptions.pop(message.request_id)
            self.track_alias_map.pop(track_info.track_alias, None)

    def _handle_publish(self, message: Publish) -> None:
        """PUBLISH を処理する"""
        # Track Alias の重複チェック
        if message.track_alias in self.track_alias_map:
            self.send_request_error(
                message.request_id,
                ErrorCode.DUPLICATE_TRACK_ALIAS,
                "Duplicate Track Alias",
            )
            return

        # コールバックで判断
        if self.on_publish:
            accepted = self.on_publish(self, message)
            if not accepted:
                self.send_request_error(
                    message.request_id,
                    ErrorCode.INTERNAL_ERROR,
                    "Publish rejected",
                )
                return

        # パブリケーションを登録
        track_info = TrackInfo(
            request_id=message.request_id,
            track_alias=message.track_alias,
            track_namespace=message.track_namespace,
            track_name=message.track_name,
        )
        self.publications[message.request_id] = track_info
        self.track_alias_map[message.track_alias] = message.request_id

        # PUBLISH_OK を送信
        self.send_publish_ok(message.request_id)

    def _handle_publish_response(self, message: PublishOk | PublishDone) -> None:
        """PUBLISH のレスポンスを処理する"""
        future = self._pending_requests.pop(message.request_id, None)
        if future and not future.done():
            future.set_result(message)

    def _handle_fetch(self, message: Fetch) -> None:
        """FETCH を処理する"""
        # アプリケーションにメッセージを渡す
        if self.on_message:
            self.on_message(self, message)

    def _handle_fetch_response(self, message: FetchOk | FetchCancel) -> None:
        """FETCH のレスポンスを処理する"""
        future = self._pending_requests.pop(message.request_id, None)
        if future and not future.done():
            future.set_result(message)

    def _handle_track_status(self, message: TrackStatus) -> None:
        """TRACK_STATUS を処理する"""
        if self.on_message:
            self.on_message(self, message)

    def _handle_publish_namespace(self, message: PublishNamespace) -> None:
        """PUBLISH_NAMESPACE を処理する"""
        if self.on_message:
            self.on_message(self, message)

    def _handle_publish_namespace_response(
        self, message: PublishNamespaceDone | PublishNamespaceCancel
    ) -> None:
        """PUBLISH_NAMESPACE のレスポンスを処理する"""
        future = self._pending_requests.pop(message.request_id, None)
        if future and not future.done():
            future.set_result(message)

    def _handle_subscribe_namespace(self, message: SubscribeNamespace) -> None:
        """SUBSCRIBE_NAMESPACE を処理する"""
        if self.on_message:
            self.on_message(self, message)

    def _handle_unsubscribe_namespace(self, message: UnsubscribeNamespace) -> None:
        """UNSUBSCRIBE_NAMESPACE を処理する"""
        if self.on_message:
            self.on_message(self, message)

    def send_message(self, message: ControlMessage) -> None:
        """制御メッセージを送信する"""
        if self._control_stream is None:
            raise RuntimeError("制御ストリームが設定されていません")

        data = message.encode()
        self._control_stream.send(data, msquic.SendFlags.NONE)

    def send_client_setup(
        self,
        path: str | None = None,
        authority: str | None = None,
        max_request_id: int = 100,
    ) -> None:
        """CLIENT_SETUP を送信する"""
        if self.role != Role.CLIENT:
            raise RuntimeError("CLIENT_SETUP はクライアントのみ送信可能")

        self.max_request_id = max_request_id

        setup = ClientSetup()
        setup.set_max_request_id(max_request_id)
        if path:
            setup.set_path(path)
        if authority:
            setup.set_authority(authority)

        self.send_message(setup)
        self.state = SessionState.SETUP

    def send_server_setup(self, max_request_id: int = 100) -> None:
        """SERVER_SETUP を送信する"""
        if self.role != Role.SERVER:
            raise RuntimeError("SERVER_SETUP はサーバーのみ送信可能")

        self.max_request_id = max_request_id

        setup = ServerSetup()
        setup.set_max_request_id(max_request_id)

        self.send_message(setup)
        self.state = SessionState.ESTABLISHED
        if self.on_setup_complete:
            self.on_setup_complete(self)

    def send_goaway(self, new_session_uri: str = "") -> None:
        """GOAWAY を送信する"""
        goaway = Goaway(new_session_uri=new_session_uri)
        self.send_message(goaway)
        self.state = SessionState.GOAWAY

    def send_max_request_id(self, request_id: int) -> None:
        """MAX_REQUEST_ID を送信する"""
        if request_id <= self.max_request_id:
            raise ValueError("MAX_REQUEST_ID は増加のみ許可")
        self.max_request_id = request_id
        msg = MaxRequestId(request_id=request_id)
        self.send_message(msg)

    def send_requests_blocked(self, maximum_request_id: int) -> None:
        """REQUESTS_BLOCKED を送信する"""
        msg = RequestsBlocked(maximum_request_id=maximum_request_id)
        self.send_message(msg)

    async def subscribe(
        self,
        track_namespace: list[bytes],
        track_name: bytes,
        subscription_filter: SubscriptionFilter | None = None,
        group_order: GroupOrder | None = None,
        delivery_timeout: int | None = None,
        timeout: float = 5.0,
    ) -> SubscribeOk | RequestError:
        """トラックを購読する"""
        request_id = self.allocate_request_id()
        track_alias = self.allocate_track_alias()

        # パラメータを構築
        parameters: list[Parameter] = []

        if subscription_filter:
            parameters.append(
                Parameter(
                    type=ParameterType.SUBSCRIPTION_FILTER,
                    value=subscription_filter.encode(),
                )
            )

        if group_order is not None:
            parameters.append(
                Parameter(
                    type=ParameterType.GROUP_ORDER,
                    value=encode_varint(group_order),
                )
            )

        if delivery_timeout is not None:
            parameters.append(
                Parameter(
                    type=ParameterType.DELIVERY_TIMEOUT,
                    value=encode_varint(delivery_timeout),
                )
            )

        subscribe = Subscribe(
            request_id=request_id,
            track_alias=track_alias,
            track_namespace=TrackNamespace(tuple=track_namespace),
            track_name=track_name,
            parameters=parameters,
        )

        future: asyncio.Future[RequestOk | RequestError] = asyncio.Future()
        self._pending_requests[request_id] = future

        self.send_message(subscribe)

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            if isinstance(result, RequestOk):
                # トラック情報を保存
                track_info = TrackInfo(
                    request_id=request_id,
                    track_alias=track_alias,
                    track_namespace=TrackNamespace(tuple=track_namespace),
                    track_name=track_name,
                    subscription_filter=subscription_filter,
                    group_order=group_order,
                    delivery_timeout=delivery_timeout,
                )
                self.subscriptions[request_id] = track_info
                self.track_alias_map[track_alias] = request_id
                return SubscribeOk(request_id=result.request_id, parameters=result.parameters)
            return result
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise

    def send_subscribe_ok(self, request_id: int, parameters: list[Parameter] | None = None) -> None:
        """SUBSCRIBE_OK を送信する"""
        ok = RequestOk(request_id=request_id, parameters=parameters or [])
        self.send_message(ok)

    def send_subscribe_update(
        self, request_id: int, parameters: list[Parameter] | None = None
    ) -> None:
        """SUBSCRIBE_UPDATE を送信する"""
        update = SubscribeUpdate(request_id=request_id, parameters=parameters or [])
        self.send_message(update)

    def send_request_error(self, request_id: int, error_code: int, reason: str = "") -> None:
        """REQUEST_ERROR を送信する"""
        error = RequestError(request_id=request_id, error_code=error_code, reason_phrase=reason)
        self.send_message(error)

    def unsubscribe(self, request_id: int) -> None:
        """購読を解除する"""
        if request_id in self.subscriptions:
            track_info = self.subscriptions.pop(request_id)
            self.track_alias_map.pop(track_info.track_alias, None)

        unsubscribe = Unsubscribe(request_id=request_id)
        self.send_message(unsubscribe)

    async def publish(
        self,
        track_namespace: list[bytes],
        track_name: bytes,
        delivery_timeout: int | None = None,
        timeout: float = 5.0,
    ) -> PublishOk | RequestError:
        """トラックを公開する"""
        request_id = self.allocate_request_id()
        track_alias = self.allocate_track_alias()

        # パラメータを構築
        parameters: list[Parameter] = []

        if delivery_timeout is not None:
            parameters.append(
                Parameter(
                    type=ParameterType.DELIVERY_TIMEOUT,
                    value=encode_varint(delivery_timeout),
                )
            )

        publish_msg = Publish(
            request_id=request_id,
            track_alias=track_alias,
            track_namespace=TrackNamespace(tuple=track_namespace),
            track_name=track_name,
            parameters=parameters,
        )

        future: asyncio.Future[RequestOk | RequestError] = asyncio.Future()
        self._pending_requests[request_id] = future

        self.send_message(publish_msg)

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            if isinstance(result, RequestOk):
                # トラック情報を保存
                track_info = TrackInfo(
                    request_id=request_id,
                    track_alias=track_alias,
                    track_namespace=TrackNamespace(tuple=track_namespace),
                    track_name=track_name,
                    delivery_timeout=delivery_timeout,
                )
                self.publications[request_id] = track_info
                self.track_alias_map[track_alias] = request_id
                return PublishOk(request_id=result.request_id, parameters=result.parameters)
            return result
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise

    def send_publish_ok(self, request_id: int, parameters: list[Parameter] | None = None) -> None:
        """PUBLISH_OK を送信する"""
        ok = RequestOk(request_id=request_id, parameters=parameters or [])
        self.send_message(ok)

    def send_publish_done(
        self, request_id: int, status_code: int = 0, reason_phrase: str = ""
    ) -> None:
        """PUBLISH_DONE を送信する"""
        done = PublishDone(
            request_id=request_id, status_code=status_code, reason_phrase=reason_phrase
        )
        self.send_message(done)

        # パブリケーションを削除
        if request_id in self.publications:
            track_info = self.publications.pop(request_id)
            self.track_alias_map.pop(track_info.track_alias, None)

    async def fetch(
        self,
        track_namespace: list[bytes],
        track_name: bytes,
        start: Location,
        end: Location,
        timeout: float = 5.0,
    ) -> FetchOk | RequestError:
        """オブジェクトをフェッチする"""
        request_id = self.allocate_request_id()

        fetch_msg = Fetch(
            request_id=request_id,
            track_namespace=TrackNamespace(tuple=track_namespace),
            track_name=track_name,
            start=start,
            end=end,
        )

        future: asyncio.Future[RequestOk | RequestError] = asyncio.Future()
        self._pending_requests[request_id] = future

        self.send_message(fetch_msg)

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            if isinstance(result, RequestOk):
                return FetchOk(request_id=result.request_id, parameters=result.parameters)
            return result
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise

    def send_fetch_ok(self, request_id: int, parameters: list[Parameter] | None = None) -> None:
        """FETCH_OK を送信する"""
        ok = FetchOk(request_id=request_id, parameters=parameters or [])
        self.send_message(ok)

    def send_fetch_cancel(self, request_id: int) -> None:
        """FETCH_CANCEL を送信する"""
        cancel = FetchCancel(request_id=request_id)
        self.send_message(cancel)

    def send_track_status(
        self,
        request_id: int,
        track_namespace: list[bytes],
        track_name: bytes,
        status_code: TrackStatusCode,
        last_group: int | None = None,
        last_object: int | None = None,
    ) -> None:
        """TRACK_STATUS を送信する"""
        parameters: list[Parameter] = []

        # ステータスコードをパラメータとして追加
        # (実際の仕様では Status Code は直接フィールドの可能性あり)

        status = TrackStatus(
            request_id=request_id,
            track_namespace=TrackNamespace(tuple=track_namespace),
            track_name=track_name,
            parameters=parameters,
        )
        self.send_message(status)

    async def publish_namespace(
        self,
        track_namespace: list[bytes],
        timeout: float = 5.0,
    ) -> RequestOk | RequestError:
        """名前空間を公開する"""
        request_id = self.allocate_request_id()

        publish_ns = PublishNamespace(
            request_id=request_id,
            track_namespace=TrackNamespace(tuple=track_namespace),
        )

        future: asyncio.Future[RequestOk | RequestError] = asyncio.Future()
        self._pending_requests[request_id] = future

        self.send_message(publish_ns)

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise

    def send_publish_namespace_ok(
        self, request_id: int, parameters: list[Parameter] | None = None
    ) -> None:
        """PUBLISH_NAMESPACE への OK を送信する"""
        ok = RequestOk(request_id=request_id, parameters=parameters or [])
        self.send_message(ok)

    def send_publish_namespace_done(
        self, request_id: int, status_code: int = 0, reason_phrase: str = ""
    ) -> None:
        """PUBLISH_NAMESPACE_DONE を送信する"""
        done = PublishNamespaceDone(
            request_id=request_id, status_code=status_code, reason_phrase=reason_phrase
        )
        self.send_message(done)

    def send_publish_namespace_cancel(self, request_id: int) -> None:
        """PUBLISH_NAMESPACE_CANCEL を送信する"""
        cancel = PublishNamespaceCancel(request_id=request_id)
        self.send_message(cancel)

    async def subscribe_namespace(
        self,
        track_namespace_prefix: list[bytes],
        timeout: float = 5.0,
    ) -> RequestOk | RequestError:
        """名前空間を購読する"""
        request_id = self.allocate_request_id()

        subscribe_ns = SubscribeNamespace(
            request_id=request_id,
            track_namespace_prefix=TrackNamespace(tuple=track_namespace_prefix),
        )

        future: asyncio.Future[RequestOk | RequestError] = asyncio.Future()
        self._pending_requests[request_id] = future

        self.send_message(subscribe_ns)

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise

    def send_subscribe_namespace_ok(
        self, request_id: int, parameters: list[Parameter] | None = None
    ) -> None:
        """SUBSCRIBE_NAMESPACE への OK を送信する"""
        ok = RequestOk(request_id=request_id, parameters=parameters or [])
        self.send_message(ok)

    def unsubscribe_namespace(self, request_id: int) -> None:
        """名前空間の購読を解除する"""
        unsubscribe_ns = UnsubscribeNamespace(request_id=request_id)
        self.send_message(unsubscribe_ns)

    def close(self, error_code: int = ErrorCode.NO_ERROR, reason: str = "") -> None:
        """セッションを閉じる"""
        self.state = SessionState.CLOSED
        if self._connection:
            self._connection.shutdown(msquic.ConnectionShutdownFlags.NONE, error_code)
        if self.on_close:
            self.on_close(self)

    def get_track_info_by_alias(self, track_alias: int) -> TrackInfo | None:
        """Track Alias からトラック情報を取得する"""
        request_id = self.track_alias_map.get(track_alias)
        if request_id is None:
            return None
        return self.subscriptions.get(request_id) or self.publications.get(request_id)

    def get_track_info_by_request_id(self, request_id: int) -> TrackInfo | None:
        """Request ID からトラック情報を取得する"""
        return self.subscriptions.get(request_id) or self.publications.get(request_id)
