#!/usr/bin/env python3
"""MOQT クライアントサンプル

msquic-py を使用した MOQT クライアントの実装例
"""

from __future__ import annotations

import argparse
import threading
import time
from collections.abc import Sequence

import msquic
from moqt import (
    ClientSetup,
    ControlMessage,
    Publish,
    RequestError,
    RequestOk,
    ServerSetup,
    StreamType,
    Subscribe,
    TrackNamespace,
    decode_control_message,
    encode_varint,
)


class MoqtClientSession:
    """MOQT クライアントセッション"""

    def __init__(self, connection: msquic.Connection):
        self.connection = connection
        self.control_stream: msquic.Stream | None = None
        self.receive_buffer = bytearray()
        self.setup_complete = threading.Event()
        self.server_setup: ServerSetup | None = None
        self.pending_requests: dict[int, threading.Event] = {}
        self.request_results: dict[int, ControlMessage] = {}
        self.next_request_id = 0
        self.peer_max_request_id = 0

    def open_control_stream(self) -> None:
        """制御ストリームを開く"""
        self.control_stream = self.connection.open_stream(msquic.StreamOpenFlags.NONE)
        self.control_stream.start(msquic.StreamStartFlags.IMMEDIATE)

        def on_receive(data: Sequence[int], _fin: bool) -> None:
            self._on_receive(bytes(data), _fin)

        self.control_stream.set_on_receive(on_receive)

        # Stream Type を送信
        self.control_stream.send(encode_varint(StreamType.CONTROL), msquic.SendFlags.NONE)

    def _on_receive(self, data: bytes, _fin: bool) -> None:
        """データ受信時の処理"""
        self.receive_buffer.extend(data)
        self._process_messages()

    def _process_messages(self) -> None:
        """受信バッファからメッセージを処理"""
        while len(self.receive_buffer) > 0:
            try:
                message, consumed = decode_control_message(bytes(self.receive_buffer))
                del self.receive_buffer[:consumed]
                self._handle_message(message)
            except ValueError:
                break

    def _handle_message(self, message: ControlMessage) -> None:
        """メッセージを処理"""
        if isinstance(message, ServerSetup):
            self._handle_server_setup(message)
        elif isinstance(message, (RequestOk, RequestError)):
            self._handle_request_response(message)
        else:
            print(f"受信: {type(message).__name__}")

    def _handle_server_setup(self, message: ServerSetup) -> None:
        """SERVER_SETUP を処理"""
        print("SERVER_SETUP を受信")
        self.server_setup = message

        # MAX_REQUEST_ID パラメータを取得
        from moqt import ParameterType, decode_varint

        max_req_param = message.get_parameter(ParameterType.MAX_REQUEST_ID)
        if max_req_param:
            self.peer_max_request_id, _ = decode_varint(max_req_param.value)

        self.setup_complete.set()

    def _handle_request_response(self, message: RequestOk | RequestError) -> None:
        """リクエストレスポンスを処理"""
        if isinstance(message, RequestOk):
            print(f"REQUEST_OK を受信: request_id={message.request_id}")
        else:
            print(
                f"REQUEST_ERROR を受信: request_id={message.request_id}, "
                f"error_code={message.error_code}, reason={message.reason_phrase}"
            )

        self.request_results[message.request_id] = message
        event = self.pending_requests.pop(message.request_id, None)
        if event:
            event.set()

    def send_client_setup(self, path: str = "/moqt", max_request_id: int = 100) -> None:
        """CLIENT_SETUP を送信"""
        setup = ClientSetup()
        setup.set_path(path)
        setup.set_max_request_id(max_request_id)

        self._send_message(setup)
        print("CLIENT_SETUP を送信")

    def allocate_request_id(self) -> int:
        """新しい Request ID を割り当て"""
        request_id = self.next_request_id
        self.next_request_id += 2
        return request_id

    def subscribe(
        self,
        namespace: list[str],
        track_name: str,
        track_alias: int | None = None,
        timeout: float = 5.0,
    ) -> RequestOk | RequestError | None:
        """トラックを購読"""
        request_id = self.allocate_request_id()
        if track_alias is None:
            track_alias = request_id

        subscribe = Subscribe(
            request_id=request_id,
            track_alias=track_alias,
            track_namespace=TrackNamespace(tuple=[ns.encode() for ns in namespace]),
            track_name=track_name.encode(),
        )

        event = threading.Event()
        self.pending_requests[request_id] = event

        self._send_message(subscribe)
        print(f"SUBSCRIBE を送信: {'/'.join(namespace)}/{track_name}")

        if event.wait(timeout=timeout):
            result = self.request_results.get(request_id)
            if isinstance(result, (RequestOk, RequestError)):
                return result
        return None

    def publish(
        self,
        namespace: list[str],
        track_name: str,
        track_alias: int | None = None,
        timeout: float = 5.0,
    ) -> RequestOk | RequestError | None:
        """トラックを公開"""
        request_id = self.allocate_request_id()
        if track_alias is None:
            track_alias = request_id

        publish_msg = Publish(
            request_id=request_id,
            track_alias=track_alias,
            track_namespace=TrackNamespace(tuple=[ns.encode() for ns in namespace]),
            track_name=track_name.encode(),
        )

        event = threading.Event()
        self.pending_requests[request_id] = event

        self._send_message(publish_msg)
        print(f"PUBLISH を送信: {'/'.join(namespace)}/{track_name}")

        if event.wait(timeout=timeout):
            result = self.request_results.get(request_id)
            if isinstance(result, (RequestOk, RequestError)):
                return result
        return None

    def _send_message(self, message: ControlMessage) -> None:
        """メッセージを送信"""
        if self.control_stream is None:
            raise RuntimeError("制御ストリームが設定されていません")
        self.control_stream.send(message.encode(), msquic.SendFlags.NONE)


