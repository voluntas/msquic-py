"""0-RTT Resumption (セッション再開) のテスト

msquic の 0-RTT Resumption 機能をテストする
"""

import threading

import msquic

from conftest import get_free_port


def test_resumption_ticket_received(certificates):
    """Resumption Ticket 受信テスト

    サーバーが send_resumption_ticket() を呼んだ後、
    クライアントが RESUMPTION_TICKET_RECEIVED イベントを受信することを確認する。
    """
    port = get_free_port()
    server_connected_event = threading.Event()
    client_connected_event = threading.Event()
    client_shutdown_event = threading.Event()
    server_shutdown_event = threading.Event()
    ticket_received_event = threading.Event()
    received_ticket = []

    # サーバー側 (RESUME_AND_ZERORTT を設定)
    server_reg = msquic.Registration("zero_rtt_server", msquic.ExecutionProfile.LOW_LATENCY)
    server_config = msquic.Configuration(
        server_reg,
        ["zero-rtt-test"],
        idle_timeout_ms=5000,
        peer_bidi_stream_count=10,
        server_resumption_level=msquic.ServerResumptionLevel.RESUME_AND_ZERORTT,
    )
    server_config.load_credential_file(
        certificates["cert_file"],
        certificates["key_file"],
    )

    listener = msquic.Listener(server_reg)
    server_connections = []

    def on_new_connection(server_conn):
        server_connections.append(server_conn)

        def on_server_connected(_session_resumed):
            # サーバー側で Resumption Ticket を送信
            server_conn.send_resumption_ticket()
            server_connected_event.set()

        def on_server_shutdown_complete(_app_close_in_progress):
            server_shutdown_event.set()

        server_conn.set_on_connected(on_server_connected)
        server_conn.set_on_shutdown_complete(on_server_shutdown_complete)

    listener.set_on_new_connection(on_new_connection)
    listener.start(server_config, ["zero-rtt-test"], port)

    # クライアント側
    client_reg = msquic.Registration("zero_rtt_client", msquic.ExecutionProfile.LOW_LATENCY)
    client_config = msquic.Configuration(
        client_reg,
        ["zero-rtt-test"],
        idle_timeout_ms=5000,
    )
    client_config.load_credential_none(no_certificate_validation=True)

    conn = msquic.Connection(client_reg)

    def on_connected(_session_resumed):
        client_connected_event.set()

    def on_shutdown_complete(_app_close_in_progress):
        client_shutdown_event.set()

    def on_resumption_ticket_received(ticket):
        received_ticket.append(bytes(ticket))
        ticket_received_event.set()

    conn.set_on_connected(on_connected)
    conn.set_on_shutdown_complete(on_shutdown_complete)
    conn.set_on_resumption_ticket_received(on_resumption_ticket_received)

    # 接続開始
    conn.start(client_config, "127.0.0.1", port)

    # 接続完了を待機
    assert client_connected_event.wait(timeout=5.0), "Client connection timeout"
    assert server_connected_event.wait(timeout=5.0), "Server connection timeout"

    # Resumption Ticket を待機
    assert ticket_received_event.wait(timeout=5.0), "Ticket receive timeout"
    assert len(received_ticket) == 1
    assert len(received_ticket[0]) > 0, "Ticket should not be empty"

    # クリーンアップ
    conn.shutdown(msquic.ConnectionShutdownFlags.NONE, 0)
    assert client_shutdown_event.wait(timeout=5.0), "Client shutdown timeout"
    assert server_shutdown_event.wait(timeout=5.0), "Server shutdown timeout"
    listener.close()


