"""MOQT Control Messages

draft-ietf-moq-transport-15 Section 9 に基づく実装
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass, field
from enum import IntEnum
from typing import ClassVar

from .varint import decode_varint, encode_varint


class ErrorCode(IntEnum):
    """MOQT Error Codes (Section 8.2)"""

    NO_ERROR = 0x0
    INTERNAL_ERROR = 0x1
    UNAUTHORIZED = 0x2
    PROTOCOL_VIOLATION = 0x3
    DUPLICATE_TRACK_ALIAS = 0x4
    PARAMETER_LENGTH_MISMATCH = 0x5
    TOO_MANY_SUBSCRIBERS = 0x6
    GOAWAY_TIMEOUT = 0x10


class TrackStatusCode(IntEnum):
    """Track Status Codes (Section 9.7.1)"""

    IN_PROGRESS = 0x0
    TRACK_DOES_NOT_EXIST = 0x1
    NO_OBJECTS = 0x2
    GROUP_DOES_NOT_EXIST = 0x3


class StreamType(IntEnum):
    """MOQT Stream Types (Section 10.1)"""

    CONTROL = 0x00
    SUBGROUP = 0x04
    FETCH = 0x05


class MessageType(IntEnum):
    """MOQT メッセージタイプ"""

    # Setup
    CLIENT_SETUP = 0x20
    SERVER_SETUP = 0x21

    # Session
    GOAWAY = 0x10
    MAX_REQUEST_ID = 0x15
    REQUESTS_BLOCKED = 0x1A

    # Request/Response
    REQUEST_OK = 0x07
    REQUEST_ERROR = 0x05

    # Subscribe
    SUBSCRIBE = 0x03
    SUBSCRIBE_OK = 0x04
    SUBSCRIBE_UPDATE = 0x02
    UNSUBSCRIBE = 0x0A

    # Publish
    PUBLISH = 0x1D
    PUBLISH_OK = 0x1E
    PUBLISH_DONE = 0x0B

    # Fetch
    FETCH = 0x16
    FETCH_OK = 0x18
    FETCH_CANCEL = 0x17

    # Track Status
    TRACK_STATUS = 0x0D

    # Namespace
    PUBLISH_NAMESPACE = 0x06
    PUBLISH_NAMESPACE_DONE = 0x09
    PUBLISH_NAMESPACE_CANCEL = 0x0C
    SUBSCRIBE_NAMESPACE = 0x11
    UNSUBSCRIBE_NAMESPACE = 0x14


class ParameterType(IntEnum):
    """MOQT パラメータタイプ"""

    # Setup Parameters
    PATH = 0x01
    MAX_REQUEST_ID = 0x02
    MAX_AUTH_TOKEN_CACHE_SIZE = 0x04
    AUTHORITY = 0x05
    MOQT_IMPLEMENTATION = 0x07

    # Version Specific Parameters
    AUTHORIZATION_TOKEN = 0x00
    DELIVERY_TIMEOUT = 0x02
    MAX_CACHE_DURATION = 0x04
    EXPIRES = 0x08
    LARGEST_OBJECT = 0x09
    PUBLISHER_PRIORITY = 0x0E
    FORWARD = 0x10
    SUBSCRIBER_PRIORITY = 0x20
    SUBSCRIPTION_FILTER = 0x21
    GROUP_ORDER = 0x22
    DYNAMIC_GROUPS = 0x30


class GroupOrder(IntEnum):
    """グループの順序"""

    ASCENDING = 0x01
    DESCENDING = 0x02


class FilterType(IntEnum):
    """サブスクリプションフィルタータイプ"""

    LATEST_GROUP = 0x01
    LATEST_OBJECT = 0x02
    ABSOLUTE_START = 0x03
    ABSOLUTE_RANGE = 0x04


@dataclass
class Location:
    """オブジェクトの位置"""

    group: int
    object: int

    def encode(self) -> bytes:
        """Location をエンコードする"""
        return encode_varint(self.group) + encode_varint(self.object)

    @classmethod
    def decode(cls, data: bytes, offset: int = 0) -> tuple[Location, int]:
        """Location をデコードする"""
        group, consumed1 = decode_varint(data, offset)
        obj, consumed2 = decode_varint(data, offset + consumed1)
        return cls(group=group, object=obj), consumed1 + consumed2


@dataclass
class Parameter:
    """MOQT パラメータ"""

    type: int
    value: bytes

    def encode(self) -> bytes:
        """パラメータをエンコードする"""
        result = encode_varint(self.type)
        # Type が奇数の場合は Length が必要
        if self.type % 2 == 1:
            result += encode_varint(len(self.value))
        result += self.value
        return result

    @classmethod
    def decode(cls, data: bytes, offset: int = 0) -> tuple[Parameter, int]:
        """パラメータをデコードする"""
        param_type, consumed = decode_varint(data, offset)
        total_consumed = consumed

        if param_type % 2 == 1:
            # 奇数型は Length プレフィックス付き
            length, consumed = decode_varint(data, offset + total_consumed)
            total_consumed += consumed
            value = bytes(data[offset + total_consumed : offset + total_consumed + length])
            total_consumed += length
        else:
            # 偶数型は varint 値
            val, consumed = decode_varint(data, offset + total_consumed)
            value = encode_varint(val)
            total_consumed += consumed

        return cls(type=param_type, value=value), total_consumed


@dataclass
class ControlMessage:
    """MOQT Control Message の基底クラス"""

    MESSAGE_TYPE: ClassVar[MessageType]

    def encode_payload(self) -> bytes:
        """メッセージのペイロードをエンコードする"""
        raise NotImplementedError

    def encode(self) -> bytes:
        """メッセージ全体をエンコードする"""
        payload = self.encode_payload()
        # Type (varint) + Length (16-bit) + Payload
        result = encode_varint(self.MESSAGE_TYPE)
        result += len(payload).to_bytes(2, "big")
        result += payload
        return result

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> ControlMessage:
        """ペイロードからメッセージをデコードする"""
        raise NotImplementedError


@dataclass
class ClientSetup(ControlMessage):
    """CLIENT_SETUP メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.CLIENT_SETUP

    parameters: list[Parameter] = field(default_factory=list)

    def encode_payload(self) -> bytes:
        result = encode_varint(len(self.parameters))
        for param in self.parameters:
            result += param.encode()
        return result

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> ClientSetup:
        num_params, consumed = decode_varint(data, offset)
        total_consumed = consumed
        parameters = []
        for _ in range(num_params):
            param, consumed = Parameter.decode(data, offset + total_consumed)
            parameters.append(param)
            total_consumed += consumed
        return cls(parameters=parameters)

    def get_parameter(self, param_type: int) -> Parameter | None:
        """指定したタイプのパラメータを取得する"""
        for param in self.parameters:
            if param.type == param_type:
                return param
        return None

    def set_parameter(self, param_type: int, value: bytes) -> None:
        """パラメータを設定する"""
        # 既存のパラメータを削除
        self.parameters = [p for p in self.parameters if p.type != param_type]
        self.parameters.append(Parameter(type=param_type, value=value))

    def set_path(self, path: str) -> None:
        """PATH パラメータを設定する"""
        self.set_parameter(ParameterType.PATH, path.encode("utf-8"))

    def set_authority(self, authority: str) -> None:
        """AUTHORITY パラメータを設定する"""
        self.set_parameter(ParameterType.AUTHORITY, authority.encode("utf-8"))

    def set_max_request_id(self, max_id: int) -> None:
        """MAX_REQUEST_ID パラメータを設定する"""
        self.set_parameter(ParameterType.MAX_REQUEST_ID, encode_varint(max_id))


