#!/usr/bin/python3
"""Route compile and complete release builds to physically separate engines."""
import sys

def main():
    if any(arg == '--stage' or arg.startswith('--stage=') for arg in sys.argv[1:]):
        from build_compile import main as execute
    else:
        from build_release import main as execute
    return execute()

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Producer failed ('+type(error).__name__+'); no candidate is cleared for publication', file=sys.stderr)
        raise SystemExit(1) from None