class MoqtClient:
    """MOQT クライアント"""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.session: MoqtClientSession | None = None
        self.connected = threading.Event()
        self.shutdown_complete = threading.Event()

        # msquic 初期化
        self.registration = msquic.Registration("moqt_client", msquic.ExecutionProfile.LOW_LATENCY)
        self.configuration = msquic.Configuration(
            self.registration,
            ["moqt-15"],
            idle_timeout_ms=30000,
            peer_bidi_stream_count=100,
        )
        self.configuration.load_credential_none(no_certificate_validation=True)
        self.connection = msquic.Connection(self.registration)

    def connect(self) -> bool:
        """サーバーに接続"""

        def on_connected() -> None:
            self.connected.set()

        def on_shutdown_complete(_app_close_in_progress: bool) -> None:
            self.shutdown_complete.set()

        self.connection.set_on_connected(on_connected)
        self.connection.set_on_shutdown_complete(on_shutdown_complete)

        self.connection.start(self.configuration, self.host, self.port)
        print(f"接続中: {self.host}:{self.port}")

        if not self.connected.wait(timeout=5.0):
            print("接続タイムアウト")
            return False

        print("接続完了")

        # セッション作成
        self.session = MoqtClientSession(self.connection)
        self.session.open_control_stream()

        return True

    def setup(self, path: str = "/moqt") -> bool:
        """MOQT セットアップ"""
        if self.session is None:
            return False

        self.session.send_client_setup(path=path)

        if not self.session.setup_complete.wait(timeout=5.0):
            print("セットアップタイムアウト")
            return False

        print("セットアップ完了")
        return True

    def subscribe(
        self, namespace: list[str], track_name: str, timeout: float = 5.0
    ) -> RequestOk | RequestError | None:
        """トラックを購読"""
        if self.session is None:
            return None
        return self.session.subscribe(namespace, track_name, timeout=timeout)

    def publish(
        self, namespace: list[str], track_name: str, timeout: float = 5.0
    ) -> RequestOk | RequestError | None:
        """トラックを公開"""
        if self.session is None:
            return None
        return self.session.publish(namespace, track_name, timeout=timeout)

    def close(self) -> None:
        """接続を閉じる"""
        self.connection.shutdown(msquic.ConnectionShutdownFlags.NONE, 0)
        self.shutdown_complete.wait(timeout=5.0)
        print("接続終了")


def main():
    parser = argparse.ArgumentParser(description="MOQT Client")
    parser.add_argument("--host", default="127.0.0.1", help="ホスト")
    parser.add_argument("--port", type=int, default=4433, help="ポート")
    parser.add_argument("--path", default="/moqt", help="パス")
    parser.add_argument("--subscribe", help="購読するトラック (namespace/track)")
    parser.add_argument("--publish", help="公開するトラック (namespace/track)")
    args = parser.parse_args()

    client = MoqtClient(args.host, args.port)

    try:
        if not client.connect():
            return

        if not client.setup(path=args.path):
            return

        # 購読
        if args.subscribe:
            parts = args.subscribe.rsplit("/", 1)
            if len(parts) == 2:
                namespace = parts[0].split("/")
                track_name = parts[1]
                result = client.subscribe(namespace, track_name)
                if isinstance(result, RequestOk):
                    print(f"購読成功: {args.subscribe}")
                elif isinstance(result, RequestError):
                    print(f"購読失敗: {result.reason_phrase}")
                else:
                    print("購読タイムアウト")

        # 公開
        if args.publish:
            parts = args.publish.rsplit("/", 1)
            if len(parts) == 2:
                namespace = parts[0].split("/")
                track_name = parts[1]
                result = client.publish(namespace, track_name)
                if isinstance(result, RequestOk):
                    print(f"公開成功: {args.publish}")
                elif isinstance(result, RequestError):
                    print(f"公開失敗: {result.reason_phrase}")
                else:
                    print("公開タイムアウト")

        # デモ用に少し待機
        if not args.subscribe and not args.publish:
            print("接続維持中... (Ctrl+C で終了)")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass

    finally:
        client.close()


if __name__ == "__main__":
    main()
