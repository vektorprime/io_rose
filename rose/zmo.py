"""
ZMO (ROSE Online Motion) file format parser.

Based on the Rust reference implementation from rose-file-readers/src/zmo.rs

ZMO files contain animation data for skeletal animations and morph target animations.
They support multiple channel types including position, rotation, scale, normals, 
UV coordinates, alpha, and texture animations.
"""

import struct
from enum import IntFlag
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, BinaryIO, Union

from .utils import (
    read_str, read_u32, read_u16, read_f32,
    read_vector2_f32, read_vector3_f32, read_quat_wxyz,
    Vector2, Vector3, Quat
)


class ZmoChannelType(IntFlag):
    """Channel type flags for ZMO animation channels."""
    EMPTY = 1
    POSITION = 2
    ROTATION = 4
    NORMAL = 8
    ALPHA = 16
    UV1 = 32
    UV2 = 64
    UV3 = 128
    UV4 = 256
    TEXTURE = 512
    SCALE = 1024


@dataclass
class ZmoChannel:
    """Base class for ZMO animation channels."""
    channel_type: ZmoChannelType
    bone_index: int


@dataclass
class ZmoPositionChannel(ZmoChannel):
    """Position animation channel (Vec3 per frame)."""
    values: List[Vector3] = field(default_factory=list)


@dataclass
class ZmoRotationChannel(ZmoChannel):
    """Rotation animation channel (Quaternion per frame)."""
    values: List[Quat] = field(default_factory=list)


@dataclass
class ZmoNormalChannel(ZmoChannel):
    """Normal animation channel (Vec3 per frame)."""
    values: List[Vector3] = field(default_factory=list)


@dataclass
class ZmoAlphaChannel(ZmoChannel):
    """Alpha animation channel (float per frame)."""
    values: List[float] = field(default_factory=list)


@dataclass
class ZmoUVChannel(ZmoChannel):
    """UV animation channel (Vec2 per frame)."""
    values: List[Vector2] = field(default_factory=list)


@dataclass
class ZmoTextureChannel(ZmoChannel):
    """Texture animation channel (float per frame, typically texture index)."""
    values: List[float] = field(default_factory=list)


@dataclass
class ZmoScaleChannel(ZmoChannel):
    """Scale animation channel (float per frame, uniform scale)."""
    values: List[float] = field(default_factory=list)


@dataclass
class ZmoFile:
    """
    ZMO animation file structure.
    
    ZMO files contain animation data that can be applied to skeletal meshes (ZMD/ZMS)
    or used for morph target animations on meshes.
    
    File format:
    - Magic header: "ZMO0002" (null-terminated)
    - FPS: uint32
    - Num frames: uint32
    - Channel count: uint32
    - Channels: array of (channel_type: uint32, bone_index: uint32)
    - Frame data: for each frame, for each channel, read appropriate data type
    - Optional extended data (EZMO or 3ZMO):
      - Frame events: uint16 array
      - Interpolation interval: uint32 (3ZMO only)
    """
    fps: int = 30
    num_frames: int = 0
    channels: List[Tuple[int, ZmoChannel]] = field(default_factory=list)
    frame_events: List[int] = field(default_factory=list)
    total_attack_frames: int = 0
    interpolation_interval_ms: Optional[int] = None
    
    def get_duration_seconds(self) -> float:
        """Get animation duration in seconds."""
        if self.fps == 0:
            return 0.0
        return self.num_frames / self.fps
    
    def get_bone_channels(self) -> dict:
        """
        Get a dictionary mapping bone indices to their channels.
        
        Returns:
            Dict where keys are bone indices and values are dicts of channel_type -> channel
        """
        bone_channels = {}
        for bone_index, channel in self.channels:
            if bone_index not in bone_channels:
                bone_channels[bone_index] = {}
            bone_channels[bone_index][channel.channel_type] = channel
        return bone_channels


