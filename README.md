# Sing-Box Manager

Minimal TUI for keeping `clientsTable.json` and sing-box `config.json` in sync. It assumes the
sing-box instance is the official Docker image `ghcr.io/sagernet/sing-box:latest` so that config
checks and restarts behave the same way they do in production.

## Quick Start

```bash
uv sync
sudo ./install.sh             # installs /usr/local/bin/singbox-users by default
singbox-users [--config PATH] [--clients-table PATH]
```

Prefer not to symlink? Run it ad-hoc with `uv run singbox-users ...`. The CLI always looks for
`settings.toml` in the repository root, no matter where you launch it from. CLI flags override
whatever is in that file, so you can point at ad-hoc JSON paths without editing the config.

### Dependencies

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) for dependency resolution (`uv sync`)
- [`qrcode[pil]`](https://pypi.org/project/qrcode/) (installed via `uv`) for QR rendering
- [`python-imgcat`](https://github.com/wookayin/python-imgcat) for inline QR previews that work in
  iTerm2, tmux, and other terminals supporting the protocol

### Settings file

Copy `settings.example.toml` to `settings.toml` and tweak the values:

```toml
config_path = "/opt/singbox/config.json"
clients_table = "/opt/singbox/clientsTable.json"
vless_tag = "vless-in"
container = "singbox"
docker_image = "ghcr.io/sagernet/sing-box:latest"
server_ip = "203.0.113.10"
share_description = "Amsterdam #1"
share_dns1 = "1.1.1.1"
share_dns2 = "1.0.0.1"
```

Leave any key out to inherit the defaults shown above. Flags `--config` / `--clients-table` take
priority over the TOML settings at runtime.

Key meanings:

- `config_path`: Location of sing-box `config.json`.
- `clients_table`: Location of `clientsTable.json`.
- `vless_tag`: Which inbound's users array is synced.
- `container`: Container restarted by `S`/`x`.
- `docker_image`: Image pulled for the `docker run ... check` command.
- `server_ip`: Server host/IP for the exported config.
- `share_description`, `share_dns*`: Additional metadata for the exported config.

## Why use it?

- curses UI with instant add/rename/delete for VLESS users
- automatic UUIDs, atomic writes, and backup copies under `backup/`
- optional Docker actions: `c` runs `docker run ghcr.io/sagernet/sing-box:latest check ...`, `x`/`S`
  call `docker restart <container>`

## Keyboard Cheatsheet

| Key                       | Action                            |
| ------------------------- | --------------------------------- |
| `↑/↓`, `PgUp/PgDn`, `j/k` | Navigate list                     |
| `a`                       | Add client                        |
| `e`                       | Rename client                     |
| `d`                       | Delete client (with confirm)      |
| `s`                       | Save (syncs JSON + makes backups) |
| `S`                       | Save + restart Docker container   |
| `r`                       | Reload from disk                  |
| `c`                       | Docker config check               |
| `x`                       | Docker restart                    |
| `g`                       | Copy vpn:// link and show QR code |
| `q`/`Ctrl-C`              | Quit (asks to save if dirty)      |

## Files it touches

- `clientsTable.json`: stores `clientId`, `clientName`, `creationDate`.
- `config.json`: VLESS inbound users array rewritten from the table.
- `backup/`: timestamped copies of both files right before each save.

That's it—run the TUI, keep both JSON files tidy, and rely on the Docker image
`ghcr.io/sagernet/sing-box:latest` for validation/restarts.
