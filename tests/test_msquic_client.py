"""msquic-py クライアント接続テスト

aioquic で起動した QUIC サーバーに msquic-py クライアントから接続するテスト
"""

import threading

import msquic


def test_connect_to_server(quic_server):
    """サーバーへの接続テスト"""
    connected_event = threading.Event()
    shutdown_event = threading.Event()

    # Registration 作成
    reg = msquic.Registration("test_client", msquic.ExecutionProfile.LOW_LATENCY)

    # Configuration 作成 (ALPN: echo)
    config = msquic.Configuration(
        reg,
        quic_server["alpn"],
        idle_timeout_ms=5000,
    )
    # クライアント証明書なし、サーバー証明書検証なし
    config.load_credential_none(no_certificate_validation=True)

    # Connection 作成
    conn = msquic.Connection(reg)

    def on_connected(_session_resumed):
        connected_event.set()

    def on_shutdown_complete(_app_close_in_progress):
        shutdown_event.set()

    conn.set_on_connected(on_connected)
    conn.set_on_shutdown_complete(on_shutdown_complete)

    # 接続開始
    conn.start(config, quic_server["host"], quic_server["port"])

    # 接続完了を待機
    assert connected_event.wait(timeout=5.0), "Connection timeout"

    # クリーンアップ
    conn.shutdown(msquic.ConnectionShutdownFlags.NONE, 0)
    assert shutdown_event.wait(timeout=5.0), "Shutdown timeout"


def test_echo_stream(quic_server):
    """Echo ストリームのテスト"""
    connected_event = threading.Event()
    received_event = threading.Event()
    shutdown_event = threading.Event()
    received_data = []

    # Registration 作成
    reg = msquic.Registration("test_client", msquic.ExecutionProfile.LOW_LATENCY)

    # Configuration 作成
    config = msquic.Configuration(
        reg,
        quic_server["alpn"],
        idle_timeout_ms=5000,
    )
    config.load_credential_none(no_certificate_validation=True)

    # Connection 作成
    conn = msquic.Connection(reg)

    def on_connected(_session_resumed):
        connected_event.set()

    def on_shutdown_complete(_app_close_in_progress):
        shutdown_event.set()

    conn.set_on_connected(on_connected)
    conn.set_on_shutdown_complete(on_shutdown_complete)

    # 接続開始
    conn.start(config, quic_server["host"], quic_server["port"])

    # 接続完了を待機
    assert connected_event.wait(timeout=5.0), "Connection timeout"

    # Stream を開く
    stream = conn.open_stream(msquic.StreamOpenFlags.NONE)

    def on_receive(data, fin):
        received_data.append(bytes(data))
        if fin:
            received_event.set()

    stream.set_on_receive(on_receive)

    # Stream 開始
    stream.start(msquic.StreamStartFlags.IMMEDIATE)

    # データ送信
    test_message = b"Hello, QUIC!"
    stream.send(test_message, msquic.SendFlags.FIN)

    # エコー応答を待機
    assert received_event.wait(timeout=5.0), "Echo response timeout"

    # 受信データを検証
    assert b"".join(received_data) == test_message

    # クリーンアップ
    conn.shutdown(msquic.ConnectionShutdownFlags.NONE, 0)
    assert shutdown_event.wait(timeout=5.0), "Shutdown timeout"


def test_multiple_streams(quic_server):
    """複数ストリームのテスト"""
    connected_event = threading.Event()
    shutdown_event = threading.Event()
    stream_results = {}
    stream_events = {}

    num_streams = 3

    # Registration 作成
    reg = msquic.Registration("test_client", msquic.ExecutionProfile.LOW_LATENCY)

    # Configuration 作成
    config = msquic.Configuration(
        reg,
        quic_server["alpn"],
        idle_timeout_ms=5000,
    )
    config.load_credential_none(no_certificate_validation=True)

    # Connection 作成
    conn = msquic.Connection(reg)

    def on_connected(_session_resumed):
        connected_event.set()

    def on_shutdown_complete(_app_close_in_progress):
        shutdown_event.set()

    conn.set_on_connected(on_connected)
    conn.set_on_shutdown_complete(on_shutdown_complete)

    # 接続開始
    conn.start(config, quic_server["host"], quic_server["port"])
    assert connected_event.wait(timeout=5.0), "Connection timeout"

    # 複数ストリームを開く
    for i in range(num_streams):
        stream_id = i
        stream_results[stream_id] = []
        stream_events[stream_id] = threading.Event()

        stream = conn.open_stream(msquic.StreamOpenFlags.NONE)

        def make_on_receive(sid):
            def on_receive(data, fin):
                stream_results[sid].append(bytes(data))
                if fin:
                    stream_events[sid].set()

            return on_receive

        stream.set_on_receive(make_on_receive(stream_id))
        stream.start(msquic.StreamStartFlags.IMMEDIATE)

        # 各ストリームで異なるメッセージを送信
        message = f"Stream {i} message".encode()
        stream.send(message, msquic.SendFlags.FIN)

    # すべてのストリームの応答を待機
    for i in range(num_streams):
        assert stream_events[i].wait(timeout=5.0), f"Stream {i} timeout"
        expected = f"Stream {i} message".encode()
        assert b"".join(stream_results[i]) == expected

    # クリーンアップ
    conn.shutdown(msquic.ConnectionShutdownFlags.NONE, 0)
    assert shutdown_event.wait(timeout=5.0), "Shutdown timeout"
