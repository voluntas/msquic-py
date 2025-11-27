"""msquic-py サーバーテスト

msquic で起動した QUIC サーバーに aioquic クライアントから接続するテスト
"""

import asyncio
import socket

import pytest
from aioquic.asyncio import connect
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import HandshakeCompleted, StreamDataReceived

import msquic


def get_free_port():
    """空いているポートを取得"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def create_echo_client_protocol(*args, **kwargs):
    """aioquic Echo クライアント用プロトコル"""
    protocol = EchoClientProtocol(*args, **kwargs)
    return protocol


class EchoClientProtocol(QuicConnectionProtocol):
    """aioquic Echo クライアント用プロトコル"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.received_data = []
        self.received_event = asyncio.Event()

    def quic_event_received(self, event):
        if isinstance(event, HandshakeCompleted):
            pass
        elif isinstance(event, StreamDataReceived):
            self.received_data.append(event.data)
            if event.end_stream:
                self.received_event.set()


@pytest.fixture
def msquic_server(certificates):
    """msquic Echo サーバーを起動するフィクスチャ"""
    port = get_free_port()

    # Registration 作成
    reg = msquic.Registration("test_server", msquic.ExecutionProfile.LOW_LATENCY)

    # Configuration 作成
    config = msquic.Configuration(
        reg,
        ["echo"],
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
            def on_receive(data, fin):
                # エコーバック
                if fin:
                    stream.send(bytes(data), msquic.SendFlags.FIN)
                else:
                    stream.send(bytes(data), msquic.SendFlags.NONE)

            stream.set_on_receive(on_receive)

        conn.set_on_peer_stream_started(on_peer_stream_started)

    listener.set_on_new_connection(on_new_connection)
    listener.start(config, ["echo"], port)

    yield {
        "host": "127.0.0.1",
        "port": port,
        "alpn": ["echo"],
        "certificates": certificates,
        "listener": listener,
        "connections": connections,
        "registration": reg,
    }

    # listener.stop() はデストラクタで自動的に呼ばれる


@pytest.mark.asyncio
async def test_aioquic_connect_to_msquic_server(msquic_server):
    """aioquic クライアントから msquic サーバーへの接続テスト"""
    configuration = QuicConfiguration(
        is_client=True,
        alpn_protocols=msquic_server["alpn"],
    )
    # サーバー証明書の検証をスキップ
    configuration.verify_mode = False

    async with connect(
        msquic_server["host"],
        msquic_server["port"],
        configuration=configuration,
        create_protocol=EchoClientProtocol,
    ) as protocol:
        # 接続が確立されたことを確認
        await protocol.wait_connected()
        assert protocol._quic._state.name == "CONNECTED"


@pytest.mark.asyncio
async def test_aioquic_echo_to_msquic_server(msquic_server):
    """aioquic クライアントから msquic サーバーへの Echo テスト"""
    configuration = QuicConfiguration(
        is_client=True,
        alpn_protocols=msquic_server["alpn"],
    )
    configuration.verify_mode = False

    async with connect(
        msquic_server["host"],
        msquic_server["port"],
        configuration=configuration,
        create_protocol=EchoClientProtocol,
    ) as protocol:
        await protocol.wait_connected()

        # ストリームを開いてデータを送信
        stream_id = protocol._quic.get_next_available_stream_id()
        test_message = b"Hello from aioquic!"

        protocol._quic.send_stream_data(stream_id, test_message, end_stream=True)
        protocol.transmit()

        # エコー応答を待機
        try:
            await asyncio.wait_for(protocol.received_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pytest.fail("Echo response timeout")

        # 受信データを検証
        received = b"".join(protocol.received_data)
        assert received == test_message


@pytest.mark.asyncio
async def test_aioquic_multiple_streams_to_msquic_server(msquic_server):
    """aioquic クライアントから msquic サーバーへの複数ストリームテスト"""
    configuration = QuicConfiguration(
        is_client=True,
        alpn_protocols=msquic_server["alpn"],
    )
    configuration.verify_mode = False

    async with connect(
        msquic_server["host"],
        msquic_server["port"],
        configuration=configuration,
        create_protocol=EchoClientProtocol,
    ) as protocol:
        await protocol.wait_connected()

        num_streams = 3
        messages = [f"Message {i}".encode() for i in range(num_streams)]

        # 複数ストリームでデータを送信
        for i in range(num_streams):
            stream_id = protocol._quic.get_next_available_stream_id()
            protocol._quic.send_stream_data(stream_id, messages[i], end_stream=True)

        protocol.transmit()

        # すべての応答を待機
        try:
            await asyncio.wait_for(protocol.received_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pytest.fail("Echo response timeout")

        # 少なくとも1つのメッセージが受信されたことを確認
        assert len(protocol.received_data) > 0


class MultiStreamClientProtocol(QuicConnectionProtocol):
    """複数ストリーム対応クライアントプロトコル"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stream_data = {}
        self.completed_streams = set()
        self.all_complete = asyncio.Event()
        self.expected_streams = 0

    def quic_event_received(self, event):
        if isinstance(event, StreamDataReceived):
            if event.stream_id not in self.stream_data:
                self.stream_data[event.stream_id] = b""
            self.stream_data[event.stream_id] += event.data
            if event.end_stream:
                self.completed_streams.add(event.stream_id)
                if len(self.completed_streams) >= self.expected_streams:
                    self.all_complete.set()


@pytest.mark.asyncio
async def test_large_data_echo(msquic_server):
    """大量のデータ送受信テスト"""
    configuration = QuicConfiguration(
        is_client=True,
        alpn_protocols=msquic_server["alpn"],
    )
    configuration.verify_mode = False

    async with connect(
        msquic_server["host"],
        msquic_server["port"],
        configuration=configuration,
        create_protocol=EchoClientProtocol,
    ) as protocol:
        await protocol.wait_connected()

        # 64KB のデータを送信
        large_data = b"X" * 65536
        stream_id = protocol._quic.get_next_available_stream_id()
        protocol._quic.send_stream_data(stream_id, large_data, end_stream=True)
        protocol.transmit()

        await asyncio.wait_for(protocol.received_event.wait(), timeout=5.0)

        received = b"".join(protocol.received_data)
        assert len(received) == len(large_data)
        assert received == large_data


@pytest.mark.asyncio
async def test_multiple_concurrent_clients(msquic_server):
    """複数クライアントからの同時接続テスト"""
    configuration = QuicConfiguration(
        is_client=True,
        alpn_protocols=msquic_server["alpn"],
    )
    configuration.verify_mode = False

    num_clients = 5

    async def client_task(client_id):
        async with connect(
            msquic_server["host"],
            msquic_server["port"],
            configuration=configuration,
            create_protocol=EchoClientProtocol,
        ) as protocol:
            await protocol.wait_connected()

            message = f"Client {client_id} message".encode()
            stream_id = protocol._quic.get_next_available_stream_id()
            protocol._quic.send_stream_data(stream_id, message, end_stream=True)
            protocol.transmit()

            await asyncio.wait_for(protocol.received_event.wait(), timeout=5.0)

            received = b"".join(protocol.received_data)
            assert received == message
            return client_id

    # 全クライアントを同時に実行
    tasks = [client_task(i) for i in range(num_clients)]
    results = await asyncio.gather(*tasks)
    assert len(results) == num_clients


@pytest.mark.asyncio
async def test_many_streams_single_connection(msquic_server):
    """単一接続で多数のストリームを使用するテスト"""
    configuration = QuicConfiguration(
        is_client=True,
        alpn_protocols=msquic_server["alpn"],
    )
    configuration.verify_mode = False

    num_streams = 20

    async with connect(
        msquic_server["host"],
        msquic_server["port"],
        configuration=configuration,
        create_protocol=MultiStreamClientProtocol,
    ) as protocol:
        await protocol.wait_connected()
        protocol.expected_streams = num_streams

        messages = {}
        for i in range(num_streams):
            stream_id = protocol._quic.get_next_available_stream_id()
            message = f"Stream {i} data".encode()
            messages[stream_id] = message
            protocol._quic.send_stream_data(stream_id, message, end_stream=True)

        protocol.transmit()

        await asyncio.wait_for(protocol.all_complete.wait(), timeout=5.0)

        # 全ストリームのデータを検証
        assert len(protocol.completed_streams) == num_streams
        for stream_id, expected in messages.items():
            assert protocol.stream_data[stream_id] == expected


@pytest.mark.asyncio
async def test_sequential_streams(msquic_server):
    """ストリームを順次開閉するテスト"""
    configuration = QuicConfiguration(
        is_client=True,
        alpn_protocols=msquic_server["alpn"],
    )
    configuration.verify_mode = False

    async with connect(
        msquic_server["host"],
        msquic_server["port"],
        configuration=configuration,
        create_protocol=EchoClientProtocol,
    ) as protocol:
        await protocol.wait_connected()

        for i in range(5):
            # 各イテレーションで新しいストリームを使用
            protocol.received_data = []
            protocol.received_event = asyncio.Event()

            message = f"Sequential message {i}".encode()
            stream_id = protocol._quic.get_next_available_stream_id()
            protocol._quic.send_stream_data(stream_id, message, end_stream=True)
            protocol.transmit()

            await asyncio.wait_for(protocol.received_event.wait(), timeout=5.0)

            received = b"".join(protocol.received_data)
            assert received == message


@pytest.mark.asyncio
async def test_rapid_connect_disconnect(msquic_server):
    """高速な接続・切断の繰り返しテスト"""
    configuration = QuicConfiguration(
        is_client=True,
        alpn_protocols=msquic_server["alpn"],
    )
    configuration.verify_mode = False

    for i in range(10):
        async with connect(
            msquic_server["host"],
            msquic_server["port"],
            configuration=configuration,
            create_protocol=EchoClientProtocol,
        ) as protocol:
            await protocol.wait_connected()
            assert protocol._quic._state.name == "CONNECTED"
