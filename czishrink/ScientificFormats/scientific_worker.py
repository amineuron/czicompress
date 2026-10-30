# SPDX-FileCopyrightText: 2026 Amin Khosrozadeh
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Contributor: Amin Khosrozadeh, PhD.
# Affiliation: Prof. Vassiliki Nikoletopoulou lab, DNF, UNIL.
"""Portable, process-isolated lossless processing for HDF5 and TIFF.

The GUI owns cancellation and removes its temporary output on failure. This
worker writes only a new destination and verifies data before reporting success.
"""
import argparse
import hashlib
import itertools
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile


def progress(percent):
    print(json.dumps({'progress': max(0, min(100, int(percent)))}), flush=True)


def file_hash(path):
    value = hashlib.sha256()
    with open(path, 'rb') as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def _tool(name):
    suffix = '.exe' if os.name == 'nt' else ''
    root = Path(os.environ.get('CZISHRINK_FORMAT_TOOLS', Path(__file__).resolve().parent))
    local = root / 'hdf5' / (name + suffix)
    if local.is_file():
        return str(local)
    found = shutil.which(name)
    if found:
        return found
    raise ValueError('The portable HDF5 tools are missing; keep the entire app folder together')


def _run(command):
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
    if completed.returncode:
        message = (completed.stderr + completed.stdout).decode('utf-8', 'replace').strip()
        raise ValueError(Path(command[0]).name + ' failed: ' + message[-4000:])


def hdf5_repack(source, destination, mode, level):
    import h5py
    selected = []
    with h5py.File(source, 'r') as handle:
        if handle.userblock_size:
            raise ValueError('HDF5 user-block metadata requires a dedicated preservation workflow')
        # Relative external files and virtual data must not be silently relocated.
        visited = set()

        def check(group):
            address = h5py.h5o.get_info(group.id).addr
            if address in visited:
                return
            visited.add(address)
            for key in group:
                link = group.get(key, getlink=True)
                if isinstance(link, h5py.ExternalLink):
                    raise ValueError('HDF5 external links require a dedicated relocation workflow')
                if isinstance(link, h5py.SoftLink):
                    continue
                item = group[key]
                if isinstance(item, h5py.Group):
                    check(item)
                elif isinstance(item, h5py.Dataset):
                    if item.is_virtual or item.external:
                        raise ValueError('Virtual or externally stored HDF5 datasets are not supported')

        check(handle)

        def choose(name, item):
            if not isinstance(item, h5py.Dataset):
                return
            if mode == 'Decompress':
                if item.id.get_create_plist().get_nfilters():
                    selected.append('/' + name)
            elif item.shape and item.size and not item.dtype.hasobject:
                if mode == 'CompressAll' or not item.id.get_create_plist().get_nfilters():
                    selected.append('/' + name)

        handle.visititems(choose)
    if not selected:
        shutil.copyfile(source, destination)
        return {'unchanged': True, 'datasets': 0}
    command = [_tool('h5repack')]
    for name in selected:
        if any(character in name for character in (',', ':', '\n', '\r')):
            raise ValueError('HDF5 dataset name cannot be represented safely by h5repack')
        if mode == 'Decompress':
            command += ['-f', name + ':NONE']
        else:
            command += ['-f', name + ':SHUF', '-f', name + ':GZIP=' + str(level)]
    progress(10)
    if sum(len(part) + 3 for part in command) > 28000:
        if any(any(character.isspace() for character in name) for name in selected):
            raise ValueError('Long HDF5 filter lists with whitespace in dataset names are not supported')
        # h5repack's options file avoids the Windows command-line length limit.
        with tempfile.NamedTemporaryFile(mode='w', suffix='.h5repack-options', dir=os.environ.get('CZISHRINK_WORK_DIRECTORY', destination.parent),
                                         encoding='utf-8', delete=False) as options:
            for index in range(1, len(command), 2):
                options.write(command[index] + ' ' + command[index + 1] + '\n')
        try:
            _run([_tool('h5repack'), '-e', options.name, str(source), str(destination)])
        finally:
            Path(options.name).unlink()
    else:
        _run(command + [str(source), str(destination)])
    with h5py.File(destination, 'r') as handle:
        for name in selected:
            filters = handle[name].id.get_create_plist()
            ids = [filters.get_filter(index)[0] for index in range(filters.get_nfilters())]
            if (mode == 'Decompress' and ids) or (mode != 'Decompress' and 1 not in ids):
                raise ValueError('HDF5 did not apply the requested filter to dataset ' + name)
    progress(75)
    # h5diff handles attributes, references and decoded dataset contents.
    verify_hdf5(source, destination)
    progress(99)
    return {'datasets': len(selected), 'verified': True, 'compression': 'none' if mode == 'Decompress' else 'gzip+shuffle'}