class ZMO:
    """
    ZMO file reader class.
    
    Usage:
        zmo = ZMO("path/to/animation.zmo")
        print(f"FPS: {zmo.fps}, Frames: {zmo.num_frames}")
        for bone_index, channel in zmo.channels:
            print(f"Bone {bone_index}: {channel.channel_type.name}")
    """
    
    MAGIC = "ZMO0002"
    EXTENDED_MAGIC_EZMO = "EZMO"
    EXTENDED_MAGIC_3ZMO = "3ZMO"
    
    def __init__(self, filepath: str = None, buffer: bytes = None, 
                 skip_animation: bool = False, report_func=None):
        """
        Initialize ZMO reader.
        
        Args:
            filepath: Path to ZMO file
            buffer: Optional byte buffer to read from instead of file
            skip_animation: If True, skip reading animation frame data
            report_func: Optional callback for reporting messages (level, message)
        """
        self.fps: int = 30
        self.num_frames: int = 0
        self.channels: List[Tuple[int, ZmoChannel]] = []
        self.frame_events: List[int] = []
        self.total_attack_frames: int = 0
        self.interpolation_interval_ms: Optional[int] = None
        
        self._report_func = report_func
        
        if filepath:
            with open(filepath, 'rb') as f:
                self._read(f, skip_animation)
        elif buffer:
            import io
            f = io.BytesIO(buffer)
            self._read(f, skip_animation)
    
    def _report(self, level: str, message: str):
        """Report a message if callback is set."""
        if self._report_func:
            self._report_func(level, message)
    
    def _read(self, f: BinaryIO, skip_animation: bool = False):
        """Read ZMO file from binary stream."""
        # Read magic header
        magic = read_str(f)
        if magic != self.MAGIC:
            raise ValueError(f"Invalid ZMO magic header: {magic}, expected {self.MAGIC}")
        
        # Read header
        self.fps = read_u32(f)
        self.num_frames = read_u32(f)
        
        self._report('INFO', f"ZMO: fps={self.fps}, num_frames={self.num_frames}")
        
        if not skip_animation:
            # Read channels
            channel_count = read_u32(f)
            self._report('INFO', f"ZMO: channel_count={channel_count}")
            
            # First pass: read channel headers
            for _ in range(channel_count):
                channel_type = read_u32(f)
                bone_index = read_u32(f)
                
                channel = self._create_channel(channel_type, bone_index)
                self.channels.append((bone_index, channel))
            
            # Second pass: read frame data for each channel
            for frame_idx in range(self.num_frames):
                for bone_index, channel in self.channels:
                    self._read_channel_frame(f, channel)
        
        # Try to read extended data (frame events)
        self._read_extended_data(f)
    
    def _create_channel(self, channel_type: int, bone_index: int) -> ZmoChannel:
        """Create appropriate channel type based on channel type flag."""
        ct = ZmoChannelType(channel_type)
        
        if ct == ZmoChannelType.EMPTY:
            return ZmoChannel(channel_type=ct, bone_index=bone_index)
        elif ct == ZmoChannelType.POSITION:
            return ZmoPositionChannel(channel_type=ct, bone_index=bone_index)
        elif ct == ZmoChannelType.ROTATION:
            return ZmoRotationChannel(channel_type=ct, bone_index=bone_index)
        elif ct == ZmoChannelType.NORMAL:
            return ZmoNormalChannel(channel_type=ct, bone_index=bone_index)
        elif ct == ZmoChannelType.ALPHA:
            return ZmoAlphaChannel(channel_type=ct, bone_index=bone_index)
        elif ct in (ZmoChannelType.UV1, ZmoChannelType.UV2, 
                    ZmoChannelType.UV3, ZmoChannelType.UV4):
            return ZmoUVChannel(channel_type=ct, bone_index=bone_index)
        elif ct == ZmoChannelType.TEXTURE:
            return ZmoTextureChannel(channel_type=ct, bone_index=bone_index)
        elif ct == ZmoChannelType.SCALE:
            return ZmoScaleChannel(channel_type=ct, bone_index=bone_index)
        else:
            raise ValueError(f"Invalid ZMO channel type: {channel_type}")
    
    def _read_channel_frame(self, f: BinaryIO, channel: ZmoChannel):
        """Read a single frame of data for a channel."""
        if isinstance(channel, ZmoPositionChannel):
            channel.values.append(read_vector3_f32(f))
        elif isinstance(channel, ZmoRotationChannel):
            # ZMO uses WXYZ order for quaternions
            channel.values.append(read_quat_wxyz(f))
        elif isinstance(channel, ZmoNormalChannel):
            channel.values.append(read_vector3_f32(f))
        elif isinstance(channel, ZmoAlphaChannel):
            channel.values.append(read_f32(f))
        elif isinstance(channel, ZmoUVChannel):
            channel.values.append(read_vector2_f32(f))
        elif isinstance(channel, ZmoTextureChannel):
            channel.values.append(read_f32(f))
        elif isinstance(channel, ZmoScaleChannel):
            channel.values.append(read_f32(f))
        # ZmoChannel (EMPTY) has no frame data
    
    def _read_extended_data(self, f: BinaryIO):
        """Read extended ZMO data (frame events)."""
        try:
            # Seek to 4 bytes before end to check for extended magic
            f.seek(0, 2)  # End of file
            file_size = f.tell()
            
            if file_size < 8:
                return
            
            f.seek(file_size - 4)
            extended_magic = f.read(4).decode('ascii')
            
            if extended_magic not in (self.EXTENDED_MAGIC_EZMO, self.EXTENDED_MAGIC_3ZMO):
                return
            
            self._report('INFO', f"ZMO: Found extended data: {extended_magic}")
            
            # Read position of extended data
            f.seek(file_size - 8)
            position = read_u32(f)
            f.seek(position)
            
            # Read frame events
            num_frame_events = read_u16(f)
            self._report('INFO', f"ZMO: num_frame_events={num_frame_events}")
            
            for _ in range(num_frame_events):
                frame_event = read_u16(f)
                self.frame_events.append(frame_event)
                
                # Count attack frames (based on Rust implementation)
                if frame_event in (10,) or 20 <= frame_event <= 28 or \
                   56 <= frame_event <= 57 or 66 <= frame_event <= 67:
                    self.total_attack_frames += 1
            
            # Read interpolation interval for 3ZMO format
            if extended_magic == self.EXTENDED_MAGIC_3ZMO:
                self.interpolation_interval_ms = read_u32(f)
                self._report('INFO', f"ZMO: interpolation_interval_ms={self.interpolation_interval_ms}")
                
        except Exception as e:
            self._report('WARNING', f"ZMO: Failed to read extended data: {e}")
    
    def get_duration_seconds(self) -> float:
        """Get animation duration in seconds."""
        if self.fps == 0:
            return 0.0
        return self.num_frames / self.fps
    
    def get_bone_channels(self) -> dict:
        """
        Get a dictionary mapping bone indices to their channels.
        
        Returns:
            Dict where keys are bone indices and values are dicts of channel_type -> channel
        """
        bone_channels = {}
        for bone_index, channel in self.channels:
            if bone_index not in bone_channels:
                bone_channels[bone_index] = {}
            bone_channels[bone_index][channel.channel_type] = channel
        return bone_channels
    
    def get_position_channel(self, bone_index: int) -> Optional[ZmoPositionChannel]:
        """Get position channel for a specific bone."""
        for bidx, channel in self.channels:
            if bidx == bone_index and isinstance(channel, ZmoPositionChannel):
                return channel
        return None
    
    def get_rotation_channel(self, bone_index: int) -> Optional[ZmoRotationChannel]:
        """Get rotation channel for a specific bone."""
        for bidx, channel in self.channels:
            if bidx == bone_index and isinstance(channel, ZmoRotationChannel):
                return channel
        return None
    
    def get_scale_channel(self, bone_index: int) -> Optional[ZmoScaleChannel]:
        """Get scale channel for a specific bone."""
        for bidx, channel in self.channels:
            if bidx == bone_index and isinstance(channel, ZmoScaleChannel):
                return channel
        return None
