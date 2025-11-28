#!/usr/bin/env python3
"""MOQT ビデオ Publish サンプル

msquic-py と webcodecs-py を使用して映像を MOQT サーバーに配信する
"""

from __future__ import annotations

import argparse
import signal
import threading
import time
from collections.abc import Sequence

import numpy as np

import msquic
from moqt import (
    ClientSetup,
    ControlMessage,
    Publish,
    RequestError,
    RequestOk,
    ServerSetup,
    StreamType,
    TrackNamespace,
    decode_control_message,
    encode_varint,
)

try:
    from webcodecs import (
        EncodedVideoChunk,
        EncodedVideoChunkType,
        LatencyMode,
        VideoEncoder,
        VideoEncoderBitrateMode,
        VideoEncoderConfig,
        VideoFrame,
        VideoFrameBufferInit,
        VideoPixelFormat,
    )

    WEBCODECS_AVAILABLE = True
except ImportError:
    WEBCODECS_AVAILABLE = False


def generate_test_pattern(width: int, height: int, frame_number: int) -> np.ndarray:
    """テストパターンを生成する (I420 フォーマット)"""
    # Y プレーン (輝度)
    y_plane = np.zeros((height, width), dtype=np.uint8)

    # 8 分割のカラーバー
    bar_width = width // 8
    colors_y = [235, 210, 170, 145, 105, 75, 35, 16]

    for i, y_val in enumerate(colors_y):
        start = i * bar_width
        end = (i + 1) * bar_width if i < 7 else width
        y_plane[:, start:end] = y_val

    # フレーム番号に基づいてパターンを少し変化させる
    shift = (frame_number * 10) % width
    y_plane = np.roll(y_plane, shift, axis=1)

    # U プレーン (Cb)
    u_height = height // 2
    u_width = width // 2
    u_plane = np.full((u_height, u_width), 128, dtype=np.uint8)

    # V プレーン (Cr)
    v_plane = np.full((u_height, u_width), 128, dtype=np.uint8)

    # I420 フォーマットに結合
    i420_data = np.concatenate([y_plane.flatten(), u_plane.flatten(), v_plane.flatten()])
    return i420_data


class SubgroupHeader:
    """SUBGROUP ヘッダー"""

    def __init__(
        self,
        track_alias: int,
        group_id: int,
        subgroup_id: int,
        publisher_priority: int = 0,
    ):
        self.track_alias = track_alias
        self.group_id = group_id
        self.subgroup_id = subgroup_id
        self.publisher_priority = publisher_priority

    def encode(self) -> bytes:
        """ヘッダーをエンコードする"""
        result = encode_varint(StreamType.SUBGROUP)
        result += encode_varint(self.track_alias)
        result += encode_varint(self.group_id)
        result += encode_varint(self.subgroup_id)
        result += encode_varint(self.publisher_priority)
        return result