@dataclass
class ServerSetup(ControlMessage):
    """SERVER_SETUP メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.SERVER_SETUP

    parameters: list[Parameter] = field(default_factory=list)

    def encode_payload(self) -> bytes:
        result = encode_varint(len(self.parameters))
        for param in self.parameters:
            result += param.encode()
        return result

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> ServerSetup:
        num_params, consumed = decode_varint(data, offset)
        total_consumed = consumed
        parameters = []
        for _ in range(num_params):
            param, consumed = Parameter.decode(data, offset + total_consumed)
            parameters.append(param)
            total_consumed += consumed
        return cls(parameters=parameters)

    def get_parameter(self, param_type: int) -> Parameter | None:
        """指定したタイプのパラメータを取得する"""
        for param in self.parameters:
            if param.type == param_type:
                return param
        return None

    def set_parameter(self, param_type: int, value: bytes) -> None:
        """パラメータを設定する"""
        self.parameters = [p for p in self.parameters if p.type != param_type]
        self.parameters.append(Parameter(type=param_type, value=value))

    def set_max_request_id(self, max_id: int) -> None:
        """MAX_REQUEST_ID パラメータを設定する"""
        self.set_parameter(ParameterType.MAX_REQUEST_ID, encode_varint(max_id))


@dataclass
class Goaway(ControlMessage):
    """GOAWAY メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.GOAWAY

    new_session_uri: str = ""

    def encode_payload(self) -> bytes:
        uri_bytes = self.new_session_uri.encode("utf-8")
        return encode_varint(len(uri_bytes)) + uri_bytes

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> Goaway:
        uri_len, consumed = decode_varint(data, offset)
        uri = data[offset + consumed : offset + consumed + uri_len].decode("utf-8")
        return cls(new_session_uri=uri)


