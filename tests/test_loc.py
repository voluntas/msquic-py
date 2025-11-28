"""LOC (Low Overhead Container) のテスト"""

from moqt.loc import (
    LocExtensionType,
    CaptureTimestamp,
    VideoConfig,
    VideoFrameMarking,
    AudioLevel,
    create_loc_extensions,
    parse_loc_extensions,
)
from moqt.data_stream import ObjectExtensions


# CaptureTimestamp のテスト


def test_capture_timestamp_encode_decode_small():
    """小さい値の Capture Timestamp"""
    ts = CaptureTimestamp(microseconds=1000)
    encoded = ts.encode()
    decoded = CaptureTimestamp.decode(encoded)
    assert decoded.microseconds == 1000


def test_capture_timestamp_encode_decode_large():
    """大きい値の Capture Timestamp (Unix epoch からのマイクロ秒)"""
    ts = CaptureTimestamp(microseconds=1704067200000000)
    encoded = ts.encode()
    decoded = CaptureTimestamp.decode(encoded)
    assert decoded.microseconds == 1704067200000000


# VideoConfig のテスト


def test_video_config_encode_decode_avc():
    """AVC (H.264) の Video Config"""
    config = VideoConfig(codec_config=b"\x01\x42\xE0\x1E\xFF\xE1\x00\x10")
    encoded = config.encode()
    decoded = VideoConfig.decode(encoded)
    assert decoded.codec_config == b"\x01\x42\xE0\x1E\xFF\xE1\x00\x10"


def test_video_config_encode_decode_hevc():
    """HEVC (H.265) の Video Config"""
    config = VideoConfig(codec_config=b"\x01\x01\x60\x00\x00\x00\x00\x00")
    encoded = config.encode()
    decoded = VideoConfig.decode(encoded)
    assert decoded.codec_config == b"\x01\x01\x60\x00\x00\x00\x00\x00"


# VideoFrameMarking のテスト


def test_video_frame_marking_encode_decode_keyframe():
    """キーフレーム"""
    marking = VideoFrameMarking(
        independent=True,
        discardable=False,
        base_layer_sync=True,
        temporal_id=0,
        spatial_id=0,
    )
    encoded = marking.encode()
    decoded = VideoFrameMarking.decode(encoded)
    assert decoded.independent is True
    assert decoded.discardable is False
    assert decoded.base_layer_sync is True
    assert decoded.temporal_id == 0
    assert decoded.spatial_id == 0


def test_video_frame_marking_encode_decode_delta_frame():
    """デルタフレーム"""
    marking = VideoFrameMarking(
        independent=False,
        discardable=True,
        base_layer_sync=False,
        temporal_id=1,
        spatial_id=0,
    )
    encoded = marking.encode()
    decoded = VideoFrameMarking.decode(encoded)
    assert decoded.independent is False
    assert decoded.discardable is True
    assert decoded.temporal_id == 1


def test_video_frame_marking_encode_decode_with_layers():
    """時間・空間レイヤー付き"""
    marking = VideoFrameMarking(
        independent=True,
        discardable=False,
        base_layer_sync=True,
        temporal_id=2,
        spatial_id=3,
    )
    encoded = marking.encode()
    decoded = VideoFrameMarking.decode(encoded)
    assert decoded.temporal_id == 2
    assert decoded.spatial_id == 3


# AudioLevel のテスト


def test_audio_level_encode_decode_silent():
    """無音"""
    level = AudioLevel(voice_activity=False, level=127)
    encoded = level.encode()
    decoded = AudioLevel.decode(encoded)
    assert decoded.voice_activity is False
    assert decoded.level == 127


def test_audio_level_encode_decode_voice():
    """音声あり"""
    level = AudioLevel(voice_activity=True, level=50)
    encoded = level.encode()
    decoded = AudioLevel.decode(encoded)
    assert decoded.voice_activity is True
    assert decoded.level == 50


def test_audio_level_encode_decode_max_level():
    """最大レベル"""
    level = AudioLevel(voice_activity=True, level=0)
    encoded = level.encode()
    decoded = AudioLevel.decode(encoded)
    assert decoded.level == 0


