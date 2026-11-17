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

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_HUB_ROOT = os.path.dirname(os.path.dirname(_CURRENT_DIR))
if _HUB_ROOT not in sys.path:
    pass
    sys.path.insert(0, _HUB_ROOT)

# ── Constants ─────────────────────────────────────────────────────────────────
from core.config import get_asset_path, get_local_dest_base, LOCAL_DEST_BASE
STDF_EXTENSIONS = frozenset({".stdf", ".std", ".bak", ".old", ".stdf_open", ".std_open", ".gz"})
_STDF_EXTENSIONS_TUPLE = tuple(STDF_EXTENSIONS)
STDF_FILEDIALOG_EXTENSIONS = "*.stdf *.std *.bak *.old *.stdf_open *.std_open *.gz"

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

# Valid STDF filenames are defined per MASK and per TESTER platform in the
# external, user-editable ``assets/stdf_filename_conventions.json``. Get STDF
# fetches ONLY server files whose name matches one of those conventions (see
# ``resolve_stdf_filename_conventions`` / ``compile_stdf_filename_patterns``).
_CONVENTIONS_JSON_NAME = "stdf_filename_conventions.json"
_CONV_TEMPLATES_KEY = "templates"
_CONV_MASKS_KEY = "masks"
_CONV_TOKENS_KEY = "_tokens"
_MASK_LENGTH = 5
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
_DEFAULT_CONVENTION_TOKENS = {"N": r"\d+", "timestamp": r"\d+", "time": r"\d+", "RJN": r"RJ\d+", "rjn": r"rj\d+"}

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
    return get_asset_path(filename)

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
    _load_stdf_filename_conventions_file.cache_clear()


# ── Valid STDF filename conventions (per mask / tester) ───────────────────────

