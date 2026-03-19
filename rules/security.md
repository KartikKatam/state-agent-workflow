---
globs: ["hooks/**/*.py", "scripts/**/*.py", "scripts/**/*.sh"]
---

# Security Rules

## Shell Injection Prevention

- **Never** interpolate environment variables or user input directly into shell commands
- Use `shlex.quote()` for any value passed to shell execution
- Use `subprocess.run()` with list arguments instead of shell strings:
  ```python
  # WRONG — shell injection risk
  subprocess.run(f"git checkout {branch}", shell=True)

  # RIGHT — safe argument passing
  subprocess.run(["git", "checkout", branch])
  ```
- If `shell=True` is unavoidable, quote every variable with `shlex.quote()`

## Environment Variable Handling

- Always validate environment variables before use — never assume they are safe
- Treat `WORKFLOW_ID`, `CLAUDE_CODE_AGENT_NAME`, `AGENT_ROLE` as untrusted input
- Sanitize values used in file paths: reject characters outside `[a-zA-Z0-9._-]`
- When writing env vars to files, use proper escaping (not string interpolation):
  ```python
  # WRONG — injection via env var value
  f.write(f'export {key}="{value}"\n')

  # RIGHT — shell-safe quoting
  f.write(f"export {key}={shlex.quote(value)}\n")
  ```

## Path Validation

- Validate all file paths derived from environment variables or user input
- Reject path traversal attempts (`..`, absolute paths where relative expected)
- Use `pathlib.Path.resolve()` and check the result is within the expected directory:
  ```python
  resolved = base_dir / user_path
  resolved = resolved.resolve()
  if not resolved.is_relative_to(base_dir.resolve()):
      raise ValueError(f"Path escapes base directory: {user_path}")
  ```

## Socket Security

- Unix domain sockets MUST NOT be placed in `/tmp` without proper validation — `/tmp` is world-writable
- Use `~/.claude/` or the project directory for socket files
- Set socket file permissions to `0o600` (owner-only)
- Validate data received from sockets — never trust socket input

## File Permissions

- State files (`~/.claude/state/`) — `0o644` (readable by hooks and agents)
- Log files (`~/.claude/logs/`) — `0o644`
- Socket files — `0o600` (owner-only)
- Hook scripts — `0o755` (executable)
- Never create files with `0o777` permissions

## Atomic File Operations

- Use write-to-temp-then-rename for all read-modify-write operations on shared state files
- This prevents partial writes from corrupting state if a process crashes mid-write:
  ```python
  import tempfile, os
  fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target))
  try:
      os.write(fd, data)
      os.fsync(fd)
      os.close(fd)
      os.replace(tmp, target)
  except:
      os.close(fd)
      os.unlink(tmp)
      raise
  ```

## Rules

- Every hook and script MUST use `shlex.quote()` when constructing shell commands with dynamic values
- Every file path from an env var MUST be validated before use
- No `eval()`, `exec()`, or `os.system()` with dynamic input
- No `pickle.loads()` on untrusted data
- JSONL log files are append-only — never read-modify-write a log file
- Secrets (tokens, keys) MUST NOT appear in log files, state files, or context packets
- If a hook encounters an invalid/suspicious input, log a warning and fail open (permissive fallback) — do not crash the agent
