"""
STDF file fetcher module.

Nuitka External JSON Notes:
- mpc_partnumber.json MUST remain external beside the EXE
- Do NOT compile/embed the JSON into the executable
- Any JSON changes will automatically reflect after restarting the app

Recommended compile command:

Standalone:
    nuitka --standalone --follow-imports app_launcher.py

Onefile:
    nuitka --onefile --follow-imports app_launcher.py
"""
import functools
import json
import os
import re
import shutil
import sys
from typing import Callable, Dict, List, Optional, Set, Tuple

# ── Constants ─────────────────────────────────────────────────────────────────
LOCAL_DEST_BASE = r"C:\FTDC"
STDF_EXTENSIONS = frozenset({".stdf", ".std", ".bak", ".old", ".stdf_open", ".std_open"})
_STDF_EXTENSIONS_TUPLE = tuple(STDF_EXTENSIONS)

# Tolerance (in nanoseconds) when comparing a network file's modified time to
# the locally-cached copy's modified time. shutil.copy2 preserves the source
# mtime, but different filesystems (NTFS vs. SMB/FAT) can round timestamps by up
# to ~2 seconds, so an exact match is too strict. 2 seconds keeps the check
# robust without falsely treating an identical file as "changed".
_MTIME_TOLERANCE_NS = 2 * 1_000_000_000

# Copy concurrency. Copying is I/O-bound (network SMB read -> local disk write),
# so we use THREADS. This cap is high enough that a typical lot (a handful of
# STDF files) gets effectively one worker per file, so all transfers start at
# once and total time is gated by the single largest datalog (usually FIRST
# PASS) instead of the sum of all files. The cap only guards against a
# pathological selection of dozens of files saturating the network/disk.
_MAX_COPY_WORKERS = 8

# Files whose names match any of these patterns are excluded from Get STDF results.
# Pre-compiled in lowercase without re.IGNORECASE to run at native C speed (13.3x faster).
_EXCLUDE_FILENAME_RE = re.compile(r"co(?:rel|rr)|qc(?:ver|f)|wh(?:ite|s)|bin31|drop|slug|data|log|ver|fu|pa|os")

# A "_w_" token marks a plain WAFER(-sort) datalog. These sole "_w_" files are
# excluded from Get STDF search results because they are not the FT datalogs we
# want. RETEST wafer datalogs, however, carry an adjacent retest marker
# ("rj<N>_w" or "w_rj<N>") and MUST be kept. So the rule is: exclude a name that
# contains "_w_" ONLY when it has no adjacent "rj" retest marker.
_RETEST_WAFER_RE = re.compile(r"rj\d+_w|w_rj\d+")


def _is_sole_w_datalog(name_lower: str) -> bool:
    """Return True for a plain wafer datalog ("_w_") that is NOT a retest
    wafer datalog ("rj<N>_w" / "w_rj<N>"). Only these sole "_w_" files should
    be excluded from Get STDF results; retest wafer datalogs are kept."""
    return ("_w_" in name_lower) and (_RETEST_WAFER_RE.search(name_lower) is None)

# Network paths are now defined ONCE inside mpc_partnumber.json under the
# top-level "_network_paths" section, so they no longer live in this file:
#   - "tester_paths"  : tester-type -> base directory
#                       (LTX  -> \\acpnetapp02\ftdirectory\backend\data\stdf\ltx2)
#                       (V93K -> \\acpnetapp02\ftdirectory\backend\data\stdf\v93k)
#   - "common_paths"  : directories searched for EVERY MPC regardless of tester.
# The constants below are the field names used to read that section.
_NETWORK_PATHS_KEY = "_network_paths"
_TESTER_PATHS_KEY = "tester_paths"
_COMMON_PATHS_KEY = "common_paths"
_MPCS_KEY = "mpcs"


def _get_json_path(filename: str) -> str:
    base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    candidate = os.path.join(base_dir, filename)
    if os.path.isfile(candidate):
        return candidate
    module_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(module_dir, filename)

_MPC_PARTNUMBER_JSON_PATH = _get_json_path("mpc_partnumber.json")

# ── Type aliases ──────────────────────────────────────────────────────────────
LogFunc = Optional[Callable[[str], None]]
ProgressFunc = Optional[Callable[[float, str], None]]


