"""The ``http:`` block Remote UI needs, and the one line that pulls it in.

A phone reaches the house through a proxy the homeowner does not run, so Home
Assistant has to be told which peers may set ``X-Forwarded-For``
(``use_x_forwarded_for`` and ``trusted_proxies``), or it refuses the request.
That is configuration, and Home Assistant only reads it at start. This module
writes the list to ``<config>/ha_dispatch_client/http.yaml`` and, when -- and
only when -- the homeowner has no ``http:`` key of their own, adds one line to
``configuration.yaml`` that includes it.

Three rules, each of which has a test that fails when it is broken:

  - **``configuration.yaml`` is never re-serialised.** It is the homeowner's
    file: comments, ``!include`` lines, ``!secret`` references and hand
    formatting all survive only if the file is scanned line by line and
    *appended to*. Parse-and-dump would lose every one of them. The original
    bytes are always a prefix of the new file.
  - **An ``http:`` block that is not ours is never touched.** Merging two
    ``http:`` blocks is a decision for the person who wrote the first. It is
    reported (``present_unmanaged``) so a technician can do it.
  - **``http.yaml`` exists before anything includes it.** An ``!include`` of a
    file that is not there makes Home Assistant refuse to load its
    configuration, so the order is: file first, line second.

Everything here is blocking file I/O: call through the executor.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

from .const import REMOTE_UI_HTTP_COMMENT, REMOTE_UI_HTTP_INCLUDE

_LOGGER = logging.getLogger(__name__)

HTTP_BLOCK_MANAGED = "managed"
HTTP_BLOCK_UNMANAGED = "present_unmanaged"
HTTP_BLOCK_MISSING = "missing"
# DNS-only mode: the phone talks straight to the relay, nothing rewrites the
# client address, so Home Assistant needs no trusted_proxies and no http:
# block. Reported when the server sends an empty proxy list.
HTTP_BLOCK_NOT_NEEDED = "not_needed"

# What a scan of configuration.yaml can conclude.
SCAN_OURS = "ours"          # our include line is there
SCAN_FOREIGN = "foreign"    # an http: key that is not ours
SCAN_ABSENT = "absent"      # no top-level http: key at all
SCAN_NO_FILE = "no_file"    # no configuration.yaml to look in

# A top-level http: key. ``http:`` alone (children follow), ``http: # note``,
# or ``http: <anything>`` -- an inline value, an !include, a flow mapping.
_HTTP_KEY = re.compile(r"^http:\s*(#.*)?$|^http:\s+\S")
_OUR_INCLUDE = re.compile(r"^http:\s+!include\s+ha_dispatch_client/http\.yaml\s*(#.*)?$")


def clean_trusted_proxies(proxies: Any) -> List[str]:
    """The CIDRs worth writing, validated and in the server's order.

    A malformed entry is dropped rather than written: a bad address in
    ``http.yaml`` would stop Home Assistant starting. A bare address becomes
    a /32 (or /128); duplicates collapse.
    """
    if not isinstance(proxies, (list, tuple)):
        return []
    cleaned: List[str] = []
    for entry in proxies:
        if not isinstance(entry, str):
            continue
        try:
            network = str(ipaddress.ip_network(entry.strip(), strict=False))
        except ValueError:
            _LOGGER.warning("Remote UI: ignoring %r in trusted_proxies: not an address range", entry)
            continue
        if network not in cleaned:
            cleaned.append(network)
    return cleaned


def render_http_yaml(proxies: Iterable[str]) -> str:
    """The text of ``http.yaml``. Deterministic: the same list is the same bytes."""
    lines = [
        "# Managed by HA Dispatch Client. Rewritten when the trusted proxy list changes.",
        "use_x_forwarded_for: true",
        "trusted_proxies:",
    ]
    lines.extend(f"  - {cidr}" for cidr in proxies)
    return "\n".join(lines) + "\n"


def scan_configuration(raw: bytes) -> str:
    """Look for a top-level ``http:`` key without parsing the file.

    Only a line that *starts* with ``http:`` counts, so a commented-out block,
    an indented ``http:`` under some other key, and a longer key such as
    ``httpx:`` are all correctly ignored.
    """
    text = raw.decode("utf-8", errors="replace").lstrip("﻿")
    ours = False
    for line in text.splitlines():
        if not _HTTP_KEY.match(line):
            continue
        if _OUR_INCLUDE.match(line):
            ours = True
        else:
            return SCAN_FOREIGN
    return SCAN_OURS if ours else SCAN_ABSENT


def inspect_configuration(path: Path) -> str:
    """Read ``configuration.yaml`` (read-only) and say what is in it."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return SCAN_NO_FILE
    return scan_configuration(raw)


