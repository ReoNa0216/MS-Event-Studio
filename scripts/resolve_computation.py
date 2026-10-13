"""Install pinned calculation wheels, or clean Git archives without a Release.

Local builds reuse sibling repositories via git archive (never their dirty
working trees). Feature builds use the official Release wheel and its provenance;
hosted builds can also use the identical hash-pinned wheel secret.
No release is created and no credential is serialized.
"""
from pathlib import Path
import base64
from email.parser import BytesParser
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def verify_wheel(package, lock, path):
    if hashlib.sha256(path.read_bytes()).hexdigest() != lock['wheel']['sha256']:
        raise RuntimeError('Calculation wheel differs from pinned SHA256')
    with zipfile.ZipFile(path) as archive:
        metadata_files = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
        if len(metadata_files) != 1:
            raise RuntimeError('Calculation wheel metadata is ambiguous')
        metadata = BytesParser().parsebytes(archive.read(metadata_files[0]))
        if metadata['Name'] != package or metadata['Version'] != lock['version']:
            raise RuntimeError('Calculation wheel package/version mismatch')


def release_wheel(package, lock, work):
    target = work / package
    target.mkdir()
    subprocess.run(['gh', 'release', 'download', lock['release_tag'], '--repo', lock['repository'],
                    '--dir', str(target), '--pattern', lock['wheel']['filename'],
                    '--pattern', 'SHA256SUMS.txt', '--pattern', 'release-manifest.json'], check=True)
    for name, key in [('SHA256SUMS.txt', 'checksums_sha256'),
                      ('release-manifest.json', 'release_manifest_sha256')]:
        if hashlib.sha256((target / name).read_bytes()).hexdigest() != lock[key]:
            raise RuntimeError(f'Calculation Release {name} differs from pinned SHA256')
    manifest = json.loads((target / 'release-manifest.json').read_text('utf-8'))
    if (lock['release_tag'] != 'v' + lock['version'] or manifest['version'] != lock['version']
            or manifest['commit'] != lock['commit']):
        raise RuntimeError('Calculation Release version/commit mismatch')
    wheel = target / lock['wheel']['filename']
    verify_wheel(package, lock, wheel)
    checksums = dict(line.split(maxsplit=1)[::-1]
                     for line in (target / 'SHA256SUMS.txt').read_text('utf-8').splitlines() if line.strip())
    for name in [wheel.name, 'release-manifest.json']:
        if checksums.get(name) != hashlib.sha256((target / name).read_bytes()).hexdigest():
            raise RuntimeError('Calculation Release checksum list mismatch')
    info = manifest['artifacts'][wheel.name]
    if info['sha256'] != lock['wheel']['sha256'] or info['bytes'] != wheel.stat().st_size:
        raise RuntimeError('Calculation Release wheel manifest mismatch')
    with zipfile.ZipFile(wheel) as archive:
        for name, digest in manifest['runtime_sha256'].items():
            if hashlib.sha256(archive.read(name.removeprefix('src/'))).hexdigest() != digest:
                raise RuntimeError('Calculation Release runtime source mismatch')
    return wheel


def pinned_wheel(lock, work):
    wheel = lock.get('wheel', {})
    encoded = os.environ.get(wheel.get('secret', ''), '')
    if not encoded:
        return None
    payload = base64.b64decode(encoded, validate=True)
    if hashlib.sha256(payload).hexdigest() != wheel['sha256']:
        raise RuntimeError('Calculation wheel differs from pinned SHA256')
    target = work / wheel['filename']
    target.write_bytes(payload)
    return target


def main():
    locks = json.loads((ROOT/'packaging/computation.json').read_text('utf-8'))
    with tempfile.TemporaryDirectory(prefix='flame-build-') as work:
        work = Path(work)
        sources = []
        for package, lock in locks.items():
            wheel = pinned_wheel(lock, work)
            if wheel is None and lock.get('release_tag'):
                wheel = release_wheel(package, lock, work)
            if wheel is not None:
                verify_wheel(package, lock, wheel)
                print(f'{package} {lock["version"]} commit={lock["commit"]} verified wheel', flush=True)
                sources.append(str(wheel))
                continue
            checkout = ROOT.parent/package
            if not (checkout/'.git').exists():
                checkout = work/(package+'-git')
                subprocess.run(['gh','repo','clone',lock['repository'],str(checkout),'--','--no-checkout'],check=True)
            commit = subprocess.check_output(['git','-C',str(checkout),'rev-parse',lock['commit']+'^{commit}'],text=True).strip()
            if commit != lock['commit']:
                raise RuntimeError('Calculation source commit mismatch')
            payload = subprocess.check_output(['git','-C',str(checkout),'archive','--format=zip',commit])
            source = work/package
            source.mkdir()
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                for member in archive.infolist():
                    target = (source/member.filename).resolve()
                    if not target.is_relative_to(source.resolve()) or (member.external_attr >> 16) & 0o170000 == 0o120000:
                        raise RuntimeError('Unsafe calculation archive')
                archive.extractall(source)
            metadata = tomllib.loads((source/'pyproject.toml').read_text('utf-8'))['project']
            if metadata['name'] != package or metadata['version'] != lock['version']:
                raise RuntimeError('Calculation package version mismatch')
            print(f'{package} {lock["version"]} commit={commit}',flush=True)
            sources.append(str(source))
        subprocess.run([sys.executable,'-m','pip','install','--force-reinstall',*sources],check=True)


if __name__ == '__main__':
    main()