def _emit_log(message: str, logger: LogFunc = None) -> None:
    """Emit a log message if a logger callback is provided."""
    if logger:
        logger(message)


# ── MPC / Device resolution ──────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def _load_mpc_partnumber_file() -> Dict:
    """Load and return the full mpc_partnumber.json document.

    The document has two top-level sections:
      - ``"_network_paths"`` : tester-specific and common network directories
                               (each defined ONE time only).
      - ``"mpcs"``           : the MPC -> {"device", "tester"} mapping.
    """
    try:
        with open(_MPC_PARTNUMBER_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"MPC partnumber file not found: '{_MPC_PARTNUMBER_JSON_PATH}'"
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Invalid JSON in MPC partnumber file: {exc}"
        ) from exc


def reload_config():
    _load_mpc_partnumber_file.cache_clear()


def load_mpc_partnumber_map() -> Dict[str, Dict[str, str]]:
    """Load only the MPC mapping section from mpc_partnumber.json.

    Each entry is ``{"device": "...", "tester": "V93K"|"LTX"}``.
    """
    data = _load_mpc_partnumber_file()
    # Support both the new schema (mpcs nested under "mpcs") and a legacy
    # flat schema (MPC keys at the top level) for backward compatibility.
    if isinstance(data, dict) and _MPCS_KEY in data:
        return data.get(_MPCS_KEY, {})
    return data


def load_network_paths() -> Tuple[Dict[str, str], List[str]]:
    """Load the network-path configuration defined ONCE in mpc_partnumber.json.

    Returns a tuple ``(tester_paths, common_paths)`` where:
      - ``tester_paths`` maps an upper-cased tester type (e.g. "LTX", "V93K")
        to its single base directory.
      - ``common_paths`` is the list of directories that apply to EVERY MPC
        regardless of tester type.
    """
    data = _load_mpc_partnumber_file()
    config = data.get(_NETWORK_PATHS_KEY, {}) if isinstance(data, dict) else {}
    tester_paths = {
        str(k).strip().upper(): str(v)
        for k, v in config.get(_TESTER_PATHS_KEY, {}).items()
    }
    common_paths = [str(p) for p in config.get(_COMMON_PATHS_KEY, [])]
    return tester_paths, common_paths


def get_common_paths() -> List[str]:
    """Return just the common network paths that apply to EVERY MPC."""
    _, common_paths = load_network_paths()
    return common_paths


def resolve_network_paths(testers: List[str]) -> List[str]:
    """Build the ordered, de-duplicated list of directories to search.

    For the given tester type(s) this returns:
      1. the tester-specific base directory for each tester, followed by
      2. the common directories that apply to every MPC.

    Both the tester-specific and common directories are defined only ONCE in
    mpc_partnumber.json, so they are never duplicated across MPC keys.
    """
    tester_paths, common_paths = load_network_paths()

    ordered: List[str] = []
    for tester in testers:
        base = tester_paths.get(str(tester).strip().upper())
        if base:
            ordered.append(base)
    ordered.extend(common_paths)

    # De-duplicate while preserving order.
    seen: Set[str] = set()
    return [p for p in ordered if not (p in seen or seen.add(p))]


