#!/usr/bin/env python3
"""MOQT ビデオ Subscribe サンプル

msquic-py と mp4-py を使用して MOQT サーバーから映像を受信し MP4 に保存する
"""

from __future__ import annotations

import argparse
import signal
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass

import msquic
from moqt import (
    ClientSetup,
    ControlMessage,
    RequestError,
    RequestOk,
    ServerSetup,
    StreamType,
    Subscribe,
    TrackNamespace,
    decode_control_message,
    encode_varint,
)
from moqt.varint import decode_varint

try:
    from mp4 import (
        Mp4FileMuxer,
        Mp4FileMuxerOptions,
        Mp4MuxSample,
        Mp4SampleEntryAv01,
    )

    MP4_AVAILABLE = True
except ImportError:
    MP4_AVAILABLE = False


@dataclass
class MediaObject:
    """メディアオブジェクト"""

    group_id: int
    subgroup_id: int
    object_id: int
    data: bytes
    is_keyframe: bool = False


class MP4Writer:
    """MP4 ファイルへの書き込みを行うクラス"""

    def __init__(self, filename: str, width: int, height: int, fps: int):
        self.filename = filename
        self.width = width
        self.height = height
        self.fps = fps
        self.timescale = 1_000_000
        self.frame_duration = self.timescale // fps
        self.frame_count = 0
        self.muxer: Mp4FileMuxer | None = None
        self.sample_entry: Mp4SampleEntryAv01 | None = None

    def start(self) -> None:
        """ライターを開始"""
        if not MP4_AVAILABLE:
            print("mp4-py が利用できません")
            return

        # moov ボックスのサイズを見積もる
        estimated_frames = self.fps * 60 * 10
        reserved_size = Mp4FileMuxerOptions.estimate_maximum_moov_box_size(0, estimated_frames)
        options = Mp4FileMuxerOptions(reserved_moov_box_size=reserved_size)

        self.muxer = Mp4FileMuxer(self.filename, options)
        print(f"MP4 ファイルを開く: {self.filename}")

    def write(self, data: bytes, keyframe: bool) -> None:
        """フレームを書き込む"""
        if self.muxer is None:
            return

        # 最初のフレームでサンプルエントリーを作成
        sample_entry = None
        if self.sample_entry is None:
            self.sample_entry = Mp4SampleEntryAv01(
                width=self.width,
                height=self.height,
                config_obus=b"",
                seq_profile=0,
                seq_level_idx_0=8,
                seq_tier_0=0,
                high_bitdepth=0,
                twelve_bit=0,
                monochrome=0,
                chroma_subsampling_x=1,
                chroma_subsampling_y=1,
                chroma_sample_position=0,
            )
            sample_entry = self.sample_entry

        sample = Mp4MuxSample(
            track_kind="video",
            sample_entry=sample_entry,
            keyframe=keyframe,
            timescale=self.timescale,
            duration=self.frame_duration,
            data=data,
        )
        self.muxer.append_sample(sample)
        self.frame_count += 1

    def stop(self) -> None:
        """ライターを停止"""
        if self.muxer:
            self.muxer.finalize()
            self.muxer.close()
            print(f"MP4 ファイルを保存: {self.filename} ({self.frame_count} フレーム)")