def test_session_resumption(certificates):
    """セッション再開テスト

    1. 最初の接続で Resumption Ticket を取得
    2. 2回目の接続で Ticket を使用してセッションを再開
    注意: これは Session Resumption のテストであり、0-RTT データ送信のテストではない
    """
    port = get_free_port()
    received_ticket = []

    # サーバー側
    server_reg = msquic.Registration("zero_rtt_server", msquic.ExecutionProfile.LOW_LATENCY)
    server_config = msquic.Configuration(
        server_reg,
        ["zero-rtt-test"],
        idle_timeout_ms=5000,
        peer_bidi_stream_count=10,
        server_resumption_level=msquic.ServerResumptionLevel.RESUME_AND_ZERORTT,
    )
    server_config.load_credential_file(
        certificates["cert_file"],
        certificates["key_file"],
    )

    listener = msquic.Listener(server_reg)
    server_connections = []
    server_session_resumed = []
    server_connected_events = []
    server_shutdown_events = []

    def on_new_connection(server_conn):
        server_connections.append(server_conn)
        connected_event = threading.Event()
        shutdown_event = threading.Event()
        server_connected_events.append(connected_event)
        server_shutdown_events.append(shutdown_event)

        def on_server_connected(session_resumed):
            server_session_resumed.append(session_resumed)
            # Resumption Ticket を送信
            server_conn.send_resumption_ticket()
            connected_event.set()

        def on_server_shutdown_complete(_app_close_in_progress):
            shutdown_event.set()

        server_conn.set_on_connected(on_server_connected)
        server_conn.set_on_shutdown_complete(on_server_shutdown_complete)

    listener.set_on_new_connection(on_new_connection)
    listener.start(server_config, ["zero-rtt-test"], port)

    # クライアント側
    client_reg = msquic.Registration("zero_rtt_client", msquic.ExecutionProfile.LOW_LATENCY)
    client_config = msquic.Configuration(
        client_reg,
        ["zero-rtt-test"],
        idle_timeout_ms=5000,
    )
    client_config.load_credential_none(no_certificate_validation=True)

    # === 最初の接続 (Ticket を取得) ===
    conn1 = msquic.Connection(client_reg)
    client_connected_event1 = threading.Event()
    client_shutdown_event1 = threading.Event()
    ticket_received_event = threading.Event()
    client_session_resumed1 = [False]

    def on_connected1(session_resumed):
        client_session_resumed1[0] = session_resumed
        client_connected_event1.set()

    def on_shutdown_complete1(_app_close_in_progress):
        client_shutdown_event1.set()

    def on_resumption_ticket_received(ticket):
        received_ticket.append(bytes(ticket))
        ticket_received_event.set()

    conn1.set_on_connected(on_connected1)
    conn1.set_on_shutdown_complete(on_shutdown_complete1)
    conn1.set_on_resumption_ticket_received(on_resumption_ticket_received)

    conn1.start(client_config, "127.0.0.1", port)

    assert client_connected_event1.wait(timeout=5.0), "First client connection timeout"
    assert server_connected_events[0].wait(timeout=5.0), "First server connection timeout"
    assert ticket_received_event.wait(timeout=5.0), "Ticket receive timeout"
    assert not client_session_resumed1[0], "First connection should not be resumed"

    # 最初の接続を閉じる
    conn1.shutdown(msquic.ConnectionShutdownFlags.NONE, 0)
    assert client_shutdown_event1.wait(timeout=5.0), "First client shutdown timeout"
    assert server_shutdown_events[0].wait(timeout=5.0), "First server shutdown timeout"

    # === 2回目の接続 (Ticket を使用) ===
    conn2 = msquic.Connection(client_reg)
    client_connected_event2 = threading.Event()
    client_shutdown_event2 = threading.Event()
    client_session_resumed2 = [False]

    def on_connected2(session_resumed):
        client_session_resumed2[0] = session_resumed
        client_connected_event2.set()

    def on_shutdown_complete2(_app_close_in_progress):
        client_shutdown_event2.set()

    conn2.set_on_connected(on_connected2)
    conn2.set_on_shutdown_complete(on_shutdown_complete2)

    # Resumption Ticket を設定
    conn2.set_resumption_ticket(received_ticket[0])

    conn2.start(client_config, "127.0.0.1", port)

    assert client_connected_event2.wait(timeout=5.0), "Second client connection timeout"
    assert server_connected_events[1].wait(timeout=5.0), "Second server connection timeout"

    # セッションが再開されたことを確認
    assert client_session_resumed2[0], "Second connection should be resumed (client)"
    assert len(server_session_resumed) >= 2, "Server should have 2 connections"
    assert server_session_resumed[1], "Second connection should be resumed (server)"

    # 2回目の接続を閉じる
    conn2.shutdown(msquic.ConnectionShutdownFlags.NONE, 0)
    assert client_shutdown_event2.wait(timeout=5.0), "Second client shutdown timeout"
    assert server_shutdown_events[1].wait(timeout=5.0), "Second server shutdown timeout"

    listener.close()


