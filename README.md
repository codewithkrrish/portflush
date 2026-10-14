# portflush

Find and free whatever is listening on a port — end the `EADDRINUSE` dance in one command.

Every developer knows the moment: you restart your dev server and get `Error: listen EADDRINUSE: address already in use :::3000`. Then comes the ritual — `lsof -i :3000`, squint at the output, `kill <pid>`, hope it was the right one, repeat when it comes back. `portflush` compresses all of that into one safe, cross-platform command.

## Why portflush

- **One command, any OS.** Discovers listeners via `lsof`, `ss`, `netstat`, or `/proc/net/tcp` on Linux — whichever your system has. Works on macOS, Linux, and Windows.
- **Safe by default.** Running `portflush 3000` only *lists* what is listening. Killing always asks first unless you pass `--yes` or `--force`.
- **Graceful, then firm.** Sends SIGTERM, waits for a clean shutdown, and only escalates to SIGKILL if the process refuses to die. `--force` skips straight to SIGKILL.
- **Script-friendly.** `--json` output, `--dry-run` previews, and real exit codes (0 ok, 2 usage, 3 nothing listening, 4 kill failed) make it composable in shell scripts and CI.
- **Zero dependencies.** Pure Python standard library. Install it and forget it.

## Example output

```
$ portflush 3000
  PORT      PID  PROCESS               ADDRESS
----------------------------------------------------------------
  3000    12345  node                  *:3000

$ portflush 3000 --kill --yes
killed port 3000 PID 12345 (node)

$ portflush 3000 --kill --dry-run
would kill port 3000 PID 12345 (node)
Dry run: nothing was killed.

$ portflush 3000
portflush: nothing is listening on 3000
```

## Install

Requires Python 3.10+.

```bash
pip install portflush
```

Or run it without installing:

```bash
git clone https://github.com/codewithkrrish/portflush.git
cd portflush
python -m portflush 3000
```

## Usage

```
portflush [PORT ...] [--port PORT] [--list] [--kill] [--force] [--yes]
          [--dry-run] [--json] [--pick] [--timeout SECONDS]
```

| Command | What it does |
|---|---|
| `portflush 3000` | List what is listening on port 3000 |
| `portflush 3000 8080` | List listeners on several ports |
| `portflush --list` | List every listening port on the machine |
| `portflush 3000 --kill` | Kill it (asks for confirmation) |
| `portflush 3000 --kill --yes` | Kill it without asking (still graceful) |
| `portflush 3000 --kill --force` | Kill it now — SIGKILL, no questions |
| `portflush 3000 --kill --dry-run` | Show what *would* be killed |
| `portflush 3000 --kill --pick` | Pick from a numbered list when several match |
| `portflush 3000 --json` | Machine-readable output |

Flags:

- `--timeout SECONDS` — how long to wait after SIGTERM before SIGKILL (default: 3.0)
- `--port PORT` — repeatable alternative to positional ports

Safety notes:

- `portflush` never kills its own process, and never kills a listener whose PID is unknown.
- Without `--yes` / `--force` / `--dry-run`, killing requires an interactive terminal and a `y` confirmation. In scripts, always pass one of those flags explicitly.

## Project structure

```
portflush/
  portflush/
    __init__.py     version
    __main__.py     `python -m portflush` entry point
    cli.py          thin CLI: argument parsing, I/O, exit codes
    discovery.py    cross-platform listener discovery + parsers
    killing.py      graceful SIGTERM -> SIGKILL escalation
    errors.py       specific exception types with exit codes
    models.py       Listener dataclass
  tests/
    test_discovery.py   parser tests (lsof / ss / netstat / /proc)
    test_killing.py     escalation, dry-run, self-protection tests
    test_cli.py         CLI behaviour, exit codes, JSON output
  pyproject.toml
```

## Development

```bash
python -m pytest -q
```

## License

MIT — see [LICENSE](LICENSE).

## Author

Built by [Krrish](https://github.com/codewithkrrish).