@functools.lru_cache(maxsize=1)
def _load_stdf_filename_conventions_file() -> Dict:
    """Load and return the full stdf_filename_conventions.json document."""
    path = _get_json_path(_CONVENTIONS_JSON_NAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as exc:
        raise RuntimeError(f"STDF filename conventions file not found: '{path}'") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in STDF filename conventions file '{path}': {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"STDF filename conventions file '{path}' must contain a JSON object.")
    return data


def get_mask_from_mpc(mpc_or_mask: str) -> str:
    """Return the 5-character mask (upper-case) from a 12-char MPC or a mask."""
    return str(mpc_or_mask or "").strip().upper()[:_MASK_LENGTH]


def _normalize_convention_set(raw, templates: Dict, where: str) -> Dict[str, List[str]]:
    """Turn a conventions entry (template name or category->pattern(s) object)
    into ``{category: [pattern, ...]}``."""
    if isinstance(raw, str):
        tmpl = templates.get(raw.strip()) or templates.get(raw.strip().upper())
        if tmpl is None:
            raise ValueError(f"{where} references unknown template '{raw}' in {_CONVENTIONS_JSON_NAME}.")
        raw = tmpl
    if not isinstance(raw, dict):
        raise ValueError(f"{where} in {_CONVENTIONS_JSON_NAME} must be a template name or an object.")
    out: Dict[str, List[str]] = {}
    for category, patterns in raw.items():
        if str(category).startswith("_"):
            continue
        if isinstance(patterns, str):
            patterns = [patterns]
        if not isinstance(patterns, list):
            raise ValueError(f"{where} -> '{category}' must be a string or a list of strings.")
        cleaned = [str(p).strip() for p in patterns if str(p).strip()]
        if cleaned:
            out[str(category).strip().upper()] = cleaned
    if not out:
        raise ValueError(f"{where} in {_CONVENTIONS_JSON_NAME} defines no filename conventions.")
    return out


def resolve_stdf_filename_conventions(
    mpc_or_mask: str, tester: Optional[str] = None
) -> Dict[str, List[str]]:
    """Return ``{category: [convention, ...]}`` of valid STDF filenames for the
    mask of ``mpc_or_mask`` on ``tester``.

    If ``tester`` is None, the conventions of EVERY tester configured for the
    mask are merged. Raises ValueError if the mask (or the mask/tester pair) is
    not configured in stdf_filename_conventions.json.
    """
    data = _load_stdf_filename_conventions_file()
    templates_raw = data.get(_CONV_TEMPLATES_KEY, {}) or {}
    templates = {str(k).strip().upper(): v for k, v in templates_raw.items()} if isinstance(templates_raw, dict) else {}
    masks_raw = data.get(_CONV_MASKS_KEY, {}) or {}
    masks = {str(k).strip().upper(): v for k, v in masks_raw.items() if not str(k).startswith("_")}

    mask = get_mask_from_mpc(mpc_or_mask)
    if mask not in masks:
        raise ValueError(
            f"Mask '{mask}' has no valid STDF filename convention configured.\n\n"
            f"Please add it to assets\\{_CONVENTIONS_JSON_NAME} (under \"masks\") and restart the app."
        )
    per_tester = masks[mask]
    if not isinstance(per_tester, dict):
        raise ValueError(f"Mask '{mask}' entry in {_CONVENTIONS_JSON_NAME} must be an object of tester -> conventions.")
    per_tester = {str(k).strip().upper(): v for k, v in per_tester.items() if not str(k).startswith("_")}

    if tester is not None:
        tester_key = str(tester).strip().upper()
        if tester_key not in per_tester:
            configured = ", ".join(sorted(per_tester)) or "(none)"
            raise ValueError(
                f"Mask '{mask}' has no valid STDF filename convention for tester '{tester}' "
                f"(configured: {configured}).\n\nPlease update assets\\{_CONVENTIONS_JSON_NAME} and restart the app."
            )
        selected = [(tester_key, per_tester[tester_key])]
    else:
        selected = list(per_tester.items())

    merged: Dict[str, List[str]] = {}
    for tester_key, raw in selected:
        conv = _normalize_convention_set(raw, templates, f"Mask '{mask}' / tester '{tester_key}'")
        for category, patterns in conv.items():
            bucket = merged.setdefault(category, [])
            bucket.extend(p for p in patterns if p not in bucket)
    return merged


def compile_stdf_filename_patterns(
    lot_id: str, conventions: Dict[str, List[str]]
) -> List[Tuple[str, "re.Pattern"]]:
    """Compile conventions into case-insensitive regexes for one Lot ID.

    Matches either at the START of the filename or in the MIDDLE (e.g. preceded
    by a device prefix like 'ATA5293_'). A non-alphanumeric boundary prevents
    partial substring matches when one lot ID contains another. Whatever
    follows the convention must begin with '_' or '.' (e.g. '_ft1.stdf.old' or
    '.std.bak') or be the end of the filename.
    """
    lot = str(lot_id or "").strip()
    if not lot:
        raise ValueError("Lot ID must not be empty.")
    data = _load_stdf_filename_conventions_file()
    tokens = dict(_DEFAULT_CONVENTION_TOKENS)
    user_tokens = data.get(_CONV_TOKENS_KEY, {})
    if isinstance(user_tokens, dict):
        tokens.update({str(k): str(v) for k, v in user_tokens.items() if str(k) != "lot"})

    compiled: List[Tuple[str, "re.Pattern"]] = []
    for category, patterns in conventions.items():
        for convention in patterns:
            parts: List[str] = []
            pos = 0
            for m in _PLACEHOLDER_RE.finditer(convention):
                parts.append(re.escape(convention[pos:m.start()]))
                name = m.group(1)
                if name == "lot":
                    parts.append(re.escape(lot))
                elif name in tokens:
                    parts.append(f"(?:{tokens[name]})")
                else:
                    raise ValueError(
                        f"Unknown placeholder '{{{name}}}' in convention '{convention}' ({_CONVENTIONS_JSON_NAME})."
                    )
                pos = m.end()
            parts.append(re.escape(convention[pos:]))
            regex = r"(?:^|[^A-Za-z0-9])" + "".join(parts) + r"(?=[_.]|$)"
            compiled.append((category, re.compile(regex, re.IGNORECASE)))
    return compiled


def match_stdf_filename_convention(
    filename: str, compiled_patterns: List[Tuple[str, "re.Pattern"]]
) -> Optional[str]:
    """Return the matched category (e.g. 'FIRST PASS', 'RETEST', 'QC',
    'QC RETEST') for ``filename``, or None if it matches no valid convention."""
    for category, pattern in compiled_patterns:
        if pattern.search(filename):
            return category
    return None


def load_mpc_partnumber_map() -> Dict[str, Dict[str, str]]:
    """Load only the MPC mapping section from mpc_partnumber.json.

    Each entry is ``{"device": "...", "tester": "V93K"|"LTX"}``.
    Keys are normalized case-insensitively (uppercase).
    """
    data = _load_mpc_partnumber_file()
    # Support both the new schema (mpcs nested under "mpcs") and a legacy
    # flat schema (MPC keys at the top level) for backward compatibility.
    raw_map = data.get(_MPCS_KEY, {}) if (isinstance(data, dict) and _MPCS_KEY in data) else (data if isinstance(data, dict) else {})
    normalized_map = {}
    for k, v in raw_map.items():
        if not str(k).startswith("_"):
            normalized_map[str(k).strip().upper()] = v
    return normalized_map


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
    mpc_key = mpc_text.strip().upper()

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


def save_device_configuration(
    mpc: str,
    devices,
    testers,
    wxy_tests: Optional[Dict[str, int]] = None,
    custom_conventions: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    """Add or update a device/MPC configuration across all JSON files:
      1. ``assets/mpc_partnumber.json``          (MPC -> device & tester)
      2. ``assets/stdf_filename_conventions.json`` (5-char mask -> tester convention)
      3. ``assets/mask_wxy_map.json``            (5-char mask -> wafer/x/y test numbers, when wxy_tests is supplied)

    Reloads all in-memory configuration caches immediately so no app restart is needed.
    """
    mpc_key = str(mpc or "").strip().upper()
    if not re.fullmatch(r"[A-Za-z0-9]{12}", mpc_key):
        raise ValueError(
            f"Invalid MPC '{mpc_key}'. MPC must be exactly 12 alphanumeric characters (e.g. SG4064RTBA10)."
        )
    mask_key = mpc_key[:_MASK_LENGTH]

    # Normalize device folder pattern(s)
    if isinstance(devices, str):
        dev_list = [d.strip() for d in devices.split(",") if d.strip()]
    elif isinstance(devices, (list, tuple)):
        dev_list = [str(d).strip() for d in devices if str(d).strip()]
    else:
        dev_list = []
    seen_dev: Set[str] = set()
    dev_unique = [d for d in dev_list if not (d in seen_dev or seen_dev.add(d))]
    if not dev_unique:
        raise ValueError("Device folder name/pattern is required (e.g. PIC32CX5109BZ*).")

    # Normalize tester(s)
    if isinstance(testers, str):
        tst_list = [t.strip().upper() for t in testers.split(",") if t.strip()]
    elif isinstance(testers, (list, tuple)):
        tst_list = [str(t).strip().upper() for t in testers if str(t).strip()]
    else:
        tst_list = []
    seen_tst: Set[str] = set()
    tst_unique = [t for t in tst_list if not (t in seen_tst or seen_tst.add(t))]
    if not tst_unique:
        raise ValueError("At least one Tester platform (V93K or LTX) is required.")

    tester_paths, _ = load_network_paths()
    unknown_tst = [t for t in tst_unique if t not in tester_paths]
    if unknown_tst:
        configured = ", ".join(sorted(tester_paths)) or "V93K, LTX"
        raise ValueError(
            f"Unknown tester(s): {', '.join(unknown_tst)}. Configured testers: {configured}."
        )

    # 1. Update assets/mpc_partnumber.json
    mpc_doc = json.loads(json.dumps(_load_mpc_partnumber_file()))
    if not isinstance(mpc_doc, dict):
        raise RuntimeError("mpc_partnumber.json root must be a JSON object.")
    mpcs_section = mpc_doc.setdefault(_MPCS_KEY, {})
    mpc_entry = {
        "device": dev_unique[0] if len(dev_unique) == 1 else dev_unique,
        "tester": tst_unique[0] if len(tst_unique) == 1 else tst_unique,
    }
    mpcs_section[mpc_key] = mpc_entry
    mpc_tmp = _MPC_PARTNUMBER_JSON_PATH + ".tmp"
    with open(mpc_tmp, "w", encoding="utf-8") as f:
        json.dump(mpc_doc, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(mpc_tmp, _MPC_PARTNUMBER_JSON_PATH)

    # 2. Update assets/stdf_filename_conventions.json for this 5-char mask
    conv_path = _get_json_path(_CONVENTIONS_JSON_NAME)
    conv_doc = json.loads(json.dumps(_load_stdf_filename_conventions_file()))
    masks_section = conv_doc.setdefault(_CONV_MASKS_KEY, {})
    mask_convs = masks_section.get(mask_key)
    if not isinstance(mask_convs, dict):
        mask_convs = {}
    for tst in tst_unique:
        if custom_conventions and isinstance(custom_conventions, dict):
            mask_convs[tst] = custom_conventions
        elif tst not in mask_convs:
            mask_convs[tst] = tst
    masks_section[mask_key] = mask_convs
    conv_tmp = conv_path + ".tmp"
    with open(conv_tmp, "w", encoding="utf-8") as f:
        json.dump(conv_doc, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(conv_tmp, conv_path)

    # 3. Optionally update assets/mask_wxy_map.json for this 5-char mask
    saved_wxy = None
    if wxy_tests is not None:
        from apps.ftdc_checker.guid_analysis import save_mask_wxy_entry
        saved_wxy = save_mask_wxy_entry(
            mask_key,
            int(wxy_tests["wafer"]),
            int(wxy_tests["x"]),
            int(wxy_tests["y"]),
            uid=wxy_tests.get("uid"),
            bt=wxy_tests.get("bt"),
            zb=wxy_tests.get("zb"),
            macid_prefix=wxy_tests.get("macid_prefix"),
            bt_prefix=wxy_tests.get("bt_prefix"),
            zb_prefix=wxy_tests.get("zb_prefix"),
            macid_invalid=wxy_tests.get("macid_invalid"),
        )

    reload_config()
    return {
        "mpc": mpc_key,
        "mask": mask_key,
        "device": mpc_entry["device"],
        "tester": mpc_entry["tester"],
        "wxy": saved_wxy,
        "conventions": mask_convs,
    }


def delete_mpc_configuration(mpc: str) -> bool:
    """Remove an MPC entry from assets/mpc_partnumber.json and reload in-memory caches."""
    mpc_key = str(mpc or "").strip().upper()
    if not mpc_key:
        return False
    mpc_doc = json.loads(json.dumps(_load_mpc_partnumber_file()))
    mpcs_section = mpc_doc.get(_MPCS_KEY, {}) if isinstance(mpc_doc, dict) else {}
    removed = False
    for k in list(mpcs_section.keys()):
        if str(k).strip().upper() == mpc_key:
            del mpcs_section[k]
            removed = True
    if removed:
        mpc_tmp = _MPC_PARTNUMBER_JSON_PATH + ".tmp"
        with open(mpc_tmp, "w", encoding="utf-8") as f:
            json.dump(mpc_doc, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(mpc_tmp, _MPC_PARTNUMBER_JSON_PATH)
        reload_config()
    return removed



# ── Local destination cache ───────────────────────────────────────────────────

def get_local_dest_dir(lot_id: str) -> str:
    """Return the local destination folder for a lot: ``C:\\FTDC\\{lot_id}``."""
    base = get_local_dest_base() if callable(get_local_dest_base) else LOCAL_DEST_BASE
    return os.path.join(base, lot_id.strip())


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


# ── SMB server-side filter pushdown (Windows FindFirstFileExW) ────────────────
#
# ``os.scandir`` asks the file server for EVERY entry of a directory, so a
# device folder holding tens of thousands of datalogs costs hundreds of SMB2
# QUERY_DIRECTORY round trips just to find the handful of files of one lot.
# ``FindFirstFileExW`` with a wildcard pattern (e.g. ``*mmt-272500536.800*``)
# sends that pattern INSIDE the SMB2 QUERY_DIRECTORY request, so the server
# filters in its own memory and returns only matching entries.
#
# The pattern is only a coarse pre-filter: every returned name is still checked
# by the exact valid-filename-convention regexes, so results are identical to
# the ``os.scandir`` path. Any unexpected failure falls back to ``os.scandir``.

# Set to False to force the plain os.scandir path everywhere.
_SMB_PUSHDOWN_ENABLED = True

_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILETIME_EPOCH_DIFF_100NS = 116444736000000000  # 1601-01-01 -> 1970-01-01
_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3
_ERROR_NO_MORE_FILES = 18
_ERROR_BAD_NETPATH = 53
_ERROR_BAD_NET_NAME = 67
_ERROR_DIRECTORY = 267
_FIND_EX_INFO_BASIC = 1              # skip 8.3 short names (faster, long-name match only)
_FIND_EX_SEARCH_NAME_MATCH = 0
_FIND_EX_SEARCH_LIMIT_TO_DIRECTORIES = 1
_FIND_FIRST_EX_LARGE_FETCH = 2       # larger SMB directory query buffer
_WILDCARD_CHARS = frozenset('*?<>"')


class _PushdownStat:
    """Minimal ``os.stat_result`` stand-in (size + mtime) built from
    WIN32_FIND_DATAW, so no extra network stat call is needed."""
    __slots__ = ("st_size", "st_mtime_ns", "st_mtime")

    def __init__(self, size: int, mtime_ns: int):
        self.st_size = size
        self.st_mtime_ns = mtime_ns
        self.st_mtime = mtime_ns / 1_000_000_000


class _PushdownEntry:
    """Minimal ``os.DirEntry`` stand-in returned by the pushdown scanner."""
    __slots__ = ("name", "path", "_is_dir", "_stat")

    def __init__(self, directory: str, name: str, is_dir: bool, size: int, mtime_ns: int):
        self.name = name
        self.path = os.path.join(directory, name)
        self._is_dir = is_dir
        self._stat = _PushdownStat(size, mtime_ns)

    def is_file(self) -> bool:
        return not self._is_dir

    def is_dir(self) -> bool:
        return self._is_dir

    def stat(self) -> _PushdownStat:
        return self._stat


class _PushdownUnavailable(Exception):
    """Raised when server-side filtering cannot be used; caller falls back."""


_kernel32_find = None


def _get_kernel32_find():
    """Lazily bind FindFirstFileExW / FindNextFileW / FindClose (Windows only)."""
    global _kernel32_find
    if _kernel32_find is not None:
        return _kernel32_find
    import ctypes
    from ctypes import wintypes

    class WIN32_FIND_DATAW(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("dwReserved0", wintypes.DWORD),
            ("dwReserved1", wintypes.DWORD),
            ("cFileName", wintypes.WCHAR * 260),
            ("cAlternateFileName", wintypes.WCHAR * 14),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    find_first = k32.FindFirstFileExW
    find_first.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(WIN32_FIND_DATAW),
        ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    find_first.restype = wintypes.HANDLE
    find_next = k32.FindNextFileW
    find_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(WIN32_FIND_DATAW)]
    find_next.restype = wintypes.BOOL
    find_close = k32.FindClose
    find_close.argtypes = [wintypes.HANDLE]
    find_close.restype = wintypes.BOOL
    invalid = wintypes.HANDLE(-1).value

    _kernel32_find = (ctypes, WIN32_FIND_DATAW, find_first, find_next, find_close, invalid)
    return _kernel32_find


def _pushdown_scandir(directory: str, pattern: str, dirs_only: bool = False) -> List[_PushdownEntry]:
    """List entries of ``directory`` whose names match the Windows wildcard
    ``pattern`` using server-side filtering (FindFirstFileExW).

    Returns an empty list when nothing matches. Raises ``FileNotFoundError``
    when the directory itself is missing/unreachable (same as ``os.scandir``),
    and ``_PushdownUnavailable`` for any other condition so the caller can
    fall back to ``os.scandir``.
    """
    if os.name != "nt" or not _SMB_PUSHDOWN_ENABLED:
        raise _PushdownUnavailable("server-side filtering is only available on Windows")
    try:
        ctypes, FindData, find_first, find_next, find_close, invalid = _get_kernel32_find()
    except Exception as exc:  # ctypes/kernel32 unavailable
        raise _PushdownUnavailable(f"kernel32 binding failed: {exc}") from exc

    data = FindData()
    query = os.path.join(directory, pattern)
    search_op = _FIND_EX_SEARCH_LIMIT_TO_DIRECTORIES if dirs_only else _FIND_EX_SEARCH_NAME_MATCH
    handle = find_first(query, _FIND_EX_INFO_BASIC, ctypes.byref(data), search_op, None, _FIND_FIRST_EX_LARGE_FETCH)
    if handle == invalid or handle is None:
        err = ctypes.get_last_error()
        if err == _ERROR_FILE_NOT_FOUND:
            # Directory exists but no entry matches the pattern.
            return []
        if err in (_ERROR_PATH_NOT_FOUND, _ERROR_BAD_NETPATH, _ERROR_BAD_NET_NAME, _ERROR_DIRECTORY):
            raise FileNotFoundError(err, f"Directory not found or unreachable (Win32 error {err})", directory)
        raise _PushdownUnavailable(f"FindFirstFileExW failed (Win32 error {err})")

    entries: List[_PushdownEntry] = []
    try:
        while True:
            name = data.cFileName
            if name not in (".", ".."):
                is_dir = bool(data.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY)
                if not dirs_only or is_dir:
                    size = (int(data.nFileSizeHigh) << 32) | int(data.nFileSizeLow)
                    ft = (int(data.ftLastWriteTime.dwHighDateTime) << 32) | int(data.ftLastWriteTime.dwLowDateTime)
                    mtime_ns = (ft - _FILETIME_EPOCH_DIFF_100NS) * 100
                    entries.append(_PushdownEntry(directory, name, is_dir, size, mtime_ns))
            if not find_next(handle, ctypes.byref(data)):
                err = ctypes.get_last_error()
                if err != _ERROR_NO_MORE_FILES:
                    raise _PushdownUnavailable(f"FindNextFileW failed (Win32 error {err})")
                break
    finally:
        find_close(handle)
    return entries


def scandir_filtered(directory: str, pattern: str, dirs_only: bool = False, logger: LogFunc = None):
    """Return directory entries matching ``pattern`` (Windows wildcard),
    preferring SMB server-side filtering and falling back to ``os.scandir``.

    Returns ``(entries, used_pushdown)``. With the fallback, ALL entries are
    returned (no client-side wildcard filtering), because callers always apply
    their own exact checks afterwards. ``FileNotFoundError`` / ``OSError`` for
    an unreachable directory propagate exactly like ``os.scandir``.
    """
    # Only push down simple "*literal*" / "literal*" patterns whose literal part
    # holds no wildcard characters (so the server can never under-match).
    literal = pattern.strip("*")
    if literal and not (set(literal) & _WILDCARD_CHARS):
        try:
            return _pushdown_scandir(directory, pattern, dirs_only=dirs_only), True
        except _PushdownUnavailable as exc:
            if logger and os.name == "nt" and _SMB_PUSHDOWN_ENABLED:
                _emit_log(f"  Server-side filter unavailable for {directory} ({exc}); using full directory scan.", logger)
    with os.scandir(directory) as it:
        entries = list(it)
    if dirs_only:
        entries = [e for e in entries if e.is_dir()]
    return entries, False


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
    mpc: Optional[str] = None,
    filename_patterns: Optional[List[Tuple[str, "re.Pattern"]]] = None,
) -> List[str]:
    """
    Search the network directory(ies) for STDF files of ``lot_id`` whose
    filename matches a VALID STDF filename convention.

    Valid filenames are defined per mask and tester platform in
    ``assets/stdf_filename_conventions.json`` (e.g. V93K RETEST
    ``{lot}_w_rj{N}_{timestamp}``, LTX RETEST ``{lot}.rj{N}_w_{time}``). Supply
    either pre-compiled ``filename_patterns`` or the ``mpc`` (its mask plus
    ``tester_type`` select the conventions). Files that do not match any
    configured convention are ignored.

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
    import threading
    import time as _time

    lot_id_lower = lot_id.strip().lower()
    if not lot_id_lower:
        raise ValueError("Lot ID must not be empty.")

    # ── Resolve the valid STDF filename conventions (per mask / tester) ─────
    if filename_patterns is None:
        if not mpc:
            raise ValueError(
                "search_stdf_files requires 'mpc' or 'filename_patterns' to apply the "
                f"valid STDF filename conventions ({_CONVENTIONS_JSON_NAME})."
            )
        conventions = resolve_stdf_filename_conventions(mpc, tester_type)
        filename_patterns = compile_stdf_filename_patterns(lot_id, conventions)
        if logger:
            _emit_log(
                f"  Valid STDF filename conventions (mask '{get_mask_from_mpc(mpc)}'"
                f"{', tester ' + repr(tester_type) if tester_type else ''}):",
                logger,
            )
            for category, patterns in conventions.items():
                _emit_log(f"    - {category}: {', '.join(patterns)}", logger)
    if not filename_patterns:
        raise ValueError("No valid STDF filename conventions resolved; nothing can be fetched.")

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
                            # Server-side filter "PREFIX*" (directories only),
                            # with os.scandir fallback; prefix re-checked below.
                            dir_entries, _ = scandir_filtered(base_dir, prefix + "*", dirs_only=True, logger=logger)
                            for entry in dir_entries:
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
        """Return the full path if the file matches the lot id, has a valid STDF
        extension, matches a configured valid STDF filename convention and is
        not already cached; otherwise None.

        ``entry`` is an ``os.scandir`` DirEntry so its cached ``stat()`` can be
        reused for the size/mtime comparison without an extra network call.
        """
        filename = entry.name
        name_lower = filename.lower()
        if not name_lower.endswith(_STDF_EXTENSIONS_TUPLE):
            return None
        # Cheap pre-filter before running the convention regexes.
        if lot_id_lower not in name_lower:
            return None
        if match_stdf_filename_convention(filename, filename_patterns) is None:
            if logger:
                _emit_log(f"  Skipped (not a valid STDF filename convention): {filename}", logger)
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

    # Server-side filter pattern: only names containing the Lot ID are returned
    # by the file server. Exact convention checks still run in _match_file.
    lot_pattern = f"*{lot_id.strip()}*"
    pushdown_stats = {"dirs": 0, "pushdown_dirs": 0, "lot_hits": 0}
    stats_lock = threading.Lock()

    def scan_flat_dir(search_dir: str, allow_pushdown: bool = True) -> List[str]:
        """Scan a single directory one level deep (server-side filtered when possible)."""
        res: List[str] = []
        try:
            if allow_pushdown:
                entries, used_pushdown = scandir_filtered(search_dir, lot_pattern, logger=logger)
            else:
                with os.scandir(search_dir) as it:
                    entries, used_pushdown = list(it), False
            if logger:
                how = f"server-side filter '{lot_pattern}'" if used_pushdown else "full directory scan"
                _emit_log(f"Searching: {search_dir} ({how}, {len(entries)} entr{'y' if len(entries) == 1 else 'ies'} returned)", logger)
            lot_hits = 0
            for entry in entries:
                if entry.is_file():
                    if lot_id_lower in entry.name.lower():
                        lot_hits += 1
                    matched = _match_file(entry)
                    if matched:
                        res.append(matched)
            with stats_lock:
                pushdown_stats["dirs"] += 1
                pushdown_stats["lot_hits"] += lot_hits
                if used_pushdown:
                    pushdown_stats["pushdown_dirs"] += 1
        except (FileNotFoundError, PermissionError, OSError) as exc:
            if logger:
                _emit_log(f"  Directory inaccessible or search failed: {search_dir} — {exc}", logger)
        return res

    # Increase worker pool for network directories (scanning over UNC network shares is 99% I/O wait)
    def _run_scan(allow_pushdown: bool) -> List[str]:
        results: List[str] = []
        workers = min(len(flat_dirs), 16)
        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_dir = {
                    executor.submit(scan_flat_dir, d, allow_pushdown): d for d in flat_dirs
                }
                completed_dirs = 0
                for future in as_completed(future_to_dir):
                    completed_dirs += 1
                    if progress_callback:
                        frac = 0.10 + 0.55 * (completed_dirs / total_dirs)
                        d_name = os.path.basename(future_to_dir[future])
                        progress_callback(frac, f"Scanned {d_name}…")
                    results.extend(future.result())
        else:
            for i, search_dir in enumerate(flat_dirs):
                if progress_callback:
                    frac = 0.10 + 0.55 * (i / total_dirs)
                    dir_name = os.path.basename(search_dir)
                    progress_callback(frac, f"Scanning {dir_name}…")
                results.extend(scan_flat_dir(search_dir, allow_pushdown))
        return results

    scan_t0 = _time.perf_counter()
    found_files.extend(_run_scan(allow_pushdown=True))

    # Safety net: if server-side filtering was used but returned NO file whose
    # name contains the Lot ID (e.g. an unexpected case-sensitive share), verify
    # with a full os.scandir pass so a lot's files can never be silently missed.
    if pushdown_stats["pushdown_dirs"] > 0 and pushdown_stats["lot_hits"] == 0:
        if logger:
            _emit_log(
                "  Server-side filter returned no files for this Lot ID — verifying with a full directory scan…",
                logger,
            )
        pushdown_stats.update(dirs=0, pushdown_dirs=0, lot_hits=0)
        found_files.extend(_run_scan(allow_pushdown=False))

    if logger:
        _emit_log(
            f"  Directory search took {_time.perf_counter() - scan_t0:.2f}s "
            f"({pushdown_stats['pushdown_dirs']}/{pushdown_stats['dirs']} folder(s) server-side filtered).",
            logger,
        )

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

# Type alias for the per-file progress callback: (fraction_of_current_file, message)
PerFileProgressFunc = Optional[Callable[[float, str], None]]
# Type alias for the file-counter callback: (file_index_1based, total_files, filename)
FileCounterFunc = Optional[Callable[[int, int, str], None]]
# Type alias for live copy stats callback: (stats_label_text)
CopyStatsFunc = Optional[Callable[[str], None]]

# Minimum byte interval between progress reports to avoid flooding the UI timer.
_PROGRESS_REPORT_INTERVAL = 1 * 1024 * 1024  # 1 MB


def _format_size_pair(bytes_copied: int, total_bytes: int) -> str:
    """Format copied/total bytes using matched units (e.g. '1.8GB/3.4GB' or '420.0MB/850.0MB')."""
    total = max(int(total_bytes), 0)
    copied = max(int(bytes_copied), 0)
    gb = 1024 ** 3
    mb = 1024 ** 2
    kb = 1024
    if total >= gb:
        return f"{copied / gb:.1f}GB/{total / gb:.1f}GB"
    if total >= mb:
        return f"{copied / mb:.1f}MB/{total / mb:.1f}MB"
    if total >= kb:
        return f"{copied / kb:.0f}KB/{total / kb:.0f}KB"
    return f"{copied}B/{total}B"


def _format_transfer_speed(bps: float) -> str:
    """Format transfer speed in bytes/sec (e.g. '410MB/s')."""
    if bps <= 0:
        return "0MB/s"
    gb = 1024 ** 3
    mb = 1024 ** 2
    kb = 1024
    if bps >= gb:
        return f"{bps / gb:.2f}GB/s"
    if bps >= 10 * mb:
        return f"{bps / mb:.0f}MB/s"
    if bps >= mb:
        return f"{bps / mb:.1f}MB/s"
    return f"{bps / kb:.0f}KB/s"


def _format_eta(seconds: float) -> str:
    """Format ETA in M:SSs (e.g. '0:41s')."""
    sec = max(0, int(round(seconds)))
    mins, s = divmod(sec, 60)
    hrs, mins = divmod(mins, 60)
    if hrs > 0:
        return f"{hrs}:{mins:02d}:{s:02d}s"
    return f"{mins}:{s:02d}s"


def format_copy_stats_text(
    file_index: int,
    total_files: int,
    bytes_copied: int,
    file_size: int,
    elapsed_sec: float,
) -> str:
    """Build live copy telemetry text, e.g.:
    'File 2/5 | 1.8GB/3.4GB | 410MB/s | ETA 0:41s'
    """
    speed_bps = (bytes_copied / elapsed_sec) if elapsed_sec > 0.001 else 0.0
    remaining = max(0, file_size - bytes_copied)
    eta_sec = (remaining / speed_bps) if speed_bps > 0 else 0.0
    return (
        f"File {file_index}/{total_files} | "
        f"{_format_size_pair(bytes_copied, file_size)} | "
        f"{_format_transfer_speed(speed_bps)} | "
        f"ETA {_format_eta(eta_sec)}"
    )


def format_parallel_copy_stats_text(
    completed_files: int,
    total_files: int,
    bytes_copied: int,
    total_bytes: int,
    elapsed_sec: float,
) -> str:
    """Build Option 2 live aggregate copy telemetry text across parallel copy workers, e.g.:
    'Done 2/5 | 1.8GB/3.4GB | 460MB/s | ETA 0:04s'
    """
    speed_bps = (bytes_copied / elapsed_sec) if elapsed_sec > 0.001 else 0.0
    remaining = max(0, total_bytes - bytes_copied)
    eta_sec = (remaining / speed_bps) if speed_bps > 0 else 0.0
    return (
        f"Done {completed_files}/{total_files} | "
        f"{_format_size_pair(bytes_copied, total_bytes)} | "
        f"{_format_transfer_speed(speed_bps)} | "
        f"ETA {_format_eta(eta_sec)}"
    )


_kernel32_copy = None


def _get_kernel32_copy():
    """Lazily bind CopyFileExW and LPPROGRESS_ROUTINE (Windows only)."""
    global _kernel32_copy
    if _kernel32_copy is not None:
        return _kernel32_copy
    import ctypes
    from ctypes import wintypes

    LPPROGRESS_ROUTINE = ctypes.WINFUNCTYPE(
        wintypes.DWORD,
        ctypes.c_longlong,  # TotalFileSize
        ctypes.c_longlong,  # TotalBytesTransferred
        ctypes.c_longlong,  # StreamSize
        ctypes.c_longlong,  # StreamBytesTransferred
        wintypes.DWORD,     # dwStreamNumber
        wintypes.DWORD,     # dwCallbackReason
        wintypes.HANDLE,    # hSourceFile
        wintypes.HANDLE,    # hDestinationFile
        wintypes.LPVOID,    # lpData
    )
    copy_file_ex = ctypes.windll.kernel32.CopyFileExW
    copy_file_ex.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        LPPROGRESS_ROUTINE,
        wintypes.LPVOID,
        wintypes.LPBOOL,
        wintypes.DWORD,
    ]
    copy_file_ex.restype = wintypes.BOOL
    _kernel32_copy = (LPPROGRESS_ROUTINE, copy_file_ex)
    return _kernel32_copy


def _fast_copy_file(src: str, dst: str, buffer_size: int = 8 * 1024 * 1024):
    """Copy file optimized for 1GB+ files over Windows SMB3 network shares.

    Copies to ``{dst}.part`` first and atomically renames to ``dst`` only when
    the transfer succeeds. Uses ``kernel32.CopyFileExW`` on Windows and falls
    back to an 8MB buffered Python loop on non-Windows systems.
    """
    _fast_copy_file_with_progress(src, dst, progress_callback=None, buffer_size=buffer_size)


def _fast_copy_file_with_progress(
    src: str,
    dst: str,
    progress_callback: PerFileProgressFunc = None,
    buffer_size: int = 8 * 1024 * 1024,
    file_index: int = 1,
    total_files: int = 1,
    stats_callback: CopyStatsFunc = None,
    byte_progress_callback: Optional[Callable[[int, int], None]] = None,
) -> None:
    """Copy a file to ``{dst}.part`` first, then rename to ``dst`` on success.

    On Windows, uses ``kernel32.CopyFileExW`` with an ``LPPROGRESS_ROUTINE``
    callback for kernel-mode SMB3/DMA copying with live byte progress.
    On non-Windows systems (or if ``CopyFileExW`` is unavailable), falls back
    to a Python buffered read/write loop.
    """
    import time

    try:
        file_size = os.path.getsize(src)
    except OSError:
        file_size = 0

    filename = os.path.basename(src)
    part_dst = dst + ".part"
    t0 = time.perf_counter()

    if progress_callback:
        progress_callback(0.0, f"Copying {filename}…")
    if stats_callback:
        stats_callback(format_copy_stats_text(file_index, total_files, 0, file_size, 0.0))
    if byte_progress_callback:
        byte_progress_callback(0, file_size)

    copied_via_win32 = False
    try:
        if os.name == "nt":
            try:
                LPPROGRESS_ROUTINE, copy_file_ex = _get_kernel32_copy()
                last_state = [0, t0]  # [last_reported_bytes, last_reported_time]

                def _win32_progress_cb(
                    total_sz, transferred_sz, _stream_sz, _stream_trans,
                    _stream_num, _reason, _hsrc, _hdst, _lpdata
                ):
                    now = time.perf_counter()
                    tot = int(total_sz) if total_sz > 0 else file_size
                    cur = max(0, int(transferred_sz))
                    if (
                        cur == 0
                        or cur >= tot
                        or (cur - last_state[0] >= _PROGRESS_REPORT_INTERVAL)
                        or (now - last_state[1] >= 0.05)
                    ):
                        last_state[0] = cur
                        last_state[1] = now
                        frac = (cur / tot) if tot > 0 else 1.0
                        if progress_callback:
                            progress_callback(min(frac, 0.99), f"Copying {filename}…")
                        if stats_callback:
                            stats_callback(
                                format_copy_stats_text(file_index, total_files, cur, tot, now - t0)
                            )
                        if byte_progress_callback:
                            byte_progress_callback(cur, tot)
                    return 0  # PROGRESS_CONTINUE

                c_cb = LPPROGRESS_ROUTINE(_win32_progress_cb)
                res = copy_file_ex(
                    os.path.abspath(src),
                    os.path.abspath(part_dst),
                    c_cb,
                    None,
                    None,
                    0,
                )
                if res:
                    copied_via_win32 = True
            except Exception:
                copied_via_win32 = False

        if not copied_via_win32:
            # Python buffered loop fallback for non-Windows systems (or if CopyFileExW failed)
            bytes_copied = 0
            last_reported_bytes = 0
            last_reported_time = t0

            with open(src, "rb") as fsrc, open(part_dst, "wb") as fdst:
                if file_size > 0:
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
                    now = time.perf_counter()
                    if (
                        (bytes_copied - last_reported_bytes >= _PROGRESS_REPORT_INTERVAL)
                        or (now - last_reported_time >= 0.05)
                    ):
                        last_reported_bytes = bytes_copied
                        last_reported_time = now
                        frac = (bytes_copied / file_size) if file_size > 0 else 1.0
                        if progress_callback:
                            progress_callback(min(frac, 0.99), f"Copying {filename}…")
                        if stats_callback:
                            stats_callback(
                                format_copy_stats_text(
                                    file_index, total_files, bytes_copied, file_size, now - t0
                                )
                            )
                        if byte_progress_callback:
                            byte_progress_callback(bytes_copied, file_size)
            try:
                shutil.copystat(src, part_dst)
            except OSError:
                pass

        # Atomically rename {filename}.part -> {filename} only after full copy succeeds
        os.replace(part_dst, dst)
    except Exception:
        if os.path.exists(part_dst):
            try:
                os.remove(part_dst)
            except OSError:
                pass
        raise

    elapsed = max(time.perf_counter() - t0, 0.001)
    if stats_callback:
        stats_callback(format_copy_stats_text(file_index, total_files, file_size, file_size, elapsed))
    if progress_callback:
        progress_callback(1.0, f"Copied {filename}")
    if byte_progress_callback:
        byte_progress_callback(file_size, file_size)


def copy_stdf_files(
    source_paths: List[str],
    lot_id: str,
    logger: LogFunc = None,
    progress_callback: ProgressFunc = None,
) -> str:
    r"""
    Copy the given STDF files to ``C:\FTDC\{lot_id}\``.

    Creates the destination directory if it does not exist.
    Uses ``{filename}.part`` staging and atomic rename on completion.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    dest_dir = get_local_dest_dir(lot_id)
    os.makedirs(dest_dir, exist_ok=True)

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
        dest_path = os.path.join(dest_dir, filename)
        try:
            _fast_copy_file(src_path, dest_path)
            with copied_lock:
                copied += 1
            _emit_log(f"  Copied: {filename}", logger)
            return True, filename
        except (OSError, shutil.SameFileError) as exc:
            _emit_log(f"  FAILED to copy {filename}: {exc}", logger)
            return False, filename

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


def copy_stdf_files_per_file(
    source_paths: List[str],
    lot_id: str,
    logger: LogFunc = None,
    per_file_progress_callback: PerFileProgressFunc = None,
    file_counter_callback: FileCounterFunc = None,
    copy_stats_callback: CopyStatsFunc = None,
    max_workers: int = 4,
    on_file_copied: Optional[Callable[[str], None]] = None,
) -> str:
    """Copy STDF files in parallel (up to ``max_workers`` concurrent workers) with:
    - Option 2 Aggregate Telemetry (``Done X/N | copied/total | speed | ETA``)
    - "Lead / Largest Active File" Spotlight status text (``Copying {lead} (+K more)…``)
    - Monotonically increasing batch progress bar (``0% -> 100%`` of total lot bytes)
    - Kernel-mode ``CopyFileExW`` on Windows (with Python loop fallback on non-Windows)
    - Atomic ``{filename}.part`` staging + rename on completion
    - Optional ``on_file_copied(dest_path)`` hook fired for EACH file right after its
      ``.part`` -> final rename succeeds (used to stream the Check STDF pre-warm while
      the remaining files are still copying). Exceptions raised by the hook are logged
      and never abort the copy.
    """
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import time

    dest_dir = get_local_dest_dir(lot_id)
    os.makedirs(dest_dir, exist_ok=True)

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

    if total == 0:
        _emit_log(f"Done - 0/0 file(s) copied to {dest_dir}", logger)
        return dest_dir

    filenames: List[str] = [os.path.basename(p) for p in unique_paths]
    file_sizes: List[int] = []
    for p in unique_paths:
        try:
            file_sizes.append(max(0, int(os.path.getsize(p))))
        except OSError:
            file_sizes.append(0)
    total_batch_bytes = sum(file_sizes)

    state_lock = threading.Lock()
    bytes_by_idx: List[int] = [0] * total
    active_indices: Set[int] = set()
    completed_count = [0]
    copied_count = [0]
    batch_t0 = time.perf_counter()
    last_emit_time = [0.0]

    def _emit_batch_telemetry(force: bool = False):
        with state_lock:
            now = time.perf_counter()
            if not force and (now - last_emit_time[0] < 0.05):
                return
            last_emit_time[0] = now
            agg_copied = sum(bytes_by_idx)
            done = completed_count[0]
            frac = (
                (agg_copied / total_batch_bytes)
                if total_batch_bytes > 0
                else (done / max(total, 1))
            )
            if active_indices:
                # Pin spotlight to the largest currently-copying file (ties broken by lowest index)
                lead_idx = max(active_indices, key=lambda i: (file_sizes[i], -i))
                lead_name = filenames[lead_idx]
                other_active = len(active_indices) - 1
                if other_active > 0:
                    status_msg = f"Copying {lead_name} (+{other_active} more)…"
                else:
                    status_msg = f"Copying {lead_name}…"
                frac = min(max(0.0, frac), 0.99)
            else:
                status_msg = f"Copied {done}/{total} file(s)"
                frac = min(max(0.0, frac), 1.0)
            elapsed = max(now - batch_t0, 0.001)
            stats_txt = format_parallel_copy_stats_text(
                done, total, agg_copied, total_batch_bytes, elapsed
            )

        if per_file_progress_callback:
            per_file_progress_callback(frac, status_msg)
        if copy_stats_callback:
            copy_stats_callback(stats_txt)

    def _copy_worker(idx: int) -> bool:
        src_path = unique_paths[idx]
        filename = filenames[idx]
        dest_path = os.path.join(dest_dir, filename)

        with state_lock:
            active_indices.add(idx)
            if file_sizes[idx] == 0:
                try:
                    sz = max(0, int(os.path.getsize(src_path)))
                    file_sizes[idx] = sz
                except OSError:
                    pass

        if file_counter_callback:
            file_counter_callback(idx + 1, total, filename)
        _emit_batch_telemetry(force=True)

        def _on_bytes(cur_bytes: int, tot_bytes: int):
            with state_lock:
                if tot_bytes > file_sizes[idx]:
                    file_sizes[idx] = tot_bytes
                bytes_by_idx[idx] = min(max(0, cur_bytes), max(file_sizes[idx], cur_bytes))
            _emit_batch_telemetry(force=False)

        try:
            _fast_copy_file_with_progress(
                src_path,
                dest_path,
                progress_callback=None,
                file_index=idx + 1,
                total_files=total,
                stats_callback=None,
                byte_progress_callback=_on_bytes,
            )
            with state_lock:
                bytes_by_idx[idx] = file_sizes[idx]
                active_indices.discard(idx)
                completed_count[0] += 1
                copied_count[0] += 1
            _emit_batch_telemetry(force=True)
            _emit_log(f"  Copied: {filename}", logger)
            if on_file_copied is not None:
                # File is complete (atomic .part -> final rename succeeded): hand it off
                # for streaming Check STDF pre-warm while the rest of the batch copies.
                try:
                    on_file_copied(dest_path)
                except Exception as hook_exc:  # never let the hook break the copy
                    _emit_log(f"  Pre-warm hook failed for {filename}: {hook_exc}", logger)
            return True
        except (OSError, shutil.SameFileError) as exc:
            with state_lock:
                active_indices.discard(idx)
                completed_count[0] += 1
            _emit_batch_telemetry(force=True)
            _emit_log(f"  FAILED to copy {filename}: {exc}", logger)
            return False

    worker_cnt = max(1, min(total, int(max_workers or 4), _MAX_COPY_WORKERS))
    if worker_cnt == 1:
        for i in range(total):
            _copy_worker(i)
    else:
        with ThreadPoolExecutor(max_workers=worker_cnt, thread_name_prefix="stdf_copy") as executor:
            futures = [executor.submit(_copy_worker, i) for i in range(total)]
            for fut in futures:
                fut.result()

    _emit_log(f"Done - {copied_count[0]}/{total} file(s) copied to {dest_dir}", logger)
    return dest_dir
