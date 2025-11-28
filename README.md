# msquic-py

[![PyPI](https://img.shields.io/pypi/v/msquic-py)](https://pypi.org/project/msquic-py/)
[![image](https://img.shields.io/pypi/pyversions/msquic-py.svg)](https://pypi.python.org/pypi/msquic-py)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Actions status](https://github.com/shiguredo/msquic-py/workflows/wheel/badge.svg)](https://github.com/shiguredo/msquic-py/actions)

## msquic-py について

msquic-py は Microsoft の [MsQuic](https://github.com/microsoft/msquic) ライブラリの Python バインディングです。 QUIC プロトコルを Python から利用できるようにします。

また、[Media over QUIC Transport (MOQT)](https://datatracker.ietf.org/doc/draft-ietf-moq-transport/) プロトコルの実装も含まれています。

## 特徴

- MsQuic の Python バインディング
  - QUIC クライアント/サーバー
  - 双方向/単方向ストリーム
  - QUIC DATAGRAM (RFC 9221)
  - 0-RTT / Session Resumption
  - 証明書のカスタム検証
- MOQT (Media over QUIC Transport) 実装
  - draft-ietf-moq-transport-15 準拠
  - draft-ietf-moq-loc-01 (LOC Header Extensions) 対応
  - SUBSCRIBE/PUBLISH/FETCH
  - Data Stream (SUBGROUP/FETCH)
- クロスプラットフォーム対応
  - macOS
  - Ubuntu
  - Windows

## サンプルコード

### QUIC クライアント

```python
import msquic

# Registration 作成
registration = msquic.Registration("my_client", msquic.ExecutionProfile.LOW_LATENCY)

# Configuration 作成
configuration = msquic.Configuration(
    registration,
    ["h3"],
    idle_timeout_ms=30000,
    peer_bidi_stream_count=100,
)
configuration.load_credential_none(no_certificate_validation=True)

# Connection 作成
connection = msquic.Connection(registration)


def on_connected():
    print("接続完了")


def on_shutdown_complete(app_close_in_progress):
    print("接続終了")


connection.set_on_connected(on_connected)
connection.set_on_shutdown_complete(on_shutdown_complete)

# 接続開始
connection.start(configuration, "127.0.0.1", 4433)
```

### MOQT クライアント

```python
import msquic
from moqt import ClientSetup, StreamType, encode_varint

# Registration 作成
registration = msquic.Registration("moqt_client", msquic.ExecutionProfile.LOW_LATENCY)

# Configuration 作成
configuration = msquic.Configuration(
    registration,
    ["moqt-15"],
    idle_timeout_ms=30000,
    peer_bidi_stream_count=100,
)
configuration.load_credential_none(no_certificate_validation=True)

# Connection 作成
connection = msquic.Connection(registration)
connection.start(configuration, "127.0.0.1", 4433)

# 制御ストリームを開く
stream = connection.open_stream(msquic.StreamOpenFlags.NONE)
stream.start(msquic.StreamStartFlags.IMMEDIATE)

# Stream Type + CLIENT_SETUP を送信
stream_type = encode_varint(StreamType.CONTROL)
client_setup = ClientSetup()
client_setup.set_max_request_id(100)
client_setup.set_path("/moqt")
stream.send(stream_type + client_setup.encode(), msquic.SendFlags.NONE)
```

### MOQT Data Stream

```python
from moqt import (
    SubgroupHeader,
    SubgroupObject,
    ObjectExtensions,
    CaptureTimestamp,
    VideoFrameMarking,
    create_loc_extensions,
)

# SUBGROUP ヘッダーを作成
header = SubgroupHeader(
    track_alias=0,
    group_id=1,
    subgroup_id=0,
    publisher_priority=0,
)

# LOC Extensions を作成
extensions = create_loc_extensions(
    capture_timestamp=CaptureTimestamp(milliseconds=1234567890),
    video_frame_marking=VideoFrameMarking(
        is_keyframe=True,
        is_discardable=False,
        base_layer_sync=True,
    ),
)

# オブジェクトを作成
obj = SubgroupObject(
    object_id=0,
    extensions=ObjectExtensions(extensions),
    payload=b"video frame data",
)

# エンコード
header_bytes = header.encode()
object_bytes = obj.encode()
```

## インストール

`uv add msquic-py`

## MOQT 実装状況

### 対応メッセージ

- CLIENT_SETUP / SERVER_SETUP
- SUBSCRIBE / SUBSCRIBE_OK / SUBSCRIBE_UPDATE / UNSUBSCRIBE
- PUBLISH / PUBLISH_OK / PUBLISH_DONE
- FETCH / FETCH_OK / FETCH_CANCEL
- SUBSCRIBE_NAMESPACE / UNSUBSCRIBE_NAMESPACE
- PUBLISH_NAMESPACE / PUBLISH_NAMESPACE_DONE / PUBLISH_NAMESPACE_CANCEL
- REQUEST_OK / REQUEST_ERROR
- TRACK_STATUS
- GOAWAY
- MAX_REQUEST_ID / REQUESTS_BLOCKED

### 対応 Data Stream

- SUBGROUP Stream
- FETCH Stream
- Object Datagram

### 対応拡張

- LOC Header Extensions (draft-ietf-moq-loc-01)
  - Capture Timestamp
  - Video Config
  - Video Frame Marking
  - Audio Level

## Python

- 3.14

## プラットフォーム

- macOS 26 arm64
- macOS 15 arm64
- Ubuntu 24.04 LTS x86_64
- Ubuntu 24.04 LTS arm64
- Ubuntu 22.04 LTS x86_64
- Ubuntu 22.04 LTS arm64
- Windows 11 x86_64

## ビルド

```bash
make develop
```

## テスト

```bash
uv sync
make test
```

## サンプル

```bash
uv sync --group example
make develop

# MOQT クライアント
uv run python examples/moqt_client.py --host 127.0.0.1 --port 4433

# MOQT サーバー
uv run python examples/moqt_server.py --cert cert.pem --key key.pem

# ビデオ配信 (webcodecs-py + mp4-py)
uv run python examples/moqt_video_relay.py --cert cert.pem --key key.pem
uv run python examples/moqt_video_publisher.py --host 127.0.0.1 --port 4433
uv run python examples/moqt_video_subscriber.py --host 127.0.0.1 --port 4433 --output output.mp4
```

## 依存ライブラリ

- MsQuic
  - <https://github.com/microsoft/msquic>

## ライセンス

Apache License 2.0

```text
Copyright 2025-2025, @voluntas

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```
