"""LOC (Low Overhead Container) Header Extensions

draft-ietf-moq-loc-01 に基づく実装
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .data_stream import ObjectExtensions
from .varint import decode_varint, encode_varint


class LocExtensionType(IntEnum):
    """LOC Header Extension Type

    これらの ID は IANA MOQ Header Extensions Registry に登録される
    """

    # 偶数タイプ (varint 値)
    CAPTURE_TIMESTAMP = 2
    VIDEO_FRAME_MARKING = 4
    AUDIO_LEVEL = 6

    # 奇数タイプ (Length プレフィックス付き)
    VIDEO_CONFIG = 13


@dataclass
class CaptureTimestamp:
    """Capture Timestamp

    Unix epoch からのマイクロ秒単位の壁時計時間
    """

    microseconds: int

    def encode(self) -> bytes:
        """Capture Timestamp をエンコードする"""
        return encode_varint(self.microseconds)

    @classmethod
    def decode(cls, data: bytes, offset: int = 0) -> CaptureTimestamp:
        """Capture Timestamp をデコードする"""
        value, _ = decode_varint(data, offset)
        return cls(microseconds=value)


@dataclass
class VideoConfig:
    """Video Config

    コーデック固有の設定データ (extradata)
    - AVC: AVCDecoderConfigurationRecord
    - HEVC: HEVCDecoderConfigurationRecord
    """

    codec_config: bytes

    def encode(self) -> bytes:
        """Video Config をエンコードする"""
        return self.codec_config

    @classmethod
    def decode(cls, data: bytes, offset: int = 0) -> VideoConfig:
        """Video Config をデコードする"""
        return cls(codec_config=bytes(data[offset:]))


@dataclass
class VideoFrameMarking:
    """Video Frame Marking

    RFC 9626 に基づくビデオフレームのメタデータ

    ビットレイアウト (varint の下位ビット):
    - bit 0: Independent (I)
    - bit 1: Discardable (D)
    - bit 2: Base Layer Sync (B)
    - bits 3-5: Temporal ID (TID)
    - bits 6-7: Spatial ID (SID)
    """

    independent: bool
    discardable: bool
    base_layer_sync: bool
    temporal_id: int
    spatial_id: int

    def encode(self) -> bytes:
        """Video Frame Marking をエンコードする"""
        value = 0
        if self.independent:
            value |= 0x01
        if self.discardable:
            value |= 0x02
        if self.base_layer_sync:
            value |= 0x04
        value |= (self.temporal_id & 0x07) << 3
        value |= (self.spatial_id & 0x03) << 6
        return encode_varint(value)

    @classmethod
    def decode(cls, data: bytes, offset: int = 0) -> VideoFrameMarking:
        """Video Frame Marking をデコードする"""
        value, _ = decode_varint(data, offset)
        return cls(
            independent=bool(value & 0x01),
            discardable=bool(value & 0x02),
            base_layer_sync=bool(value & 0x04),
            temporal_id=(value >> 3) & 0x07,
            spatial_id=(value >> 6) & 0x03,
        )


@dataclass
class AudioLevel:
    """Audio Level

    RFC 6464 に基づくオーディオレベル

    ビットレイアウト (varint の下位 8 ビット):
    - bit 7: Voice Activity (V)
    - bits 0-6: Level (0-127, 0 が最大)
    """

    voice_activity: bool
    level: int

    def encode(self) -> bytes:
        """Audio Level をエンコードする"""
        value = self.level & 0x7F
        if self.voice_activity:
            value |= 0x80
        return encode_varint(value)

    @classmethod
    def decode(cls, data: bytes, offset: int = 0) -> AudioLevel:
        """Audio Level をデコードする"""
        value, _ = decode_varint(data, offset)
        return cls(
            voice_activity=bool(value & 0x80),
            level=value & 0x7F,
        )


@dataclass
class ParsedLocExtensions:
    """パースされた LOC Extensions"""

    capture_timestamp: CaptureTimestamp | None = None
    video_config: VideoConfig | None = None
    video_frame_marking: VideoFrameMarking | None = None
    audio_level: AudioLevel | None = None


def create_loc_extensions(
    capture_timestamp: CaptureTimestamp | None = None,
    video_config: VideoConfig | None = None,
    video_frame_marking: VideoFrameMarking | None = None,
    audio_level: AudioLevel | None = None,
) -> ObjectExtensions:
    """LOC Header Extensions を ObjectExtensions として作成する"""
    headers: dict[int, bytes] = {}

    if capture_timestamp is not None:
        headers[LocExtensionType.CAPTURE_TIMESTAMP] = capture_timestamp.encode()

    if video_config is not None:
        headers[LocExtensionType.VIDEO_CONFIG] = video_config.encode()

    if video_frame_marking is not None:
        headers[LocExtensionType.VIDEO_FRAME_MARKING] = video_frame_marking.encode()

    if audio_level is not None:
        headers[LocExtensionType.AUDIO_LEVEL] = audio_level.encode()

    return ObjectExtensions(headers=headers)


def parse_loc_extensions(extensions: ObjectExtensions) -> ParsedLocExtensions:
    """ObjectExtensions から LOC Header Extensions をパースする"""
    result = ParsedLocExtensions()

    if LocExtensionType.CAPTURE_TIMESTAMP in extensions.headers:
        result.capture_timestamp = CaptureTimestamp.decode(
            extensions.headers[LocExtensionType.CAPTURE_TIMESTAMP]
        )

    if LocExtensionType.VIDEO_CONFIG in extensions.headers:
        result.video_config = VideoConfig.decode(
            extensions.headers[LocExtensionType.VIDEO_CONFIG]
        )

    if LocExtensionType.VIDEO_FRAME_MARKING in extensions.headers:
        result.video_frame_marking = VideoFrameMarking.decode(
            extensions.headers[LocExtensionType.VIDEO_FRAME_MARKING]
        )

    if LocExtensionType.AUDIO_LEVEL in extensions.headers:
        result.audio_level = AudioLevel.decode(
            extensions.headers[LocExtensionType.AUDIO_LEVEL]
        )

    return result
