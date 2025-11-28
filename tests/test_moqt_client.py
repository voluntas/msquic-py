"""MOQT クライアントテスト

msquic-py の MOQT クライアントから aioquic の MOQT サーバーへ接続するテスト
"""

import asyncio
import threading

import pytest
import pytest_asyncio
from aioquic.asyncio import serve
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import HandshakeCompleted, StreamDataReceived

import msquic
from moqt import (
    ClientSetup,
    ServerSetup,
    StreamType,
    decode_control_message,
    encode_varint,
)

from conftest import get_free_port


class MoqtServerProtocol(QuicConnectionProtocol):
    """aioquic MOQT サーバー用プロトコル"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.control_stream_id = None
        self.receive_buffer = bytearray()
        self.setup_received = asyncio.Event()
        self.client_setup = None

    def quic_event_received(self, event):
        if isinstance(event, HandshakeCompleted):
            pass
        elif isinstance(event, StreamDataReceived):
            self._handle_stream_data(event)

    def _handle_stream_data(self, event):
        """ストリームデータを処理"""
        if self.control_stream_id is None:
            # 最初のストリームを制御ストリームとして扱う
            self.control_stream_id = event.stream_id
            # Stream Type をスキップ (最初の varint)
            data = event.data
            if len(data) > 0 and data[0] == StreamType.CONTROL:
                data = data[1:]
            self.receive_buffer.extend(data)
        elif event.stream_id == self.control_stream_id:
            self.receive_buffer.extend(event.data)

        self._process_messages()

    def _process_messages(self):
        """受信バッファからメッセージを処理"""
        while len(self.receive_buffer) > 0:
            try:
                message, consumed = decode_control_message(bytes(self.receive_buffer))
                del self.receive_buffer[:consumed]

                if isinstance(message, ClientSetup):
                    self.client_setup = message
                    self._send_server_setup()
                    self.setup_received.set()
            except ValueError:
                break

    def _send_server_setup(self):
        """SERVER_SETUP を送信"""
        if self.control_stream_id is None:
            return
        setup = ServerSetup()
        setup.set_max_request_id(100)
        data = setup.encode()
        self._quic.send_stream_data(self.control_stream_id, data, end_stream=False)
        self.transmit()


@pytest_asyncio.fixture
async def moqt_aioquic_server(certificates):
    """aioquic MOQT サーバーを起動するフィクスチャ"""
    port = get_free_port()
    protocols = []

    def create_protocol(*args, **kwargs):
        protocol = MoqtServerProtocol(*args, **kwargs)
        protocols.append(protocol)
        return protocol

    configuration = QuicConfiguration(
        is_client=False,
        alpn_protocols=["moqt-15"],
    )
    configuration.load_cert_chain(
        certificates["cert_file"],
        certificates["key_file"],
    )

    server = await serve(
        "127.0.0.1",
        port,
        configuration=configuration,
        create_protocol=create_protocol,
    )

    yield {
        "host": "127.0.0.1",
        "port": port,
        "alpn": ["moqt-15"],
        "protocols": protocols,
    }

    server.close()


@pytest.mark.asyncio
async def test_moqt_client_setup(moqt_aioquic_server):
    """MOQT クライアントの Setup テスト"""
    connected_event = threading.Event()
    shutdown_event = threading.Event()
    received_data: list[bytes] = []
    receive_event = threading.Event()

    def run_client():
        """別スレッドで msquic クライアントを実行"""
        # Registration 作成
        reg = msquic.Registration("moqt_client", msquic.ExecutionProfile.LOW_LATENCY)

        # Configuration 作成
        config = msquic.Configuration(
            reg,
            moqt_aioquic_server["alpn"],
            idle_timeout_ms=5000,
            peer_bidi_stream_count=100,
        )
        config.load_credential_none(no_certificate_validation=True)

        # Connection 作成
        conn = msquic.Connection(reg)

        def on_connected():
            connected_event.set()

        def on_shutdown_complete(_app_close_in_progress):
            shutdown_event.set()

        conn.set_on_connected(on_connected)
        conn.set_on_shutdown_complete(on_shutdown_complete)

        # 接続開始
        conn.start(config, moqt_aioquic_server["host"], moqt_aioquic_server["port"])

        # 接続完了を待機
        if not connected_event.wait(timeout=5.0):
            return False

        # 制御ストリームを開く
        stream = conn.open_stream(msquic.StreamOpenFlags.NONE)
        stream.start(msquic.StreamStartFlags.IMMEDIATE)

        def on_receive(data, _fin):
            received_data.append(bytes(data))
            if len(b"".join(received_data)) > 0:
                receive_event.set()

        stream.set_on_receive(on_receive)

        # Stream Type + CLIENT_SETUP を送信
        stream_type = encode_varint(StreamType.CONTROL)
        client_setup = ClientSetup()
        client_setup.set_max_request_id(100)
        client_setup.set_path("/moqt")

        stream.send(stream_type + client_setup.encode(), msquic.SendFlags.NONE)

        # SERVER_SETUP を待機
        if not receive_event.wait(timeout=5.0):
            conn.shutdown(msquic.ConnectionShutdownFlags.NONE, 0)
            return False

        # クリーンアップ
        conn.shutdown(msquic.ConnectionShutdownFlags.NONE, 0)
        shutdown_event.wait(timeout=5.0)
        return True

    # クライアントを別スレッドで実行
    result = await asyncio.to_thread(run_client)
    assert result, "Client failed"

    # SERVER_SETUP をデコード
    response_data = b"".join(received_data)
    server_setup, _ = decode_control_message(response_data)
    assert isinstance(server_setup, ServerSetup)