def test_zero_rtt_early_data(certificates):
    """0-RTT 早期データ送信テスト

    真の 0-RTT テスト: CONNECTED イベントを待たずにデータを送信し、
    サーバーがそのデータを受信できることを確認する。
    """
    port = get_free_port()
    received_ticket = []

    # サーバー側
    server_reg = msquic.Registration("zero_rtt_server", msquic.ExecutionProfile.LOW_LATENCY)
    server_config = msquic.Configuration(
        server_reg,
        ["zero-rtt-test"],
        idle_timeout_ms=5000,
        peer_bidi_stream_count=10,
        server_resumption_level=msquic.ServerResumptionLevel.RESUME_AND_ZERORTT,
    )
    server_config.load_credential_file(
        certificates["cert_file"],
        certificates["key_file"],
    )

    listener = msquic.Listener(server_reg)
    server_connections = []
    server_connected_events = []
    server_shutdown_events = []
    server_received_data = []
    server_data_received_event = threading.Event()

    def on_new_connection(server_conn):
        server_connections.append(server_conn)
        connected_event = threading.Event()
        shutdown_event = threading.Event()
        server_connected_events.append(connected_event)
        server_shutdown_events.append(shutdown_event)

        def on_server_connected(_session_resumed):
            server_conn.send_resumption_ticket()
            connected_event.set()

        def on_server_shutdown_complete(_app_close_in_progress):
            shutdown_event.set()

        def on_peer_stream_started(stream):
            def on_receive(data, fin):
                server_received_data.append(bytes(data))
                if fin:
                    server_data_received_event.set()

            stream.set_on_receive(on_receive)

        server_conn.set_on_connected(on_server_connected)
        server_conn.set_on_shutdown_complete(on_server_shutdown_complete)
        server_conn.set_on_peer_stream_started(on_peer_stream_started)

    listener.set_on_new_connection(on_new_connection)
    listener.start(server_config, ["zero-rtt-test"], port)

    # クライアント側
    client_reg = msquic.Registration("zero_rtt_client", msquic.ExecutionProfile.LOW_LATENCY)
    client_config = msquic.Configuration(
        client_reg,
        ["zero-rtt-test"],
        idle_timeout_ms=5000,
    )
    client_config.load_credential_none(no_certificate_validation=True)

    # === 最初の接続 (Ticket を取得) ===
    conn1 = msquic.Connection(client_reg)
    client_connected_event1 = threading.Event()
    client_shutdown_event1 = threading.Event()
    ticket_received_event = threading.Event()

    def on_connected1(_session_resumed):
        client_connected_event1.set()

    def on_shutdown_complete1(_app_close_in_progress):
        client_shutdown_event1.set()

    def on_resumption_ticket_received(ticket):
        received_ticket.append(bytes(ticket))
        ticket_received_event.set()

    conn1.set_on_connected(on_connected1)
    conn1.set_on_shutdown_complete(on_shutdown_complete1)
    conn1.set_on_resumption_ticket_received(on_resumption_ticket_received)

    conn1.start(client_config, "127.0.0.1", port)

    assert client_connected_event1.wait(timeout=5.0), "First client connection timeout"
    assert server_connected_events[0].wait(timeout=5.0), "First server connection timeout"
    assert ticket_received_event.wait(timeout=5.0), "Ticket receive timeout"

    conn1.shutdown(msquic.ConnectionShutdownFlags.NONE, 0)
    assert client_shutdown_event1.wait(timeout=5.0), "First client shutdown timeout"
    assert server_shutdown_events[0].wait(timeout=5.0), "First server shutdown timeout"

    # サーバーの受信データをリセット
    server_received_data.clear()
    server_data_received_event.clear()

    # === 2回目の接続 (0-RTT 早期データ送信) ===
    conn2 = msquic.Connection(client_reg)
    client_connected_event2 = threading.Event()
    client_shutdown_event2 = threading.Event()
    client_session_resumed = [False]

    def on_connected2(session_resumed):
        client_session_resumed[0] = session_resumed
        client_connected_event2.set()

    def on_shutdown_complete2(_app_close_in_progress):
        client_shutdown_event2.set()

    conn2.set_on_connected(on_connected2)
    conn2.set_on_shutdown_complete(on_shutdown_complete2)

    # Resumption Ticket を設定
    conn2.set_resumption_ticket(received_ticket[0])

    # 接続開始
    conn2.start(client_config, "127.0.0.1", port)

    # 重要: CONNECTED を待たずに即座にストリームを開いてデータを送信
    # これが 0-RTT の本質
    stream = conn2.open_stream(msquic.StreamOpenFlags.NONE)
    stream.start(msquic.StreamStartFlags.IMMEDIATE)

    early_data = b"0-RTT Early Data!"
    stream.send(early_data, msquic.SendFlags.FIN)

    # 接続完了を待機
    assert client_connected_event2.wait(timeout=5.0), "Second client connection timeout"
    assert server_connected_events[1].wait(timeout=5.0), "Second server connection timeout"

    # サーバーがデータを受信したことを確認
    assert server_data_received_event.wait(timeout=5.0), "Server did not receive early data"
    assert b"".join(server_received_data) == early_data, "Early data mismatch"

    # セッションが再開されたことを確認
    assert client_session_resumed[0], "Session should be resumed for 0-RTT"

    # クリーンアップ
    conn2.shutdown(msquic.ConnectionShutdownFlags.NONE, 0)
    assert client_shutdown_event2.wait(timeout=5.0), "Second client shutdown timeout"
    assert server_shutdown_events[1].wait(timeout=5.0), "Second server shutdown timeout"

    listener.close()


def test_resumption_levels_enum():
    """ServerResumptionLevel の enum 値テスト"""
    # enum 値が定義されていることを確認
    assert hasattr(msquic, "ServerResumptionLevel")
    assert hasattr(msquic.ServerResumptionLevel, "NO_RESUME")
    assert hasattr(msquic.ServerResumptionLevel, "RESUME_ONLY")
    assert hasattr(msquic.ServerResumptionLevel, "RESUME_AND_ZERORTT")

    # 各レベルが異なる値であることを確認
    no_resume = msquic.ServerResumptionLevel.NO_RESUME
    resume_only = msquic.ServerResumptionLevel.RESUME_ONLY
    resume_and_zerortt = msquic.ServerResumptionLevel.RESUME_AND_ZERORTT

    assert no_resume != resume_only
    assert resume_only != resume_and_zerortt
    assert no_resume != resume_and_zerortt


def test_send_resumption_flags_enum():
    """SendResumptionFlags の enum 値テスト"""
    assert hasattr(msquic, "SendResumptionFlags")
    assert hasattr(msquic.SendResumptionFlags, "NONE")
    assert hasattr(msquic.SendResumptionFlags, "FINAL")