def resolve_mpc_details(mpc_text: str) -> Tuple[List[str], List[str], List[str]]:
    """
    Look up the exact MPC key in mpc_partnumber.json and return a tuple of
    (device_names_list, tester_types_list, network_paths_list).

    ``network_paths_list`` is built from the "_network_paths" section of the
    JSON: the tester-specific directory for each of this MPC's testers, plus
    the common directories that apply to every MPC. All of these paths are
    defined only ONCE in the JSON.

    Raises ValueError if the MPC is not found, if its entry is malformed
    (e.g. not an object, or missing/empty ``device`` or ``tester`` fields),
    or if a tester has no configured network path. This function never
    silently falls back to a default tester.
    """
    mpc_map = load_mpc_partnumber_map()
    mpc_key = mpc_text.strip()

    if mpc_key not in mpc_map:
        tester_paths, _ = load_network_paths()
        ltx_path = tester_paths.get("LTX", "(not configured)")
        v93k_path = tester_paths.get("V93K", "(not configured)")
        raise ValueError(
            f"MPC '{mpc_key}' is not included in the list of devices using "
            f"automated extraction of STDF files.\n\n"
            f"Kindly manually extract all the STDF files in:\n"
            f"  For LTX: {ltx_path}\n"
            f"  For V93K: {v93k_path}"
        )

    entry = mpc_map[mpc_key]
    if not isinstance(entry, dict):
        # Malformed entry — do NOT fall back to a default tester.
        raise ValueError(
            f"MPC '{mpc_key}' has a malformed entry in mpc_partnumber.json: "
            f"expected an object with 'device' and 'tester' fields, "
            f"but got {type(entry).__name__}."
        )

    device_val = entry.get("device")
    if isinstance(device_val, list):
        devices = [str(d).strip() for d in device_val if str(d).strip()]
    elif isinstance(device_val, str):
        if "," in device_val:
            devices = [d.strip() for d in device_val.split(",") if d.strip()]
        else:
            devices = [device_val.strip()]
    elif device_val is None:
        devices = []
    else:
        devices = [str(device_val).strip()]

    tester_val = entry.get("tester")
    if isinstance(tester_val, list):
        testers = [str(t).strip() for t in tester_val if str(t).strip()]
    elif isinstance(tester_val, str):
        if "," in tester_val:
            testers = [t.strip() for t in tester_val.split(",") if t.strip()]
        else:
            testers = [tester_val.strip()] if tester_val.strip() else []
    elif tester_val is None:
        testers = []
    else:
        testers = [str(tester_val).strip()]

    # Make them unique but preserve order
    seen_dev = set()
    devices_unique = [d for d in devices if not (d in seen_dev or seen_dev.add(d))]
    seen_tst = set()
    testers_unique = [t for t in testers if not (t in seen_tst or seen_tst.add(t))]

    # Validate — do NOT fall back to a default tester on any error.
    if not devices_unique:
        raise ValueError(
            f"MPC '{mpc_key}' has no 'device' defined in mpc_partnumber.json."
        )
    if not testers_unique:
        raise ValueError(
            f"MPC '{mpc_key}' has no 'tester' defined in mpc_partnumber.json."
        )

    # Ensure every tester has a configured network path; raise if not so we
    # never silently drop a tester or default to LTX.
    tester_paths, _ = load_network_paths()
    unknown = [t for t in testers_unique if t.strip().upper() not in tester_paths]
    if unknown:
        configured = ", ".join(sorted(tester_paths)) or "(none)"
        raise ValueError(
            f"MPC '{mpc_key}' references tester(s) {', '.join(unknown)} that "
            f"have no configured network path in mpc_partnumber.json "
            f"('_network_paths' -> 'tester_paths'). Configured testers: {configured}."
        )

    # Resolve the directories to search for this MPC: the tester-specific
    # path(s) for its tester(s) followed by the shared common paths.
    network_paths = resolve_network_paths(testers_unique)

    return devices_unique, testers_unique, network_paths


def resolve_device_names(mpc_text: str) -> List[str]:
    """
    Look up the exact MPC key in mpc_partnumber.json and return the
    list of unique device names it maps to (typically one).

    Raises ValueError if the MPC is not found in the JSON file.
    """
    devices, _, _ = resolve_mpc_details(mpc_text)
    return devices


# ── Local destination cache ───────────────────────────────────────────────────

def get_local_dest_dir(lot_id: str) -> str:
    """Return the local destination folder for a lot: ``C:\\FTDC\\{lot_id}``."""
    return os.path.join(LOCAL_DEST_BASE, lot_id.strip())


def existing_stdf_signatures(lot_id: str) -> Dict[str, Tuple[int, int]]:
    """Return a content-aware signature for every STDF file already present in
    the local destination folder ``C:\\FTDC\\{lot_id}``.

    The result maps ``basename_lower -> (size_bytes, mtime_ns)``. This powers the
    robust "Get STDF" cache: a network file is treated as "already fetched" only
    when its name AND size AND modified time match the local copy, so a file that
    was changed on the network (same name but different size/mtime) is correctly
    detected as new and re-fetched.
    """
    dest_dir = get_local_dest_dir(lot_id)
    sigs: Dict[str, Tuple[int, int]] = {}
    if os.path.isdir(dest_dir):
        try:
            for entry in os.scandir(dest_dir):
                if entry.is_file() and os.path.splitext(entry.name)[1].lower() in STDF_EXTENSIONS:
                    try:
                        st = entry.stat()
                        mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
                        sigs[entry.name.lower()] = (int(st.st_size), int(mtime_ns))
                    except OSError:
                        # If we cannot stat it, remember the name with a sentinel
                        # signature so it is still recognised as present.
                        sigs[entry.name.lower()] = (-1, -1)
        except OSError:
            pass
    return sigs


