"""Build the Windows bootstrap from reviewed source and the app's own brand."""
from pathlib import Path
import zipfile


def build(root, destination, channel="stable", wheel=None):
    root, destination = Path(root), Path(destination)
    if channel not in {"stable", "dev"}: raise ValueError("Unknown channel")
    if channel == "dev" and not wheel: raise ValueError("Dev Windows bundle requires its pinned wheel")
    files = {p.name: p for p in (root / 'scripts/windows').glob('*') if p.suffix in {'.ps1', '.cmd', '.vbs'}}
    files.update({name: root / 'src/bactologix/ui' / name for name in ('brand.ico', 'brand.png')})
    files['install_bactologix.py'] = root / 'scripts/install_bactologix.py'
    files['README.txt'] = root / ('docs/development-install.txt' if channel == 'dev' else 'docs/windows-desktop.txt')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, path in sorted(files.items()):
            output_name = name.replace('Bactologix', 'Bactologix-Dev') if channel == 'dev' else name
            payload = path.read_bytes()
            if channel == 'dev' and path.suffix in {'.ps1','.cmd','.vbs'}:
                payload = payload.decode().replace('Bactologix','Bactologix-Dev').replace('bactologix.cli','bactologix.dev_cli').replace("'brand.ico','brand.png'", "'brand.ico','brand.png','dev-package.json','" + Path(wheel).name + "'").replace("'--install-conda','--upgrade-app'","'--dev','--install-conda','--upgrade-app'").encode()
            folder = 'Bactologix-Dev/' if channel == 'dev' else 'Bactologix/'
            info = zipfile.ZipInfo(folder + output_name, date_time=(2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, payload)
        if channel == 'dev' and wheel:
            import hashlib,json
            wheel=Path(wheel)
            content = wheel.read_bytes()
            extra = {wheel.name: content, 'dev-package.json': json.dumps({'wheel':wheel.name,'sha256':hashlib.sha256(content).hexdigest()}, sort_keys=True).encode('utf-8')}
            for name, content in sorted(extra.items()):
                info = zipfile.ZipInfo('Bactologix-Dev/' + name, date_time=(2026, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, content)
    return destination


if __name__ == '__main__':
    build(Path(__file__).resolve().parents[1], Path('dist/Bactologix-Windows.zip'))