class MoqtVideoPublisher:
    """MOQT ビデオ配信クライアント"""

    def __init__(
        self,
        host: str,
        port: int,
        namespace: list[str],
        track_name: str,
        width: int = 640,
        height: int = 480,
        fps: int = 30,
        bitrate: int = 500000,
    ):
        self.host = host
        self.port = port
        self.namespace = namespace
        self.track_name = track_name
        self.width = width
        self.height = height
        self.fps = fps
        self.bitrate = bitrate

        self.running = False
        self.connected = threading.Event()
        self.setup_complete = threading.Event()
        self.publish_complete = threading.Event()
        self.shutdown_complete = threading.Event()

        self.control_stream: msquic.Stream | None = None
        self.data_stream: msquic.Stream | None = None
        self.receive_buffer = bytearray()
        self.track_alias: int | None = None
        self.request_id: int | None = None

        self.group_id = 0
        self.subgroup_id = 0
        self.object_id = 0

        # msquic 初期化
        self.registration = msquic.Registration(
            "moqt_video_publisher", msquic.ExecutionProfile.LOW_LATENCY
        )
        self.configuration = msquic.Configuration(
            self.registration,
            ["moqt-15"],
            idle_timeout_ms=30000,
            peer_bidi_stream_count=100,
        )
        self.configuration.load_credential_none(no_certificate_validation=True)
        self.connection = msquic.Connection(self.registration)

        # エンコーダー (webcodecs が利用可能な場合)
        self.encoder: VideoEncoder | None = None
        self.encoded_chunks: list[EncodedVideoChunk] = []
        self.chunk_lock = threading.Lock()

    def _setup_encoder(self) -> None:
        """エンコーダーをセットアップする"""
        if not WEBCODECS_AVAILABLE:
            print("webcodecs-py が利用できません")
            return

        def on_output(chunk: EncodedVideoChunk) -> None:
            with self.chunk_lock:
                self.encoded_chunks.append(chunk)

        def on_error(error: str) -> None:
            print(f"エンコーダーエラー: {error}")

        self.encoder = VideoEncoder(on_output, on_error)

        config: VideoEncoderConfig = {
            "codec": "av01.0.04M.08",
            "width": self.width,
            "height": self.height,
            "bitrate": self.bitrate,
            "framerate": float(self.fps),
            "bitrate_mode": VideoEncoderBitrateMode.CONSTANT,
            "latency_mode": LatencyMode.REALTIME,
        }
        self.encoder.configure(config)
        print(f"エンコーダーを初期化: {self.width}x{self.height} @ {self.fps}fps")

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

    def publish(self) -> bool:
        """トラックを公開"""
        self.request_id = 0
        self.track_alias = 0

        publish_msg = Publish(
            request_id=self.request_id,
            track_alias=self.track_alias,
            track_namespace=TrackNamespace(tuple=[ns.encode() for ns in self.namespace]),
            track_name=self.track_name.encode(),
        )
        self._send_control_message(publish_msg)
        print(f"PUBLISH を送信: {'/'.join(self.namespace)}/{self.track_name}")

        if not self.publish_complete.wait(timeout=5.0):
            print("PUBLISH タイムアウト")
            return False

        print("PUBLISH 完了")
        return True

    def _on_control_receive(self, data: bytes) -> None:
        """制御ストリームからデータを受信"""
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
            print("SERVER_SETUP を受信")
            self.setup_complete.set()
        elif isinstance(message, RequestOk):
            print(f"REQUEST_OK を受信: request_id={message.request_id}")
            self.publish_complete.set()
        elif isinstance(message, RequestError):
            print(f"REQUEST_ERROR を受信: {message.reason_phrase}")
        else:
            print(f"受信: {type(message).__name__}")

    def _send_control_message(self, message: ControlMessage) -> None:
        """制御メッセージを送信"""
        if self.control_stream is None:
            raise RuntimeError("制御ストリームが設定されていません")
        self.control_stream.send(message.encode(), msquic.SendFlags.NONE)

    def open_data_stream(self) -> None:
        """データストリームを開く"""
        self.data_stream = self.connection.open_stream(msquic.StreamOpenFlags.UNIDIRECTIONAL)
        self.data_stream.start(msquic.StreamStartFlags.IMMEDIATE)

        # SUBGROUP ヘッダーを送信
        header = SubgroupHeader(
            track_alias=self.track_alias or 0,
            group_id=self.group_id,
            subgroup_id=self.subgroup_id,
            publisher_priority=0,
        )
        self.data_stream.send(header.encode(), msquic.SendFlags.NONE)
        print(f"データストリームを開く: group={self.group_id}, subgroup={self.subgroup_id}")

    def send_object(self, data: bytes, is_keyframe: bool) -> None:
        """オブジェクトを送信"""
        if self.data_stream is None:
            return

        # Object Header: Object ID (varint) + Extensions Count (0) + Payload Length + Payload
        object_header = encode_varint(self.object_id)
        object_header += encode_varint(0)  # extensions count
        object_header += encode_varint(len(data))

        self.data_stream.send(object_header + data, msquic.SendFlags.NONE)
        self.object_id += 1

        # キーフレームごとに新しいグループを開始
        if is_keyframe and self.object_id > 1:
            self.group_id += 1
            self.object_id = 0
            self.open_data_stream()

    def run(self, duration: float = 10.0) -> None:
        """映像配信を実行"""
        if not WEBCODECS_AVAILABLE:
            print("webcodecs-py が必要です")
            print("インストール: uv sync --group example")
            return

        self._setup_encoder()
        self.running = True

        print(f"映像配信開始: {duration}秒")
        self.open_data_stream()

        frame_duration = 1_000_000 // self.fps
        timestamp = 0
        frame_count = 0
        start_time = time.time()

        while self.running and (time.time() - start_time) < duration:
            # テストパターンを生成
            i420_data = generate_test_pattern(self.width, self.height, frame_count)

            # VideoFrame を作成
            init = VideoFrameBufferInit(
                format=VideoPixelFormat.I420,
                coded_width=self.width,
                coded_height=self.height,
                timestamp=timestamp,
            )
            video_frame = VideoFrame(i420_data, init)

            # エンコード
            keyframe = frame_count % (self.fps * 2) == 0
            if self.encoder:
                self.encoder.encode(video_frame, {"keyFrame": keyframe})
            video_frame.close()

            # エンコード済みチャンクを送信
            with self.chunk_lock:
                for chunk in self.encoded_chunks:
                    destination = np.zeros(chunk.byte_length, dtype=np.uint8)
                    chunk.copy_to(destination)
                    is_key = chunk.type == EncodedVideoChunkType.KEY
                    self.send_object(bytes(destination), is_key)
                self.encoded_chunks.clear()

            timestamp += frame_duration
            frame_count += 1

            # FPS 制御
            elapsed = time.time() - start_time
            expected = frame_count / self.fps
            if expected > elapsed:
                time.sleep(expected - elapsed)

        print(f"配信終了: {frame_count} フレーム")

        # エンコーダーをフラッシュ
        if self.encoder:
            self.encoder.flush()
            self.encoder.close()

    def stop(self) -> None:
        """配信を停止"""
        self.running = False

    def close(self) -> None:
        """接続を閉じる"""
        self.connection.shutdown(msquic.ConnectionShutdownFlags.NONE, 0)
        self.shutdown_complete.wait(timeout=5.0)
        print("接続終了")


