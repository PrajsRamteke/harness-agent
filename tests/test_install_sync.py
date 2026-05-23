"""Install sync helpers."""
from jarvis.install_sync import find_install_root, harness_agent_models_available


def test_find_install_root_from_dev_tree():
    root = find_install_root()
    assert root is not None
    assert (root / "jarvis" / "cli.py").is_file()
    assert (root / "pyproject.toml").is_file()


def test_harness_agent_models_available():
    assert harness_agent_models_available() is True
