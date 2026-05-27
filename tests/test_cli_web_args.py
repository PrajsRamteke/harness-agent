from jarvis.cli import _build_parser, _normalize_web_args


def _parse(argv: list[str]):
    return _build_parser().parse_args(_normalize_web_args(argv))


def test_web_flag_only():
    args = _parse(["--web"])
    assert args.web is True
    assert args.no_web is False
    assert args.no_browser is False
    assert args.web_port is None


def test_web_shorthand_port():
    args = _parse(["--web", "9000"])
    assert args.web is True
    assert args.no_web is False
    assert args.web_port == 9000


def test_web_port_flag_still_works():
    args = _parse(["--web", "--web-port", "8765"])
    assert args.web is True
    assert args.no_web is False
    assert args.web_port == 8765


def test_web_with_startup_prompt():
    args = _parse(["--web", "fix", "this", "bug"])
    assert args.web is True
    assert args.no_web is False
    assert args.web_port is None
    assert args.startup_prompt == ["fix", "this", "bug"]


def test_no_web_flag():
    args = _parse(["--no-web"])
    assert args.no_web is True
    assert args.web is False


def test_no_browser_flag():
    args = _parse(["--no-browser"])
    assert args.no_browser is True
    assert args.web is False


def test_normalize_web_args_expansion():
    assert _normalize_web_args(["--web", "9000"]) == ["--web", "--web-port", "9000"]
    assert _normalize_web_args(["--web", "hello"]) == ["--web", "hello"]