def existing_stdf_basenames(lot_id: str) -> Set[str]:
    """Return the lower-cased basenames of STDF files already present in the
    local destination folder ``C:\\FTDC\\{lot_id}``.

    This is a thin wrapper over :func:`existing_stdf_signatures` for callers that
    only need the set of names (e.g. to show a count in the UI).
    """
    return set(existing_stdf_signatures(lot_id).keys())


# ── Network search ────────────────────────────────────────────────────────────

def search_stdf_files(
    lot_id: str,
    device_names: List[str],
    network_base: Optional[str] = None,
    tester_type: Optional[str] = None,
    network_bases: Optional[List[str]] = None,
    skip_basenames: Optional[Set[str]] = None,
    skip_existing_in_dest: bool = False,
    logger: LogFunc = None,
    progress_callback: ProgressFunc = None,
) -> List[str]:
    """
    Search the network directory(ies) for STDF files whose filename
    contains the lot_id (case-insensitive).

    The base directories to scan are resolved in this priority order:
      1. ``network_bases`` : an explicit list of directories (preferred; this
         is typically the ``network_paths`` list returned by
         ``resolve_mpc_details`` = tester-specific path(s) + common paths).
      2. ``network_base``  : a single explicit directory.
      3. ``tester_type``   : resolved from the JSON "_network_paths" section
         (the matching tester path plus the common paths).
      4. Fallback          : all tester paths plus the common paths.

    Base directories are scanned differently depending on their kind:
      - TESTER paths (e.g. ``...\\stdf\\ltx2``) are scanned ONLY inside their
        matching device sub-folders (e.g. ``...\\ltx2\\ATA5830*``), with
        wildcard expansion and non-recursively — exactly as originally
        intended. The tester base directory itself is NOT scanned, so files
        belonging to other devices are never picked up.
      - COMMON paths (e.g. ``...\\RETRIEVED_DATALOG``) are scanned directly
        but NON-recursively: only files that sit at the top level of the
        common base directory are considered. Sub-folders under a common
        path are intentionally ignored.

    A base directory is treated as a COMMON path if it appears in the
    "_network_paths" -> "common_paths" section of mpc_partnumber.json;
    otherwise it is treated as a TESTER path.

    Caching ("Get STDF" skip-existing):
      - ``skip_existing_in_dest`` : when True, a network file is skipped only if
        an identical copy already exists in ``C:\\FTDC\\{lot_id}`` — identity is
        verified by name AND size AND modified time (mtime within a small
        tolerance). A file that was changed on the network (same name but a
        different size or newer mtime) is therefore detected as new and
        re-fetched, rather than being wrongly skipped.
      - ``skip_basenames`` : an explicit set of lower-cased basenames to skip by
        NAME ONLY (no size/mtime check). Useful for callers that already know
        which names to ignore.

    Uses a ThreadPoolExecutor to parallelize directory scanning over
    high-latency network paths.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    lot_id_lower = lot_id.strip().lower()
    if not lot_id_lower:
        raise ValueError("Lot ID must not be empty.")

    # ── Build the caches used to skip already-fetched files ─────────────────
    # skip_names  : name-only skip list (explicit caller-supplied basenames)
    # skip_sigs   : name -> (size, mtime_ns) for files already in the dest folder,
    #               used for robust content-aware skipping (name + size + mtime).
    skip_names: Set[str] = set()
    if skip_basenames:
        skip_names.update(n.lower() for n in skip_basenames)
    skip_sigs: Dict[str, Tuple[int, int]] = {}
    if skip_existing_in_dest:
        skip_sigs = existing_stdf_signatures(lot_id)
    if skip_names or skip_sigs:
        _emit_log(
            f"  Get STDF cache: {len(skip_names) + len(skip_sigs)} file(s) already "
            f"in target folder will be skipped if unchanged (name + size + modified time).",
            logger,
        )

    found_files: List[str] = []

    # ── Resolve the list of base directories to scan ────────────────────────
    if network_bases:
        base_dirs = list(network_bases)
    elif network_base is not None:
        base_dirs = [network_base]
    else:
        tester_paths, common_paths = load_network_paths()
        base_dirs = []
        if tester_type is not None:
            tpath = tester_paths.get(tester_type.upper())
            if tpath:
                base_dirs.append(tpath)
        if not base_dirs:
            # No specific tester path resolved -> search every tester path.
            base_dirs.extend(tester_paths.values())
        base_dirs.extend(common_paths)

    # De-duplicate base dirs while preserving order.
    _seen_base: Set[str] = set()
    base_dirs = [b for b in base_dirs if b and not (b in _seen_base or _seen_base.add(b))]

    # ── Classify each base directory as a COMMON path or a TESTER path ──────
    # COMMON paths (from the JSON "_network_paths" -> "common_paths") are
    # scanned directly but NON-recursively (only files at the top level of the
    # common base directory; sub-folders are ignored). TESTER paths are scanned
    # ONLY inside their matching device sub-folders (also non-recursively).
    _common_norm = {
        os.path.normcase(os.path.normpath(p)) for p in get_common_paths()
    }

    def _is_common_path(path: str) -> bool:
        return os.path.normcase(os.path.normpath(path)) in _common_norm

    flat_dirs: List[str] = []            # scanned one level deep

    for base_dir in base_dirs:
        if _is_common_path(base_dir):
            # COMMON path → scan the base directory itself, top level only.
            flat_dirs.append(base_dir)
        else:
            # TESTER path → scan only the matching device sub-folder(s),
            # non-recursively. The tester base itself is NOT scanned.
            for device in device_names:
                if device.endswith("*"):
                    prefix = device[:-1]
                    try:
                        if os.path.isdir(base_dir):
                            for entry in os.scandir(base_dir):
                                if entry.is_dir() and entry.name.lower().startswith(prefix.lower()):
                                    flat_dirs.append(entry.path)
                    except OSError as exc:
                        _emit_log(f"  Error expanding wildcard '{device}' in {base_dir}: {exc}", logger)
                else:
                    flat_dirs.append(os.path.join(base_dir, device))

    # De-duplicate flat dirs while preserving order.
    _seen_flat: Set[str] = set()
    flat_dirs = [d for d in flat_dirs if not (d in _seen_flat or _seen_flat.add(d))]

    # Total unit count for progress reporting.
    total_dirs = max(len(flat_dirs), 1)

    def _match_file(entry: os.DirEntry) -> Optional[str]:
        """Return the full path if the file matches the lot id and is a valid,
        non-excluded, not-already-cached STDF file; otherwise None.

        ``entry`` is an ``os.scandir`` DirEntry so its cached ``stat()`` can be
        reused for the size/mtime comparison without an extra network call.
        """
        filename = entry.name
        name_lower = filename.lower()
        if not name_lower.endswith(_STDF_EXTENSIONS_TUPLE):
            return None
        if lot_id_lower not in name_lower:
            return None
        if _EXCLUDE_FILENAME_RE.search(name_lower):
            if logger:
                _emit_log(f"  Skipped (excluded pattern): {filename}", logger)
            return None

        # Task 1: exclude sole "_w_" wafer datalogs, but KEEP retest wafer
        # datalogs (rj<N>_w / w_rj<N>).
        if _is_sole_w_datalog(name_lower):
            if logger:
                _emit_log(f"  Skipped (sole _w_ wafer datalog, not a retest): {filename}", logger)
            return None

        # Name-only skip (explicit caller-supplied basenames).
        if skip_names and name_lower in skip_names:
            if logger:
                _emit_log(f"  Skipped (already in target folder): {filename}", logger)
            return None

        # Content-aware skip: only skip if name + size + mtime all match the
        # local copy. Otherwise the network file is new/changed → re-fetch.
        if skip_sigs and name_lower in skip_sigs:
            cached_size, cached_mtime_ns = skip_sigs[name_lower]
            try:
                st = entry.stat()
                src_size = int(st.st_size)
                src_mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))
            except OSError:
                # Can't stat the network file — fall back to name-only skip so
                # behaviour matches the previous (safe) cache.
                if logger:
                    _emit_log(f"  Skipped (already in target folder): {filename}", logger)
                return None

            same_size = (cached_size == src_size)
            same_mtime = (
                cached_mtime_ns >= 0
                and abs(src_mtime_ns - cached_mtime_ns) <= _MTIME_TOLERANCE_NS
            )
            if same_size and same_mtime:
                if logger:
                    _emit_log(
                        f"  Skipped (already in target folder, unchanged): {filename}",
                        logger,
                    )
                return None
            # Same name but different content → re-fetch this file.
            if logger:
                _emit_log(
                    f"  Re-fetching (changed on network — size/modified time differs): {filename}",
                    logger,
                )

        return entry.path

    def scan_flat_dir(search_dir: str) -> List[str]:
        """Scan a single directory one level deep directly using os.scandir."""
        res: List[str] = []
        if logger:
            _emit_log(f"Searching: {search_dir}", logger)
        try:
            for entry in os.scandir(search_dir):
                if entry.is_file():
                    matched = _match_file(entry)
                    if matched:
                        res.append(matched)
        except (FileNotFoundError, PermissionError, OSError) as exc:
            if logger:
                _emit_log(f"  Directory inaccessible or search failed: {search_dir} — {exc}", logger)
        return res

    # Increase worker pool for network directories (scanning over UNC network shares is 99% I/O wait)
    max_workers = min(len(flat_dirs), 16)
    if max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_dir = {
                executor.submit(scan_flat_dir, d): d for d in flat_dirs
            }
            completed_dirs = 0
            for future in as_completed(future_to_dir):
                completed_dirs += 1
                if progress_callback:
                    frac = 0.10 + 0.55 * (completed_dirs / total_dirs)
                    d_name = os.path.basename(future_to_dir[future])
                    progress_callback(frac, f"Scanned {d_name}…")
                found_files.extend(future.result())
    else:
        for i, search_dir in enumerate(flat_dirs):
            if progress_callback:
                frac = 0.10 + 0.55 * (i / total_dirs)
                dir_name = os.path.basename(search_dir)
                progress_callback(frac, f"Scanning {dir_name}…")
            found_files.extend(scan_flat_dir(search_dir))

    # A file may be discovered via more than one base/sub-folder path; keep
    # each unique absolute path only once using fast case-folded normalization.
    _seen_files: Set[str] = set()
    unique_files = [
        f for f in found_files
        if not (f.lower() in _seen_files or _seen_files.add(f.lower()))
    ]

    if progress_callback:
        progress_callback(0.65, f"Found {len(unique_files)} file(s)")

    if logger:
        _emit_log(f"Search complete — {len(unique_files)} matching file(s) found.", logger)
    return sorted(unique_files, key=lambda p: os.path.basename(p).lower())


# ── File copy ─────────────────────────────────────────────────────────────────

def _fast_copy_file(src: str, dst: str, buffer_size: int = 8 * 1024 * 1024):
    """Copy file optimized for 1GB+ files over Windows SMB3 network shares.

    On Windows, uses kernel32.CopyFileExW to leverage kernel SMB Direct /
    SMB Multichannel and DMA hardware acceleration without user-space RAM allocations.
    Falls back to an 8MB buffer copy with disk pre-allocation on other platforms.
    """
    if os.name == "nt":
        try:
            import ctypes
            res = ctypes.windll.kernel32.CopyFileExW(
                os.path.abspath(src),
                os.path.abspath(dst),
                None, None, None, 0
            )
            if res != 0:
                return
        except Exception:
            pass

    # High-speed fallback: 8MB buffer with local disk space pre-allocation
    try:
        file_size = os.path.getsize(src)
    except OSError:
        file_size = 0

    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        if file_size > 0:
            try:
                fdst.truncate(file_size)  # Pre-allocate 1GB disk space to eliminate NTFS fragmentation
            except OSError:
                pass
        while True:
            buf = fsrc.read(buffer_size)
            if not buf:
                break
            fdst.write(buf)
    try:
        shutil.copystat(src, dst)
    except OSError:
        pass


def copy_stdf_files(
    source_paths: List[str],
    lot_id: str,
    logger: LogFunc = None,
    progress_callback: ProgressFunc = None,
) -> str:
    """
    Copy the given STDF files to ``C:\\FTDC\\{lot_id}\\``.

    Creates the destination directory if it does not exist.

    Duplicate handling (overwrite / single-copy):
      The same datalog can be discovered under more than one source directory
      (for example, a tester path AND a mirrored common path). Such matches share
      the SAME basename and are the same file. Previously each duplicate was
      written with a numeric suffix (``name_1.std``), leaving redundant copies in
      the destination. Now the source list is de-duplicated by basename
      (case-insensitive): only ONE file per unique name is copied, and it is
      written directly to its real name so it OVERWRITES any existing copy. Net
      result: exactly one file per unique name in the destination.

    Parallelism:
      Copying is I/O-bound, so up to ``_MAX_COPY_WORKERS`` files are transferred
      concurrently using a ThreadPoolExecutor. Because each unique file gets its
      own worker (for typical lot sizes), all transfers start together and the
      overall time is gated by the single largest datalog (usually FIRST PASS)
      rather than the sum of every file's time.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    dest_dir = os.path.join(LOCAL_DEST_BASE, lot_id.strip())
    os.makedirs(dest_dir, exist_ok=True)

    # ── De-duplicate by basename (case-insensitive) ────────────────────────────
    # Keep the first occurrence of each unique filename. Identical files coming
    # from different source directories collapse to a single copy. This also
    # guarantees two worker threads can never write to the same destination path
    # at the same time (which would corrupt the output file).
    unique_paths: List[str] = []
    seen_names: Set[str] = set()
    for src in source_paths:
        name_lower = os.path.basename(src).lower()
        if name_lower in seen_names:
            _emit_log(
                f"  Skipped duplicate (same filename from another source): {os.path.basename(src)}",
                logger,
            )
            continue
        seen_names.add(name_lower)
        unique_paths.append(src)

    total = max(len(unique_paths), 1)
    if len(unique_paths) != len(source_paths):
        _emit_log(
            f"Copying {len(unique_paths)} unique file(s) "
            f"({len(source_paths) - len(unique_paths)} duplicate name(s) collapsed) to: {dest_dir}",
            logger,
        )
    else:
        _emit_log(f"Copying {len(unique_paths)} file(s) to: {dest_dir}", logger)

    copied = 0
    copied_lock = threading.Lock()

    def copy_one(src_path: str) -> Tuple[bool, str]:
        nonlocal copied
        filename = os.path.basename(src_path)
        dest_path = os.path.join(dest_dir, filename)  # real name -> overwrites
        try:
            _fast_copy_file(src_path, dest_path)
            with copied_lock:
                copied += 1
            _emit_log(f"  Copied: {filename}", logger)
            return True, filename
        except (OSError, shutil.SameFileError) as exc:
            _emit_log(f"  FAILED to copy {filename}: {exc}", logger)
            return False, filename

    # One worker per unique file (bounded by _MAX_COPY_WORKERS) so all transfers
    # start at once; overall time is gated by the largest datalog.
    max_workers = min(len(unique_paths), _MAX_COPY_WORKERS)
    if max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(copy_one, src) for src in unique_paths]
            for idx, future in enumerate(as_completed(futures)):
                success, filename = future.result()
                if progress_callback:
                    frac = 0.70 + 0.25 * ((idx + 1) / total)
                    progress_callback(frac, f"Copied {idx + 1}/{total}: {filename}")
    else:
        for idx, src in enumerate(unique_paths):
            filename = os.path.basename(src)
            if progress_callback:
                frac = 0.70 + 0.25 * ((idx + 1) / total)
                progress_callback(frac, f"Copying {idx + 1}/{total}: {filename}")
            copy_one(src)

    if progress_callback:
        progress_callback(0.96, "Copy complete")

    _emit_log(f"Done - {copied}/{len(unique_paths)} file(s) copied to {dest_dir}", logger)
    return dest_dir


