# WinLAN-Cache

WinLAN-Cache is a Python-based LAN caching prototype for game download traffic on Windows networks. It combines:

- A local DNS interceptor and forwarder
- An HTTP caching proxy for cacheable download hosts
- Windows DNS Server integration through generated PowerShell
- A Tkinter GUI with a main dashboard, config editor, metrics view, and Steam preload tools
- A file-backed cache store with metadata in SQLite

This project is designed around Windows and Windows Server environments.

## What It Does

- Rewrites selected DNS answers to your local cache IP for configured platforms
- Forwards all other DNS requests to an upstream resolver
- Separates platform traffic into:
  - Cacheable HTTP hosts
  - Passthrough hosts
  - HTTPS-only hosts
- Proxies and caches eligible HTTP responses on disk
- Tunnels HTTPS `CONNECT` traffic for passthrough hosts without breaking TLS
- Exposes a local proxy health endpoint at `/__lancache__/health` for client validation without contacting upstream content hosts
- Generates and can apply Windows DNS Server zone overrides for cache-routed domains
- Lets you edit the full JSON config in the GUI and save or apply changes
- Tracks Steam warmup installs in SQLite and shows them in the GUI

## Current Limitations

- HTTPS content is not man-in-the-middle proxied or cached
- Only explicitly configured HTTP-cacheable hosts are cache candidates
- Port `53` usually requires administrative rights on Windows
- Windows DNS Server integration requires a machine with the DNS Server role installed and PowerShell access to the `DnsServer` module
- This repo does not yet include automated integration tests

## Requirements

- Windows 10, Windows 11, or Windows Server 2016, 19, 22, 25
- Python 3.13+ recommended
- Administrative rights if you want to bind to port `53`, apply DNS server changes, or integrate with Windows Server DNS

The Program lets you:

- Start services
- Stop services
- View a `Main` tab showing tracked Steam games, first install time, and cached client download counts
- Choose the cache directory with a folder picker
- View runtime metrics
- Edit the full JSON config
- Save named Steam preload entries by Steam app ID
- Pick a SteamCMD executable
- Warm selected Steam preload entries through SteamCMD
- Watch the live SteamCMD terminal output in the warmup log while a warmup is running
- Save the config to disk
- Apply config changes and restart services if needed
- Reload the config editor from disk
