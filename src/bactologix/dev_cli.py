"""Dedicated entry point; never repurpose a stable application environment."""
import os


def main():
    os.environ['BACTOLOGIX_CHANNEL'] = 'dev'
    from .cli import main as launch
    launch()


if __name__ == '__main__':
    main()
