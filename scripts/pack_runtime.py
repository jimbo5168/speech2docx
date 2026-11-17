#!/usr/bin/env python3
"""Pack an installed standalone Python tree with regular files and pinned metadata."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile


def pack(root, archive):
    root = root.resolve()
    # Console scripts installed by pip need a relative interpreter after relocation.
    for script in (root / 'bin').iterdir():
        if script.is_file() and not script.is_symlink():
            data = script.read_bytes()
            first, _, body = data.partition(b'\n')
            if first.startswith(b'#!') and b'python' in first:
                script.write_bytes(b'#!/bin/sh\n' + b"\'\'\'exec\' \"$(dirname \"$0\")/python3\" \"$0\" \"$@\"\n\' \'\'\'\n" + body)
    size = 0
    with tarfile.open(archive, 'w:gz', dereference=True, compresslevel=6) as bundle:
        for path in sorted(root.rglob('*')):
            relative = path.relative_to(root)
            if '__pycache__' in relative.parts or path.name.startswith('._'):
                continue
            if path.is_file():
                size += path.stat().st_size
                bundle.add(path, arcname=str(relative), recursive=False)
            elif not path.is_dir():
                raise ValueError(f'Unsupported runtime member {path}')
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    metadata = dict(compressed_bytes=archive.stat().st_size, extracted_bytes=size, sha256=digest)
    archive.with_suffix('.gz.sha256').write_text(f'{digest}  {archive.name}\n')
    archive.with_suffix('.gz.sizes.json').write_text(json.dumps(metadata, indent=2) + '\n')
    return metadata


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    print(json.dumps(pack(args.root, args.archive)))