def include_suffix(raw: bytes) -> bytes:
    """The bytes appended to a configuration.yaml that has no ``http:`` key."""
    tail = f"\n{REMOTE_UI_HTTP_COMMENT}\n{REMOTE_UI_HTTP_INCLUDE}\n".encode("utf-8")
    # A file that does not end in a newline would otherwise have our comment
    # glued to its last line.
    return b"\n" + tail if raw and not raw.endswith(b"\n") else tail


def append_include(path: Path) -> bool:
    """Append our include line to ``configuration.yaml``. Returns whether it did.

    Re-reads the file and re-checks immediately before writing, so a homeowner
    who added an ``http:`` block since the scan is not overwritten. The file
    is replaced atomically with its own permissions and, if it is a symlink,
    the link target is the file that changes.
    """
    target = Path(os.path.realpath(path))
    try:
        raw = target.read_bytes()
        mode = stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        return False
    if scan_configuration(raw) != SCAN_ABSENT:
        return False
    write_atomic(target, raw + include_suffix(raw), mode)
    return True


def write_if_changed(path: Path, text: str, mode: int = 0o644, force: bool = False) -> bool:
    """Write ``text`` atomically, only when it differs (or ``force``). Returns whether it wrote."""
    data = text.encode("utf-8")
    if not force:
        try:
            if path.read_bytes() == data:
                return False
        except FileNotFoundError:
            pass
    write_atomic(path, data, mode)
    return True


def write_atomic(path: Path, data: bytes, mode: int) -> None:
    """Write a file whole or not at all: temp in the same directory, then replace.

    Blocking I/O. The mode is set on the temp file before the rename, so a
    secret never sits readable on disk even for an instant.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def apply_http_config(
    http_yaml: Path, configuration: Path, proxies: List[str]
) -> Tuple[str, bool]:
    """Make the house trust the relay's proxies. Returns (http_block, changed).

    ``changed`` is true when something Home Assistant reads at start was
    written -- the content of ``http.yaml``, or the include line itself -- and
    therefore a restart is owed. When the include line is newly added,
    ``http.yaml`` is rewritten even if its text is identical, so its mtime is
    a faithful record of the last change a restart must pick up.
    """
    state = inspect_configuration(configuration)
    text = render_http_yaml(proxies)
    needs_include = state == SCAN_ABSENT

    wrote = write_if_changed(http_yaml, text, force=needs_include)

    if state == SCAN_FOREIGN:
        # The homeowner's own block. The file is there for a technician to
        # merge from; nothing reads it, so no restart is owed for it.
        return HTTP_BLOCK_UNMANAGED, False
    if state == SCAN_NO_FILE:
        return HTTP_BLOCK_MISSING, False
    if needs_include and not append_include(configuration):
        # Lost a race with a homeowner edit, or the file vanished: say what is
        # true now and let the next change try again.
        return _http_block_for(inspect_configuration(configuration)), wrote
    return HTTP_BLOCK_MANAGED, wrote or needs_include


def _http_block_for(state: str) -> str:
    if state == SCAN_OURS:
        return HTTP_BLOCK_MANAGED
    if state == SCAN_FOREIGN:
        return HTTP_BLOCK_UNMANAGED
    return HTTP_BLOCK_MISSING


def restart_is_owed(http_yaml: Path, started_at: Optional[float]) -> Optional[bool]:
    """Whether ``http.yaml`` was written after Home Assistant last started.

    The file's mtime is the write time and the process start is the boot
    marker, so the answer survives an integration reload (which is not a
    restart) and clears by itself when Home Assistant has been restarted --
    with no state of ours to lose. ``None`` when the process start is unknown.
    """
    if started_at is None:
        return None
    try:
        return http_yaml.stat().st_mtime > started_at
    except OSError:
        return False
