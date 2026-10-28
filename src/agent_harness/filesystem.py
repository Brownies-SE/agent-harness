import fnmatch
import os
import stat
from pathlib import Path

from .errors import SecurityError
from .executor import Tool

SENSITIVE_PARTS = {
    ".ssh",
    ".aws",
    ".config",
    ".gnupg",
    ".azure",
    ".kube",
    ".docker",
    "browser",
    "browsers",
    "google",
    "chromium",
    "chrome",
    "firefox",
    "safari",
    "keychains",
    "mozilla",
}
SENSITIVE_FILES = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "credentials*",
    "*credentials*",
    "id_rsa*",
    "id_ed25519*",
    "cookies*",
    "login data",
    "*.p12",
    "*.pfx",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "*token*",
    "*secret*",
)
MAX_FILE_BYTES = 128 * 1024


def check_path(policy, raw):
    if not isinstance(raw, str) or not raw or "\x00" in raw or "\\" in raw:
        raise SecurityError("invalid_path")
    path = Path(raw)
    if ".." in path.parts or raw.startswith("~"):
        raise SecurityError("path_traversal")
    if not path.is_absolute():
        if not policy.roots:
            raise SecurityError("path_outside_roots")
        path = policy.roots[0] / path
    for root in policy.roots:
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if not relative.parts:
            raise SecurityError("file_required")
        sensitive = any(part.lower() in SENSITIVE_PARTS for part in path.parts)
        sensitive |= any(
            fnmatch.fnmatch(part.lower(), pattern)
            for part in path.parts
            for pattern in SENSITIVE_FILES
        )
        if sensitive and path not in policy.sensitive_allow:
            raise SecurityError("sensitive_path")
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise SecurityError("symlink_denied")
        return root, path
    raise SecurityError("path_outside_roots")


def _open(policy, raw, *, write=False):
    root, path = check_path(policy, raw)
    parts = path.relative_to(root).parts
    parent = None
    fd = None
    try:
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        flags = os.O_WRONLY | os.O_CREAT if write else os.O_RDONLY
        fd = os.open(parts[-1], flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SecurityError("unsafe_file")
        return fd
    except SecurityError:
        if fd is not None:
            os.close(fd)
        raise
    except OSError as exc:
        if fd is not None:
            os.close(fd)
        raise SecurityError("path_unavailable") from exc
    finally:
        if parent is not None:
            os.close(parent)


def filesystem_tools(policy):
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise RuntimeError("secure filesystem tools require POSIX no-follow opens")

    def read_file(path):
        with os.fdopen(_open(policy, path), "rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise SecurityError("file_too_large")
        try:
            return data.decode("utf-8")
        except UnicodeError as exc:
            raise SecurityError("text_file_required") from exc

    def write_file(path, content):
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise SecurityError("file_too_large")
        with os.fdopen(_open(policy, path, write=True), "wb") as handle:
            os.ftruncate(handle.fileno(), 0)
            handle.write(data)
        return {"written": len(data)}

    props = {"path": {"type": "string", "minLength": 1, "maxLength": 4096}}
    read_schema = {
        "type": "object",
        "properties": props,
        "required": ["path"],
        "additionalProperties": False,
    }
    write_schema = {
        "type": "object",
        "properties": {**props, "content": {"type": "string"}},
        "required": ["path", "content"],
        "additionalProperties": False,
    }
    return [
        Tool("read_file", read_schema, ("filesystem.read",), read_file, {"path": "path"}),
        Tool("write_file", write_schema, ("filesystem.write",), write_file, {"path": "path"}),
    ]
