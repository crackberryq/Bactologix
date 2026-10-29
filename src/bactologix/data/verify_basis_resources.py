"""Read-only native resource verification; never loads or executes models."""
from pathlib import Path
import sys

if __name__ == '__main__':
    root = Path(sys.argv[1])
    sys.path[:0] = [str(root / 'workflow/lib'), str(root / 'workflow/vendor')]
    from safety.resources import verify_receipt
    for path in sys.argv[2:]:
        verify_receipt(path, full=True)