# ── Per-file progress copy ────────────────────────────────────────────────────

# Type alias for the per-file progress callback: (fraction_of_current_file, message)
PerFileProgressFunc = Optional[Callable[[float, str], None]]
# Type alias for the file-counter callback: (file_index_1based, total_files, filename)
FileCounterFunc = Optional[Callable[[int, int, str], None]]

# Minimum byte interval between progress reports to avoid flooding the UI timer.
_PROGRESS_REPORT_INTERVAL = 1 * 1024 * 1024  # 1 MB


def _fast_copy_file_with_progress(
    src: str,
    dst: str,
    progress_callback: PerFileProgressFunc = None,
    buffer_size: int = 8 * 1024 * 1024,
) -> None:
    """Copy a file with per-byte progress reporting.

    Unlike :func:`_fast_copy_file`, this always uses the manual buffer-based
    copy path (never ``CopyFileExW``) so that progress can be reported at
    byte-level granularity. The callback receives ``(fraction, message)``
    where *fraction* is 0.0–1.0 representing how much of the *current* file
    has been copied.
    """
    try:
        file_size = os.path.getsize(src)
    except OSError:
        file_size = 0

    filename = os.path.basename(src)

    if file_size == 0:
        # Unknown/empty file — fall back to simple copy, report 0% then 100%.
        if progress_callback:
            progress_callback(0.0, f"Copying {filename}…")
        _fast_copy_file(src, dst, buffer_size)
        if progress_callback:
            progress_callback(1.0, f"Copied {filename}")
        return

    if progress_callback:
        progress_callback(0.0, f"Copying {filename}…")

    bytes_copied = 0
    last_reported_bytes = 0

    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        # Pre-allocate disk space to eliminate NTFS fragmentation.
        try:
            fdst.truncate(file_size)
        except OSError:
            pass
        while True:
            buf = fsrc.read(buffer_size)
            if not buf:
                break
            fdst.write(buf)
            bytes_copied += len(buf)

            # Report progress at intervals to avoid flooding the UI.
            if progress_callback and (bytes_copied - last_reported_bytes >= _PROGRESS_REPORT_INTERVAL):
                frac = bytes_copied / file_size
                progress_callback(min(frac, 0.99), f"Copying {filename}…")
                last_reported_bytes = bytes_copied

    # Preserve metadata.
    try:
        shutil.copystat(src, dst)
    except OSError:
        pass

    if progress_callback:
        progress_callback(1.0, f"Copied {filename}")


