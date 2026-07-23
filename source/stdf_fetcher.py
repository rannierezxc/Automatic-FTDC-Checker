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
import json
import os
import re
import shutil
from typing import Callable, Dict, List, Optional, Set, Tuple

# ── Constants ─────────────────────────────────────────────────────────────────
LOCAL_DEST_BASE = r"C:\FTDC"
STDF_EXTENSIONS = frozenset({".stdf", ".std", ".bak", ".old", ".stdf_open", ".std_open"})

# Files whose names match any of these patterns (case-insensitive) are excluded
# from Get STDF results: white-slug variants, correlation variants, and QC/verification variants.
_EXCLUDE_FILENAME_RE = re.compile(r"whs|white|corr|corel|qcf|qcver|ver|os|pa|fu|bin31|drop|slug|log|data", re.IGNORECASE)

# Tester specific base directories
TESTER_NETWORK_PATHS = {
    "LTX": r"\\acpnetapp02\ftdirectory\backend\data\stdf\ltx2",
    "V93K": r"\\acpnetapp02\ftdirectory\backend\data\stdf\v93k"
}

def _get_json_path(filename: str) -> str:
    import sys
    
    base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
 

    return os.path.join(base_dir, filename)

_MPC_PARTNUMBER_JSON_PATH = _get_json_path("mpc_partnumber.json")

# ── Type aliases ──────────────────────────────────────────────────────────────
LogFunc = Optional[Callable[[str], None]]
ProgressFunc = Optional[Callable[[float, str], None]]


def _emit_log(message: str, logger: LogFunc = None) -> None:
    """Emit a log message if a logger callback is provided."""
    if logger:
        logger(message)


# ── MPC / Device resolution ──────────────────────────────────────────────────

def load_mpc_partnumber_map() -> Dict[str, Dict[str, str]]:
    """Load the full MPC mapping from mpc_partnumber.json.

    Each entry is ``{"device": "...", "tester": "V93K"|"LTX"}``.
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


def resolve_mpc_details(mpc_text: str) -> Tuple[List[str], List[str]]:
    """
    Look up the exact MPC key in mpc_partnumber.json and return a tuple of
    (device_names_list, tester_types_list).
    
    Raises ValueError if the MPC is not found.
    """
    mpc_map = load_mpc_partnumber_map()
    mpc_key = mpc_text.strip()

    if mpc_key not in mpc_map:
        ltx_path = TESTER_NETWORK_PATHS["LTX"]
        v93k_path = TESTER_NETWORK_PATHS["V93K"]
        raise ValueError(
            f"MPC '{mpc_key}' is not included in the list of devices using "
            f"automated extraction of STDF files.\n\n"
            f"Kindly manually extract all the STDF files in:\n"
            f"  For LTX: {ltx_path}\n"
            f"  For V93K: {v93k_path}"
        )

    entry = mpc_map[mpc_key]
    if not isinstance(entry, dict):
        # Fallback if entry is just a string
        return [entry], ["LTX"]

    device_val = entry.get("device")
    if isinstance(device_val, list):
        devices = [str(d).strip() for d in device_val if str(d).strip()]
    elif isinstance(device_val, str):
        if "," in device_val:
            devices = [d.strip() for d in device_val.split(",") if d.strip()]
        else:
            devices = [device_val.strip()]
    else:
        devices = [str(device_val).strip()]

    tester_val = entry.get("tester", "LTX")
    if isinstance(tester_val, list):
        testers = [str(t).strip() for t in tester_val if str(t).strip()]
    elif isinstance(tester_val, str):
        if "," in tester_val:
            testers = [t.strip() for t in tester_val.split(",") if t.strip()]
        else:
            testers = [tester_val.strip()]
    else:
        testers = [str(tester_val).strip()]

    # Make them unique but preserve order
    seen_dev = set()
    devices_unique = [d for d in devices if not (d in seen_dev or seen_dev.add(d))]
    seen_tst = set()
    testers_unique = [t for t in testers if not (t in seen_tst or seen_tst.add(t))]

    return devices_unique, testers_unique


def resolve_device_names(mpc_text: str) -> List[str]:
    """
    Look up the exact MPC key in mpc_partnumber.json and return the
    list of unique device names it maps to (typically one).

    Raises ValueError if the MPC is not found in the JSON file.
    """
    devices, _ = resolve_mpc_details(mpc_text)
    return devices


# ── Network search ────────────────────────────────────────────────────────────

def search_stdf_files(
    lot_id: str,
    device_names: List[str],
    network_base: Optional[str] = None,
    tester_type: Optional[str] = None,
    logger: LogFunc = None,
    progress_callback: ProgressFunc = None,
) -> List[str]:
    """
    Search the network directory for STDF files whose filename
    contains the lot_id (case-insensitive).

    Uses a ThreadPoolExecutor to parallelize directory scanning over high-latency network paths.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    lot_id_lower = lot_id.strip().lower()
    if not lot_id_lower:
        raise ValueError("Lot ID must not be empty.")

    found_files: List[str] = []
    
    # Resolve default fallback based on tester_type
    if network_base is not None:
        base_dir = network_base
    elif tester_type is not None:
        base_dir = TESTER_NETWORK_PATHS.get(tester_type.upper(), TESTER_NETWORK_PATHS["LTX"])
    else:
        base_dir = TESTER_NETWORK_PATHS["LTX"]

    search_dirs = []
    for device in device_names:
        if device.endswith("*"):
            prefix = device[:-1]
            try:
                if os.path.isdir(base_dir):
                    for entry in os.scandir(base_dir):
                        if entry.is_dir() and entry.name.lower().startswith(prefix.lower()):
                            search_dirs.append(entry.path)
            except OSError as exc:
                _emit_log(f"  Error expanding wildcard '{device}' in {base_dir}: {exc}", logger)
        else:
            search_dirs.append(os.path.join(base_dir, device))

    total_dirs = max(len(search_dirs), 1)

    def scan_single_dir(search_dir: str) -> List[str]:
        res = []
        _emit_log(f"Searching: {search_dir}", logger)
        if not os.path.isdir(search_dir):
            _emit_log(f"  Directory not found or inaccessible: {search_dir}", logger)
            return res
        try:
            for entry in os.scandir(search_dir):
                if not entry.is_file():
                    continue
                name_lower = entry.name.lower()
                _, ext = os.path.splitext(name_lower)
                if ext not in STDF_EXTENSIONS:
                    continue
                if lot_id_lower in name_lower:
                    if _EXCLUDE_FILENAME_RE.search(name_lower):
                        _emit_log(f"  Skipped (excluded pattern): {entry.name}", logger)
                        continue
                    res.append(entry.path)
        except PermissionError as exc:
            _emit_log(f"  Permission denied: {search_dir} — {exc}", logger)
        except OSError as exc:
            _emit_log(f"  Error scanning directory: {exc}", logger)
        return res

    max_workers = min(len(search_dirs), 8)
    if max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_dir = {executor.submit(scan_single_dir, d): d for d in search_dirs}
            completed_dirs = 0
            for future in as_completed(future_to_dir):
                completed_dirs += 1
                if progress_callback:
                    frac = 0.10 + 0.55 * (completed_dirs / total_dirs)
                    d_name = os.path.basename(future_to_dir[future])
                    progress_callback(frac, f"Scanned {d_name}…")
                found_files.extend(future.result())
    else:
        for i, search_dir in enumerate(search_dirs):
            if progress_callback:
                frac = 0.10 + 0.55 * (i / total_dirs)
                dir_name = os.path.basename(search_dir)
                progress_callback(frac, f"Scanning {dir_name}…")
            found_files.extend(scan_single_dir(search_dir))

    if progress_callback:
        progress_callback(0.65, f"Found {len(found_files)} file(s)")

    _emit_log(f"Search complete — {len(found_files)} matching file(s) found.", logger)
    return sorted(found_files, key=lambda p: os.path.basename(p).lower())