@dataclass
class MaxRequestId(ControlMessage):
    """MAX_REQUEST_ID メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.MAX_REQUEST_ID

    request_id: int = 0

    def encode_payload(self) -> bytes:
        return encode_varint(self.request_id)

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> MaxRequestId:
        request_id, _ = decode_varint(data, offset)
        return cls(request_id=request_id)


@dataclass
class RequestsBlocked(ControlMessage):
    """REQUESTS_BLOCKED メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.REQUESTS_BLOCKED

    maximum_request_id: int = 0

    def encode_payload(self) -> bytes:
        return encode_varint(self.maximum_request_id)

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> RequestsBlocked:
        max_id, _ = decode_varint(data, offset)
        return cls(maximum_request_id=max_id)


@dataclass
class RequestOk(ControlMessage):
    """REQUEST_OK メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.REQUEST_OK

    request_id: int = 0
    parameters: list[Parameter] = field(default_factory=list)

    def encode_payload(self) -> bytes:
        result = encode_varint(self.request_id)
        result += encode_varint(len(self.parameters))
        for param in self.parameters:
            result += param.encode()
        return result

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> RequestOk:
        request_id, consumed = decode_varint(data, offset)
        total_consumed = consumed
        num_params, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        parameters = []
        for _ in range(num_params):
            param, consumed = Parameter.decode(data, offset + total_consumed)
            parameters.append(param)
            total_consumed += consumed
        return cls(request_id=request_id, parameters=parameters)


@dataclass
class RequestError(ControlMessage):
    """REQUEST_ERROR メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.REQUEST_ERROR

    request_id: int = 0
    error_code: int = 0
    reason_phrase: str = ""

    def encode_payload(self) -> bytes:
        result = encode_varint(self.request_id)
        result += encode_varint(self.error_code)
        reason_bytes = self.reason_phrase.encode("utf-8")
        result += encode_varint(len(reason_bytes))
        result += reason_bytes
        return result

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> RequestError:
        request_id, consumed = decode_varint(data, offset)
        total_consumed = consumed
        error_code, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        reason_len, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        reason = data[offset + total_consumed : offset + total_consumed + reason_len].decode(
            "utf-8"
        )
        return cls(request_id=request_id, error_code=error_code, reason_phrase=reason)


@dataclass
class TrackNamespace:
    """Track Namespace"""

    tuple: list[bytes] = field(default_factory=list)

    def encode(self) -> bytes:
        result = encode_varint(len(self.tuple))
        for element in self.tuple:
            result += encode_varint(len(element))
            result += element
        return result

    @classmethod
    def decode(cls, data: bytes, offset: int = 0) -> builtins.tuple[TrackNamespace, int]:
        num_elements, consumed = decode_varint(data, offset)
        total_consumed = consumed
        elements = []
        for _ in range(num_elements):
            elem_len, consumed = decode_varint(data, offset + total_consumed)
            total_consumed += consumed
            elements.append(bytes(data[offset + total_consumed : offset + total_consumed + elem_len]))
            total_consumed += elem_len
        return cls(tuple=elements), total_consumed