def copy_stdf_files_per_file(
    source_paths: List[str],
    lot_id: str,
    logger: LogFunc = None,
    per_file_progress_callback: PerFileProgressFunc = None,
    file_counter_callback: FileCounterFunc = None,
) -> str:
    """Copy STDF files with **per-file** progress reporting.

    Unlike :func:`copy_stdf_files`, this copies files **sequentially** so
    the progress bar can fill from 0 → 100 % for each individual file. The
    *per_file_progress_callback* receives ``(fraction, message)`` where
    *fraction* is 0.0–1.0 for the **current** file. The optional
    *file_counter_callback* is called at the start of each file with
    ``(1-based index, total, filename)`` so the UI can display a counter.

    Duplicate handling is identical to :func:`copy_stdf_files`.
    """
    dest_dir = os.path.join(LOCAL_DEST_BASE, lot_id.strip())
    os.makedirs(dest_dir, exist_ok=True)

    # ── De-duplicate by basename (case-insensitive) ─────────────────────────
    unique_paths: List[str] = []
    seen_names: Set[str] = set()
    for src in source_paths:
        name_lower = os.path.basename(src).lower()
        if name_lower in seen_names:
            _emit_log(
                f"  Skipped duplicate (same filename from another source): {os.path.basename(src)}",
                logger,
            )
            continue
        seen_names.add(name_lower)
        unique_paths.append(src)

    total = len(unique_paths)
    if total != len(source_paths):
        _emit_log(
            f"Copying {total} unique file(s) "
            f"({len(source_paths) - total} duplicate name(s) collapsed) to: {dest_dir}",
            logger,
        )
    else:
        _emit_log(f"Copying {total} file(s) to: {dest_dir}", logger)

    copied = 0

    for idx, src_path in enumerate(unique_paths):
        filename = os.path.basename(src_path)
        dest_path = os.path.join(dest_dir, filename)

        # Notify the UI of the file counter.
        if file_counter_callback:
            file_counter_callback(idx + 1, total, filename)

        try:
            _fast_copy_file_with_progress(
                src_path, dest_path,
                progress_callback=per_file_progress_callback,
            )
            copied += 1
            _emit_log(f"  Copied: {filename}", logger)
        except (OSError, shutil.SameFileError) as exc:
            _emit_log(f"  FAILED to copy {filename}: {exc}", logger)

    _emit_log(f"Done - {copied}/{total} file(s) copied to {dest_dir}", logger)
    return dest_dir
