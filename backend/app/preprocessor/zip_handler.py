"""Safe ZIP inspection. Members are read into memory only (never extracted to disk), with
zip-slip, encryption, nesting, count, size and compression-ratio guards."""
from __future__ import annotations

import io
import posixpath
import re
import zipfile

from .models import (MAX_ZIP_MEMBER_BYTES, MAX_ZIP_MEMBERS, MAX_ZIP_RATIO, MAX_ZIP_TOTAL_BYTES, SUPPORTED_EXTENSIONS,
                     PreprocessError)

_DRIVE = re.compile(r"^[a-zA-Z]:")


def safe_display_name(name: str) -> str:
    """Printable, length-limited name for UI/logs (never used as a filesystem path)."""
    clean = "".join(ch if ch.isprintable() else "?" for ch in name)
    return clean[-180:]


def _unsafe_path(name: str) -> str | None:
    n = name.replace("\\", "/")
    if n.startswith("/") or _DRIVE.match(n):
        return "absolute path"
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return "path traversal ('..')"
    if "\x00" in n:
        return "NUL byte in name"
    return None


def read_members(raw: bytes) -> tuple[list[tuple[str, bytes]], list[dict]]:
    """Return ([(member_name, bytes)] for supported files, [file report entries for every member])."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise PreprocessError("The ZIP archive is corrupt or not a ZIP file.")
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_MEMBERS:
        raise PreprocessError(f"The archive contains {len(infos)} entries; the limit is {MAX_ZIP_MEMBERS}.")
    supported: list[tuple[str, bytes]] = []
    report: list[dict] = []
    total = 0
    for info in infos:
        name = safe_display_name(info.filename)
        ext = posixpath.splitext(info.filename.lower())[1]
        entry = {"name": name, "ext": ext or "", "size": info.file_size, "status": "ignored", "reason": ""}
        report.append(entry)
        if info.is_dir():
            entry.update(status="skipped", reason="directory")
            continue
        unsafe = _unsafe_path(info.filename)
        if unsafe:
            entry.update(status="rejected", reason=f"unsafe member name: {unsafe} (possible zip-slip); not processed")
            continue
        base = posixpath.basename(info.filename.replace("\\", "/"))
        if base.startswith(".") or "__MACOSX/" in info.filename:
            entry.update(status="ignored", reason="system/hidden file")
            continue
        if info.flag_bits & 0x1:
            entry.update(status="rejected", reason="encrypted member")
            continue
        if ext == ".zip":
            entry.update(status="ignored", reason="nested archives are not processed")
            continue
        if ext not in SUPPORTED_EXTENSIONS:
            entry.update(status="ignored", reason="unsupported file type")
            continue
        if info.file_size > MAX_ZIP_MEMBER_BYTES:
            entry.update(status="rejected", reason=f"larger than {MAX_ZIP_MEMBER_BYTES // 2**20} MB")
            continue
        if info.compress_size and info.file_size / max(info.compress_size, 1) > MAX_ZIP_RATIO:
            entry.update(status="rejected", reason="suspicious compression ratio (possible decompression bomb)")
            continue
        if total + info.file_size > MAX_ZIP_TOTAL_BYTES:
            entry.update(status="rejected", reason=f"archive exceeds {MAX_ZIP_TOTAL_BYTES // 2**20} MB uncompressed")
            continue
        # stream with a hard cap: header sizes can lie
        buf = bytearray()
        try:
            with zf.open(info) as fh:
                while chunk := fh.read(1 << 20):
                    buf += chunk
                    if len(buf) > MAX_ZIP_MEMBER_BYTES or total + len(buf) > MAX_ZIP_TOTAL_BYTES:
                        raise PreprocessError("limit")
        except PreprocessError:
            entry.update(status="rejected", reason="decompressed size exceeds the limit")
            continue
        except (zipfile.BadZipFile, RuntimeError, OSError, EOFError):
            entry.update(status="rejected", reason="member could not be read (corrupt)")
            continue
        total += len(buf)
        entry.update(status="supported", reason="")
        supported.append((info.filename, bytes(buf)))
    return supported, report
