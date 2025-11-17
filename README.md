# Sing-Box Manager

Minimal TUI for keeping `clientsTable.json` and sing-box `config.json` in sync. It assumes the
sing-box instance is the official Docker image `ghcr.io/sagernet/sing-box:latest` so that config
checks and restarts behave the same way they do in production.

## Quick Start

```bash
chmod +x singbox-manage.py
./singbox-manage.py [--config PATH] [--clients-table PATH]
```

Defaults: `/opt/singbox/{config.json,clientsTable.json}`; VLESS tag and container are read from
`singbox-manage.toml` (see below) or fall back to `vless-in` / `singbox` if the file/keys are
missing.

### Settings file

Copy `settings.example.toml` to `singbox-manage.toml` and tweak the values:

```toml
vless_tag = "vless-in"
container = "singbox"
docker_image = "ghcr.io/sagernet/sing-box:latest"
```

All keys are optional; omit any line to fall back to the defaults above. They control:

- `vless_tag`: Which inbound's users array is synced.
- `container`: Container restarted by `S`/`x`.
- `docker_image`: Image pulled for the `docker run ... check` command.

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
| `q`/`Ctrl-C`              | Quit (asks to save if dirty)      |

## Files it touches

- `clientsTable.json`: stores `clientId`, `clientName`, `creationDate`.
- `config.json`: VLESS inbound users array rewritten from the table.
- `backup/`: timestamped copies of both files right before each save.

That's it—run the TUI, keep both JSON files tidy, and rely on the Docker image
`ghcr.io/sagernet/sing-box:latest` for validation/restarts.