@dataclass
class Subscribe(ControlMessage):
    """SUBSCRIBE メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.SUBSCRIBE

    request_id: int = 0
    track_alias: int = 0
    track_namespace: TrackNamespace = field(default_factory=TrackNamespace)
    track_name: bytes = b""
    parameters: list[Parameter] = field(default_factory=list)

    def encode_payload(self) -> bytes:
        result = encode_varint(self.request_id)
        result += encode_varint(self.track_alias)
        result += self.track_namespace.encode()
        result += encode_varint(len(self.track_name))
        result += self.track_name
        result += encode_varint(len(self.parameters))
        for param in self.parameters:
            result += param.encode()
        return result

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> Subscribe:
        request_id, consumed = decode_varint(data, offset)
        total_consumed = consumed
        track_alias, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        track_namespace, consumed = TrackNamespace.decode(data, offset + total_consumed)
        total_consumed += consumed
        track_name_len, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        track_name = bytes(data[offset + total_consumed : offset + total_consumed + track_name_len])
        total_consumed += track_name_len
        num_params, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        parameters = []
        for _ in range(num_params):
            param, consumed = Parameter.decode(data, offset + total_consumed)
            parameters.append(param)
            total_consumed += consumed
        return cls(
            request_id=request_id,
            track_alias=track_alias,
            track_namespace=track_namespace,
            track_name=track_name,
            parameters=parameters,
        )


@dataclass
class SubscribeOk(ControlMessage):
    """SUBSCRIBE_OK メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.SUBSCRIBE_OK

    request_id: int = 0
    parameters: list[Parameter] = field(default_factory=list)

    def encode_payload(self) -> bytes:
        result = encode_varint(self.request_id)
        result += encode_varint(len(self.parameters))
        for param in self.parameters:
            result += param.encode()
        return result

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> SubscribeOk:
        request_id, consumed = decode_varint(data, offset)
        total_consumed = consumed
        num_params, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        parameters = []
        for _ in range(num_params):
            param, consumed = Parameter.decode(data, offset + total_consumed)
            parameters.append(param)
            total_consumed += consumed
        return cls(request_id=request_id, parameters=parameters)


@dataclass
class SubscribeUpdate(ControlMessage):
    """SUBSCRIBE_UPDATE メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.SUBSCRIBE_UPDATE

    request_id: int = 0
    parameters: list[Parameter] = field(default_factory=list)

    def encode_payload(self) -> bytes:
        result = encode_varint(self.request_id)
        result += encode_varint(len(self.parameters))
        for param in self.parameters:
            result += param.encode()
        return result

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> SubscribeUpdate:
        request_id, consumed = decode_varint(data, offset)
        total_consumed = consumed
        num_params, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        parameters = []
        for _ in range(num_params):
            param, consumed = Parameter.decode(data, offset + total_consumed)
            parameters.append(param)
            total_consumed += consumed
        return cls(request_id=request_id, parameters=parameters)


@dataclass
class Unsubscribe(ControlMessage):
    """UNSUBSCRIBE メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.UNSUBSCRIBE

    request_id: int = 0

    def encode_payload(self) -> bytes:
        return encode_varint(self.request_id)

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> Unsubscribe:
        request_id, _ = decode_varint(data, offset)
        return cls(request_id=request_id)


@dataclass
class Publish(ControlMessage):
    """PUBLISH メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.PUBLISH

    request_id: int = 0
    track_alias: int = 0
    track_namespace: TrackNamespace = field(default_factory=TrackNamespace)
    track_name: bytes = b""
    parameters: list[Parameter] = field(default_factory=list)

    def encode_payload(self) -> bytes:
        result = encode_varint(self.request_id)
        result += encode_varint(self.track_alias)
        result += self.track_namespace.encode()
        result += encode_varint(len(self.track_name))
        result += self.track_name
        result += encode_varint(len(self.parameters))
        for param in self.parameters:
            result += param.encode()
        return result

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> Publish:
        request_id, consumed = decode_varint(data, offset)
        total_consumed = consumed
        track_alias, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        track_namespace, consumed = TrackNamespace.decode(data, offset + total_consumed)
        total_consumed += consumed
        track_name_len, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        track_name = bytes(data[offset + total_consumed : offset + total_consumed + track_name_len])
        total_consumed += track_name_len
        num_params, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        parameters = []
        for _ in range(num_params):
            param, consumed = Parameter.decode(data, offset + total_consumed)
            parameters.append(param)
            total_consumed += consumed
        return cls(
            request_id=request_id,
            track_alias=track_alias,
            track_namespace=track_namespace,
            track_name=track_name,
            parameters=parameters,
        )


@dataclass
class PublishOk(ControlMessage):
    """PUBLISH_OK メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.PUBLISH_OK

    request_id: int = 0
    parameters: list[Parameter] = field(default_factory=list)

    def encode_payload(self) -> bytes:
        result = encode_varint(self.request_id)
        result += encode_varint(len(self.parameters))
        for param in self.parameters:
            result += param.encode()
        return result

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> PublishOk:
        request_id, consumed = decode_varint(data, offset)
        total_consumed = consumed
        num_params, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        parameters = []
        for _ in range(num_params):
            param, consumed = Parameter.decode(data, offset + total_consumed)
            parameters.append(param)
            total_consumed += consumed
        return cls(request_id=request_id, parameters=parameters)