# ── File copy ─────────────────────────────────────────────────────────────────

def copy_stdf_files(
    source_paths: List[str],
    lot_id: str,
    logger: LogFunc = None,
    progress_callback: ProgressFunc = None,
) -> str:
    """
    Copy the given STDF files to ``C:\\FTDC\\{lot_id}\\``.

    Creates the destination directory if it does not exist.
    Overwrites files that already exist in the destination.

    Uses ThreadPoolExecutor to copy multiple STDF files concurrently to optimize transfer speed.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    dest_dir = os.path.join(LOCAL_DEST_BASE, lot_id.strip())
    os.makedirs(dest_dir, exist_ok=True)

    total = max(len(source_paths), 1)
    _emit_log(f"Copying {len(source_paths)} file(s) to: {dest_dir}", logger)

    copied = 0
    copied_lock = threading.Lock()

    def copy_one(src_path: str) -> Tuple[bool, str]:
        nonlocal copied
        filename = os.path.basename(src_path)
        dest_path = os.path.join(dest_dir, filename)
        try:
            shutil.copy2(src_path, dest_path)
            with copied_lock:
                copied += 1
            _emit_log(f"  Copied: {filename}", logger)
            return True, filename
        except (OSError, shutil.SameFileError) as exc:
            _emit_log(f"  FAILED to copy {filename}: {exc}", logger)
            return False, filename

    # Copy files in parallel (up to 4 concurrently to utilize network bandwidth without saturating disk I/O)
    max_workers = min(len(source_paths), 4)
    if max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(copy_one, src) for src in source_paths]
            for idx, future in enumerate(as_completed(futures)):
                success, filename = future.result()
                if progress_callback:
                    frac = 0.70 + 0.25 * ((idx + 1) / total)
                    progress_callback(frac, f"Copied {idx + 1}/{total}: {filename}")
    else:
        for idx, src in enumerate(source_paths):
            filename = os.path.basename(src)
            if progress_callback:
                frac = 0.70 + 0.25 * ((idx + 1) / total)
                progress_callback(frac, f"Copying {idx + 1}/{total}: {filename}")
            copy_one(src)

    if progress_callback:
        progress_callback(0.96, "Copy complete")

    _emit_log(f"Done — {copied}/{len(source_paths)} file(s) copied to {dest_dir}", logger)
    return dest_dir
