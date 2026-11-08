"""Package the exact development wheel with independent desktop/bootstrap files."""
import argparse
from pathlib import Path
from build_windows_bundle import build


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel',type=Path)
    parser.add_argument('--output',type=Path,default=Path('dist/Bactologix-Dev-Windows.zip'))
    args=parser.parse_args()
    if args.wheel is None:
        candidates=list(Path('dist').glob('bactologix-*.dev*-py3-none-any.whl'))
        if len(candidates)!=1:parser.error('Specify --wheel when dist does not contain exactly one development wheel.')
        args.wheel=candidates[0]
    from install_bactologix import bundled_dev_wheel
    import hashlib,json,tempfile,shutil
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp);shutil.copy2(args.wheel,root/args.wheel.name)
        manifest=root/'dev-package.json';manifest.write_text(json.dumps({'wheel':args.wheel.name,'sha256':hashlib.sha256(args.wheel.read_bytes()).hexdigest()}))
        bundled_dev_wheel(manifest)
    print(build(Path(__file__).resolve().parents[1],args.output,channel='dev',wheel=args.wheel))


if __name__=='__main__':main()