# LOC Extensions 統合テスト


def test_loc_extensions_create_video():
    """ビデオ用の LOC Extensions を作成"""
    timestamp = CaptureTimestamp(microseconds=1000000)
    frame_marking = VideoFrameMarking(
        independent=True,
        discardable=False,
        base_layer_sync=True,
        temporal_id=0,
        spatial_id=0,
    )

    ext = create_loc_extensions(
        capture_timestamp=timestamp,
        video_frame_marking=frame_marking,
    )

    assert LocExtensionType.CAPTURE_TIMESTAMP in ext.headers
    assert LocExtensionType.VIDEO_FRAME_MARKING in ext.headers


def test_loc_extensions_create_video_with_config():
    """Video Config 付きの LOC Extensions"""
    timestamp = CaptureTimestamp(microseconds=2000000)
    config = VideoConfig(codec_config=b"\x01\x42\xE0\x1E")
    frame_marking = VideoFrameMarking(
        independent=True,
        discardable=False,
        base_layer_sync=True,
        temporal_id=0,
        spatial_id=0,
    )

    ext = create_loc_extensions(
        capture_timestamp=timestamp,
        video_config=config,
        video_frame_marking=frame_marking,
    )

    assert LocExtensionType.VIDEO_CONFIG in ext.headers


def test_loc_extensions_create_audio():
    """オーディオ用の LOC Extensions"""
    timestamp = CaptureTimestamp(microseconds=3000000)
    audio_level = AudioLevel(voice_activity=True, level=30)

    ext = create_loc_extensions(
        capture_timestamp=timestamp,
        audio_level=audio_level,
    )

    assert LocExtensionType.CAPTURE_TIMESTAMP in ext.headers
    assert LocExtensionType.AUDIO_LEVEL in ext.headers


def test_loc_extensions_parse_video():
    """ビデオ用の LOC Extensions をパース"""
    timestamp = CaptureTimestamp(microseconds=5000000)
    frame_marking = VideoFrameMarking(
        independent=True,
        discardable=False,
        base_layer_sync=True,
        temporal_id=1,
        spatial_id=2,
    )

    ext = create_loc_extensions(
        capture_timestamp=timestamp,
        video_frame_marking=frame_marking,
    )

    parsed = parse_loc_extensions(ext)

    assert parsed.capture_timestamp is not None
    assert parsed.capture_timestamp.microseconds == 5000000
    assert parsed.video_frame_marking is not None
    assert parsed.video_frame_marking.temporal_id == 1
    assert parsed.video_frame_marking.spatial_id == 2


def test_loc_extensions_parse_audio():
    """オーディオ用の LOC Extensions をパース"""
    timestamp = CaptureTimestamp(microseconds=6000000)
    audio_level = AudioLevel(voice_activity=False, level=100)

    ext = create_loc_extensions(
        capture_timestamp=timestamp,
        audio_level=audio_level,
    )

    parsed = parse_loc_extensions(ext)

    assert parsed.capture_timestamp is not None
    assert parsed.capture_timestamp.microseconds == 6000000
    assert parsed.audio_level is not None
    assert parsed.audio_level.voice_activity is False
    assert parsed.audio_level.level == 100


def test_loc_extensions_roundtrip_through_object_extensions():
    """ObjectExtensions を経由したラウンドトリップ"""
    timestamp = CaptureTimestamp(microseconds=7000000)
    frame_marking = VideoFrameMarking(
        independent=True,
        discardable=False,
        base_layer_sync=True,
        temporal_id=0,
        spatial_id=0,
    )

    # LOC Extensions を作成
    loc_ext = create_loc_extensions(
        capture_timestamp=timestamp,
        video_frame_marking=frame_marking,
    )

    # ObjectExtensions としてエンコード/デコード
    encoded = loc_ext.encode()
    decoded_ext, _ = ObjectExtensions.decode(encoded)

    # LOC Extensions としてパース
    parsed = parse_loc_extensions(decoded_ext)

    assert parsed.capture_timestamp is not None
    assert parsed.capture_timestamp.microseconds == 7000000
    assert parsed.video_frame_marking is not None
    assert parsed.video_frame_marking.independent is True