@dataclass
class PublishDone(ControlMessage):
    """PUBLISH_DONE メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.PUBLISH_DONE

    request_id: int = 0
    status_code: int = 0
    reason_phrase: str = ""

    def encode_payload(self) -> bytes:
        result = encode_varint(self.request_id)
        result += encode_varint(self.status_code)
        reason_bytes = self.reason_phrase.encode("utf-8")
        result += encode_varint(len(reason_bytes))
        result += reason_bytes
        return result

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> PublishDone:
        request_id, consumed = decode_varint(data, offset)
        total_consumed = consumed
        status_code, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        reason_len, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        reason = data[offset + total_consumed : offset + total_consumed + reason_len].decode(
            "utf-8"
        )
        return cls(request_id=request_id, status_code=status_code, reason_phrase=reason)


@dataclass
class Fetch(ControlMessage):
    """FETCH メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.FETCH

    request_id: int = 0
    track_namespace: TrackNamespace = field(default_factory=TrackNamespace)
    track_name: bytes = b""
    start: Location = field(default_factory=lambda: Location(0, 0))
    end: Location = field(default_factory=lambda: Location(0, 0))
    parameters: list[Parameter] = field(default_factory=list)

    def encode_payload(self) -> bytes:
        result = encode_varint(self.request_id)
        result += self.track_namespace.encode()
        result += encode_varint(len(self.track_name))
        result += self.track_name
        result += self.start.encode()
        result += self.end.encode()
        result += encode_varint(len(self.parameters))
        for param in self.parameters:
            result += param.encode()
        return result

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> Fetch:
        request_id, consumed = decode_varint(data, offset)
        total_consumed = consumed
        track_namespace, consumed = TrackNamespace.decode(data, offset + total_consumed)
        total_consumed += consumed
        track_name_len, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        track_name = bytes(data[offset + total_consumed : offset + total_consumed + track_name_len])
        total_consumed += track_name_len
        start, consumed = Location.decode(data, offset + total_consumed)
        total_consumed += consumed
        end, consumed = Location.decode(data, offset + total_consumed)
        total_consumed += consumed
        num_params, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        parameters = []
        for _ in range(num_params):
            param, consumed = Parameter.decode(data, offset + total_consumed)
            parameters.append(param)
            total_consumed += consumed
        return cls(
            request_id=request_id,
            track_namespace=track_namespace,
            track_name=track_name,
            start=start,
            end=end,
            parameters=parameters,
        )


@dataclass
class FetchOk(ControlMessage):
    """FETCH_OK メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.FETCH_OK

    request_id: int = 0
    parameters: list[Parameter] = field(default_factory=list)

    def encode_payload(self) -> bytes:
        result = encode_varint(self.request_id)
        result += encode_varint(len(self.parameters))
        for param in self.parameters:
            result += param.encode()
        return result

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> FetchOk:
        request_id, consumed = decode_varint(data, offset)
        total_consumed = consumed
        num_params, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        parameters = []
        for _ in range(num_params):
            param, consumed = Parameter.decode(data, offset + total_consumed)
            parameters.append(param)
            total_consumed += consumed
        return cls(request_id=request_id, parameters=parameters)


@dataclass
class FetchCancel(ControlMessage):
    """FETCH_CANCEL メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.FETCH_CANCEL

    request_id: int = 0

    def encode_payload(self) -> bytes:
        return encode_varint(self.request_id)

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> FetchCancel:
        request_id, _ = decode_varint(data, offset)
        return cls(request_id=request_id)


