"""Scaffold smoke test: the package imports cleanly (Phase 1 verification)."""


def test_package_imports() -> None:
    import simulation

    assert simulation.__version__
