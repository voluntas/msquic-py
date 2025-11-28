#!/usr/bin/env python3
"""MOQT サーバーサンプル

msquic-py を使用した MOQT サーバーの実装例
"""

from __future__ import annotations

import argparse
import signal
import threading
import time
from collections.abc import Sequence

import msquic
from moqt import (
    ClientSetup,
    ControlMessage,
    Publish,
    ServerSetup,
    StreamType,
    Subscribe,
    decode_control_message,
)


class MoqtServerSession:
    """MOQT サーバーセッション"""

    def __init__(self, connection: msquic.Connection):
        self.connection = connection
        self.control_stream: msquic.Stream | None = None
        self.receive_buffer = bytearray()
        self.stream_type_received = False
        self.setup_complete = False
        self.subscriptions: dict[int, dict] = {}
        self.publications: dict[int, dict] = {}
        self.next_request_id = 1

    def set_control_stream(self, stream: msquic.Stream) -> None:
        """制御ストリームを設定"""
        self.control_stream = stream

        def on_receive(data: Sequence[int], _fin: bool) -> None:
            self._on_receive(bytes(data))

        stream.set_on_receive(on_receive)

    def _on_receive(self, data: bytes) -> None:
        """データ受信時の処理"""
        # 最初のバイトは Stream Type
        if not self.stream_type_received:
            if len(data) > 0 and data[0] == StreamType.CONTROL:
                self.stream_type_received = True
                data = data[1:]

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
        if isinstance(message, ClientSetup):
            self._handle_client_setup(message)
        elif isinstance(message, Subscribe):
            self._handle_subscribe(message)
        elif isinstance(message, Publish):
            self._handle_publish(message)
        else:
            print(f"受信: {type(message).__name__}")

    def _handle_client_setup(self, message: ClientSetup) -> None:
        """CLIENT_SETUP を処理"""
        print("CLIENT_SETUP を受信")

        # SERVER_SETUP を送信
        setup = ServerSetup()
        setup.set_max_request_id(1000)

        self._send_message(setup)
        self.setup_complete = True
        print("SERVER_SETUP を送信")

    def _handle_subscribe(self, message: Subscribe) -> None:
        """SUBSCRIBE を処理"""
        namespace = "/".join(elem.decode() for elem in message.track_namespace.tuple)
        track_name = message.track_name.decode()
        print(f"SUBSCRIBE を受信: {namespace}/{track_name}")

        # サブスクリプションを登録
        self.subscriptions[message.request_id] = {
            "track_alias": message.track_alias,
            "namespace": message.track_namespace,
            "track_name": message.track_name,
        }

        # REQUEST_OK を送信 (ここでは簡略化)
        from moqt import RequestOk

        ok = RequestOk(request_id=message.request_id)
        self._send_message(ok)
        print(f"REQUEST_OK を送信: request_id={message.request_id}")

    def _handle_publish(self, message: Publish) -> None:
        """PUBLISH を処理"""
        namespace = "/".join(elem.decode() for elem in message.track_namespace.tuple)
        track_name = message.track_name.decode()
        print(f"PUBLISH を受信: {namespace}/{track_name}")

        # パブリケーションを登録
        self.publications[message.request_id] = {
            "track_alias": message.track_alias,
            "namespace": message.track_namespace,
            "track_name": message.track_name,
        }

        # REQUEST_OK を送信
        from moqt import RequestOk

        ok = RequestOk(request_id=message.request_id)
        self._send_message(ok)
        print(f"REQUEST_OK を送信: request_id={message.request_id}")

    def _send_message(self, message: ControlMessage) -> None:
        """メッセージを送信"""
        if self.control_stream is None:
            raise RuntimeError("制御ストリームが設定されていません")
        self.control_stream.send(message.encode(), msquic.SendFlags.NONE)


class MoqtServer:
    """MOQT サーバー"""

    def __init__(self, host: str, port: int, cert_file: str, key_file: str):
        self.host = host
        self.port = port
        self.cert_file = cert_file
        self.key_file = key_file
        self.sessions: dict[int, MoqtServerSession] = {}
        self.running = False

        # msquic 初期化
        self.registration = msquic.Registration("moqt_server", msquic.ExecutionProfile.LOW_LATENCY)
        self.configuration = msquic.Configuration(
            self.registration,
            ["moqt-15"],
            idle_timeout_ms=30000,
            peer_bidi_stream_count=100,
        )
        self.configuration.load_credential_file(cert_file, key_file)
        self.listener = msquic.Listener(self.registration)

    def start(self) -> None:
        """サーバーを開始"""
        session_id = [0]

        def on_new_connection(conn: msquic.Connection) -> None:
            current_id = session_id[0]
            session_id[0] += 1

            session = MoqtServerSession(conn)
            self.sessions[current_id] = session
            print(f"新しい接続: session_id={current_id}")

            def on_peer_stream_started(stream: msquic.Stream) -> None:
                print(f"新しいストリーム: session_id={current_id}")
                session.set_control_stream(stream)

            def on_shutdown_complete(_app_close_in_progress: bool) -> None:
                print(f"接続終了: session_id={current_id}")
                self.sessions.pop(current_id, None)

            conn.set_on_peer_stream_started(on_peer_stream_started)
            conn.set_on_shutdown_complete(on_shutdown_complete)

        self.listener.set_on_new_connection(on_new_connection)
        self.listener.start(self.configuration, ["moqt-15"], self.port)
        self.running = True
        print(f"MOQT サーバー起動: {self.host}:{self.port}")

    def stop(self) -> None:
        """サーバーを停止"""
        self.running = False
        print("MOQT サーバー停止")


def main():
    parser = argparse.ArgumentParser(description="MOQT Server")
    parser.add_argument("--host", default="127.0.0.1", help="ホスト")
    parser.add_argument("--port", type=int, default=4433, help="ポート")
    parser.add_argument("--cert", required=True, help="証明書ファイル")
    parser.add_argument("--key", required=True, help="秘密鍵ファイル")
    args = parser.parse_args()

    server = MoqtServer(args.host, args.port, args.cert, args.key)

    shutdown_event = threading.Event()

    def signal_handler(_signum, _frame):
        print("\n終了シグナルを受信")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    server.start()

    try:
        while not shutdown_event.is_set():
            time.sleep(0.1)
    finally:
        server.stop()


if __name__ == "__main__":
    main()