@dataclass
class TrackStatus(ControlMessage):
    """TRACK_STATUS メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.TRACK_STATUS

    request_id: int = 0
    track_namespace: TrackNamespace = field(default_factory=TrackNamespace)
    track_name: bytes = b""
    parameters: list[Parameter] = field(default_factory=list)

    def encode_payload(self) -> bytes:
        result = encode_varint(self.request_id)
        result += self.track_namespace.encode()
        result += encode_varint(len(self.track_name))
        result += self.track_name
        result += encode_varint(len(self.parameters))
        for param in self.parameters:
            result += param.encode()
        return result

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> TrackStatus:
        request_id, consumed = decode_varint(data, offset)
        total_consumed = consumed
        track_namespace, consumed = TrackNamespace.decode(data, offset + total_consumed)
        total_consumed += consumed
        track_name_len, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        track_name = bytes(data[offset + total_consumed : offset + total_consumed + track_name_len])
        total_consumed += track_name_len
        num_params, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        parameters = []
        for _ in range(num_params):
            param, consumed = Parameter.decode(data, offset + total_consumed)
            parameters.append(param)
            total_consumed += consumed
        return cls(
            request_id=request_id,
            track_namespace=track_namespace,
            track_name=track_name,
            parameters=parameters,
        )


@dataclass
class PublishNamespace(ControlMessage):
    """PUBLISH_NAMESPACE メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.PUBLISH_NAMESPACE

    request_id: int = 0
    track_namespace: TrackNamespace = field(default_factory=TrackNamespace)
    parameters: list[Parameter] = field(default_factory=list)

    def encode_payload(self) -> bytes:
        result = encode_varint(self.request_id)
        result += self.track_namespace.encode()
        result += encode_varint(len(self.parameters))
        for param in self.parameters:
            result += param.encode()
        return result

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> PublishNamespace:
        request_id, consumed = decode_varint(data, offset)
        total_consumed = consumed
        track_namespace, consumed = TrackNamespace.decode(data, offset + total_consumed)
        total_consumed += consumed
        num_params, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        parameters = []
        for _ in range(num_params):
            param, consumed = Parameter.decode(data, offset + total_consumed)
            parameters.append(param)
            total_consumed += consumed
        return cls(
            request_id=request_id,
            track_namespace=track_namespace,
            parameters=parameters,
        )


@dataclass
class PublishNamespaceDone(ControlMessage):
    """PUBLISH_NAMESPACE_DONE メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.PUBLISH_NAMESPACE_DONE

    request_id: int = 0
    status_code: int = 0
    reason_phrase: str = ""

    def encode_payload(self) -> bytes:
        result = encode_varint(self.request_id)
        result += encode_varint(self.status_code)
        reason_bytes = self.reason_phrase.encode("utf-8")
        result += encode_varint(len(reason_bytes))
        result += reason_bytes
        return result

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> PublishNamespaceDone:
        request_id, consumed = decode_varint(data, offset)
        total_consumed = consumed
        status_code, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        reason_len, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        reason = data[offset + total_consumed : offset + total_consumed + reason_len].decode(
            "utf-8"
        )
        return cls(request_id=request_id, status_code=status_code, reason_phrase=reason)


@dataclass
class PublishNamespaceCancel(ControlMessage):
    """PUBLISH_NAMESPACE_CANCEL メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.PUBLISH_NAMESPACE_CANCEL

    request_id: int = 0

    def encode_payload(self) -> bytes:
        return encode_varint(self.request_id)

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> PublishNamespaceCancel:
        request_id, _ = decode_varint(data, offset)
        return cls(request_id=request_id)


