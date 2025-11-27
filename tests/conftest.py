import asyncio
import ipaddress
import socket
import threading
import traceback
from datetime import datetime, timedelta, timezone

import pytest
from aioquic.asyncio import QuicConnectionProtocol, serve
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import HandshakeCompleted, StreamDataReceived
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


@pytest.fixture
def sample_alpn():
    return ["h3"]


def get_free_port():
    """空いているポートを取得"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def certificates(tmp_path_factory):
    """cryptography を使って動的に自己署名証明書を生成"""
    tmp_dir = tmp_path_factory.mktemp("certs")
    cert_file = tmp_dir / "cert.pem"
    key_file = tmp_dir / "key.pem"

    # 秘密鍵を生成 (ECDSA P-256)
    private_key = ec.generate_private_key(ec.SECP256R1())

    # 証明書を生成
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "JP"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Tokyo"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Chiyoda"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Test"),
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                x509.IPAddress(ipaddress.IPv6Address("::1")),
            ]),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )

    # ファイルに書き出し
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    return {"cert_file": str(cert_file), "key_file": str(key_file)}


class EchoQuicProtocol(QuicConnectionProtocol):
    """Echo サーバー用プロトコル"""

    def quic_event_received(self, event):
        if isinstance(event, HandshakeCompleted):
            pass
        elif isinstance(event, StreamDataReceived):
            # エコーバック
            self._quic.send_stream_data(
                event.stream_id,
                event.data,
                end_stream=event.end_stream,
            )


class QuicServer:
    """aioquic QUIC サーバーを別スレッドで実行"""

    def __init__(self, host: str, port: int, certificates: dict, alpn_protocols: list[str]):
        self.host = host
        self.port = port
        self.certificates = certificates
        self.alpn_protocols = alpn_protocols
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = None
        self._started = threading.Event()
        self._error: Exception | None = None

    def start(self):
        self._thread = threading.Thread(target=self._run_server, daemon=True)
        self._thread.start()
        # サーバーが起動するまで待機
        if not self._started.wait(timeout=5.0):
            if self._error is not None:
                raise RuntimeError(f"QUIC server failed to start: {self._error}")
            raise RuntimeError("QUIC server failed to start: timeout")

    def stop(self):
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._shutdown)
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def _shutdown(self):
        if self._server is not None:
            self._server.close()
        self._loop.stop()

    def _run_server(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._loop.run_until_complete(self._start_server())
            self._started.set()
            self._loop.run_forever()
        except Exception as e:
            self._error = e
            traceback.print_exc()
        finally:
            # 残っているタスクをクリーンアップ
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()

    async def _start_server(self):
        configuration = QuicConfiguration(
            is_client=False,
            alpn_protocols=self.alpn_protocols,
        )
        configuration.load_cert_chain(
            self.certificates["cert_file"],
            self.certificates["key_file"],
        )

        self._server = await serve(
            self.host,
            self.port,
            configuration=configuration,
            create_protocol=EchoQuicProtocol,
        )


@pytest.fixture
def quic_server(certificates):
    """aioquic Echo サーバーを起動するフィクスチャ"""
    port = get_free_port()
    server = QuicServer(
        host="127.0.0.1",
        port=port,
        certificates=certificates,
        alpn_protocols=["echo"],
    )
    server.start()
    yield {
        "host": "127.0.0.1",
        "port": port,
        "alpn": ["echo"],
        "certificates": certificates,
    }
    server.stop()
