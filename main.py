from __future__ import annotations

import argparse
import signal
import sys
import time

from lancache import LanCacheApplication, load_config
from lancache.app import AppStartupError
from lancache.config import save_config, set_cache_root_dir
from lancache.windows_runtime import ensure_pywin32
from lancache.windows_dns import WindowsDNSManager

# Application version. Update this when creating releases.
__version__ = "1.0 beta 2"

def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(description="Windows LAN Cache")
	parser.add_argument(
		"command",
		nargs="?",
		default="gui",
		choices=["run", "gui", "write-config", "write-dns-script", "apply-dns", "service"],
		help="open the GUI, run services, write the default config, manage Windows DNS integration, or manage the Windows service",
	)
	parser.add_argument(
		"service_action",
		nargs="?",
		default=None,
		choices=["install", "remove", "start", "stop", "restart", "status", "run"],
		help="service action to perform when command is 'service'",
	)
	parser.add_argument(
		"--config",
		default="config.json",
		help="Path to the JSON configuration file",
	)
	parser.add_argument(
		"--cache-dir",
		default=None,
		help="Override the cache storage directory for this run or when writing config",
	)
	return parser


def load_runtime_config(config_path: str, cache_dir: str | None):
	config = load_config(config_path)
	if cache_dir:
		set_cache_root_dir(config, cache_dir)
	return config


def run_console(config_path: str, cache_dir: str | None = None) -> None:
	config = load_runtime_config(config_path, cache_dir)
	app = LanCacheApplication(config, config_path)
	try:
		app.start()
	except AppStartupError as exc:
		raise SystemExit(str(exc)) from exc

	stop_requested = False

	def _handle_signal(signum, frame):
		del signum, frame
		nonlocal stop_requested
		stop_requested = True

	signal.signal(signal.SIGINT, _handle_signal)
	if hasattr(signal, "SIGTERM"):
		signal.signal(signal.SIGTERM, _handle_signal)

	try:
		while not stop_requested:
			time.sleep(1.0)
	finally:
		app.stop()


def run_gui(config_path: str, cache_dir: str | None = None) -> None:
	from lancache.gui import LanCacheGUI

	config = load_runtime_config(config_path, cache_dir)
	gui = LanCacheGUI(LanCacheApplication(config, config_path), version=__version__)
	gui.run()


def write_config(config_path: str, cache_dir: str | None = None) -> None:
	config = load_runtime_config(config_path, cache_dir)
	save_config(config, config_path)


def write_dns_script(config_path: str, cache_dir: str | None = None) -> None:
	config = load_runtime_config(config_path, cache_dir)
	path = WindowsDNSManager(config).export_script()
	print(path)


def apply_dns(config_path: str, cache_dir: str | None = None) -> None:
	config = load_runtime_config(config_path, cache_dir)
	result = WindowsDNSManager(config).apply()
	if result.stdout:
		print(result.stdout.strip())
	if result.returncode != 0:
		raise SystemExit(result.stderr.strip() or result.returncode)


def main() -> None:
	if sys.platform.startswith("win"):
		try:
			ensure_pywin32()
		except RuntimeError as exc:
			raise SystemExit(str(exc)) from exc

	args = build_parser().parse_args()
	if args.command == "run":
		run_console(args.config, args.cache_dir)
		return
	if args.command == "gui":
		run_gui(args.config, args.cache_dir)
		return
	if args.command == "write-dns-script":
		write_dns_script(args.config, args.cache_dir)
		return
	if args.command == "apply-dns":
		apply_dns(args.config, args.cache_dir)
		return
	if args.command == "service":
		from lancache.service import handle_service_command

		message = handle_service_command(args.service_action or "status", args.config, args.cache_dir)
		if message:
			print(message)
		return
	write_config(args.config, args.cache_dir)


if __name__ == "__main__":
	main()
