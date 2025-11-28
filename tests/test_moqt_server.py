"""MOQT サーバーテスト

aioquic の MOQT クライアントから msquic-py の MOQT サーバーへ接続するテスト
"""

import asyncio
import socket

import pytest
from aioquic.asyncio import connect
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


def get_free_port():
    """空いているポートを取得"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MoqtClientProtocol(QuicConnectionProtocol):
    """aioquic MOQT クライアント用プロトコル"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.control_stream_id = None
        self.receive_buffer = bytearray()
        self.setup_complete = asyncio.Event()
        self.server_setup = None

    def quic_event_received(self, event):
        if isinstance(event, HandshakeCompleted):
            pass
        elif isinstance(event, StreamDataReceived):
            self._handle_stream_data(event)

    def _handle_stream_data(self, event):
        """ストリームデータを処理"""
        if event.stream_id == self.control_stream_id:
            self.receive_buffer.extend(event.data)
            self._process_messages()

    def _process_messages(self):
        """受信バッファからメッセージを処理"""
        while len(self.receive_buffer) > 0:
            try:
                message, consumed = decode_control_message(bytes(self.receive_buffer))
                del self.receive_buffer[:consumed]

                if isinstance(message, ServerSetup):
                    self.server_setup = message
                    self.setup_complete.set()
            except ValueError:
                break

    def send_client_setup(self):
        """CLIENT_SETUP を送信"""
        # 制御ストリームを開く
        self.control_stream_id = self._quic.get_next_available_stream_id()

        # Stream Type を送信
        stream_type = encode_varint(StreamType.CONTROL)

        # CLIENT_SETUP を送信
        setup = ClientSetup()
        setup.set_max_request_id(100)
        setup.set_path("/moqt")

        self._quic.send_stream_data(
            self.control_stream_id, stream_type + setup.encode(), end_stream=False
        )
        self.transmit()


@pytest.fixture
def moqt_msquic_server(certificates):
    """msquic MOQT サーバーを起動するフィクスチャ"""
    port = get_free_port()

    # Registration 作成
    reg = msquic.Registration("moqt_server", msquic.ExecutionProfile.LOW_LATENCY)

    # Configuration 作成
    config = msquic.Configuration(
        reg,
        ["moqt-15"],
        idle_timeout_ms=5000,
        peer_bidi_stream_count=100,
    )
    config.load_credential_file(
        certificates["cert_file"],
        certificates["key_file"],
    )

    # Listener 作成
    listener = msquic.Listener(reg)
    connections = []

    def on_new_connection(conn):
        connections.append(conn)

        def on_peer_stream_started(stream):
            receive_buffer = bytearray()
            stream_type_received = [False]

            def on_receive(data, fin):
                nonlocal receive_buffer
                data_bytes = bytes(data)

                # 最初のバイトは Stream Type
                if not stream_type_received[0]:
                    if len(data_bytes) > 0:
                        stream_type = data_bytes[0]
                        if stream_type == StreamType.CONTROL:
                            stream_type_received[0] = True
                            data_bytes = data_bytes[1:]

                receive_buffer.extend(data_bytes)

                # メッセージを処理
                while len(receive_buffer) > 0:
                    try:
                        message, consumed = decode_control_message(bytes(receive_buffer))
                        del receive_buffer[:consumed]

                        if isinstance(message, ClientSetup):
                            # SERVER_SETUP を送信
                            server_setup = ServerSetup()
                            server_setup.set_max_request_id(100)
                            stream.send(server_setup.encode(), msquic.SendFlags.NONE)
                    except ValueError:
                        break

            stream.set_on_receive(on_receive)

        conn.set_on_peer_stream_started(on_peer_stream_started)

    listener.set_on_new_connection(on_new_connection)
    listener.start(config, ["moqt-15"], port)

    yield {
        "host": "127.0.0.1",
        "port": port,
        "alpn": ["moqt-15"],
        "listener": listener,
        "connections": connections,
        "registration": reg,
    }


@pytest.mark.asyncio
async def test_aioquic_moqt_client_setup(moqt_msquic_server):
    """aioquic MOQT クライアントから msquic サーバーへの Setup テスト"""
    configuration = QuicConfiguration(
        is_client=True,
        alpn_protocols=moqt_msquic_server["alpn"],
    )
    configuration.verify_mode = False

    async with connect(
        moqt_msquic_server["host"],
        moqt_msquic_server["port"],
        configuration=configuration,
        create_protocol=MoqtClientProtocol,
    ) as protocol:
        await protocol.wait_connected()

        # CLIENT_SETUP を送信
        protocol.send_client_setup()

        # SERVER_SETUP を待機
        await asyncio.wait_for(protocol.setup_complete.wait(), timeout=5.0)

        # SERVER_SETUP を検証
        assert protocol.server_setup is not None
        assert isinstance(protocol.server_setup, ServerSetup)


@pytest.mark.asyncio
async def test_multiple_moqt_clients(moqt_msquic_server):
    """複数の MOQT クライアントからの接続テスト"""
    configuration = QuicConfiguration(
        is_client=True,
        alpn_protocols=moqt_msquic_server["alpn"],
    )
    configuration.verify_mode = False

    num_clients = 3

    async def client_task(client_id):
        async with connect(
            moqt_msquic_server["host"],
            moqt_msquic_server["port"],
            configuration=configuration,
            create_protocol=MoqtClientProtocol,
        ) as protocol:
            await protocol.wait_connected()
            protocol.send_client_setup()
            await asyncio.wait_for(protocol.setup_complete.wait(), timeout=5.0)
            assert protocol.server_setup is not None
            return client_id

    tasks = [client_task(i) for i in range(num_clients)]
    results = await asyncio.gather(*tasks)
    assert len(results) == num_clients