def main() -> None:
    parser = argparse.ArgumentParser(description="MOQT Video Publisher")
    parser.add_argument("--host", default="127.0.0.1", help="ホスト")
    parser.add_argument("--port", type=int, default=4433, help="ポート")
    parser.add_argument("--namespace", default="video", help="名前空間")
    parser.add_argument("--track", default="main", help="トラック名")
    parser.add_argument("--width", type=int, default=640, help="幅")
    parser.add_argument("--height", type=int, default=480, help="高さ")
    parser.add_argument("--fps", type=int, default=30, help="FPS")
    parser.add_argument("--bitrate", type=int, default=500000, help="ビットレート")
    parser.add_argument("--duration", type=float, default=10.0, help="配信時間 (秒)")
    args = parser.parse_args()

    publisher = MoqtVideoPublisher(
        host=args.host,
        port=args.port,
        namespace=[args.namespace],
        track_name=args.track,
        width=args.width,
        height=args.height,
        fps=args.fps,
        bitrate=args.bitrate,
    )

    shutdown_event = threading.Event()

    def signal_handler(_signum: int, _frame: object) -> None:
        print("\n終了シグナルを受信")
        publisher.stop()
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        if not publisher.connect():
            return

        if not publisher.setup():
            return

        if not publisher.publish():
            return

        publisher.run(duration=args.duration)

    finally:
        publisher.close()


if __name__ == "__main__":
    main()
