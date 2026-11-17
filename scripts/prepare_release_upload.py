#!/usr/bin/env python3
"""Generate pinned release metadata and upload instructions. Never uploads or logs in."""
import argparse
import json
from pathlib import Path, PurePosixPath
import shlex
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from model_download import MODEL_SHA, REPOSITORIES, sha256, validate_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, default=Path.home() / 'ASR/models/qwen3-asr-1.7b-buddhist-8bit')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, help='Optional relocatable macOS arm64 runtime .tar.gz')
    args = parser.parse_args()
    files = []
    for path in sorted(args.model.rglob('*')):
        if path.is_symlink():
            raise ValueError('Release files must not be symlinks')
        if path.is_file() and not any(p.startswith('.') for p in path.relative_to(args.model).parts):
            files.append(dict(path=path.relative_to(args.model).as_posix(), size=path.stat().st_size,
                              sha256=sha256(path)))
    manifest = validate_manifest(dict(version=1, release='r3-8bit', files=files))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'model_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    uploads = [(args.model / f['path'], f['path']) for f in files]
    uploads.append((args.output / 'model_manifest.json', 'model_manifest.json'))
    if args.runtime:
        runtime = dict(version=1, release='runtime-macos-arm64', files=[dict(
            path=args.runtime.name, size=args.runtime.stat().st_size, sha256=sha256(args.runtime))])
        with tarfile.open(args.runtime, 'r:gz') as archive:
            members = archive.getmembers()
            if any(not member.isfile() or PurePosixPath(member.name).is_absolute()
                   or '..' in PurePosixPath(member.name).parts or '\\' in member.name for member in members):
                raise ValueError('Runtime must contain safe regular files only')
            runtime['extracted_bytes'] = sum(member.size for member in members)
        runtime['minimum_macos'] = '26.0'
        runtime['repositories'] = dict(REPOSITORIES)
        validate_manifest(runtime, require_r3=False)
        (args.output / 'runtime_manifest.json').write_text(json.dumps(runtime, indent=2) + '\n')
        uploads.extend([(args.runtime, args.runtime.name),
                        (args.output / 'runtime_manifest.json', 'runtime_manifest.json')])
    lines = ['# Release upload file list', '', 'Run from the repository root after credentials are supplied.',
             'CLI references: https://huggingface.co/docs/huggingface_hub/en/guides/cli and '
             'https://github.com/modelscope/modelscope_hub#ms-hub-upload', '']
    for local, remote in uploads:
        lines.append(f'- {local} → {remote} | {local.stat().st_size} bytes | SHA256 `{sha256(local)}`')
    lines.extend(['', '## Hugging Face', '', '```sh'])
    for local, remote in uploads:
        lines.append('hf upload ' + ' '.join(map(shlex.quote, [REPOSITORIES['Hugging Face'], str(local), remote])))
    lines.extend(['```', '', '## ModelScope', '', '```sh'])
    for local, remote in uploads:
        lines.append('ms-hub upload ' + ' '.join(map(shlex.quote,
                     [REPOSITORIES['ModelScope'], str(local), remote])) + ' --repo-type model')
    lines.extend(['```', ''])
    (args.output / 'UPLOAD.md').write_text('\n'.join(lines))
    print(json.dumps(dict(total_bytes=sum(f['size'] for f in files), files=len(files), pinned_sha=MODEL_SHA)))


if __name__ == '__main__':
    main()
