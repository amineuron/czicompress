# SPDX-FileCopyrightText: 2026 czicompress contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Lossless repacking of modern ND2 image chunks, preserving other chunks verbatim.

Container layout references: tlambert03/nd2 and Laboratory-Imaging/limnd2.
No metadata is reconstructed from a reduced microscopy metadata model.
"""
import hashlib
import re
import shutil
import struct
import zlib

MAGIC = 0x0ABECEDA
HEADER = struct.Struct('<IIQ')
FOOTER = struct.Struct('<32sQ')
FILE_SIGNATURE = b'ND2 FILE SIGNATURE CHUNK NAME01!'
MAP_SIGNATURE = b'ND2 FILEMAP SIGNATURE NAME 0001!'
END_SIGNATURE = b'ND2 CHUNK MAP SIGNATURE 0000001!'
ATTRIBUTES = b'ImageAttributesLV!'
FRAME = re.compile(rb'ImageDataSeq\|(\d+)!$')


def read_chunk(stream, offset, expected):
    stream.seek(offset)
    header = stream.read(HEADER.size)
    if len(header) != HEADER.size:
        raise ValueError('Truncated ND2 chunk header')
    magic, name_length, data_length = HEADER.unpack(header)
    if magic != MAGIC or not 0 < name_length <= 16 * 1024 * 1024:
        raise ValueError('Invalid ND2 chunk header')
    name = stream.read(name_length)
    if not name.startswith(expected):
        raise ValueError('ND2 chunk name does not match its index')
    data = stream.read(data_length)
    if len(data) != data_length:
        raise ValueError('Truncated ND2 chunk data')
    return name, data


def read_map(stream):
    stream.seek(0, 2)
    size = stream.tell()
    if size < 152:
        raise ValueError('File is too small to be ND2')
    stream.seek(size - FOOTER.size)
    signature, offset = FOOTER.unpack(stream.read(FOOTER.size))
    if signature != END_SIGNATURE or offset >= size - FOOTER.size:
        raise ValueError('Unsupported legacy ND2 or invalid chunk index')
    _, payload = read_chunk(stream, offset, MAP_SIGNATURE)
    result = {}
    cursor = 0
    while cursor < len(payload):
        end = payload.index(b'!', cursor) + 1
        name = payload[cursor:end]
        if name == END_SIGNATURE:
            return result
        location, length = struct.unpack_from('<QQ', payload, end)
        if name in result or location >= offset:
            raise ValueError('Duplicate or invalid ND2 chunk index entry')
        result[name] = (location, length)
        cursor = end + 16
    raise ValueError('ND2 chunk index has no terminator')


def compression_field(payload):
    # CLX Lite scalar: [INT32 or UINT32, UTF-16 name length, name, value].
    name = 'eCompression\0'.encode('utf-16-le')
    positions = []
    for kind in (2, 3):
        needle = bytes((kind, len(name) // 2)) + name
        start = 0
        while (start := payload.find(needle, start)) >= 0:
            positions.append(start + len(needle))
            start += len(needle)
    if len(positions) != 1 or positions[0] + 4 > len(payload):
        raise ValueError('Unsupported ND2 image attributes; compression field cannot be safely patched')
    return positions[0]


def pad(stream):
    stream.write(b'\0' * (-stream.tell() % 4096))


def repack(source, destination, mode, level, progress):
    with open(source, 'rb') as original:
        name, header = read_chunk(original, 0, FILE_SIGNATURE)
        if header[:6] != b'Ver3.0':
            raise ValueError('Native ND2 compression currently supports modern Ver3.0 files only')
        chunks = read_map(original)
        if ATTRIBUTES not in chunks:
            raise ValueError('ND2 has no supported image attributes chunk')
        _, attributes = read_chunk(original, chunks[ATTRIBUTES][0], ATTRIBUTES)
        field = compression_field(attributes)
        compression = struct.unpack_from('<I', attributes, field)[0]
        if compression not in (0, 2):
            raise ValueError('Lossy or unknown ND2 compression is not supported')
        frames = [name for name in chunks if FRAME.fullmatch(name)]
        if not frames or any(name.startswith(b'ImageData') and not FRAME.fullmatch(name) for name in chunks):
            raise ValueError('Unsupported tiled or nonstandard ND2 image layout')
        if mode == 'CompressUncompressed' or mode == 'CompressUncompressedAndZstd':
            if compression == 0:
                shutil.copyfile(source, destination)
                return {'frames': len(frames), 'unchanged': True}
        target = 2 if mode == 'Decompress' else 0
        if mode == 'Decompress' and compression == 2:
            shutil.copyfile(source, destination)
            return {'frames': len(frames), 'unchanged': True}
        modified_attributes = bytearray(attributes)
        struct.pack_into('<I', modified_attributes, field, target)
        output_map = {}
        with open(destination, 'xb') as output:
            output.write(HEADER.pack(MAGIC, len(name), len(header)))
            output.write(name)
            output.write(header)
            ordered = sorted(chunks, key=lambda key: chunks[key][0])
            for index, key in enumerate(ordered):
                if key == FILE_SIGNATURE:
                    output_map[key] = (0, chunks[key][1])
                    continue
                chunk_name, data = read_chunk(original, chunks[key][0], key)
                if key == ATTRIBUTES:
                    data = bytes(modified_attributes)
                elif FRAME.fullmatch(key):
                    if len(chunk_name) != 4072 or len(data) < 8:
                        raise ValueError('Unsupported ND2 frame alignment')
                    pixels = zlib.decompress(data[8:]) if compression == 0 else data[8:]
                    data = data[:8] + (zlib.compress(pixels, level) if target == 0 else pixels)
                pad(output)
                offset = output.tell()
                output.write(HEADER.pack(MAGIC, len(chunk_name), len(data)))
                output.write(chunk_name)
                output.write(data)
                output_map[key] = (offset, len(data))
                progress(5 + int(65 * (index + 1) / len(ordered)))
            pad(output)
            map_offset = output.tell()
            payload = b''.join(key + struct.pack('<QQ', *output_map[key]) for key in sorted(output_map, reverse=True))
            payload += FOOTER.pack(END_SIGNATURE, map_offset)
            # A second footer at EOF is used by ND2 readers to locate this map.
            payload += b'\0' * (-(HEADER.size + len(MAP_SIGNATURE) + len(payload) + FOOTER.size) % 4096)
            payload += FOOTER.pack(END_SIGNATURE, map_offset)
            output.write(HEADER.pack(MAGIC, len(MAP_SIGNATURE), len(payload)))
            output.write(MAP_SIGNATURE)
            output.write(payload)
    verify(source, destination, progress)
    return {'frames': len(frames), 'verified': True, 'compression': 'none' if target == 2 else 'zlib'}


def verify(source, destination, progress=lambda _: None):
    with open(source, 'rb') as before, open(destination, 'rb') as after:
        original = read_map(before)
        converted = read_map(after)
        if original.keys() != converted.keys():
            raise ValueError('ND2 verification failed: metadata or image chunks are missing')
        _, attributes_before = read_chunk(before, original[ATTRIBUTES][0], ATTRIBUTES)
        _, attributes_after = read_chunk(after, converted[ATTRIBUTES][0], ATTRIBUTES)
        first_field = compression_field(attributes_before)
        second_field = compression_field(attributes_after)
        first_compression = struct.unpack_from('<I', attributes_before, first_field)[0]
        second_compression = struct.unpack_from('<I', attributes_after, second_field)[0]
        for index, key in enumerate(original):
            _, first = read_chunk(before, original[key][0], key)
            _, second = read_chunk(after, converted[key][0], key)
            if key == ATTRIBUTES:
                first = first[:first_field] + b'\0' * 4 + first[first_field + 4:]
                second = second[:second_field] + b'\0' * 4 + second[second_field + 4:]
            elif FRAME.fullmatch(key):
                if first[:8] != second[:8]:
                    raise ValueError('ND2 verification failed: acquisition timestamps changed')
                first = zlib.decompress(first[8:]) if first_compression == 0 else first[8:]
                second = zlib.decompress(second[8:]) if second_compression == 0 else second[8:]
            if hashlib.sha256(first).digest() != hashlib.sha256(second).digest():
                raise ValueError('ND2 verification failed: data or metadata changed in ' + key.decode('ascii', 'replace'))
            progress(70 + int(15 * (index + 1) / len(original)))
    # Independent parser and decoder: compare every frame, including its dtype and shape.
    import nd2
    with nd2.ND2File(source) as first, nd2.ND2File(destination) as second:
        if first.sizes != second.sizes or first.dtype != second.dtype or first.voxel_size() != second.voxel_size():
            raise ValueError('Independent ND2 reader found different dimensions or calibration')
        frames = sorted(int(FRAME.fullmatch(key)[1]) for key in original if FRAME.fullmatch(key))
        for index, frame in enumerate(frames):
            left, right = first.read_frame(frame), second.read_frame(frame)
            if left.shape != right.shape or left.dtype != right.dtype or left.tobytes() != right.tobytes():
                raise ValueError('Independent ND2 pixel verification failed')
            progress(85 + int(14 * (index + 1) / len(frames)))
