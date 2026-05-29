from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .app import LanCacheApplication
from .blocked_ips import BlockedIPRegistry
from .config import PlatformPolicy, SteamPreloadItem, config_from_dict, config_to_dict, default_platform_policies, set_cache_root_dir
from .preload import CachePreloader
from .service import handle_service_command
from .windows_dns import WindowsDNSManager


PLATFORM_PRESET_NAMES = ("steam", "epic", "blizzard")


class LanCacheGUI:
    def __init__(self, app: LanCacheApplication, version: str | None = None) -> None:
        self.app = app
        self.version = version
        self.root = tk.Tk()
        
        # Set window icon - works both in source and PyInstaller bundle
        if getattr(sys, 'frozen', False):
            # Running as PyInstaller bundle
            base_path = Path(sys._MEIPASS)
        else:
            # Running from source
            base_path = Path(__file__).parent.parent
        
        icon_path = base_path / "old joystick icon.ico"
        if icon_path.exists():
            try:
                self.root.iconbitmap(str(icon_path))
            except tk.TclError:
                pass  # Icon file format not supported on this platform
        
        self.root.geometry("1100x760")
        title = self._build_title_text(version=self.version)
        self.title_var = tk.StringVar(value=title)
        self.status_var = tk.StringVar(value="Stopped")
        self.message_var = tk.StringVar(value="Ready")
        self.cache_dir_var = tk.StringVar(value="")
        self.steamcmd_path_var = tk.StringVar(value="")
        self.blocked_ip_entry_var = tk.StringVar(value="")
        self.preload_name_var = tk.StringVar(value="")
        self.preload_app_id_var = tk.StringVar(value="")
        self.settings_dns_listen_host_var = tk.StringVar(value="")
        self.settings_dns_listen_port_var = tk.StringVar(value="")
        self.settings_dns_upstream_host_var = tk.StringVar(value="")
        self.settings_dns_upstream_port_var = tk.StringVar(value="")
        self.settings_network_interface_var = tk.StringVar(value="")
        self.settings_dns_cache_ipv4_var = tk.StringVar(value="")
        self.settings_proxy_listen_host_var = tk.StringVar(value="")
        self.settings_proxy_listen_port_var = tk.StringVar(value="")
        self.settings_cache_root_var = tk.StringVar(value="")
        self.settings_cache_metadata_db_var = tk.StringVar(value="")
        self.settings_cache_max_size_var = tk.StringVar(value="")
        self.settings_cache_eviction_target_var = tk.StringVar(value="")
        self.settings_logging_level_var = tk.StringVar(value="INFO")
        self.settings_logging_dir_var = tk.StringVar(value="")
        self.settings_logging_max_bytes_var = tk.StringVar(value="")
        self.settings_logging_backup_count_var = tk.StringVar(value="")
        self.settings_steamcmd_executable_path_var = tk.StringVar(value="")
        self.settings_steamcmd_username_var = tk.StringVar(value="")
        self.settings_steamcmd_password_var = tk.StringVar(value="")
        self.settings_steamcmd_download_root_var = tk.StringVar(value="")
        self.settings_steamcmd_validate_downloads_var = tk.BooleanVar(value=False)
        self.settings_platform_steam_enabled_var = tk.BooleanVar(value=False)
        self.settings_platform_epic_enabled_var = tk.BooleanVar(value=False)
        self.settings_platform_blizzard_enabled_var = tk.BooleanVar(value=False)
        self.settings_confirm_before_apply_var = tk.BooleanVar(value=False)
        self.settings_service_name_var = tk.StringVar(value="")
        self.settings_service_display_name_var = tk.StringVar(value="")
        self.settings_service_auto_start_var = tk.BooleanVar(value=True)
        self.settings_windows_dns_enabled_var = tk.BooleanVar(value=False)
        self.settings_windows_dns_server_host_var = tk.StringVar(value="")
        self.settings_windows_dns_apply_on_start_var = tk.BooleanVar(value=False)
        self.settings_windows_dns_script_path_var = tk.StringVar(value="")
        self._settings_sync_in_progress = False
        self._network_interfaces: list[dict[str, object]] = []
        self._network_interfaces_by_alias: dict[str, dict[str, object]] = {}
        self._network_display_to_alias: dict[str, str] = {}
        self._network_interface_combobox: ttk.Combobox | None = None
        self._refresh_network_interface_state(self.app.config)
        self.root.title(self.title_var.get())
        self._app_thread: threading.Thread | None = None
        self._preload_thread: threading.Thread | None = None
        self._preload_statuses: dict[str, str] = {}
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, textvariable=self.title_var, font=("Segoe UI", 18, "bold")).pack(anchor=tk.W)
        ttk.Label(frame, textvariable=self.status_var).pack(anchor=tk.W, pady=(4, 12))

        button_row = ttk.Frame(frame)
        button_row.pack(anchor=tk.W, pady=(0, 12))
        ttk.Button(button_row, text="Start", command=self.start_app).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(button_row, text="Stop", command=self.stop_app).pack(side=tk.LEFT)

        ttk.Label(frame, textvariable=self.message_var, foreground="#1f4b7a").pack(anchor=tk.W, pady=(0, 12))

        notebook = ttk.Notebook(frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        main_tab = ttk.Frame(notebook, padding=8)
        clients_tab = ttk.Frame(notebook, padding=8)
        blocked_ips_tab = ttk.Frame(notebook, padding=8)
        settings_tab = ttk.Frame(notebook, padding=8)
        config_tab = ttk.Frame(notebook, padding=8)
        preload_tab = ttk.Frame(notebook, padding=8)
        metrics_tab = ttk.Frame(notebook, padding=8)
        notebook.add(main_tab, text="Main")
        notebook.add(clients_tab, text="Steam Clients")
        notebook.add(blocked_ips_tab, text="Blocked IPs")
        notebook.add(config_tab, text="Config")
        notebook.add(preload_tab, text="Steam Preload")
        notebook.add(metrics_tab, text="Metrics")
        notebook.add(settings_tab, text="Settings")

        ttk.Label(main_tab, text="Downloaded Games", font=("Segoe UI", 12, "bold")).pack(anchor=tk.W)
        ttk.Label(
            main_tab,
            text="Shows Steam content tracked either by SteamCMD warmup or by client-discovered depot downloads, along with when it was first seen and how many cached client requests have been served.",
            wraplength=1000,
        ).pack(anchor=tk.W, pady=(4, 8))
        games_tree = ttk.Treeview(
            main_tab,
            columns=("name", "app_id", "installed", "downloads"),
            show="headings",
            height=16,
        )
        games_tree.heading("name", text="Game")
        games_tree.heading("app_id", text="App ID")
        games_tree.heading("installed", text="First Installed")
        games_tree.heading("downloads", text="Client Requests")
        games_tree.column("name", width=340, anchor=tk.W)
        games_tree.column("app_id", width=110, anchor=tk.CENTER)
        games_tree.column("installed", width=210, anchor=tk.CENTER)
        games_tree.column("downloads", width=150, anchor=tk.CENTER)
        games_tree.pack(fill=tk.BOTH, expand=True)
        self.games_tree = games_tree

        ttk.Label(clients_tab, text="Active Game Clients", font=("Segoe UI", 12, "bold")).pack(anchor=tk.W)
        ttk.Label(
            clients_tab,
            text="Shows client machines with recent game download DNS or proxy activity, including their IP address, traffic source, and how long they have been active in the current session window.",
            wraplength=1000,
        ).pack(anchor=tk.W, pady=(4, 8))
        clients_tree = ttk.Treeview(
            clients_tab,
            columns=("ip", "active_for", "last_seen", "requests", "sources", "host"),
            show="headings",
            height=16,
        )
        clients_tree.heading("ip", text="IP Address")
        clients_tree.heading("active_for", text="Active For")
        clients_tree.heading("last_seen", text="Last Seen")
        clients_tree.heading("requests", text="Requests")
        clients_tree.heading("sources", text="Source")
        clients_tree.heading("host", text="Last Host")
        clients_tree.column("ip", width=170, anchor=tk.W)
        clients_tree.column("active_for", width=120, anchor=tk.CENTER)
        clients_tree.column("last_seen", width=190, anchor=tk.CENTER)
        clients_tree.column("requests", width=90, anchor=tk.CENTER)
        clients_tree.column("sources", width=120, anchor=tk.CENTER)
        clients_tree.column("host", width=360, anchor=tk.W)
        clients_tree.pack(fill=tk.BOTH, expand=True)
        self.clients_tree = clients_tree

        self._build_blocked_ips_tab(blocked_ips_tab)

        self._build_settings_tab(settings_tab)

        ttk.Label(config_tab, text="config.json", font=("Segoe UI", 12, "bold")).pack(anchor=tk.W)
        ttk.Label(
            config_tab,
            text="Edit the JSON directly. Save writes to disk. Apply writes to disk, reloads the app configuration, and restarts services if they are running.",
            wraplength=1000,
        ).pack(anchor=tk.W, pady=(4, 8))
        config_box = tk.Text(config_tab, wrap=tk.NONE, undo=True)
        config_box.pack(fill=tk.BOTH, expand=True)
        self.config_box = config_box

        ttk.Label(preload_tab, text="Steam Cache Preload", font=("Segoe UI", 12, "bold")).pack(anchor=tk.W)
        ttk.Label(
            preload_tab,
            text=(
                "Add a game name and Steam app ID. Warmup runs SteamCMD with anonymous login by default and routes downloads through the local proxy "
                "so the cache can fill before clients request the game."
            ),
            wraplength=1000,
        ).pack(anchor=tk.W, pady=(4, 8))

        steamcmd_row = ttk.Frame(preload_tab)
        steamcmd_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(steamcmd_row, text="SteamCMD executable:").pack(side=tk.LEFT)
        ttk.Entry(steamcmd_row, textvariable=self.steamcmd_path_var, state="readonly").pack(
            side=tk.LEFT,
            fill=tk.X,
            expand=True,
            padx=(8, 8),
        )
        ttk.Button(steamcmd_row, text="Browse", command=self.choose_steamcmd_executable).pack(side=tk.LEFT)

        preload_form = ttk.Frame(preload_tab)
        preload_form.pack(fill=tk.X)
        ttk.Label(preload_form, text="Game name").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(preload_form, textvariable=self.preload_name_var).grid(row=0, column=1, sticky=tk.EW, padx=(8, 0))
        ttk.Label(preload_form, text="Steam App ID").grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        ttk.Entry(preload_form, textvariable=self.preload_app_id_var).grid(row=1, column=1, sticky=tk.EW, padx=(8, 0), pady=(8, 0))
        preload_form.columnconfigure(1, weight=1)

        preload_buttons = ttk.Frame(preload_tab)
        preload_buttons.pack(fill=tk.X, pady=(8, 8))
        ttk.Button(preload_buttons, text="Add Or Update Entry", command=self.upsert_preload_item).pack(
            side=tk.LEFT,
            padx=(0, 8),
        )
        ttk.Button(preload_buttons, text="Clear Form", command=self.clear_preload_form).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(preload_buttons, text="Remove Selected", command=self.remove_selected_preload_item).pack(side=tk.LEFT)

        warm_buttons = ttk.Frame(preload_tab)
        warm_buttons.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(warm_buttons, text="Warm Selected", command=self.warm_selected_preload).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(warm_buttons, text="Warm All", command=self.warm_all_preloads).pack(side=tk.LEFT)

        preload_tree = ttk.Treeview(preload_tab, columns=("name", "app_id", "status"), show="headings", height=10)
        preload_tree.heading("name", text="Game")
        preload_tree.heading("app_id", text="App ID")
        preload_tree.heading("status", text="Last Status")
        preload_tree.column("name", width=280, anchor=tk.W)
        preload_tree.column("app_id", width=120, anchor=tk.CENTER)
        preload_tree.column("status", width=520, anchor=tk.W)
        preload_tree.pack(fill=tk.BOTH, expand=True)
        preload_tree.bind("<<TreeviewSelect>>", self.on_preload_selection)
        self.preload_tree = preload_tree

        ttk.Label(preload_tab, text="Warmup log", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, pady=(8, 4))
        preload_log = tk.Text(preload_tab, height=8, wrap=tk.WORD)
        preload_log.pack(fill=tk.BOTH, expand=False)
        preload_log.configure(state=tk.DISABLED)
        self.preload_log = preload_log

        ttk.Label(metrics_tab, text="Runtime Metrics", font=("Segoe UI", 12, "bold")).pack(anchor=tk.W)
        metrics_box = tk.Text(metrics_tab, height=24, wrap=tk.NONE)
        metrics_box.pack(fill=tk.BOTH, expand=True)
        metrics_box.configure(state=tk.DISABLED)
        self.metrics_box = metrics_box

        for widget in (
            self.games_tree,
            self.clients_tree,
            self.blocked_ips_tree,
            self.config_box,
            self.preload_tree,
            self.preload_log,
            self.metrics_box,
        ):
            self._bind_widget_mousewheel(widget, self._build_yview_scroll_handler(widget))

        self.reload_config_text()
        self._refresh_games()
        self._refresh_steam_clients()
        self._refresh_metrics()

    @staticmethod
    def _build_title_text(ip_address: str | None = None, version: str | None = None) -> str:
        base_title = "Windows LAN Cache"
        if version:
            base_title = f"{base_title} {version}"
        if not ip_address or ip_address.startswith("127."):
            return base_title
        return f"{base_title} ({ip_address})"

    @staticmethod
    def _detect_active_local_ip_address() -> str | None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe_socket:
                probe_socket.connect(("8.8.8.8", 80))
                ip_address = probe_socket.getsockname()[0]
        except OSError:
            try:
                ip_address = socket.gethostbyname(socket.gethostname())
            except OSError:
                return None
        if not ip_address or ip_address.startswith("127."):
            return None
        return ip_address

    @staticmethod
    def _coerce_string_list(values) -> list[str]:
        if values is None:
            return []
        if isinstance(values, str):
            values = [values]
        return [str(value).strip() for value in values if str(value).strip()]

    @staticmethod
    def _load_windows_network_interfaces() -> list[dict[str, object]]:
        script = "\n".join(
            (
                "$ErrorActionPreference = 'Stop'",
                "Get-NetIPConfiguration | ForEach-Object {",
                "  [pscustomobject]@{",
                "    InterfaceAlias = $_.InterfaceAlias",
                "    InterfaceDescription = $_.InterfaceDescription",
                "    IPv4Addresses = @($_.IPv4Address | ForEach-Object { $_.IPAddress } | Where-Object { $_ })",
                "    DefaultGateways = @($_.IPv4DefaultGateway | ForEach-Object { $_.NextHop } | Where-Object { $_ })",
                "  }",
                "} | Where-Object { $_.InterfaceAlias -and $_.IPv4Addresses.Count -gt 0 } | ConvertTo-Json -Depth 4 -Compress",
            )
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return []

        if result.returncode != 0 or not result.stdout.strip():
            return []

        try:
            raw_interfaces = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []

        if isinstance(raw_interfaces, dict):
            raw_interfaces = [raw_interfaces]

        interfaces: list[dict[str, object]] = []
        for raw_interface in raw_interfaces:
            alias = str(raw_interface.get("InterfaceAlias") or "").strip()
            ipv4_addresses = LanCacheGUI._coerce_string_list(raw_interface.get("IPv4Addresses"))
            if not alias or not ipv4_addresses:
                continue
            interfaces.append(
                {
                    "alias": alias,
                    "description": str(raw_interface.get("InterfaceDescription") or "").strip(),
                    "ipv4_addresses": ipv4_addresses,
                    "default_gateways": LanCacheGUI._coerce_string_list(raw_interface.get("DefaultGateways")),
                }
            )
        return interfaces

    @staticmethod
    def _primary_interface_ip(interface: dict[str, object] | None) -> str | None:
        if not interface:
            return None
        ipv4_addresses = interface.get("ipv4_addresses")
        if not isinstance(ipv4_addresses, list) or not ipv4_addresses:
            return None
        primary_address = str(ipv4_addresses[0]).strip()
        return primary_address or None

    @classmethod
    def _detect_active_network_alias(cls, interfaces: list[dict[str, object]]) -> str | None:
        active_ip_address = cls._detect_active_local_ip_address()
        if active_ip_address:
            for interface in interfaces:
                ipv4_addresses = interface.get("ipv4_addresses")
                if isinstance(ipv4_addresses, list) and active_ip_address in ipv4_addresses:
                    return str(interface.get("alias") or "") or None

        for interface in interfaces:
            if cls._coerce_string_list(interface.get("default_gateways")):
                return str(interface.get("alias") or "") or None

        if not interfaces:
            return None
        return str(interfaces[0].get("alias") or "") or None

    @staticmethod
    def _format_network_interface_label(interface: dict[str, object], *, is_active: bool) -> str:
        alias = str(interface.get("alias") or "").strip()
        ip_address = LanCacheGUI._primary_interface_ip(interface)
        label = alias
        if ip_address:
            label = f"{label} - {ip_address}"
        if is_active:
            label = f"{label} [active]"
        return label

    def _get_selected_network_interface_alias(self) -> str | None:
        selected_display = self.settings_network_interface_var.get().strip()
        if not selected_display:
            return None
        return self._network_display_to_alias.get(selected_display)

    def _set_network_interface_selection(self, alias: str | None) -> None:
        if not self._network_display_to_alias:
            self.settings_network_interface_var.set("No IPv4 interfaces detected")
            return
        if not alias:
            self.settings_network_interface_var.set("")
            return
        for display_text, display_alias in self._network_display_to_alias.items():
            if display_alias == alias:
                self.settings_network_interface_var.set(display_text)
                return
        self.settings_network_interface_var.set("")

    def _resolve_network_interface_alias(self, config, active_alias: str | None, forced_alias: str | None = None) -> str | None:
        if forced_alias and forced_alias in self._network_interfaces_by_alias:
            return forced_alias

        preferred_alias = getattr(config.network, "preferred_interface_alias", None)
        if preferred_alias and preferred_alias in self._network_interfaces_by_alias:
            return preferred_alias

        current_alias = self._get_selected_network_interface_alias()
        if current_alias and current_alias in self._network_interfaces_by_alias:
            return current_alias

        if active_alias and active_alias in self._network_interfaces_by_alias:
            return active_alias

        if not self._network_interfaces:
            return None
        return str(self._network_interfaces[0].get("alias") or "") or None

    def _update_window_title(self, ip_address: str | None) -> None:
        title_text = self._build_title_text(ip_address, self.version)
        self.title_var.set(title_text)
        self.root.title(title_text)

    def _refresh_network_interface_state(self, config, *, forced_alias: str | None = None) -> None:
        self._network_interfaces = self._load_windows_network_interfaces()
        self._network_interfaces_by_alias = {
            str(interface.get("alias") or ""): interface for interface in self._network_interfaces
        }
        active_alias = self._detect_active_network_alias(self._network_interfaces)
        self._network_display_to_alias = {}
        display_values: list[str] = []
        for interface in self._network_interfaces:
            alias = str(interface.get("alias") or "")
            if not alias:
                continue
            display_text = self._format_network_interface_label(interface, is_active=alias == active_alias)
            self._network_display_to_alias[display_text] = alias
            display_values.append(display_text)

        if self._network_interface_combobox is not None:
            combobox_state = "readonly" if display_values else "disabled"
            self._network_interface_combobox.configure(values=tuple(display_values), state=combobox_state)

        selected_alias = self._resolve_network_interface_alias(config, active_alias, forced_alias)
        self._set_network_interface_selection(selected_alias)
        selected_interface = self._network_interfaces_by_alias.get(selected_alias) if selected_alias else None
        title_ip_address = self._primary_interface_ip(selected_interface) or config.dns.cache_ipv4
        self._update_window_title(title_ip_address)

    def _build_settings_tab(self, settings_tab: ttk.Frame) -> None:
        canvas = tk.Canvas(settings_tab, highlightthickness=0)
        scrollbar = ttk.Scrollbar(settings_tab, orient=tk.VERTICAL, command=canvas.yview)
        content = ttk.Frame(canvas, padding=(0, 0, 8, 8))
        window_id = canvas.create_window((0, 0), window=content, anchor=tk.NW)

        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        content.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window_id, width=event.width))

        ttk.Label(content, text="Settings", font=("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky=tk.W)
        ttk.Label(
            content,
            text="These controls update the same config used by the JSON editor. Save Config or Apply Changes when you want to persist them.",
            wraplength=980,
        ).grid(row=1, column=0, sticky=tk.W, pady=(4, 12))

        action_row = ttk.Frame(content)
        action_row.grid(row=2, column=0, sticky=tk.W, pady=(0, 12))
        ttk.Button(action_row, text="Save Config", command=self.save_config).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(action_row, text="Apply Changes", command=self.apply_changes).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(action_row, text="Reload From Disk", command=self.reload_config_text).pack(side=tk.LEFT)

        network_frame = ttk.LabelFrame(content, text="Network Basics", padding=12)
        network_frame.grid(row=3, column=0, sticky=tk.EW, pady=(0, 12))
        network_frame.columnconfigure(1, weight=1)
        network_frame.columnconfigure(3, weight=1)

        self._add_settings_entry(network_frame, 0, "DNS listen host", self.settings_dns_listen_host_var)
        self._add_settings_entry(network_frame, 0, "DNS listen port", self.settings_dns_listen_port_var, column=2)
        self._add_settings_entry(network_frame, 1, "Upstream DNS host", self.settings_dns_upstream_host_var)
        self._add_settings_entry(network_frame, 1, "Upstream DNS port", self.settings_dns_upstream_port_var, column=2)
        self._add_settings_entry(network_frame, 2, "Proxy listen host", self.settings_proxy_listen_host_var)
        self._add_settings_entry(network_frame, 2, "Proxy listen port", self.settings_proxy_listen_port_var, column=2)
        ttk.Label(network_frame, text="Ethernet NIC").grid(row=3, column=0, sticky=tk.W, padx=(0, 8), pady=(0, 8))
        self._network_interface_combobox = ttk.Combobox(
            network_frame,
            textvariable=self.settings_network_interface_var,
            state="readonly",
        )
        self._network_interface_combobox.grid(row=3, column=1, columnspan=3, sticky=tk.EW, pady=(0, 8))
        self._network_interface_combobox.bind("<<ComboboxSelected>>", self._on_network_interface_selected)
        self._add_settings_entry(network_frame, 4, "Cache IPv4", self.settings_dns_cache_ipv4_var)

        cache_frame = ttk.LabelFrame(content, text="Cache And Logging", padding=12)
        cache_frame.grid(row=4, column=0, sticky=tk.EW, pady=(0, 12))
        cache_frame.columnconfigure(1, weight=1)
        cache_frame.columnconfigure(3, weight=1)

        self._add_settings_entry(cache_frame, 0, "Cache directory", self.settings_cache_root_var)
        ttk.Button(cache_frame, text="Browse", command=self.choose_settings_cache_root).grid(row=0, column=4, sticky=tk.W, padx=(8, 0))
        self._add_settings_entry(cache_frame, 1, "Metadata DB path", self.settings_cache_metadata_db_var)
        self._add_settings_entry(cache_frame, 1, "Max size (GB)", self.settings_cache_max_size_var, column=2)
        self._add_settings_entry(cache_frame, 2, "Eviction target (%)", self.settings_cache_eviction_target_var)
        self._add_settings_combobox(
            cache_frame,
            3,
            "Log level",
            self.settings_logging_level_var,
            values=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        )
        self._add_settings_entry(cache_frame, 3, "Log folder", self.settings_logging_dir_var, column=2)
        ttk.Button(cache_frame, text="Browse", command=self.choose_settings_log_directory).grid(row=3, column=4, sticky=tk.W, padx=(8, 0))
        self._add_settings_entry(cache_frame, 4, "Max log file bytes", self.settings_logging_max_bytes_var)
        self._add_settings_entry(cache_frame, 4, "Backup files", self.settings_logging_backup_count_var, column=2)

        steamcmd_frame = ttk.LabelFrame(content, text="SteamCMD Defaults", padding=12)
        steamcmd_frame.grid(row=5, column=0, sticky=tk.EW, pady=(0, 12))
        steamcmd_frame.columnconfigure(1, weight=1)
        steamcmd_frame.columnconfigure(3, weight=1)

        self._add_settings_entry(steamcmd_frame, 0, "Executable path", self.settings_steamcmd_executable_path_var)
        ttk.Button(steamcmd_frame, text="Browse", command=self.choose_settings_steamcmd_executable).grid(row=0, column=4, sticky=tk.W, padx=(8, 0))
        self._add_settings_entry(steamcmd_frame, 1, "Username", self.settings_steamcmd_username_var)
        self._add_settings_entry(steamcmd_frame, 1, "Password", self.settings_steamcmd_password_var, column=2, show="*")
        self._add_settings_entry(steamcmd_frame, 2, "Download root", self.settings_steamcmd_download_root_var)
        ttk.Button(steamcmd_frame, text="Browse", command=self.choose_settings_steamcmd_download_root).grid(row=2, column=4, sticky=tk.W, padx=(8, 0))
        ttk.Checkbutton(
            steamcmd_frame,
            text="Validate downloads after app_update",
            variable=self.settings_steamcmd_validate_downloads_var,
            command=self._commit_settings_to_editor,
        ).grid(row=3, column=0, columnspan=4, sticky=tk.W, pady=(8, 0))

        platform_frame = ttk.LabelFrame(content, text="Platform Presets", padding=12)
        platform_frame.grid(row=6, column=0, sticky=tk.EW, pady=(0, 12))
        ttk.Label(
            platform_frame,
            text="Enable or disable the built-in Steam, Epic, and Blizzard policy groups without deleting their saved rules.",
            wraplength=980,
        ).grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 8))
        ttk.Checkbutton(
            platform_frame,
            text="Steam preset",
            variable=self.settings_platform_steam_enabled_var,
            command=self._commit_settings_to_editor,
        ).grid(row=1, column=0, sticky=tk.W)
        ttk.Checkbutton(
            platform_frame,
            text="Epic preset",
            variable=self.settings_platform_epic_enabled_var,
            command=self._commit_settings_to_editor,
        ).grid(row=1, column=1, sticky=tk.W, padx=(16, 0))
        ttk.Checkbutton(
            platform_frame,
            text="Blizzard preset",
            variable=self.settings_platform_blizzard_enabled_var,
            command=self._commit_settings_to_editor,
        ).grid(row=1, column=2, sticky=tk.W, padx=(16, 0))

        safety_frame = ttk.LabelFrame(content, text="Safety", padding=12)
        safety_frame.grid(row=7, column=0, sticky=tk.EW, pady=(0, 12))
        ttk.Checkbutton(
            safety_frame,
            text="Confirm before applying config changes",
            variable=self.settings_confirm_before_apply_var,
            command=self._commit_settings_to_editor,
        ).grid(row=0, column=0, sticky=tk.W)

        service_dns_frame = ttk.LabelFrame(content, text="Windows Service And DNS Integration", padding=12)
        service_dns_frame.grid(row=8, column=0, sticky=tk.EW)
        service_dns_frame.columnconfigure(1, weight=1)
        service_dns_frame.columnconfigure(3, weight=1)

        self._add_settings_entry(service_dns_frame, 0, "Service name", self.settings_service_name_var)
        self._add_settings_entry(service_dns_frame, 0, "Display name", self.settings_service_display_name_var, column=2)
        ttk.Checkbutton(
            service_dns_frame,
            text="Service auto-start",
            variable=self.settings_service_auto_start_var,
            command=self._commit_settings_to_editor,
        ).grid(row=1, column=0, sticky=tk.W, pady=(8, 0))

        service_buttons = ttk.Frame(service_dns_frame)
        service_buttons.grid(row=1, column=1, columnspan=3, sticky=tk.W, pady=(8, 0))
        ttk.Button(service_buttons, text="Install", command=lambda: self._run_service_action("install")).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(service_buttons, text="Start", command=lambda: self._run_service_action("start")).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(service_buttons, text="Stop", command=lambda: self._run_service_action("stop")).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(service_buttons, text="Status", command=lambda: self._run_service_action("status")).pack(side=tk.LEFT)

        ttk.Checkbutton(
            service_dns_frame,
            text="Enable Windows DNS integration",
            variable=self.settings_windows_dns_enabled_var,
            command=self._commit_settings_to_editor,
        ).grid(row=2, column=0, sticky=tk.W, pady=(12, 0))
        ttk.Checkbutton(
            service_dns_frame,
            text="Apply DNS changes on app start",
            variable=self.settings_windows_dns_apply_on_start_var,
            command=self._commit_settings_to_editor,
        ).grid(row=2, column=1, columnspan=3, sticky=tk.W, pady=(12, 0))

        self._add_settings_entry(service_dns_frame, 3, "DNS server host", self.settings_windows_dns_server_host_var)
        self._add_settings_entry(service_dns_frame, 4, "DNS script path", self.settings_windows_dns_script_path_var)
        ttk.Button(service_dns_frame, text="Browse", command=self.choose_windows_dns_script_path).grid(row=4, column=4, sticky=tk.W, padx=(8, 0))
        ttk.Button(service_dns_frame, text="Export DNS Script", command=self.export_windows_dns_script).grid(row=5, column=0, sticky=tk.W, pady=(12, 0))

        content.columnconfigure(0, weight=1)
        self._bind_mousewheel_recursively(settings_tab, self._build_yview_scroll_handler(canvas))

    def _build_blocked_ips_tab(self, blocked_ips_tab: ttk.Frame) -> None:
        ttk.Label(blocked_ips_tab, text="Blocked Client IP Addresses", font=("Segoe UI", 12, "bold")).pack(anchor=tk.W)
        ttk.Label(
            blocked_ips_tab,
            text="Blocked clients are dropped at the DNS and proxy server boundary, so their traffic is not forwarded and does not appear in the runtime request logs.",
            wraplength=1000,
        ).pack(anchor=tk.W, pady=(4, 8))

        form_row = ttk.Frame(blocked_ips_tab)
        form_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(form_row, text="IP address").pack(side=tk.LEFT)
        blocked_ip_entry = ttk.Entry(form_row, textvariable=self.blocked_ip_entry_var)
        blocked_ip_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8))
        blocked_ip_entry.bind("<Return>", self.add_blocked_ip)
        ttk.Button(form_row, text="Add", command=self.add_blocked_ip).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(form_row, text="Remove Selected", command=self.remove_selected_blocked_ip).pack(side=tk.LEFT)

        blocked_ips_tree = ttk.Treeview(blocked_ips_tab, columns=("ip",), show="headings", height=16)
        blocked_ips_tree.heading("ip", text="Blocked IP")
        blocked_ips_tree.column("ip", width=280, anchor=tk.W)
        blocked_ips_tree.pack(fill=tk.BOTH, expand=True)
        self.blocked_ips_tree = blocked_ips_tree

    def _add_settings_entry(
        self,
        parent: ttk.LabelFrame,
        row: int,
        label: str,
        variable: tk.StringVar,
        *,
        column: int = 0,
        show: str | None = None,
    ) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky=tk.W, padx=(0, 8), pady=(0, 8))
        entry_options = {"textvariable": variable}
        if show is not None:
            entry_options["show"] = show
        entry = ttk.Entry(parent, **entry_options)
        entry.grid(row=row, column=column + 1, sticky=tk.EW, pady=(0, 8))
        entry.bind("<FocusOut>", self._commit_settings_to_editor)
        entry.bind("<Return>", self._commit_settings_to_editor)
        return entry

    def _add_settings_combobox(
        self,
        parent: ttk.LabelFrame,
        row: int,
        label: str,
        variable: tk.StringVar,
        *,
        values: tuple[str, ...],
        column: int = 0,
    ) -> ttk.Combobox:
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky=tk.W, padx=(0, 8), pady=(0, 8))
        combobox = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly")
        combobox.grid(row=row, column=column + 1, sticky=tk.EW, pady=(0, 8))
        combobox.bind("<<ComboboxSelected>>", self._commit_settings_to_editor)
        return combobox

    @staticmethod
    def _mousewheel_units(event) -> int:
        if getattr(event, "num", None) == 4:
            return -1
        if getattr(event, "num", None) == 5:
            return 1
        delta = getattr(event, "delta", 0)
        if delta == 0:
            return 0
        return -int(delta / 120) if delta % 120 == 0 else (-1 if delta > 0 else 1)

    def _build_yview_scroll_handler(self, widget):
        def _handler(event):
            units = self._mousewheel_units(event)
            if units:
                widget.yview_scroll(units, "units")
                return "break"
            return None

        return _handler

    def _bind_widget_mousewheel(self, widget, handler) -> None:
        widget.bind("<MouseWheel>", handler, add=True)
        widget.bind("<Button-4>", handler, add=True)
        widget.bind("<Button-5>", handler, add=True)

    def _bind_mousewheel_recursively(self, root_widget, handler) -> None:
        self._bind_widget_mousewheel(root_widget, handler)
        for child in root_widget.winfo_children():
            self._bind_mousewheel_recursively(child, handler)

    def _selected_blocked_ip(self) -> str | None:
        selection = self.blocked_ips_tree.selection()
        if not selection:
            return None
        item = self.blocked_ips_tree.item(selection[0])
        values = item.get("values") or []
        if not values:
            return None
        return str(values[0])

    def _refresh_blocked_ips(self, blocked_ips: list[str]) -> None:
        selected_ip = self._selected_blocked_ip()
        self.blocked_ips_tree.delete(*self.blocked_ips_tree.get_children())
        selected_item_id = None
        for blocked_ip in blocked_ips:
            item_id = self.blocked_ips_tree.insert("", tk.END, values=(blocked_ip,))
            if blocked_ip == selected_ip:
                selected_item_id = item_id
        if selected_item_id is not None:
            self.blocked_ips_tree.selection_set(selected_item_id)

    def _apply_blocked_ip_changes(self, config) -> None:
        blocked_ips = sorted(config.blocked_ips.blocked_ips)
        config.blocked_ips.blocked_ips = blocked_ips
        self.app.config.blocked_ips.blocked_ips = list(blocked_ips)
        self.app.blocked_ips.replace_all(blocked_ips)
        self.app.save_config(config)

    def add_blocked_ip(self, event=None):
        del event
        raw_ip = self.blocked_ip_entry_var.get()
        try:
            normalized_ip = BlockedIPRegistry.normalize_ip(raw_ip)
            config = self._parse_editor_config()
        except Exception as exc:
            messagebox.showerror("Blocked IPs", str(exc))
            return "break"

        blocked_ips = set(config.blocked_ips.blocked_ips)
        if normalized_ip in blocked_ips:
            self.message_var.set(f"{normalized_ip} is already blocked")
            return "break"

        blocked_ips.add(normalized_ip)
        config.blocked_ips.blocked_ips = sorted(blocked_ips)
        try:
            self._apply_blocked_ip_changes(config)
        except Exception as exc:
            messagebox.showerror("Blocked IPs", str(exc))
            return "break"
        self._set_editor_config(config)
        self.blocked_ip_entry_var.set("")
        self.message_var.set(f"Blocked {normalized_ip}")
        return "break"

    def remove_selected_blocked_ip(self) -> None:
        selected_ip = self._selected_blocked_ip()
        if not selected_ip:
            messagebox.showerror("Blocked IPs", "Select an IP address to remove")
            return
        try:
            normalized_ip = BlockedIPRegistry.normalize_ip(selected_ip)
            config = self._parse_editor_config()
        except Exception as exc:
            messagebox.showerror("Blocked IPs", str(exc))
            return

        config.blocked_ips.blocked_ips = [
            blocked_ip
            for blocked_ip in config.blocked_ips.blocked_ips
            if blocked_ip != normalized_ip
        ]
        try:
            self._apply_blocked_ip_changes(config)
        except Exception as exc:
            messagebox.showerror("Blocked IPs", str(exc))
            return
        self._set_editor_config(config)
        self.message_var.set(f"Unblocked {normalized_ip}")

    def _parse_int_setting(self, label: str, raw_value: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
        value_text = raw_value.strip()
        if not value_text:
            raise ValueError(f"{label} is required")
        try:
            value = int(value_text)
        except ValueError as exc:
            raise ValueError(f"{label} must be an integer") from exc
        if minimum is not None and value < minimum:
            raise ValueError(f"{label} must be at least {minimum}")
        if maximum is not None and value > maximum:
            raise ValueError(f"{label} must be at most {maximum}")
        return value

    def _apply_settings_to_config(self, config) -> None:
        config.dns.listen_host = self.settings_dns_listen_host_var.get().strip()
        config.dns.listen_port = self._parse_int_setting("DNS listen port", self.settings_dns_listen_port_var.get(), minimum=1, maximum=65535)
        config.dns.upstream_host = self.settings_dns_upstream_host_var.get().strip()
        config.dns.upstream_port = self._parse_int_setting("Upstream DNS port", self.settings_dns_upstream_port_var.get(), minimum=1, maximum=65535)
        config.network.preferred_interface_alias = self._get_selected_network_interface_alias()
        config.dns.cache_ipv4 = self.settings_dns_cache_ipv4_var.get().strip()
        config.proxy.listen_host = self.settings_proxy_listen_host_var.get().strip()
        config.proxy.listen_port = self._parse_int_setting("Proxy listen port", self.settings_proxy_listen_port_var.get(), minimum=1, maximum=65535)

        cache_root = self.settings_cache_root_var.get().strip()
        metadata_db = self.settings_cache_metadata_db_var.get().strip()
        log_dir = self.settings_logging_dir_var.get().strip()
        steamcmd_executable_path = self.settings_steamcmd_executable_path_var.get().strip()
        steamcmd_username = self.settings_steamcmd_username_var.get().strip()
        steamcmd_password = self.settings_steamcmd_password_var.get()
        steamcmd_download_root = self.settings_steamcmd_download_root_var.get().strip()
        service_name = self.settings_service_name_var.get().strip()
        service_display_name = self.settings_service_display_name_var.get().strip()
        dns_server_host = self.settings_windows_dns_server_host_var.get().strip()
        dns_script_path = self.settings_windows_dns_script_path_var.get().strip()

        if not config.dns.listen_host:
            raise ValueError("DNS listen host is required")
        if not config.dns.upstream_host:
            raise ValueError("Upstream DNS host is required")
        if not config.dns.cache_ipv4:
            raise ValueError("Cache IPv4 is required")
        if not config.proxy.listen_host:
            raise ValueError("Proxy listen host is required")
        if not cache_root:
            raise ValueError("Cache directory is required")
        if not metadata_db:
            raise ValueError("Metadata DB path is required")
        if not log_dir:
            raise ValueError("Log folder is required")
        if not steamcmd_executable_path:
            raise ValueError("SteamCMD executable path is required")
        if not steamcmd_username:
            raise ValueError("SteamCMD username is required")
        if not steamcmd_download_root:
            raise ValueError("SteamCMD download root is required")
        if not service_name:
            raise ValueError("Service name is required")
        if not service_display_name:
            raise ValueError("Service display name is required")
        if not dns_server_host:
            raise ValueError("DNS server host is required")
        if not dns_script_path:
            raise ValueError("DNS script path is required")

        config.cache.root_dir = cache_root
        config.cache.metadata_db = metadata_db
        config.cache.max_size_gb = self._parse_int_setting("Cache max size", self.settings_cache_max_size_var.get(), minimum=1)
        config.cache.eviction_target_percent = self._parse_int_setting(
            "Cache eviction target",
            self.settings_cache_eviction_target_var.get(),
            minimum=1,
            maximum=100,
        )
        config.logging.level = self.settings_logging_level_var.get().strip().upper()
        config.logging.log_dir = log_dir
        config.logging.max_bytes = self._parse_int_setting("Max log file bytes", self.settings_logging_max_bytes_var.get(), minimum=1)
        config.logging.backup_count = self._parse_int_setting("Backup files", self.settings_logging_backup_count_var.get(), minimum=0)
        config.steamcmd.executable_path = steamcmd_executable_path
        config.steamcmd.username = steamcmd_username
        config.steamcmd.password = steamcmd_password if steamcmd_password else None
        config.steamcmd.download_root = steamcmd_download_root
        config.steamcmd.validate_downloads = bool(self.settings_steamcmd_validate_downloads_var.get())
        self._apply_platform_preset_settings(config)
        config.safety.confirm_before_apply = bool(self.settings_confirm_before_apply_var.get())
        config.service.service_name = service_name
        config.service.display_name = service_display_name
        config.service.auto_start = bool(self.settings_service_auto_start_var.get())
        config.windows_dns.enabled = bool(self.settings_windows_dns_enabled_var.get())
        config.windows_dns.server_host = dns_server_host
        config.windows_dns.apply_on_start = bool(self.settings_windows_dns_apply_on_start_var.get())
        config.windows_dns.script_path = dns_script_path

    def _apply_platform_preset_settings(self, config) -> None:
        desired_enabled = {
            "steam": bool(self.settings_platform_steam_enabled_var.get()),
            "epic": bool(self.settings_platform_epic_enabled_var.get()),
            "blizzard": bool(self.settings_platform_blizzard_enabled_var.get()),
        }
        default_policy_map = {policy.name: policy for policy in default_platform_policies()}
        policy_map = {policy.name: policy for policy in config.platform_policies}
        legacy_policy = next((policy for policy in config.platform_policies if policy.name == "legacy"), None)

        if legacy_policy is not None:
            for name in PLATFORM_PRESET_NAMES:
                if name in policy_map:
                    continue
                default_policy = default_policy_map[name]
                if self._legacy_policy_covers_default_policy(legacy_policy, default_policy):
                    preset_policy = self._clone_platform_policy(default_policy)
                    preset_policy.enabled = desired_enabled[name]
                    config.platform_policies.append(preset_policy)
                    policy_map[name] = preset_policy
            self._remove_default_patterns_from_legacy_policy(legacy_policy, tuple(default_policy_map[name] for name in PLATFORM_PRESET_NAMES))
            if not any(
                (
                    legacy_policy.dns_rewrite_patterns,
                    legacy_policy.cacheable_http_patterns,
                    legacy_policy.passthrough_patterns,
                    legacy_policy.https_only_patterns,
                )
            ):
                config.platform_policies = [policy for policy in config.platform_policies if policy is not legacy_policy]
                policy_map.pop("legacy", None)

        for name in PLATFORM_PRESET_NAMES:
            policy = policy_map.get(name)
            if policy is None:
                if desired_enabled[name]:
                    config.platform_policies.append(self._clone_platform_policy(default_policy_map[name]))
                continue
            policy.enabled = desired_enabled[name]

    @staticmethod
    def _clone_platform_policy(policy: PlatformPolicy) -> PlatformPolicy:
        return PlatformPolicy(
            name=policy.name,
            enabled=policy.enabled,
            dns_rewrite_patterns=list(policy.dns_rewrite_patterns),
            cacheable_http_patterns=list(policy.cacheable_http_patterns),
            passthrough_patterns=list(policy.passthrough_patterns),
            https_only_patterns=list(policy.https_only_patterns),
        )

    @staticmethod
    def _legacy_policy_covers_default_policy(legacy_policy: PlatformPolicy, default_policy: PlatformPolicy) -> bool:
        for attribute in ("dns_rewrite_patterns", "cacheable_http_patterns", "passthrough_patterns", "https_only_patterns"):
            legacy_patterns = {pattern.lower() for pattern in getattr(legacy_policy, attribute)}
            default_patterns = {pattern.lower() for pattern in getattr(default_policy, attribute)}
            if legacy_patterns & default_patterns:
                return True
        return False

    @staticmethod
    def _remove_default_patterns_from_legacy_policy(legacy_policy: PlatformPolicy, default_policies: tuple[PlatformPolicy, ...]) -> None:
        for attribute in ("dns_rewrite_patterns", "cacheable_http_patterns", "passthrough_patterns", "https_only_patterns"):
            default_patterns = {
                pattern.lower()
                for policy in default_policies
                for pattern in getattr(policy, attribute)
            }
            filtered_patterns = [
                pattern
                for pattern in getattr(legacy_policy, attribute)
                if pattern.lower() not in default_patterns
            ]
            setattr(legacy_policy, attribute, filtered_patterns)

    def _commit_settings_to_editor(self, event=None):
        del event
        if self._settings_sync_in_progress:
            return None
        try:
            config = self._parse_editor_config()
            self._apply_settings_to_config(config)
            self._set_editor_config(config)
        except Exception as exc:
            messagebox.showerror("Settings", str(exc))
            self._set_editor_config(self.app.load_config_from_disk())
            return "break"
        return None

    def _on_network_interface_selected(self, event=None):
        del event
        try:
            config = self._parse_editor_config()
        except Exception:
            config = self.app.config

        selected_alias = self._get_selected_network_interface_alias()
        self._refresh_network_interface_state(config, forced_alias=selected_alias)
        selected_interface = self._network_interfaces_by_alias.get(selected_alias) if selected_alias else None
        selected_ip_address = self._primary_interface_ip(selected_interface)
        if selected_alias and not selected_ip_address:
            messagebox.showerror("Network Interface", f"No IPv4 address is currently assigned to {selected_alias}")
            return "break"
        if selected_ip_address:
            self.settings_dns_cache_ipv4_var.set(selected_ip_address)
            self._update_window_title(selected_ip_address)
        return self._commit_settings_to_editor()

    def choose_settings_cache_root(self) -> None:
        current_dir = self.settings_cache_root_var.get().strip() or self.app.config.cache.root_dir
        selected_dir = filedialog.askdirectory(initialdir=current_dir)
        if not selected_dir:
            return
        self.settings_cache_root_var.set(selected_dir)
        if not self.settings_cache_metadata_db_var.get().strip():
            self.settings_cache_metadata_db_var.set(f"{selected_dir}\\metadata.sqlite3")
        self._commit_settings_to_editor()

    def choose_settings_log_directory(self) -> None:
        current_dir = self.settings_logging_dir_var.get().strip() or self.app.config.logging.log_dir
        selected_dir = filedialog.askdirectory(initialdir=current_dir)
        if not selected_dir:
            return
        self.settings_logging_dir_var.set(selected_dir)
        self._commit_settings_to_editor()

    def choose_settings_steamcmd_executable(self) -> None:
        selected_path = filedialog.askopenfilename(
            title="Select SteamCMD executable",
            filetypes=[("SteamCMD", "steamcmd.exe"), ("Executables", "*.exe"), ("All files", "*.*")],
        )
        if not selected_path:
            return
        self.settings_steamcmd_executable_path_var.set(selected_path)
        self._commit_settings_to_editor()

    def choose_settings_steamcmd_download_root(self) -> None:
        current_dir = self.settings_steamcmd_download_root_var.get().strip() or self.app.config.steamcmd.download_root
        selected_dir = filedialog.askdirectory(initialdir=current_dir)
        if not selected_dir:
            return
        self.settings_steamcmd_download_root_var.set(selected_dir)
        self._commit_settings_to_editor()

    def choose_windows_dns_script_path(self) -> None:
        selected_path = filedialog.asksaveasfilename(
            title="Select Windows DNS script path",
            defaultextension=".ps1",
            filetypes=[("PowerShell", "*.ps1"), ("All files", "*.*")],
            initialfile="windows-dns-server.ps1",
        )
        if not selected_path:
            return
        self.settings_windows_dns_script_path_var.set(selected_path)
        self._commit_settings_to_editor()

    def _prepare_settings_config(self):
        config = self._parse_editor_config()
        self._apply_settings_to_config(config)
        self._set_editor_config(config)
        return config

    def _run_service_action(self, action: str) -> None:
        try:
            config = self._prepare_settings_config()
            self.app.save_config(config)
            message = handle_service_command(action, self.app.config_path)
        except Exception as exc:
            messagebox.showerror("Service", str(exc))
            return
        if message:
            self.message_var.set(message)
            messagebox.showinfo("Service", message)

    def export_windows_dns_script(self) -> None:
        try:
            config = self._prepare_settings_config()
            path = WindowsDNSManager(config).export_script()
        except Exception as exc:
            messagebox.showerror("Windows DNS", str(exc))
            return
        message = f"Windows DNS script exported to {path}"
        self.message_var.set(message)
        messagebox.showinfo("Windows DNS", message)

    def start_app(self) -> None:
        if self.app.is_running:
            return
        if self._app_thread and self._app_thread.is_alive():
            return
        self.status_var.set("Starting")
        self.message_var.set("Starting services")
        self._app_thread = threading.Thread(target=self._start_app_async, name="gui-app-start", daemon=True)
        self._app_thread.start()

    def _start_app_async(self) -> None:
        try:
            self.app.start()
        except Exception as exc:
            self.root.after(0, self._handle_start_failure, str(exc))
            return
        self.root.after(0, self._handle_start_success)

    def _handle_start_success(self) -> None:
        self.status_var.set("Running" if self.app.is_running else "Stopped")
        self.message_var.set("Services started")

    def _handle_start_failure(self, message: str) -> None:
        self.status_var.set("Stopped")
        self.message_var.set("Service start failed")
        messagebox.showerror("Start Failed", message)

    def stop_app(self) -> None:
        if not self.app.is_running:
            self.status_var.set("Stopped")
            return
        self.app.stop()
        self.status_var.set("Stopped")
        self.message_var.set("Services stopped")

    def reload_config_text(self) -> None:
        self._set_editor_config(self.app.load_config_from_disk())
        self.message_var.set("Config reloaded from disk")

    def _parse_editor_config(self):
        raw_text = self.config_box.get("1.0", tk.END).strip()
        if not raw_text:
            raise ValueError("Config editor is empty")
        try:
            raw_config = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
        return config_from_dict(raw_config)

    def _set_editor_config(self, config) -> None:
        config_text = json.dumps(config_to_dict(config), indent=2)
        self.config_box.delete("1.0", tk.END)
        self.config_box.insert(tk.END, config_text)
        self._sync_form_state(config)

    def _sync_form_state(self, config) -> None:
        self._settings_sync_in_progress = True
        self.settings_dns_listen_host_var.set(config.dns.listen_host)
        self.settings_dns_listen_port_var.set(str(config.dns.listen_port))
        self.settings_dns_upstream_host_var.set(config.dns.upstream_host)
        self.settings_dns_upstream_port_var.set(str(config.dns.upstream_port))
        self.settings_dns_cache_ipv4_var.set(config.dns.cache_ipv4)
        self._refresh_network_interface_state(config)
        self.settings_proxy_listen_host_var.set(config.proxy.listen_host)
        self.settings_proxy_listen_port_var.set(str(config.proxy.listen_port))
        self.settings_cache_root_var.set(config.cache.root_dir)
        self.cache_dir_var.set(config.cache.root_dir)
        self.settings_cache_metadata_db_var.set(config.cache.metadata_db)
        self.settings_cache_max_size_var.set(str(config.cache.max_size_gb))
        self.settings_cache_eviction_target_var.set(str(config.cache.eviction_target_percent))
        self.settings_logging_level_var.set(config.logging.level)
        self.settings_logging_dir_var.set(config.logging.log_dir)
        self.settings_logging_max_bytes_var.set(str(config.logging.max_bytes))
        self.settings_logging_backup_count_var.set(str(config.logging.backup_count))
        self.settings_steamcmd_executable_path_var.set(config.steamcmd.executable_path)
        self.settings_steamcmd_username_var.set(config.steamcmd.username)
        self.settings_steamcmd_password_var.set(config.steamcmd.password or "")
        self.settings_steamcmd_download_root_var.set(config.steamcmd.download_root)
        self.settings_steamcmd_validate_downloads_var.set(config.steamcmd.validate_downloads)
        default_policy_map = {policy.name: policy for policy in default_platform_policies()}
        platform_policy_map = {policy.name: policy for policy in config.platform_policies}
        legacy_policy = platform_policy_map.get("legacy")
        self.settings_platform_steam_enabled_var.set(
            self._platform_preset_enabled(platform_policy_map, legacy_policy, default_policy_map["steam"])
        )
        self.settings_platform_epic_enabled_var.set(
            self._platform_preset_enabled(platform_policy_map, legacy_policy, default_policy_map["epic"])
        )
        self.settings_platform_blizzard_enabled_var.set(
            self._platform_preset_enabled(platform_policy_map, legacy_policy, default_policy_map["blizzard"])
        )
        self.settings_confirm_before_apply_var.set(config.safety.confirm_before_apply)
        self.settings_service_name_var.set(config.service.service_name)
        self.settings_service_display_name_var.set(config.service.display_name)
        self.settings_service_auto_start_var.set(config.service.auto_start)
        self.settings_windows_dns_enabled_var.set(config.windows_dns.enabled)
        self.settings_windows_dns_server_host_var.set(config.windows_dns.server_host)
        self.settings_windows_dns_apply_on_start_var.set(config.windows_dns.apply_on_start)
        self.settings_windows_dns_script_path_var.set(config.windows_dns.script_path)
        self.blocked_ip_entry_var.set("")
        self._refresh_blocked_ips(config.blocked_ips.blocked_ips)
        self.steamcmd_path_var.set(config.steamcmd.executable_path)
        selected_name = self._selected_preload_name()
        self.preload_tree.delete(*self.preload_tree.get_children())
        for item in config.steam_preload_items:
            self.preload_tree.insert(
                "",
                tk.END,
                iid=item.name,
                values=(item.name, item.app_id if item.app_id is not None else "", self._preload_statuses.get(item.name, "Ready")),
            )
        if selected_name and selected_name in {item.name for item in config.steam_preload_items}:
            self.preload_tree.selection_set(selected_name)
        self._settings_sync_in_progress = False

    def _platform_preset_enabled(
        self,
        platform_policy_map: dict[str, PlatformPolicy],
        legacy_policy: PlatformPolicy | None,
        default_policy: PlatformPolicy,
    ) -> bool:
        policy = platform_policy_map.get(default_policy.name)
        if policy is not None:
            return policy.enabled
        if legacy_policy is not None:
            return self._legacy_policy_covers_default_policy(legacy_policy, default_policy)
        return False

    def choose_steamcmd_executable(self) -> None:
        selected_path = filedialog.askopenfilename(
            title="Select SteamCMD executable",
            filetypes=[("SteamCMD", "steamcmd.exe"), ("Executables", "*.exe"), ("All files", "*.*")],
        )
        if not selected_path:
            return
        try:
            config = self._parse_editor_config()
            config.steamcmd.executable_path = selected_path
            self._set_editor_config(config)
        except Exception as exc:
            messagebox.showerror("SteamCMD", str(exc))
            return
        self.message_var.set(f"SteamCMD executable set to {selected_path}")

    def choose_cache_directory(self) -> None:
        current_dir = self.cache_dir_var.get().strip() or self.app.config.cache.root_dir
        selected_dir = filedialog.askdirectory(initialdir=current_dir)
        if not selected_dir:
            return
        try:
            config = self._parse_editor_config()
            set_cache_root_dir(config, selected_dir)
            self._set_editor_config(config)
        except Exception as exc:
            messagebox.showerror("Cache Directory", str(exc))
            return
        self.message_var.set(f"Cache directory set to {selected_dir}")

    def upsert_preload_item(self) -> None:
        try:
            config = self._parse_editor_config()
        except Exception as exc:
            messagebox.showerror("Steam Preload", str(exc))
            return

        name = self.preload_name_var.get().strip()
        app_id_text = self.preload_app_id_var.get().strip()
        if not name:
            messagebox.showerror("Steam Preload", "Game name is required")
            return
        if not app_id_text:
            messagebox.showerror("Steam Preload", "Steam app ID is required")
            return
        try:
            app_id = int(app_id_text)
        except ValueError:
            messagebox.showerror("Steam Preload", "Steam app ID must be a number")
            return
        if app_id <= 0:
            messagebox.showerror("Steam Preload", "Steam app ID must be a positive integer")
            return

        item = SteamPreloadItem(name=name, app_id=app_id)
        updated_items: list[SteamPreloadItem] = []
        replaced = False
        for existing_item in config.steam_preload_items:
            if existing_item.name == name:
                updated_items.append(item)
                replaced = True
            else:
                updated_items.append(existing_item)
        if not replaced:
            updated_items.append(item)
        config.steam_preload_items = updated_items
        self._preload_statuses.setdefault(name, f"Saved (App {app_id})")
        self._set_editor_config(config)
        self.preload_tree.selection_set(name)
        self.message_var.set(f"Steam preload entry saved for {name}")

    def clear_preload_form(self) -> None:
        self.preload_name_var.set("")
        self.preload_app_id_var.set("")

    def remove_selected_preload_item(self) -> None:
        selected_name = self._selected_preload_name()
        if not selected_name:
            messagebox.showerror("Steam Preload", "Select a preload entry first")
            return
        try:
            config = self._parse_editor_config()
        except Exception as exc:
            messagebox.showerror("Steam Preload", str(exc))
            return

        config.steam_preload_items = [item for item in config.steam_preload_items if item.name != selected_name]
        self._preload_statuses.pop(selected_name, None)
        self._set_editor_config(config)
        self.clear_preload_form()
        self.message_var.set(f"Removed preload entry for {selected_name}")

    def on_preload_selection(self, event=None) -> None:
        del event
        selected_name = self._selected_preload_name()
        if not selected_name:
            return
        try:
            config = self._parse_editor_config()
        except Exception:
            return
        for item in config.steam_preload_items:
            if item.name == selected_name:
                self.preload_name_var.set(item.name)
                self.preload_app_id_var.set(str(item.app_id or ""))
                break

    def warm_selected_preload(self) -> None:
        selected_name = self._selected_preload_name()
        if not selected_name:
            messagebox.showerror("Steam Preload", "Select a preload entry first")
            return
        self._start_preload([selected_name])

    def warm_all_preloads(self) -> None:
        self._start_preload(None)

    def _start_preload(self, selected_names: list[str] | None) -> None:
        if self._preload_thread and self._preload_thread.is_alive():
            messagebox.showinfo("Steam Preload", "A warmup job is already running")
            return
        if not self.app.is_running:
            messagebox.showerror("Steam Preload", "Start services first so SteamCMD can route downloads through the cache proxy")
            return
        try:
            config = self._parse_editor_config()
        except Exception as exc:
            messagebox.showerror("Steam Preload", str(exc))
            return

        items = config.steam_preload_items
        if selected_names is not None:
            items = [item for item in items if item.name in set(selected_names)]
        if not items:
            messagebox.showerror("Steam Preload", "There are no preload entries to warm")
            return

        self._append_preload_log(f"Starting warmup for {len(items)} preload entr{'y' if len(items) == 1 else 'ies'}")
        self._preload_thread = threading.Thread(
            target=self._run_preload_job,
            args=(config, items),
            name="steam-preload",
            daemon=True,
        )
        self._preload_thread.start()

    def _run_preload_job(self, config, items: list[SteamPreloadItem]) -> None:
        preloader = CachePreloader(self.app.cache, config.steamcmd, config.proxy)
        total_failures = 0

        for item in items:
            self.root.after(0, self._set_preload_status, item.name, "Running")
            self.root.after(0, self._append_preload_log, f"[{item.name}] Running SteamCMD for app {item.app_id}")
            try:
                result = preloader.preload_item(
                    item,
                    output_callback=lambda line, item_name=item.name: self.root.after(
                        0,
                        self._append_preload_log,
                        f"[{item_name}] {line}",
                    ),
                )
            except Exception as exc:
                total_failures += 1
                message = str(exc)
                self.root.after(0, self._append_preload_log, f"[{item.name}] FAILED: {message}")
                self.root.after(0, self._set_preload_status, item.name, f"failed, {message}")
                continue
            if result.status == "failed":
                total_failures += 1
            self.root.after(
                0,
                self._append_preload_log,
                f"[{item.name}] {result.status.upper()}: App {result.app_id} ({result.message})",
            )
            status = f"{result.status}, {result.bytes_downloaded} bytes in install dir"
            self.root.after(0, self._set_preload_status, item.name, status)

        final_message = "Steam preload finished" if total_failures == 0 else f"Steam preload finished with {total_failures} failures"
        self.root.after(0, self.message_var.set, final_message)
        self.root.after(0, self._refresh_games)

    def _selected_preload_name(self) -> str | None:
        selection = self.preload_tree.selection()
        if not selection:
            return None
        return selection[0]

    def _set_preload_status(self, name: str, status: str) -> None:
        self._preload_statuses[name] = status
        try:
            config = self._parse_editor_config()
        except Exception:
            return
        self._sync_form_state(config)

    def _append_preload_log(self, message: str) -> None:
        self.preload_log.configure(state=tk.NORMAL)
        self.preload_log.insert(tk.END, f"{message}\n")
        self.preload_log.see(tk.END)
        self.preload_log.configure(state=tk.DISABLED)

    def _refresh_games(self) -> None:
        selected = self.games_tree.selection()
        self.games_tree.delete(*self.games_tree.get_children())
        for game in self.app.cache.list_steam_games():
            installed_at = datetime.fromtimestamp(game.first_installed_at).strftime("%Y-%m-%d %H:%M:%S")
            self.games_tree.insert(
                "",
                tk.END,
                iid=str(game.app_id),
                values=(game.name, game.app_id, installed_at, game.client_download_count),
            )
        if selected:
            for item_id in selected:
                if self.games_tree.exists(item_id):
                    self.games_tree.selection_add(item_id)
        self.root.after(2000, self._refresh_games)

    def _refresh_steam_clients(self) -> None:
        selected = self.clients_tree.selection()
        self.clients_tree.delete(*self.clients_tree.get_children())
        for client in self.app.steam_clients.list_active_clients():
            last_seen_at = datetime.fromtimestamp(client.last_seen_at).strftime("%Y-%m-%d %H:%M:%S")
            item_id = client.ip_address.replace(":", "_")
            self.clients_tree.insert(
                "",
                tk.END,
                iid=item_id,
                values=(
                    client.ip_address,
                    self._format_duration(client.active_for_seconds),
                    last_seen_at,
                    client.request_count,
                    ", ".join(client.sources) or "-",
                    client.last_hostname or "-",
                ),
            )
        if selected:
            for item_id in selected:
                if self.clients_tree.exists(item_id):
                    self.clients_tree.selection_add(item_id)
        self.root.after(2000, self._refresh_steam_clients)

    @staticmethod
    def _format_duration(total_seconds: int) -> str:
        seconds = max(0, int(total_seconds))
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes:02d}m {secs:02d}s"
        if minutes:
            return f"{minutes}m {secs:02d}s"
        return f"{secs}s"

    def save_config(self) -> None:
        try:
            config = self._prepare_settings_config()
            self.app.save_config(config)
        except Exception as exc:
            messagebox.showerror("Save Failed", str(exc))
            self.message_var.set("Save failed")
            return
        self.message_var.set("Config saved to disk")

    def apply_changes(self) -> None:
        try:
            config = self._prepare_settings_config()
            if config.safety.confirm_before_apply and not messagebox.askyesno(
                "Apply Changes",
                "Apply the current configuration changes now?",
            ):
                self.message_var.set("Apply cancelled")
                return
            self.app.apply_config(config, save=True)
        except Exception as exc:
            messagebox.showerror("Apply Failed", str(exc))
            self.message_var.set("Apply failed")
            return

        self.status_var.set("Running" if self.app.is_running else "Stopped")
        self._set_editor_config(self.app.load_config_from_disk())
        self.message_var.set("Configuration saved and applied")

    def _refresh_metrics(self) -> None:
        snapshot = self.app.metrics.snapshot()
        self.metrics_box.configure(state=tk.NORMAL)
        self.metrics_box.delete("1.0", tk.END)
        self.metrics_box.insert(tk.END, json.dumps(snapshot, indent=2))
        self.metrics_box.configure(state=tk.DISABLED)
        self.status_var.set("Running" if self.app.is_running else "Stopped")
        self.root.after(1000, self._refresh_metrics)

    def on_close(self) -> None:
        self.stop_app()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()