"""Make the iOS build play through the loudspeaker.

The port enables "Prepare iOS For Recording" (Photon Voice needs the mic), which
puts the AVAudioSession into PlayAndRecord. With "Force iOS Speakers When
Recording" left off, iOS routes playback to the earpiece receiver instead of the
speaker -- the game sounds very quiet on an iPhone and fine everywhere else.

Both are bools in PlayerSettings, so flipping the second one is a one-byte,
same-size edit. The neighbouring bools give a 5-byte fingerprint that has to be
unique inside the object, otherwise we refuse to touch anything.
"""
import struct

# runInBackground, captureSingleScreen, muteOtherAudioSources,
# prepareIOSForRecording, forceIOSSpeakersWhenRecording
FINGERPRINT = b"\x01\x00\x01\x01\x00"
SPEAKER_OFFSET = 4          # index of forceIOSSpeakersWhenRecording in the run


def find_player_settings(data: bytes):
    """(offset, size) of the PlayerSettings object (class id 129)."""
    import serializedfile as sf

    data_offset = struct.unpack_from(">Iqqq", data, 20)[2]
    f = sf.SerializedFile(data[:data_offset])
    obj = next(o for o in f.objects if f.types[o.type_index].class_id == 129)
    return f.data_offset + obj.byte_start, obj.byte_size


def speaker_flag_offset(data: bytes) -> int:
    """Absolute offset of forceIOSSpeakersWhenRecording, or -1 if already set."""
    start, size = find_player_settings(data)
    blob = data[start:start + size]
    hits = []
    pos = blob.find(FINGERPRINT)
    while pos != -1:
        hits.append(pos)
        pos = blob.find(FINGERPRINT, pos + 1)
    if len(hits) != 1:
        raise ValueError(f"audio flag fingerprint matched {len(hits)} times; refusing to guess")
    return start + hits[0] + SPEAKER_OFFSET


def is_fixed(data: bytes) -> bool:
    try:
        speaker_flag_offset(data)
        return False
    except ValueError:
        start, size = find_player_settings(data)
        return b"\x01\x00\x01\x01\x01" in data[start:start + size]


def patch(data: bytes) -> bytes:
    off = speaker_flag_offset(data)
    out = bytearray(data)
    out[off] = 1
    assert len(out) == len(data)
    return bytes(out)