def verify_hdf5(source, destination):
    """Check metadata/references with h5diff and fixed-width values bit-for-bit."""
    import h5py
    import numpy as np
    _run([_tool('h5diff'), '-q', str(source), str(destination)])
    with h5py.File(source, 'r') as first, h5py.File(destination, 'r') as second:
        datasets = {}
        first.visititems(lambda name, item: datasets.__setitem__(name, item) if isinstance(item, h5py.Dataset) else None)
        for name, left in datasets.items():
            right = second[name]
            if left.shape != right.shape or left.dtype != right.dtype or left.maxshape != right.maxshape:
                raise ValueError('HDF5 shape, datatype or maximum shape changed: ' + name)
            # Variable-length data and references have process-local NumPy pointers;
            # h5diff compares those semantically, rather than comparing pointers.
            if left.dtype.hasobject or not left.size:
                continue
            if not left.shape:
                if np.asarray(left[()]).tobytes() != np.asarray(right[()]).tobytes():
                    raise ValueError('HDF5 scalar bytes changed: ' + name)
                continue
            budget = max(1, (8 * 1024 * 1024) // max(1, left.dtype.itemsize))
            block = [1] * len(left.shape)
            for axis in reversed(range(len(block))):
                block[axis] = min(left.shape[axis], budget)
                budget = max(1, budget // block[axis])
            for start in itertools.product(*(range(0, length, step) for length, step in zip(left.shape, block))):
                selection = tuple(slice(offset, min(offset + step, length)) for offset, step, length in zip(start, block, left.shape))
                if left[selection].tobytes() != right[selection].tobytes():
                    raise ValueError('HDF5 fixed-width dataset bytes changed: ' + name)


# These describe storage layout and are regenerated by the TIFF writer. Every
# other tag is retained byte-for-byte, or the conversion fails verification.
TIFF_STORAGE_TAGS = {254, 256, 257, 258, 259, 262, 266, 273, 277, 278, 279, 282, 283,
                     284, 296, 317, 320, 322, 323, 324, 325, 330, 338, 339}
TIFF_TEXT_TAGS = {270, 305, 306}
TIFF_POINTER_TAGS = {34665, 34853, 40965, 513, 514}


def _pages(pages):
    for page in pages:
        yield page
        if page.pages is not None:
            yield from _pages(page.pages)


def _tag_bytes(handle, tag):
    dtype = int(tag.dtype)
    formats = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4,
               10: 8, 11: 4, 12: 8, 13: 4, 16: 8, 17: 8, 18: 8}
    if dtype not in formats:
        raise ValueError('Unsupported TIFF metadata tag datatype: ' + str(dtype))
    handle.seek(tag.valueoffset)
    value = handle.read(tag.count * formats[dtype])
    if len(value) != tag.count * formats[dtype]:
        raise ValueError('Truncated TIFF metadata tag')
    return value


def tiff_repack(source, destination, mode, level):
    import tifffile
    with tifffile.TiffFile(source) as original:
        all_pages = list(_pages(original.pages))
        if not all_pages:
            raise ValueError('TIFF has no image pages')
        if any(getattr(original, flag, False) for flag in ('is_ndpi', 'is_lsm', 'is_scn', 'is_stk')):
            raise ValueError('This proprietary TIFF variant needs a dedicated metadata-preserving converter')
        if any(any(code in page.tags for code in TIFF_POINTER_TAGS) for page in all_pages):
            raise ValueError('TIFF EXIF/GPS or JPEG-offset metadata cannot be safely relocated')
        if original.is_imagej and len(original.pages) == 1 and original.series[0].size != original.pages[0].size:
            raise ValueError('Implicit ImageJ hyperstacks are not supported by this TIFF repacker')
        if mode in ('CompressUncompressed', 'CompressUncompressedAndZstd') and any(int(page.compression) != 1 for page in all_pages):
            shutil.copyfile(source, destination)
            return {'unchanged': True, 'pages': len(all_pages)}
        uncompressed_size = sum(page.size * page.dtype.itemsize for page in all_pages)
        bigtiff = original.is_bigtiff or (mode == 'Decompress' and uncompressed_size > 4 * 1024**3 - 32 * 1024**2)
        completed = 0
        with tifffile.TiffWriter(destination, bigtiff=bigtiff, byteorder=original.byteorder, ome=False, imagej=False) as writer:
            def write(page):
                nonlocal completed
                extras = []
                for tag in page.tags.values():
                    if tag.code in TIFF_STORAGE_TAGS | TIFF_TEXT_TAGS:
                        continue
                    extras.append((tag.code, int(tag.dtype), tag.count, _tag_bytes(original.filehandle, tag), False))
                children = list(page.pages) if page.pages is not None else []
                tags = page.tags
                resolution = (tags[282].value, tags[283].value) if 282 in tags and 283 in tags else None
                writer.write(
                    page.asarray(), photometric=page.photometric, planarconfig=page.planarconfig,
                    bitspersample=page.bitspersample, extrasamples=page.extrasamples or None,
                    compression=None if mode == 'Decompress' else 'deflate',
                    compressionargs=None if mode == 'Decompress' else {'level': level},
                    predictor=None if mode == 'Decompress' or page.bitspersample < 8 else
                    (3 if page.dtype.kind == 'f' else 2 if page.dtype.kind in 'ui' else None),
                    description=tags[270].value if 270 in tags else None,
                    software=tags[305].value if 305 in tags else False,
                    datetime=tags[306].value if 306 in tags else None,
                    resolution=resolution, resolutionunit=tags[296].value if 296 in tags else None,
                    colormap=page.colormap, subfiletype=tags[254].value if 254 in tags else 0,
                    subifds=len(children) or None, extratags=extras, metadata=None,
                )
                completed += 1
                progress(5 + int(65 * completed / len(all_pages)))
                for child in children:
                    write(child)
            for page in original.pages:
                write(page)
    verify_tiff(source, destination)
    progress(99)
    return {'pages': len(all_pages), 'verified': True, 'compression': 'none' if mode == 'Decompress' else 'deflate'}


def verify_tiff(source, destination):
    import tifffile
    with tifffile.TiffFile(source) as first, tifffile.TiffFile(destination) as second:
        left_pages, right_pages = list(_pages(first.pages)), list(_pages(second.pages))
        if len(left_pages) != len(right_pages):
            raise ValueError('TIFF page count changed')
        for left, right in zip(left_pages, right_pages):
            a, b = left.asarray(), right.asarray()
            if a.shape != b.shape or a.dtype != b.dtype or a.tobytes() != b.tobytes():
                raise ValueError('TIFF pixel verification failed')
            for code, tag in left.tags.items():
                if code in TIFF_STORAGE_TAGS:
                    continue
                if code not in right.tags:
                    raise ValueError('TIFF metadata tag was lost: ' + str(code))
                if _tag_bytes(first.filehandle, tag) != _tag_bytes(second.filehandle, right.tags[code]):
                    raise ValueError('TIFF metadata tag changed: ' + str(code))
            # The layout may change, but interpretation of the pixel samples must not.
            for attribute in ('photometric', 'planarconfig', 'bitspersample', 'samplesperpixel', 'sampleformat', 'extrasamples', 'subfiletype'):
                if getattr(left, attribute) != getattr(right, attribute):
                    raise ValueError('TIFF pixel interpretation changed: ' + attribute)
            if 320 in left.tags and (320 not in right.tags or _tag_bytes(first.filehandle, left.tags[320]) != _tag_bytes(second.filehandle, right.tags[320])):
                raise ValueError('TIFF color map changed')


def process(source, destination, mode, level, kind=None):
    source, destination = Path(source), Path(destination)
    if not source.is_file() or source.resolve() == destination.resolve() or destination.exists():
        raise ValueError('Source must exist and destination must be a new, different file')
    level = max(1, min(9, int(level)))
    kind = kind or source.suffix.lower()
    progress(0)
    original_hash = file_hash(source)
    try:
        if kind in ('.h5', '.hdf5'):
            result = hdf5_repack(source, destination, mode, level)
        elif kind in ('.tif', '.tiff'):
            result = tiff_repack(source, destination, mode, level)
        else:
            raise ValueError('Unsupported scientific format')
        if mode != 'Decompress' and destination.stat().st_size >= source.stat().st_size:
            destination.unlink()
            shutil.copyfile(source, destination)
            result['unchanged'] = True
            result['compression'] = 'original'
            result['reason'] = 'Copied original: no size reduction'
        if not result.get('verified') and not result.get('unchanged'):
            raise ValueError('Processing completed without verification evidence')
        if file_hash(source) != original_hash:
            raise ValueError('Input changed during processing; output was not accepted')
        output_hash = file_hash(destination)
        if result.get('unchanged') and original_hash != output_hash:
            raise ValueError('Unchanged copy failed SHA-256 verification')
        result['input_sha256'] = original_hash
        result['output_sha256'] = output_hash
        result['container_byte_identical'] = original_hash == output_hash
        shutil.copystat(source, destination)
        # Keep output writable even when source is read-only, so temp-file cleanup works.
        os.chmod(destination, stat.S_IMODE(destination.stat().st_mode) | stat.S_IWUSR)
        progress(100)
        return result
    except BaseException:
        if destination.exists():
            destination.unlink()
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--mode', required=True, choices=('CompressUncompressed', 'CompressUncompressedAndZstd', 'CompressAll', 'Decompress'))
    parser.add_argument('--level', type=int, default=1)
    args = parser.parse_args()
    try:
        print(json.dumps({'result': process(args.input, args.output, args.mode, args.level)}), flush=True)
        return 0
    except Exception as error:
        print(json.dumps({'error': str(error)}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
