#!/usr/bin/env python3
"""MOQT ビデオ中継サーバーサンプル

msquic-py を使用して MOQT ビデオ中継サーバーを実装する
Publish クライアントから受け取ったデータを Subscribe クライアントに転送する
"""

from __future__ import annotations

import argparse
import signal
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

import msquic
from moqt import (
    ClientSetup,
    ControlMessage,
    Publish,
    RequestOk,
    ServerSetup,
    StreamType,
    Subscribe,
    decode_control_message,
    encode_varint,
)
from moqt.varint import decode_varint


@dataclass
class TrackInfo:
    """トラック情報"""

    track_alias: int
    namespace: list[bytes]
    track_name: bytes
    publisher_session: MoqtRelaySession | None = None
    subscriber_sessions: list[MoqtRelaySession] = field(default_factory=list)


@dataclass
class MediaObject:
    """メディアオブジェクト"""

    group_id: int
    subgroup_id: int
    object_id: int
    data: bytes


class MoqtRelaySession:
    """MOQT 中継セッション"""

    def __init__(self, connection: msquic.Connection, server: MoqtRelayServer, session_id: int):
        self.connection = connection
        self.server = server
        self.session_id = session_id
        self.control_stream: msquic.Stream | None = None
        self.data_streams: dict[int, msquic.Stream] = {}
        self.outbound_streams: dict[tuple[int, int, int], msquic.Stream] = {}
        self.receive_buffer = bytearray()
        self.stream_type_received = False
        self.setup_complete = False

        # Publish/Subscribe 情報
        self.published_tracks: dict[int, int] = {}
        self.subscribed_tracks: dict[int, int] = {}

        # データストリーム受信用
        self.data_stream_buffers: dict[int, bytearray] = {}
        self.data_stream_headers: dict[int, dict] = {}

    def set_control_stream(self, stream: msquic.Stream) -> None:
        """制御ストリームを設定"""
        self.control_stream = stream

        def on_receive(data: Sequence[int], _fin: bool) -> None:
            self._on_control_receive(bytes(data))

        stream.set_on_receive(on_receive)

    def add_data_stream(self, stream: msquic.Stream) -> None:
        """データストリームを追加"""
        stream_id = id(stream)
        self.data_streams[stream_id] = stream
        self.data_stream_buffers[stream_id] = bytearray()

        def on_receive(data: Sequence[int], _fin: bool) -> None:
            self._on_data_receive(stream_id, bytes(data))

        stream.set_on_receive(on_receive)

    def _on_control_receive(self, data: bytes) -> None:
        """制御ストリームからデータを受信"""
        if not self.stream_type_received:
            if len(data) > 0 and data[0] == StreamType.CONTROL:
                self.stream_type_received = True
                data = data[1:]

        self.receive_buffer.extend(data)
        self._process_control_messages()

    def _process_control_messages(self) -> None:
        """制御メッセージを処理"""
        while len(self.receive_buffer) > 0:
            try:
                message, consumed = decode_control_message(bytes(self.receive_buffer))
                del self.receive_buffer[:consumed]
                self._handle_control_message(message)
            except ValueError:
                break

    def _handle_control_message(self, message: ControlMessage) -> None:
        """制御メッセージを処理"""
        if isinstance(message, ClientSetup):
            self._handle_client_setup(message)
        elif isinstance(message, Subscribe):
            self._handle_subscribe(message)
        elif isinstance(message, Publish):
            self._handle_publish(message)
        else:
            print(f"[Session {self.session_id}] 受信: {type(message).__name__}")

    def _handle_client_setup(self, message: ClientSetup) -> None:
        """CLIENT_SETUP を処理"""
        print(f"[Session {self.session_id}] CLIENT_SETUP を受信")

        setup = ServerSetup()
        setup.set_max_request_id(1000)
        self._send_control_message(setup)
        self.setup_complete = True
        print(f"[Session {self.session_id}] SERVER_SETUP を送信")

    def _handle_subscribe(self, message: Subscribe) -> None:
        """SUBSCRIBE を処理"""
        namespace = [elem for elem in message.track_namespace.tuple]
        namespace_str = "/".join(elem.decode() for elem in namespace)
        track_name = message.track_name
        print(f"[Session {self.session_id}] SUBSCRIBE: {namespace_str}/{track_name.decode()}")

        # トラックを検索
        track_key = (tuple(namespace), track_name)
        track = self.server.tracks.get(track_key)

        if track is None:
            # トラックがなければ作成
            track = TrackInfo(
                track_alias=message.track_alias,
                namespace=list(namespace),
                track_name=track_name,
            )
            self.server.tracks[track_key] = track

        track.subscriber_sessions.append(self)
        self.subscribed_tracks[message.request_id] = message.track_alias

        # REQUEST_OK を送信
        ok = RequestOk(request_id=message.request_id)
        self._send_control_message(ok)
        print(f"[Session {self.session_id}] REQUEST_OK を送信")

    def _handle_publish(self, message: Publish) -> None:
        """PUBLISH を処理"""
        namespace = [elem for elem in message.track_namespace.tuple]
        namespace_str = "/".join(elem.decode() for elem in namespace)
        track_name = message.track_name
        print(f"[Session {self.session_id}] PUBLISH: {namespace_str}/{track_name.decode()}")

        # トラックを登録
        track_key = (tuple(namespace), track_name)
        track = self.server.tracks.get(track_key)

        if track is None:
            track = TrackInfo(
                track_alias=message.track_alias,
                namespace=list(namespace),
                track_name=track_name,
            )
            self.server.tracks[track_key] = track

        track.publisher_session = self
        self.published_tracks[message.request_id] = message.track_alias

        # REQUEST_OK を送信
        ok = RequestOk(request_id=message.request_id)
        self._send_control_message(ok)
        print(f"[Session {self.session_id}] REQUEST_OK を送信")

    def _on_data_receive(self, stream_id: int, data: bytes) -> None:
        """データストリームからデータを受信"""
        buffer = self.data_stream_buffers.get(stream_id)
        if buffer is None:
            return

        buffer.extend(data)

        # ヘッダーがまだ解析されていない場合
        if stream_id not in self.data_stream_headers:
            self._parse_data_stream_header(stream_id, buffer)

        # オブジェクトを解析して転送
        self._process_data_stream(stream_id)

    def _parse_data_stream_header(self, stream_id: int, buffer: bytearray) -> None:
        """データストリームヘッダーを解析"""
        try:
            offset = 0
            stream_type, consumed = decode_varint(bytes(buffer), offset)
            offset += consumed

            if stream_type != StreamType.SUBGROUP:
                return

            track_alias, consumed = decode_varint(bytes(buffer), offset)
            offset += consumed
            group_id, consumed = decode_varint(bytes(buffer), offset)
            offset += consumed
            subgroup_id, consumed = decode_varint(bytes(buffer), offset)
            offset += consumed
            publisher_priority, consumed = decode_varint(bytes(buffer), offset)
            offset += consumed

            self.data_stream_headers[stream_id] = {
                "track_alias": track_alias,
                "group_id": group_id,
                "subgroup_id": subgroup_id,
                "publisher_priority": publisher_priority,
                "header_size": offset,
            }

            # ヘッダー部分を削除
            del buffer[:offset]

            print(
                f"[Session {self.session_id}] SUBGROUP ヘッダー: "
                f"track={track_alias}, group={group_id}, subgroup={subgroup_id}"
            )

        except (ValueError, IndexError):
            pass

    def _process_data_stream(self, stream_id: int) -> None:
        """データストリームからオブジェクトを処理"""
        buffer = self.data_stream_buffers.get(stream_id)
        header_info = self.data_stream_headers.get(stream_id)

        if buffer is None or header_info is None:
            return

        while len(buffer) > 0:
            try:
                offset = 0
                object_id, consumed = decode_varint(bytes(buffer), offset)
                offset += consumed
                extensions_count, consumed = decode_varint(bytes(buffer), offset)
                offset += consumed

                # Extensions をスキップ
                for _ in range(extensions_count):
                    _, consumed = decode_varint(bytes(buffer), offset)
                    offset += consumed
                    ext_len, consumed = decode_varint(bytes(buffer), offset)
                    offset += consumed
                    offset += ext_len

                payload_len, consumed = decode_varint(bytes(buffer), offset)
                offset += consumed

                if len(buffer) < offset + payload_len:
                    # データ不足
                    break

                payload = bytes(buffer[offset : offset + payload_len])
                del buffer[: offset + payload_len]

                # オブジェクトを転送
                media_object = MediaObject(
                    group_id=header_info["group_id"],
                    subgroup_id=header_info["subgroup_id"],
                    object_id=object_id,
                    data=payload,
                )
                self._forward_object(header_info["track_alias"], media_object)

            except (ValueError, IndexError):
                break

    def _forward_object(self, track_alias: int, obj: MediaObject) -> None:
        """オブジェクトを購読者に転送"""
        # トラックを検索
        for track_key, track in self.server.tracks.items():
            if track.publisher_session == self:
                for subscriber in track.subscriber_sessions:
                    if subscriber != self:
                        subscriber.send_object(track, obj)

    def send_object(self, track: TrackInfo, obj: MediaObject) -> None:
        """オブジェクトを送信"""
        # データストリームを開く (グループごとに新しいストリーム)
        stream_key = (track.track_alias, obj.group_id, obj.subgroup_id)

        if stream_key not in self.outbound_streams:
            stream = self.connection.open_stream(msquic.StreamOpenFlags.UNIDIRECTIONAL)
            stream.start(msquic.StreamStartFlags.IMMEDIATE)
            self.outbound_streams[stream_key] = stream

            # SUBGROUP ヘッダーを送信
            header = encode_varint(StreamType.SUBGROUP)
            header += encode_varint(track.track_alias)
            header += encode_varint(obj.group_id)
            header += encode_varint(obj.subgroup_id)
            header += encode_varint(0)  # publisher_priority
            stream.send(header, msquic.SendFlags.NONE)

        outbound_stream = self.outbound_streams.get(stream_key)
        if outbound_stream is None:
            return

        # オブジェクトを送信
        object_data = encode_varint(obj.object_id)
        object_data += encode_varint(0)  # extensions count
        object_data += encode_varint(len(obj.data))
        object_data += obj.data

        outbound_stream.send(object_data, msquic.SendFlags.NONE)

    def _send_control_message(self, message: ControlMessage) -> None:
        """制御メッセージを送信"""
        if self.control_stream is None:
            return
        self.control_stream.send(message.encode(), msquic.SendFlags.NONE)


