# Lazy as for AI agents

<!-- mcp-name: io.github.yousefoghouneim-collab/lazyas -->

[Lazy as](https://lazyas.com) turns "point at your screen and talk" into numbered review notes with marked
screenshots. This package lets AI coding agents read those notes, fix them by reference, and report each fix back
with proof, so nothing counts as done until you've checked it.

- **MCP server** (`lazyas mcp`) for Claude Code, Codex, Cursor, Gemini CLI and GitHub Copilot CLI.
- **Claude Code plugin** (this repo): the MCP server, a review skill, `/lazyas:fix`, `/lazyas:open`, and
  "done needs proof" hooks.
- **CLI**: `lazyas inbox`, `verify`, `reopen`, `report`, `export`, `link`, `setup`.

No dependencies, Python 3.9+. Everything runs on your computer; it reads the session folders the Lazy as app writes.
Get the app itself at [lazyas.com/download](https://lazyas.com/download).

```
uv tool install git+https://github.com/yousefoghouneim-collab/lazyas-agent
lazyas setup                   # prints the config for each agent
```

No uv? `pipx install git+https://github.com/yousefoghouneim-collab/lazyas-agent` does the same.

Claude Code:

```
claude plugin marketplace add yousefoghouneim-collab/lazyas-agent
claude plugin install lazyas@lazyas
```

Tests: `python3 -m unittest discover -s tests`.

MIT licence (see `LICENSE`) for the lazyas tool and the Claude Code plugin only. The Lazy as apps for Mac, Windows and
Linux are proprietary: https://lazyas.com/eula. "Lazy as" and its logo are trademarks and aren't licensed here.
