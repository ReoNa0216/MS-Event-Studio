"""Install both private calculation packages from exact, clean Git commits.

Local builds reuse sibling repositories via git archive (never their dirty
working trees). Hosted builds need authenticated gh access to both repositories.
No release is created and no credential is serialized.
"""
from pathlib import Path
import io
import json
import subprocess
import sys
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    locks = json.loads((ROOT/'packaging/computation.json').read_text('utf-8'))
    with tempfile.TemporaryDirectory(prefix='flame-build-') as work:
        work = Path(work)
        sources = []
        for package, lock in locks.items():
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
