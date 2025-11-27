"""MOQT Session 管理

msquic 上での MOQT セッションを管理する
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable

import msquic

from .message import (
    ClientSetup,
    ControlMessage,
    Goaway,
    MaxRequestId,
    MessageType,
    ParameterType,
    Publish,
    PublishOk,
    RequestError,
    RequestOk,
    ServerSetup,
    Subscribe,
    SubscribeOk,
    Unsubscribe,
    decode_control_message,
)
from .varint import encode_varint


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
class MoqtSession:
    """MOQT セッション

    msquic の Connection 上で MOQT プロトコルを処理する
    """

    role: Role
    state: SessionState = SessionState.IDLE

    # Request ID 管理
    # クライアントは偶数 (0, 2, 4, ...)、サーバーは奇数 (1, 3, 5, ...)
    next_request_id: int = 0
    max_request_id: int = 0
    peer_max_request_id: int = 0

    # コールバック
    on_setup_complete: Callable[[MoqtSession], None] | None = None
    on_message: Callable[[MoqtSession, ControlMessage], None] | None = None
    on_goaway: Callable[[MoqtSession, str], None] | None = None
    on_close: Callable[[MoqtSession], None] | None = None

    # 内部状態
    _connection: msquic.Connection | None = None
    _control_stream: msquic.Stream | None = None
    _receive_buffer: bytearray = field(default_factory=bytearray)
    _pending_requests: dict[int, asyncio.Future] = field(default_factory=dict)

    def __post_init__(self):
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

    def set_connection(self, connection: msquic.Connection) -> None:
        """接続を設定する"""
        self._connection = connection

    def set_control_stream(self, stream: msquic.Stream) -> None:
        """制御ストリームを設定する"""
        self._control_stream = stream

        def on_receive(data: list[int], fin: bool) -> None:
            self._on_control_stream_receive(bytes(data), fin)

        stream.set_on_receive(on_receive)

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
        elif isinstance(message, (RequestOk, RequestError)):
            self._handle_request_response(message)
        else:
            # アプリケーションにメッセージを渡す
            if self.on_message:
                self.on_message(self, message)

    def _handle_client_setup(self, message: ClientSetup) -> None:
        """CLIENT_SETUP を処理する"""
        if self.role != Role.SERVER:
            # クライアントが CLIENT_SETUP を受信するのはプロトコル違反
            self.close(error_code=1, reason="Protocol violation")
            return

        # MAX_REQUEST_ID パラメータを取得
        max_req_param = message.get_parameter(ParameterType.MAX_REQUEST_ID)
        if max_req_param:
            from .varint import decode_varint

            self.peer_max_request_id, _ = decode_varint(max_req_param.value)

        self.state = SessionState.SETUP

    def _handle_server_setup(self, message: ServerSetup) -> None:
        """SERVER_SETUP を処理する"""
        if self.role != Role.CLIENT:
            # サーバーが SERVER_SETUP を受信するのはプロトコル違反
            self.close(error_code=1, reason="Protocol violation")
            return

        # MAX_REQUEST_ID パラメータを取得
        max_req_param = message.get_parameter(ParameterType.MAX_REQUEST_ID)
        if max_req_param:
            from .varint import decode_varint

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
            self.close(error_code=1, reason="Protocol violation")
            return
        self.peer_max_request_id = message.request_id

    def _handle_request_response(self, message: RequestOk | RequestError) -> None:
        """リクエストへのレスポンスを処理する"""
        future = self._pending_requests.pop(message.request_id, None)
        if future and not future.done():
            future.set_result(message)

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

    async def subscribe(
        self,
        track_alias: int,
        track_namespace: list[bytes],
        track_name: bytes,
        timeout: float = 5.0,
    ) -> SubscribeOk | RequestError:
        """トラックを購読する"""
        from .message import Subscribe, TrackNamespace

        request_id = self.allocate_request_id()

        subscribe = Subscribe(
            request_id=request_id,
            track_alias=track_alias,
            track_namespace=TrackNamespace(tuple=track_namespace),
            track_name=track_name,
        )

        future: asyncio.Future[RequestOk | RequestError] = asyncio.Future()
        self._pending_requests[request_id] = future

        self.send_message(subscribe)

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            if isinstance(result, RequestOk):
                return SubscribeOk(request_id=result.request_id, parameters=result.parameters)
            return result
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise

    def send_subscribe_ok(self, request_id: int) -> None:
        """SUBSCRIBE_OK を送信する"""
        ok = RequestOk(request_id=request_id)
        self.send_message(ok)

    def send_request_error(self, request_id: int, error_code: int, reason: str = "") -> None:
        """REQUEST_ERROR を送信する"""
        error = RequestError(request_id=request_id, error_code=error_code, reason_phrase=reason)
        self.send_message(error)

    def unsubscribe(self, request_id: int) -> None:
        """購読を解除する"""
        unsubscribe = Unsubscribe(request_id=request_id)
        self.send_message(unsubscribe)

    async def publish(
        self,
        track_alias: int,
        track_namespace: list[bytes],
        track_name: bytes,
        timeout: float = 5.0,
    ) -> PublishOk | RequestError:
        """トラックを公開する"""
        from .message import Publish, TrackNamespace

        request_id = self.allocate_request_id()

        publish = Publish(
            request_id=request_id,
            track_alias=track_alias,
            track_namespace=TrackNamespace(tuple=track_namespace),
            track_name=track_name,
        )

        future: asyncio.Future[RequestOk | RequestError] = asyncio.Future()
        self._pending_requests[request_id] = future

        self.send_message(publish)

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            if isinstance(result, RequestOk):
                return PublishOk(request_id=result.request_id, parameters=result.parameters)
            return result
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise

    def send_publish_ok(self, request_id: int) -> None:
        """PUBLISH_OK を送信する"""
        ok = RequestOk(request_id=request_id)
        self.send_message(ok)

    def close(self, error_code: int = 0, reason: str = "") -> None:
        """セッションを閉じる"""
        self.state = SessionState.CLOSED
        if self._connection:
            self._connection.shutdown(msquic.ConnectionShutdownFlags.NONE, error_code)
        if self.on_close:
            self.on_close(self)
