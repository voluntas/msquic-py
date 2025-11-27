from collections.abc import Callable, Sequence
import enum


def open_api() -> None:
    """Open the MsQuic API"""

def close_api() -> None:
    """Close the MsQuic API"""

class ExecutionProfile(enum.Enum):
    LOW_LATENCY = 0

    MAX_THROUGHPUT = 1

    SCAVENGER = 2

    REAL_TIME = 3

class ConnectionShutdownFlags(enum.Enum):
    NONE = 0

    SILENT = 1

class StreamOpenFlags(enum.Enum):
    NONE = 0

    UNIDIRECTIONAL = 1

    ZERO_RTT = 2

class StreamStartFlags(enum.Enum):
    NONE = 0

    IMMEDIATE = 1

    FAIL_BLOCKED = 2

    SHUTDOWN_ON_FAIL = 4

class StreamShutdownFlags(enum.Enum):
    NONE = 0

    GRACEFUL = 1

    ABORT_SEND = 2

    ABORT_RECEIVE = 4

    ABORT = 6

    IMMEDIATE = 8

class SendFlags(enum.Enum):
    NONE = 0

    ALLOW_0_RTT = 1

    START = 2

    FIN = 4

    DGRAM_PRIORITY = 8

    DELAY_SEND = 16

class Registration:
    def __init__(self, app_name: str, profile: ExecutionProfile = ExecutionProfile.LOW_LATENCY) -> None: ...

    def shutdown(self, flags: ConnectionShutdownFlags = ConnectionShutdownFlags.NONE, error_code: int = 0) -> None: ...

    def close(self) -> None: ...

class Configuration:
    def __init__(self, registration: Registration, alpn_list: Sequence[str], idle_timeout_ms: int = 0, peer_bidi_stream_count: int = 0, peer_unidi_stream_count: int = 0) -> None: ...

    def load_credential_file(self, cert_file: str, key_file: str, is_client: bool = False) -> None: ...

    def load_credential_none(self, no_certificate_validation: bool = False) -> None: ...

    def close(self) -> None: ...

class Stream:
    def start(self, flags: StreamStartFlags = StreamStartFlags.NONE) -> None: ...

    def send(self, data: bytes, flags: SendFlags = SendFlags.NONE) -> None: ...

    def shutdown(self, flags: StreamShutdownFlags, error_code: int = 0) -> None: ...

    def set_on_receive(self, arg: Callable[[Sequence[int], bool], None], /) -> None: ...

    def set_on_send_complete(self, arg: Callable[[], None], /) -> None: ...

    def set_on_shutdown_complete(self, arg: Callable[[bool], None], /) -> None: ...

class Connection:
    def __init__(self, registration: Registration) -> None: ...

    def start(self, config: Configuration, server_name: str, port: int) -> None: ...

    def set_configuration(self, config: Configuration) -> None: ...

    def shutdown(self, flags: ConnectionShutdownFlags = ConnectionShutdownFlags.NONE, error_code: int = 0) -> None: ...

    def open_stream(self, flags: StreamOpenFlags = StreamOpenFlags.NONE) -> Stream: ...

    def set_on_connected(self, arg: Callable[[], None], /) -> None: ...

    def set_on_shutdown_complete(self, arg: Callable[[bool], None], /) -> None: ...

    def set_on_peer_stream_started(self, arg: Callable[[Stream], None], /) -> None: ...

class Listener:
    def __init__(self, registration: Registration) -> None: ...

    def start(self, config: Configuration, alpn_list: Sequence[str], port: int) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...

    def set_on_new_connection(self, arg: Callable[[Connection], None], /) -> None: ...
