"""DATAGRAM (RFC 9221) のテスト

msquic の DATAGRAM 機能をテストする
"""

import threading

import msquic

from conftest import get_free_port


def test_datagram_send_receive(certificates):
    """DATAGRAM 送受信テスト"""
    port = get_free_port()
    server_connected_event = threading.Event()
    client_connected_event = threading.Event()
    client_shutdown_event = threading.Event()
    server_shutdown_event = threading.Event()
    datagram_received_event = threading.Event()
    datagram_state_event = threading.Event()
    received_datagrams = []
    datagram_send_enabled = [False]
    max_send_length = [0]

    # サーバー側
    server_reg = msquic.Registration("datagram_server", msquic.ExecutionProfile.LOW_LATENCY)
    server_config = msquic.Configuration(
        server_reg,
        ["datagram-test"],
        idle_timeout_ms=5000,
        peer_bidi_stream_count=10,
        datagram_receive_enabled=True,
    )
    server_config.load_credential_file(
        certificates["cert_file"],
        certificates["key_file"],
    )

    listener = msquic.Listener(server_reg)
    server_connections = []

    def on_new_connection(server_conn):
        server_connections.append(server_conn)

        def on_datagram_received(data):
            received_datagrams.append(bytes(data))
            datagram_received_event.set()

        def on_server_shutdown_complete(_app_close_in_progress):
            server_shutdown_event.set()

        server_conn.set_on_datagram_received(on_datagram_received)
        server_conn.set_on_shutdown_complete(on_server_shutdown_complete)
        server_connected_event.set()

    listener.set_on_new_connection(on_new_connection)
    listener.start(server_config, ["datagram-test"], port)

    # クライアント側
    client_reg = msquic.Registration("datagram_client", msquic.ExecutionProfile.LOW_LATENCY)
    client_config = msquic.Configuration(
        client_reg,
        ["datagram-test"],
        idle_timeout_ms=5000,
        datagram_receive_enabled=True,
    )
    client_config.load_credential_none(no_certificate_validation=True)

    conn = msquic.Connection(client_reg)

    def on_connected(_session_resumed):
        client_connected_event.set()

    def on_shutdown_complete(_app_close_in_progress):
        client_shutdown_event.set()

    def on_datagram_state_changed(send_enabled, length):
        datagram_send_enabled[0] = send_enabled
        max_send_length[0] = length
        datagram_state_event.set()

    conn.set_on_connected(on_connected)
    conn.set_on_shutdown_complete(on_shutdown_complete)
    conn.set_on_datagram_state_changed(on_datagram_state_changed)

    # 接続開始
    conn.start(client_config, "127.0.0.1", port)

    # 接続完了を待機
    assert client_connected_event.wait(timeout=5.0), "Client connection timeout"
    assert server_connected_event.wait(timeout=5.0), "Server connection timeout"

    # DATAGRAM 状態変更を待機
    assert datagram_state_event.wait(timeout=5.0), "Datagram state change timeout"
    assert datagram_send_enabled[0], "Datagram send should be enabled"
    assert max_send_length[0] > 0, "Max send length should be > 0"

    # DATAGRAM を送信
    test_data = b"Hello, DATAGRAM!"
    conn.send_datagram(test_data)

    # 受信を待機
    assert datagram_received_event.wait(timeout=5.0), "Datagram receive timeout"
    assert len(received_datagrams) == 1
    assert received_datagrams[0] == test_data

    # クリーンアップ
    conn.shutdown(msquic.ConnectionShutdownFlags.NONE, 0)
    assert client_shutdown_event.wait(timeout=5.0), "Client shutdown timeout"
    assert server_shutdown_event.wait(timeout=5.0), "Server shutdown timeout"
    listener.close()


def test_datagram_bidirectional(certificates):
    """DATAGRAM 双方向送受信テスト"""
    port = get_free_port()
    server_connected_event = threading.Event()
    client_connected_event = threading.Event()
    client_shutdown_event = threading.Event()
    server_shutdown_event = threading.Event()
    client_datagram_received_event = threading.Event()
    server_datagram_received_event = threading.Event()
    client_received_datagrams = []
    server_received_datagrams = []

    # サーバー側
    server_reg = msquic.Registration("datagram_server", msquic.ExecutionProfile.LOW_LATENCY)
    server_config = msquic.Configuration(
        server_reg,
        ["datagram-test"],
        idle_timeout_ms=5000,
        peer_bidi_stream_count=10,
        datagram_receive_enabled=True,
    )
    server_config.load_credential_file(
        certificates["cert_file"],
        certificates["key_file"],
    )

    listener = msquic.Listener(server_reg)
    server_connections = []

    def on_new_connection(server_conn):
        server_connections.append(server_conn)

        def on_server_datagram_received(data):
            server_received_datagrams.append(bytes(data))
            server_datagram_received_event.set()
            # エコーバック
            server_conn.send_datagram(bytes(data))

        def on_server_datagram_state_changed(send_enabled, length):
            if send_enabled:
                server_connected_event.set()

        def on_server_shutdown_complete(_app_close_in_progress):
            server_shutdown_event.set()

        server_conn.set_on_datagram_received(on_server_datagram_received)
        server_conn.set_on_datagram_state_changed(on_server_datagram_state_changed)
        server_conn.set_on_shutdown_complete(on_server_shutdown_complete)

    listener.set_on_new_connection(on_new_connection)
    listener.start(server_config, ["datagram-test"], port)

    # クライアント側
    client_reg = msquic.Registration("datagram_client", msquic.ExecutionProfile.LOW_LATENCY)
    client_config = msquic.Configuration(
        client_reg,
        ["datagram-test"],
        idle_timeout_ms=5000,
        datagram_receive_enabled=True,
    )
    client_config.load_credential_none(no_certificate_validation=True)

    conn = msquic.Connection(client_reg)
    datagram_ready_event = threading.Event()

    def on_connected(_session_resumed):
        client_connected_event.set()

    def on_shutdown_complete(_app_close_in_progress):
        client_shutdown_event.set()

    def on_datagram_state_changed(send_enabled, length):
        if send_enabled:
            datagram_ready_event.set()

    def on_client_datagram_received(data):
        client_received_datagrams.append(bytes(data))
        client_datagram_received_event.set()

    conn.set_on_connected(on_connected)
    conn.set_on_shutdown_complete(on_shutdown_complete)
    conn.set_on_datagram_state_changed(on_datagram_state_changed)
    conn.set_on_datagram_received(on_client_datagram_received)

    # 接続開始
    conn.start(client_config, "127.0.0.1", port)

    # 接続完了を待機
    assert client_connected_event.wait(timeout=5.0), "Client connection timeout"
    assert server_connected_event.wait(timeout=5.0), "Server connection timeout"
    assert datagram_ready_event.wait(timeout=5.0), "Datagram ready timeout"

    # クライアントから DATAGRAM を送信
    test_data = b"Bidirectional DATAGRAM"
    conn.send_datagram(test_data)

    # サーバーでの受信を待機
    assert server_datagram_received_event.wait(timeout=5.0), "Server datagram receive timeout"
    assert server_received_datagrams[0] == test_data

    # クライアントでのエコーバックを待機
    assert client_datagram_received_event.wait(timeout=5.0), "Client datagram receive timeout"
    assert client_received_datagrams[0] == test_data

    # クリーンアップ
    conn.shutdown(msquic.ConnectionShutdownFlags.NONE, 0)
    assert client_shutdown_event.wait(timeout=5.0), "Client shutdown timeout"
    assert server_shutdown_event.wait(timeout=5.0), "Server shutdown timeout"
    listener.close()
