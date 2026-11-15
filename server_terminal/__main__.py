"""Startet den Dienst: python -m server_terminal [--host 172.17.0.1] [--port 5938] [--allowed-host name]"""

import argparse
import sys
from pathlib import Path

from aiohttp import web

from server_terminal import __version__
from server_terminal.config import DEFAULT_HOST, DEFAULT_PORT, Config
from server_terminal.web import create_app

DEFAULT_DATA = Path(__file__).resolve().parents[1] / "data"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="server_terminal", description="Server-Dashboard mit Terminal")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Adresse, auf der der Dienst lauscht")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--allowed-host", action="append", default=[], help="Hostname, unter dem der Dienst erreichbar ist")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--no-geo", action="store_true", help="Keine Herkunft von IP-Adressen abfragen (ip-api.com)")
    parser.add_argument("--no-ssh-watch", action="store_true", help="SSH-Anmeldungen nicht beobachten")
    parser.add_argument("--no-tmux", action="store_true", help="Terminal ohne tmux (Sitzung endet mit dem Tab)")
    args = parser.parse_args(argv)

    args.data_dir.mkdir(parents=True, exist_ok=True)
    args.data_dir.chmod(0o700)
    config = Config(
        data_dir=args.data_dir,
        host=args.host,
        port=args.port,
        allowed_hosts=frozenset({"127.0.0.1", "localhost", *args.allowed_host}),
        persistent_terminal=not args.no_tmux,
        geo_enabled=not args.no_geo,
        watch_ssh=not args.no_ssh_watch,
    )
    app = create_app(config)
    state = app["app"]
    print(f"Server-Dashboard {__version__} auf http://{config.host}:{config.port}", flush=True)
    if state.setup_code:
        print(f"Einrichtungscode für den ersten Besuch: {state.setup_code}", flush=True)
    web.run_app(app, host=config.host, port=config.port, print=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
