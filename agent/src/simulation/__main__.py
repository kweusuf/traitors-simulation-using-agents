"""Allow `python -m simulation` entrypoint."""

from simulation.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