class MoqtVideoSubscriber:
    """MOQT ビデオ購読クライアント"""

    def __init__(
        self,
        host: str,
        port: int,
        namespace: list[str],
        track_name: str,
        output_file: str,
        width: int = 640,
        height: int = 480,
        fps: int = 30,
    ):
        self.host = host
        self.port = port
        self.namespace = namespace
        self.track_name = track_name
        self.output_file = output_file
        self.width = width
        self.height = height
        self.fps = fps

        self.running = False
        self.connected = threading.Event()
        self.setup_complete = threading.Event()
        self.subscribe_complete = threading.Event()
        self.shutdown_complete = threading.Event()

        self.control_stream: msquic.Stream | None = None
        self.receive_buffer = bytearray()
        self.track_alias: int | None = None
        self.request_id: int | None = None

        # データストリーム受信用
        self.data_stream_buffers: dict[int, bytearray] = {}
        self.data_stream_headers: dict[int, dict] = {}

        # 受信オブジェクト
        self.received_objects: list[MediaObject] = []
        self.objects_lock = threading.Lock()

        # msquic 初期化
        self.registration = msquic.Registration(
            "moqt_video_subscriber", msquic.ExecutionProfile.LOW_LATENCY
        )
        self.configuration = msquic.Configuration(
            self.registration,
            ["moqt-15"],
            idle_timeout_ms=30000,
            peer_bidi_stream_count=100,
            peer_unidi_stream_count=100,
        )
        self.configuration.load_credential_none(no_certificate_validation=True)
        self.connection = msquic.Connection(self.registration)

        # MP4 ライター
        self.mp4_writer: MP4Writer | None = None

    def connect(self) -> bool:
        """サーバーに接続"""

        def on_connected() -> None:
            self.connected.set()

        def on_shutdown_complete(_app_close_in_progress: bool) -> None:
            self.shutdown_complete.set()

        def on_peer_stream_started(stream: msquic.Stream) -> None:
            self._add_data_stream(stream)

        self.connection.set_on_connected(on_connected)
        self.connection.set_on_shutdown_complete(on_shutdown_complete)
        self.connection.set_on_peer_stream_started(on_peer_stream_started)

        self.connection.start(self.configuration, self.host, self.port)
        print(f"接続中: {self.host}:{self.port}")

        if not self.connected.wait(timeout=5.0):
            print("接続タイムアウト")
            return False

        print("接続完了")
        return True

    def setup(self) -> bool:
        """MOQT セットアップ"""
        # 制御ストリームを開く
        self.control_stream = self.connection.open_stream(msquic.StreamOpenFlags.NONE)
        self.control_stream.start(msquic.StreamStartFlags.IMMEDIATE)

        def on_receive(data: Sequence[int], _fin: bool) -> None:
            self._on_control_receive(bytes(data))

        self.control_stream.set_on_receive(on_receive)

        # Stream Type を送信
        self.control_stream.send(encode_varint(StreamType.CONTROL), msquic.SendFlags.NONE)

        # CLIENT_SETUP を送信
        setup = ClientSetup()
        setup.set_max_request_id(100)
        setup.set_path("/moqt")
        self._send_control_message(setup)
        print("CLIENT_SETUP を送信")

        if not self.setup_complete.wait(timeout=5.0):
            print("セットアップタイムアウト")
            return False

        print("セットアップ完了")
        return True

    def subscribe(self) -> bool:
        """トラックを購読"""
        self.request_id = 0
        self.track_alias = 0

        subscribe = Subscribe(
            request_id=self.request_id,
            track_alias=self.track_alias,
            track_namespace=TrackNamespace(tuple=[ns.encode() for ns in self.namespace]),
            track_name=self.track_name.encode(),
        )
        self._send_control_message(subscribe)
        print(f"SUBSCRIBE を送信: {'/'.join(self.namespace)}/{self.track_name}")

        if not self.subscribe_complete.wait(timeout=5.0):
            print("SUBSCRIBE タイムアウト")
            return False

        print("SUBSCRIBE 完了")
        return True

    def _on_control_receive(self, data: bytes) -> None:
        """制御ストリームからデータを受信"""
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
        if isinstance(message, ServerSetup):
            print("SERVER_SETUP を受信")
            self.setup_complete.set()
        elif isinstance(message, RequestOk):
            print(f"REQUEST_OK を受信: request_id={message.request_id}")
            self.subscribe_complete.set()
        elif isinstance(message, RequestError):
            print(f"REQUEST_ERROR を受信: {message.reason_phrase}")
        else:
            print(f"受信: {type(message).__name__}")

    def _send_control_message(self, message: ControlMessage) -> None:
        """制御メッセージを送信"""
        if self.control_stream is None:
            raise RuntimeError("制御ストリームが設定されていません")
        self.control_stream.send(message.encode(), msquic.SendFlags.NONE)

    def _add_data_stream(self, stream: msquic.Stream) -> None:
        """データストリームを追加"""
        stream_id = id(stream)
        self.data_stream_buffers[stream_id] = bytearray()

        def on_receive(data: Sequence[int], _fin: bool) -> None:
            self._on_data_receive(stream_id, bytes(data))

        stream.set_on_receive(on_receive)

    def _on_data_receive(self, stream_id: int, data: bytes) -> None:
        """データストリームからデータを受信"""
        buffer = self.data_stream_buffers.get(stream_id)
        if buffer is None:
            return

        buffer.extend(data)

        # ヘッダーがまだ解析されていない場合
        if stream_id not in self.data_stream_headers:
            self._parse_data_stream_header(stream_id, buffer)

        # オブジェクトを解析
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
            }

            # ヘッダー部分を削除
            del buffer[:offset]

            print(
                f"SUBGROUP ヘッダー受信: track={track_alias}, "
                f"group={group_id}, subgroup={subgroup_id}"
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
                    break

                payload = bytes(buffer[offset : offset + payload_len])
                del buffer[: offset + payload_len]

                # 最初のオブジェクトはキーフレーム
                is_keyframe = object_id == 0

                obj = MediaObject(
                    group_id=header_info["group_id"],
                    subgroup_id=header_info["subgroup_id"],
                    object_id=object_id,
                    data=payload,
                    is_keyframe=is_keyframe,
                )

                with self.objects_lock:
                    self.received_objects.append(obj)

                print(
                    f"オブジェクト受信: group={obj.group_id}, "
                    f"object={obj.object_id}, size={len(payload)}, "
                    f"keyframe={is_keyframe}"
                )

            except (ValueError, IndexError):
                break

    def run(self, duration: float = 10.0) -> None:
        """映像受信を実行"""
        self.running = True

        # MP4 ライターを開始
        if MP4_AVAILABLE:
            self.mp4_writer = MP4Writer(self.output_file, self.width, self.height, self.fps)
            self.mp4_writer.start()
        else:
            print("mp4-py が必要です")
            print("インストール: uv sync --group example")

        print(f"映像受信開始: {duration}秒")
        start_time = time.time()

        while self.running and (time.time() - start_time) < duration:
            # 受信したオブジェクトを MP4 に書き込む
            with self.objects_lock:
                for obj in self.received_objects:
                    if self.mp4_writer:
                        self.mp4_writer.write(obj.data, obj.is_keyframe)
                self.received_objects.clear()

            time.sleep(0.01)

        print("映像受信終了")

        # MP4 ライターを停止
        if self.mp4_writer:
            self.mp4_writer.stop()

    def stop(self) -> None:
        """受信を停止"""
        self.running = False

    def close(self) -> None:
        """接続を閉じる"""
        self.connection.shutdown(msquic.ConnectionShutdownFlags.NONE, 0)
        self.shutdown_complete.wait(timeout=5.0)
        print("接続終了")


def main() -> None:
    parser = argparse.ArgumentParser(description="MOQT Video Subscriber")
    parser.add_argument("--host", default="127.0.0.1", help="ホスト")
    parser.add_argument("--port", type=int, default=4433, help="ポート")
    parser.add_argument("--namespace", default="video", help="名前空間")
    parser.add_argument("--track", default="main", help="トラック名")
    parser.add_argument("--output", default="output.mp4", help="出力ファイル")
    parser.add_argument("--width", type=int, default=640, help="幅")
    parser.add_argument("--height", type=int, default=480, help="高さ")
    parser.add_argument("--fps", type=int, default=30, help="FPS")
    parser.add_argument("--duration", type=float, default=10.0, help="受信時間 (秒)")
    args = parser.parse_args()

    subscriber = MoqtVideoSubscriber(
        host=args.host,
        port=args.port,
        namespace=[args.namespace],
        track_name=args.track,
        output_file=args.output,
        width=args.width,
        height=args.height,
        fps=args.fps,
    )

    shutdown_event = threading.Event()

    def signal_handler(_signum: int, _frame: object) -> None:
        print("\n終了シグナルを受信")
        subscriber.stop()
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        if not subscriber.connect():
            return

        if not subscriber.setup():
            return

        if not subscriber.subscribe():
            return

        subscriber.run(duration=args.duration)

    finally:
        subscriber.close()


if __name__ == "__main__":
    main()