class MoqtRelayServer:
    """MOQT 中継サーバー"""

    def __init__(self, host: str, port: int, cert_file: str, key_file: str):
        self.host = host
        self.port = port
        self.cert_file = cert_file
        self.key_file = key_file
        self.sessions: dict[int, MoqtRelaySession] = {}
        self.tracks: dict[tuple, TrackInfo] = {}
        self.running = False
        self.next_session_id = 0

        # msquic 初期化
        self.registration = msquic.Registration(
            "moqt_relay_server", msquic.ExecutionProfile.LOW_LATENCY
        )
        self.configuration = msquic.Configuration(
            self.registration,
            ["moqt-15"],
            idle_timeout_ms=30000,
            peer_bidi_stream_count=100,
            peer_unidi_stream_count=100,
        )
        self.configuration.load_credential_file(cert_file, key_file)
        self.listener = msquic.Listener(self.registration)

    def start(self) -> None:
        """サーバーを開始"""

        def on_new_connection(conn: msquic.Connection) -> None:
            session_id = self.next_session_id
            self.next_session_id += 1

            session = MoqtRelaySession(conn, self, session_id)
            self.sessions[session_id] = session
            print(f"新しい接続: session_id={session_id}")

            def on_peer_stream_started(stream: msquic.Stream) -> None:
                # ストリームの種類を判定 (unidirectional か bidirectional)
                if session.control_stream is None:
                    session.set_control_stream(stream)
                else:
                    session.add_data_stream(stream)

            def on_shutdown_complete(_app_close_in_progress: bool) -> None:
                print(f"接続終了: session_id={session_id}")
                self.sessions.pop(session_id, None)

                # トラックからセッションを削除
                for track in self.tracks.values():
                    if track.publisher_session == session:
                        track.publisher_session = None
                    if session in track.subscriber_sessions:
                        track.subscriber_sessions.remove(session)

            conn.set_on_peer_stream_started(on_peer_stream_started)
            conn.set_on_shutdown_complete(on_shutdown_complete)

        self.listener.set_on_new_connection(on_new_connection)
        self.listener.start(self.configuration, ["moqt-15"], self.port)
        self.running = True
        print(f"MOQT 中継サーバー起動: {self.host}:{self.port}")

    def stop(self) -> None:
        """サーバーを停止"""
        self.running = False
        print("MOQT 中継サーバー停止")


def main() -> None:
    parser = argparse.ArgumentParser(description="MOQT Video Relay Server")
    parser.add_argument("--host", default="127.0.0.1", help="ホスト")
    parser.add_argument("--port", type=int, default=4433, help="ポート")
    parser.add_argument("--cert", required=True, help="証明書ファイル")
    parser.add_argument("--key", required=True, help="秘密鍵ファイル")
    args = parser.parse_args()

    server = MoqtRelayServer(args.host, args.port, args.cert, args.key)

    shutdown_event = threading.Event()

    def signal_handler(_signum: int, _frame: object) -> None:
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