@dataclass
class SubscribeNamespace(ControlMessage):
    """SUBSCRIBE_NAMESPACE メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.SUBSCRIBE_NAMESPACE

    request_id: int = 0
    track_namespace_prefix: TrackNamespace = field(default_factory=TrackNamespace)
    parameters: list[Parameter] = field(default_factory=list)

    def encode_payload(self) -> bytes:
        result = encode_varint(self.request_id)
        result += self.track_namespace_prefix.encode()
        result += encode_varint(len(self.parameters))
        for param in self.parameters:
            result += param.encode()
        return result

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> SubscribeNamespace:
        request_id, consumed = decode_varint(data, offset)
        total_consumed = consumed
        track_namespace_prefix, consumed = TrackNamespace.decode(data, offset + total_consumed)
        total_consumed += consumed
        num_params, consumed = decode_varint(data, offset + total_consumed)
        total_consumed += consumed
        parameters = []
        for _ in range(num_params):
            param, consumed = Parameter.decode(data, offset + total_consumed)
            parameters.append(param)
            total_consumed += consumed
        return cls(
            request_id=request_id,
            track_namespace_prefix=track_namespace_prefix,
            parameters=parameters,
        )


@dataclass
class UnsubscribeNamespace(ControlMessage):
    """UNSUBSCRIBE_NAMESPACE メッセージ"""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.UNSUBSCRIBE_NAMESPACE

    request_id: int = 0

    def encode_payload(self) -> bytes:
        return encode_varint(self.request_id)

    @classmethod
    def decode_payload(cls, data: bytes, offset: int = 0) -> UnsubscribeNamespace:
        request_id, _ = decode_varint(data, offset)
        return cls(request_id=request_id)


# メッセージタイプからクラスへのマッピング
MESSAGE_CLASSES: dict[MessageType, type[ControlMessage]] = {
    MessageType.CLIENT_SETUP: ClientSetup,
    MessageType.SERVER_SETUP: ServerSetup,
    MessageType.GOAWAY: Goaway,
    MessageType.MAX_REQUEST_ID: MaxRequestId,
    MessageType.REQUESTS_BLOCKED: RequestsBlocked,
    MessageType.REQUEST_OK: RequestOk,
    MessageType.REQUEST_ERROR: RequestError,
    MessageType.SUBSCRIBE: Subscribe,
    MessageType.SUBSCRIBE_OK: SubscribeOk,
    MessageType.SUBSCRIBE_UPDATE: SubscribeUpdate,
    MessageType.UNSUBSCRIBE: Unsubscribe,
    MessageType.PUBLISH: Publish,
    MessageType.PUBLISH_OK: PublishOk,
    MessageType.PUBLISH_DONE: PublishDone,
    MessageType.FETCH: Fetch,
    MessageType.FETCH_OK: FetchOk,
    MessageType.FETCH_CANCEL: FetchCancel,
    MessageType.TRACK_STATUS: TrackStatus,
    MessageType.PUBLISH_NAMESPACE: PublishNamespace,
    MessageType.PUBLISH_NAMESPACE_DONE: PublishNamespaceDone,
    MessageType.PUBLISH_NAMESPACE_CANCEL: PublishNamespaceCancel,
    MessageType.SUBSCRIBE_NAMESPACE: SubscribeNamespace,
    MessageType.UNSUBSCRIBE_NAMESPACE: UnsubscribeNamespace,
}


def decode_control_message(data: bytes, offset: int = 0) -> tuple[ControlMessage, int]:
    """Control Message をデコードする

    Args:
        data: デコードするバイト列
        offset: 開始オフセット

    Returns:
        (デコードされたメッセージ, 消費したバイト数) のタプル
    """
    # Message Type
    msg_type_value, consumed = decode_varint(data, offset)
    total_consumed = consumed

    # Message Length (16-bit)
    if offset + total_consumed + 2 > len(data):
        raise ValueError("メッセージ長を読み取るためのデータが不足しています")
    msg_length = int.from_bytes(data[offset + total_consumed : offset + total_consumed + 2], "big")
    total_consumed += 2

    # Payload
    if offset + total_consumed + msg_length > len(data):
        raise ValueError("メッセージペイロードを読み取るためのデータが不足しています")

    try:
        msg_type = MessageType(msg_type_value)
    except ValueError:
        raise ValueError(f"不明なメッセージタイプ: {msg_type_value}")

    msg_class = MESSAGE_CLASSES.get(msg_type)
    if msg_class is None:
        raise ValueError(f"未実装のメッセージタイプ: {msg_type}")

    message = msg_class.decode_payload(data, offset + total_consumed)
    total_consumed += msg_length

    return message, total_consumed
