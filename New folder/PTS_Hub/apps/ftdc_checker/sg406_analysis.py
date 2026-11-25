"""
Multi-Identifier (WXY, UID, BT MACID, ZB MACID) analysis and Excel reporting module.

Separates the multi-parameter checking logic (WXY, UID, BT, ZB) from standard
WXY-only devices. All test numbers (Wafer, X, Y, UID, BT, ZB) and optional
MACID prefix rules are read strictly from assets/mask_wxy_map.json (managed
via Device Settings) with no hardcoded per-device defaults.
"""
import functools
import itertools
import logging
import os
import re
from array import array as _CArray
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from apps.ftdc_checker.guid_analysis import _is_valid_wxy as _shared_is_valid_wxy
from apps.ftdc_checker.stdf_parser import (
    LogFunc, ProgressFunc,
    _count_files_text, _emit_log, sort_paths_by_filename_timestamp,
    parse_stdf_file, _parse_stdf_worker, enforce_strict_analysis_validation,
    normalize_lot_id_for_match,
)

SG406_MASK = "SG406"

INVALID_WXY = "NaN_NaN_NaN"
INVALID_WXYS = {INVALID_WXY, "0_0_0"}
INVALID_UID = "NaN"

CHECK_KEYS: Tuple[str, ...] = ("wxy", "uid", "bt", "zb")
CHECK_LABELS: Dict[str, str] = {
    "wxy": "WXY",
    "uid": "UID",
    "bt": "BT MACID",
    "zb": "ZB MACID",
}

_RJ_RE = re.compile(r'rj(\d+)', re.IGNORECASE)

FP_ORIGINAL_LABEL = "FP Original"
FP_DUPLICATE_LABEL = "FP Duplicate"
RETEST_ORIGINAL_LABEL = "RETEST Original"
RETEST_DUPLICATE_LABEL = "RETEST Duplicate"
NO_DUPLICATE_LABEL = "No Duplicate"


def macid_decimal_to_hex(macid_str: Any) -> str:
    """Convert an underscore-separated decimal MACID to colon-separated uppercase 2-digit hex.

    Example:
        '64_132_50_12_192_114' -> '40:84:32:0C:C0:72'
        '64_132_50_0_0_12_194_204' -> '40:84:32:00:00:0C:C2:CC'
    """
    if not macid_str:
        return ""
    s = str(macid_str).strip()
    if not s or s.upper() in ("N/A", "NONE", "NAN"):
        return ""
    parts = s.split("_") if "_" in s else (s.split(":") if ":" in s else s.split())
    if any(p.strip().upper() == "NAN" for p in parts):
        return "N/A"
    hex_parts = []
    for p in parts:
        p_clean = p.strip()
        if not p_clean:
            continue
        try:
            val = int(round(float(p_clean)))
            hex_parts.append(f"{val & 0xFF:02X}")
        except (ValueError, TypeError):
            hex_parts.append(p_clean.upper())
    return ":".join(hex_parts)


# ══════════════════════════════════════════════════════════════════════════════
# Generic multi-identifier (UID + BT and/or ZB MACID) device configuration
# ──────────────────────────────────────────────────────────────────────────────
# Any 5-character mask in assets/mask_wxy_map.json opts into UID / MACID checking
# by defining "uid" plus at least one of "bt" / "zb" (any number of test numbers).
#
#   "XXXXX": {
#     "wafer": 1001, "x": 1002, "y": 1003,
#     "uid": 1005,                       # int or [int, ...]
#     "bt":  [2001, 2002, ...],          # optional (omit for ZB-only devices)
#     "zb":  [3001, 3002, ...],          # optional (omit for BT-only devices)
#     "macid_prefix": "40:84",           # optional, str or [str, ...] (applies to BT & ZB)
#     "bt_prefix": "40:84",              # optional per-type override
#     "zb_prefix": ["40:84", "D8:80"],   # optional per-type override
#     "macid_invalid": ["12:34:56:78:9A:BC"]  # optional extra dummy MACIDs to exclude
#   }
#
# MACID validity is a strict 2-way check:
#   1) INCLUSION : If one or more prefixes are configured in Device Settings
#                  ("macid_prefix" / "bt_prefix" / "zb_prefix"), the MACID must
#                  start with one of those prefixes. When blank/unconfigured,
#                  MACID prefix checking is disabled.
#   2) EXCLUSION : MACID must NOT be missing ('NaN'), all-zeros (00:00:...),
#                  all-FF (FF:FF:...), the dummy sequence 11:22:33:44:55:66:...,
#                  or any extra dummy listed in "macid_invalid".
# ══════════════════════════════════════════════════════════════════════════════
DEFAULT_MACID_PREFIXES: Tuple[str, ...] = ()
MACID_TYPES: Tuple[str, ...] = ("bt", "zb")
MACID_SHORT_LABELS: Dict[str, str] = {"bt": "BT", "zb": "ZB"}


def _normalize_macid_hex(value: Any) -> str:
    """Normalize a MACID / MACID prefix (hex '40:84', '40-84', '4084' or decimal '64_132')
    into canonical uppercase colon-separated hex octets ('40:84'). Returns '' if unparseable."""
    s = str(value or "").strip()
    if not s:
        return ""
    if "_" in s:
        hx = macid_decimal_to_hex(s)
        return "" if hx in ("", "N/A") else hx
    s_clean = s.replace("-", ":").replace(" ", ":").upper()
    if ":" in s_clean:
        octets = [o for o in s_clean.split(":") if o]
    else:
        if len(s_clean) % 2:
            return ""
        octets = [s_clean[i:i + 2] for i in range(0, len(s_clean), 2)]
    out = []
    for o in octets:
        try:
            out.append(f"{int(o, 16) & 0xFF:02X}")
        except ValueError:
            return ""
    return ":".join(out)


def _normalize_prefix_list(raw: Any) -> Optional[Tuple[str, ...]]:
    """Return a tuple of normalized hex prefixes, () if explicitly empty, or None if not configured."""
    if raw is None:
        return None
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    out: List[str] = []
    for it in items:
        norm = _normalize_macid_hex(it)
        if norm and norm not in out:
            out.append(norm)
    return tuple(out)


def _as_test_tuple(raw: Any) -> Tuple[int, ...]:
    if raw is None or raw == "":
        return ()
    if isinstance(raw, (list, tuple)):
        return tuple(int(v) for v in raw if str(v).strip() != "")
    return (int(raw),)


def mask_has_macid_config(mask_entry: Optional[Dict[str, Any]]) -> bool:
    """True when a mask entry defines at least one BT or ZB MACID test number."""
    if not isinstance(mask_entry, dict):
        return False
    return bool(_as_test_tuple(mask_entry.get("bt")) or _as_test_tuple(mask_entry.get("zb")))


def get_macid_mask_key(mpc_or_mask: str, mpc_wxy_map: Optional[Dict[str, Dict[str, Any]]] = None) -> Optional[str]:
    """Return the 5-char mask key if this MPC/mask is configured for UID/MACID checking, else None."""
    if mpc_wxy_map is None:
        from apps.ftdc_checker.guid_analysis import MPC_WXY_TEST_MAP
        mpc_wxy_map = MPC_WXY_TEST_MAP
    mask = str(mpc_or_mask or "").strip().upper()[:5]
    if not mask:
        return None
    return mask if mask_has_macid_config((mpc_wxy_map or {}).get(mask)) else None


def resolve_macid_device_config(
    mpc_or_mask: str = "",
    mpc_wxy_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Resolve the generic multi-identifier configuration for any MACID-enabled mask.

    All test numbers (WXY, UID, BT, ZB) and optional MACID prefixes are read
    strictly from ``mask_wxy_map.json`` (managed via Device Settings) with no
    hardcoded default values. When MACID prefixes are blank/unconfigured, prefix
    checking is disabled.
    """
    if mpc_wxy_map is None:
        from apps.ftdc_checker.guid_analysis import MPC_WXY_TEST_MAP
        mpc_wxy_map = MPC_WXY_TEST_MAP

    mask = str(mpc_or_mask or "").strip().upper()[:5]
    if not mask:
        raise ValueError("Mask or MPC is required to resolve multi-identifier configuration.")
    mapped = (mpc_wxy_map or {}).get(mask) or {}
    if not mapped:
        raise ValueError(
            f"Mask '{mask}' is not configured in mask_wxy_map.json. Please configure it in Device Settings."
        )

    def _req_int(key: str) -> int:
        if key in mapped and mapped[key] is not None and str(mapped[key]).strip() != "":
            return int(mapped[key])
        raise ValueError(
            f"Mask '{mask}' is missing the '{key}' WXY test number in mask_wxy_map.json. Please configure it in Device Settings."
        )

    wxy = {"wafer": _req_int("wafer"), "x": _req_int("x"), "y": _req_int("y")}

    uid = _as_test_tuple(mapped.get("uid"))
    bt = _as_test_tuple(mapped.get("bt"))
    zb = _as_test_tuple(mapped.get("zb"))
    if not (bt or zb):
        raise ValueError(
            f"Mask '{mask}' has no BT or ZB MACID test numbers configured in mask_wxy_map.json."
        )
    if not uid:
        raise ValueError(
            f"Mask '{mask}' has BT/ZB MACID test numbers configured but no 'uid' test number. "
            "UID is required to verify 1 MACID = 1 UID. Please update mask_wxy_map.json / Device Settings."
        )

    common_prefix = _normalize_prefix_list(mapped.get("macid_prefix"))
    prefixes: Dict[str, Tuple[str, ...]] = {}
    for mtype in MACID_TYPES:
        per_type = _normalize_prefix_list(mapped.get(f"{mtype}_prefix"))
        if per_type is not None:
            prefixes[mtype] = per_type
        elif common_prefix is not None:
            prefixes[mtype] = common_prefix
        else:
            prefixes[mtype] = ()

    extra_invalid = tuple(
        h for h in (_normalize_macid_hex(v) for v in (_normalize_prefix_list(mapped.get("macid_invalid")) or ()))
        if h
    )

    active_macids = tuple(m for m, tests in (("bt", bt), ("zb", zb)) if tests)
    active_keys = ("wxy", "uid") + active_macids
    all_tests: Set[int] = {wxy["wafer"], wxy["x"], wxy["y"], *uid, *bt, *zb}
    return {
        "mask": mask,
        "wxy": wxy,
        "uid": uid[0] if len(uid) == 1 else uid,
        "uid_tests": uid,
        "bt": bt,
        "zb": zb,
        "has_bt": bool(bt),
        "has_zb": bool(zb),
        "active_macids": active_macids,
        "active_keys": active_keys,
        "macid_prefixes": prefixes,
        "macid_invalid": extra_invalid,
        "all_tests": all_tests,
    }


def resolve_sg406_test_config(mpc_wxy_map: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Backward-compatible alias: resolve the multi-identifier configuration for SG406."""
    return resolve_macid_device_config(SG406_MASK, mpc_wxy_map)


def _cfg_active_macids(cfg: Optional[Dict[str, Any]]) -> Tuple[str, ...]:
    if not cfg:
        return MACID_TYPES
    am = cfg.get("active_macids")
    if am is not None:
        return tuple(am)
    return tuple(m for m in MACID_TYPES if cfg.get(m))


def _cfg_prefixes(cfg: Optional[Dict[str, Any]], mtype: str) -> Tuple[str, ...]:
    if not cfg:
        return ()
    pmap = cfg.get("macid_prefixes") or {}
    val = pmap.get(mtype)
    if val is None:
        return ()
    return tuple(val)


def _is_dummy_macid_hex(hex_val: str, extra_invalid: Sequence[str] = ()) -> bool:
    """EXCLUSION check: all-zeros, all-FF, the dummy 11:22:33:44:55:66:... sequence,
    or any extra configured dummy MACID."""
    if not hex_val:
        return True
    octets = hex_val.split(":")
    if any(not o for o in octets):
        octets = [o for o in octets if o]
        if not octets:
            return True
    first = octets[0]
    try:
        if len(first) == 2 and all(len(o) == 2 for o in octets):
            if first == "00" and all(o == "00" for o in octets):
                return True
            if first == "FF" and all(o == "FF" for o in octets):
                return True
            if first == "11" and all(o == f"{((i + 1) * 0x11) & 0xFF:02X}" for i, o in enumerate(octets)):
                return True
            # Still reject malformed non-hex octets if raw non-normalized string was passed directly
            for o in octets:
                int(o, 16)
        else:
            vals = [int(o, 16) for o in octets]
            if all(v == 0 for v in vals) or all(v == 0xFF for v in vals):
                return True
            if all(v == ((i + 1) * 0x11) & 0xFF for i, v in enumerate(vals)):
                return True
    except ValueError:
        return True
    for inv in extra_invalid or ():
        if inv and hex_val == inv:
            return True
    return False


@functools.lru_cache(maxsize=64)
def _compile_macid_rules(
    pfx_raw: Optional[Tuple[str, ...]],
    inv_raw: Tuple[str, ...],
) -> Tuple[Tuple[str, ...], frozenset]:
    """Pre-normalize MACID prefix tuple and invalid-MACID lookup set once per config."""
    inv_norm = frozenset(
        norm
        for v in inv_raw
        for norm in ((_normalize_macid_hex(v) or str(v).strip().upper()),)
        if norm
    )
    if pfx_raw is None:
        pfx = ()
    else:
        pfx_norm = _normalize_prefix_list(pfx_raw)
        pfx = pfx_norm if pfx_norm is not None else tuple(pfx_raw)
    return pfx, inv_norm


def clear_macid_validation_cache() -> None:
    """Clear compiled MACID prefix/exclusion rule cache."""
    _compile_macid_rules.cache_clear()


def _prepare_macid_rules(mtype: str, cfg: Optional[Dict[str, Any]] = None) -> Tuple[Tuple[str, ...], frozenset]:
    pfx_raw = _cfg_prefixes(cfg, mtype)
    inv_list = (cfg or {}).get("macid_invalid") or ()
    inv_raw = tuple(str(v) for v in inv_list if v is not None)
    return _compile_macid_rules(pfx_raw, inv_raw)


def _is_valid_macid_compiled(
    macid_val: Optional[str],
    pfx: Tuple[str, ...],
    inv_norm: frozenset,
) -> bool:
    if not macid_val:
        return False
    s = str(macid_val).strip()
    if not s or "NaN" in s or s.upper() in ("N/A", "NONE", "NAN"):
        return False
    hex_val = _normalize_macid_hex(s)
    if not hex_val:
        return False
    if _is_dummy_macid_hex(hex_val, inv_norm):
        return False
    if not pfx:
        return True
    return any(hex_val == p or hex_val.startswith(p + ":") for p in pfx)


def _is_valid_macid(
    macid_val: Optional[str],
    prefixes: Optional[Sequence[str]] = None,
    extra_invalid: Sequence[str] = (),
    *,
    valid_prefixes: Optional[Sequence[str]] = None,
    invalid_macids: Optional[Sequence[str]] = None,
) -> bool:
    """Strict 2-way MACID validation.

    1) EXCLUSION: reject missing ('NaN'), all-zeros, all-FF, dummy 11:22:33:44:55:66:...
       and any extra configured dummy MACIDs.
    2) INCLUSION: when ``prefixes`` / ``valid_prefixes`` is non-empty, MACID must start with
       one of those valid prefixes. When None or empty, prefix checking is disabled.
    """
    if not macid_val:
        return False
    inv_raw = invalid_macids if invalid_macids is not None else extra_invalid
    pfx_raw = valid_prefixes if valid_prefixes is not None else prefixes
    inv_key = tuple(str(v) for v in (inv_raw or ()) if v is not None)
    pfx_key = None if pfx_raw is None else tuple(str(v) for v in pfx_raw)
    pfx, inv_norm = _compile_macid_rules(pfx_key, inv_key)
    return _is_valid_macid_compiled(macid_val, pfx, inv_norm)


def _is_valid_macid_cfg(macid_val: Optional[str], mtype: str, cfg: Optional[Dict[str, Any]] = None) -> bool:
    if not macid_val:
        return False
    pfx, inv_norm = _prepare_macid_rules(mtype, cfg)
    return _is_valid_macid_compiled(macid_val, pfx, inv_norm)


def _is_valid_sg406_wxy(val: str) -> bool:
    """WXY is invalid if it contains 'NaN', Wafer (first W_X_Y value) is 0
    regardless of X and Y (e.g. '0_1_2', '0_137_136'), or two/more W/X/Y
    components are zero (e.g. '0_0_0', '24_0_0')."""
    return _shared_is_valid_wxy(val)


def _is_valid_identifier(val: Optional[str], key: str, cfg: Optional[Dict[str, Any]] = None) -> bool:
    """Return True if an identifier string is present and valid.
    For BT/ZB MACIDs, the device configuration's valid prefixes and dummy exclusions are applied."""
    if not val:
        return False
    if key == "wxy":
        return _is_valid_sg406_wxy(val)
    if key == "uid":
        return val != INVALID_UID and "NaN" not in val
    if key in MACID_TYPES:
        return _is_valid_macid_cfg(val, key, cfg)
    return "NaN" not in val


GOOD_UNIQUE_PARAMS: Tuple[Tuple[str, str], ...] = (
    ("wxy", "WXY"),
    ("uid", "UID"),
    ("bt", "BT MACID"),
    ("zb", "ZB MACID"),
)
GOOD_UNIQUE_FP_LABEL = "Discrepancy in Good Unique Count of WXY vs UID vs BT MACID vs ZB MACID in FP"
GOOD_UNIQUE_TOTAL_LABEL = "Discrepancy in Good Unique Count of WXY vs UID vs BT MACID vs ZB MACID in Total"


def active_good_unique_params(cfg: Optional[Dict[str, Any]] = None) -> Tuple[Tuple[str, str], ...]:
    """Return the (key, label) identifiers that are active for this device (WXY, UID + active MACIDs)."""
    active = set(("wxy", "uid") + _cfg_active_macids(cfg))
    return tuple((k, lbl) for k, lbl in GOOD_UNIQUE_PARAMS if k in active)


def compute_good_unique_discrepancy(
    good_rows: Sequence[Dict[str, Any]],
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Strict 1-die = 1 WXY = 1 UID = 1 BT MACID = 1 ZB MACID safety control on GOOD (PASS) rows.

    Only the identifiers active for this device are compared (WXY, UID + BT and/or ZB).
    For every pair of identifiers the pair discrepancy is the largest of:
      A) |unique count(A) - unique count(B)| among GOOD rows (valid values only),
      B) GOOD rows where exactly one of A/B is valid (missing / invalid / unprogrammed value,
         e.g. BT MACID 00:00:00:00:00:00 on a PASS part) - prevents errors cancelling out,
      C) values of A mapped to >1 distinct B (and vice versa), e.g. one UID burned with two
         different BT MACIDs across retest - bidirectional 1-to-1 mapping check.

    The overall discrepancy is the maximum pair discrepancy. Any value > 0 is a FAIL.
    """
    params = active_good_unique_params(cfg)
    keys = [k for k, _ in params]
    labels = dict(params)
    uniq: Dict[str, Set[str]] = {k: set() for k in keys}
    pairs = [(keys[i], keys[j]) for i in range(len(keys)) for j in range(i + 1, len(keys))]
    fwd: Dict[Tuple[str, str], Dict[str, Set[str]]] = {p: defaultdict(set) for p in pairs}
    rev: Dict[Tuple[str, str], Dict[str, Set[str]]] = {p: defaultdict(set) for p in pairs}
    one_side: Dict[Tuple[str, str], int] = {p: 0 for p in pairs}

    for r in good_rows:
        vals: Dict[str, str] = {}
        valid: Dict[str, bool] = {}
        for k in keys:
            raw = r.get(k)
            v = str(raw).strip() if raw is not None else ""
            vals[k] = v
            valid[k] = bool(v) and _is_valid_identifier(v, k, cfg)
            if valid[k]:
                uniq[k].add(v)
        for a, b in pairs:
            if valid[a] and valid[b]:
                fwd[(a, b)][vals[a]].add(vals[b])
                rev[(a, b)][vals[b]].add(vals[a])
            elif valid[a] != valid[b]:
                one_side[(a, b)] += 1

    counts = {k: len(uniq[k]) for k in keys}
    pair_details: List[Dict[str, Any]] = []
    discrepant_pairs: List[str] = []
    overall = 0
    for a, b in pairs:
        count_diff = abs(counts[a] - counts[b])
        multi_map = sum(1 for s in fwd[(a, b)].values() if len(s) > 1) + \
            sum(1 for s in rev[(a, b)].values() if len(s) > 1)
        magnitude = max(count_diff, one_side[(a, b)] + multi_map)
        name = f"{labels[a]} vs {labels[b]}"
        pair_details.append({
            "pair": name,
            "count_diff": count_diff,
            "one_side_invalid_rows": one_side[(a, b)],
            "multi_mapped_values": multi_map,
            "discrepancy": magnitude,
        })
        if magnitude > 0:
            discrepant_pairs.append(name)
        overall = max(overall, magnitude)

    return {
        "counts": {labels[k]: counts[k] for k in keys},
        "discrepancy": overall,
        "pairs": discrepant_pairs,
        "pair_details": pair_details,
    }


def _uid_sort_key(u: Any) -> Tuple[int, int, str]:
    s = str(u).strip()
    try:
        return (0, int(s), s)
    except (ValueError, TypeError):
        return (1, 0, s)


def _compute_duplicate_macid_summary(
    all_rows: Sequence[Dict[str, Any]],
    cfg: Optional[Dict[str, Any]] = None,
    dup_bt_map: Optional[Dict[str, Sequence[str]]] = None,
    dup_zb_map: Optional[Dict[str, Sequence[str]]] = None,
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]], List[Dict[str, Any]]]:
    """Single-pass computation of ``(dup_bt, dup_zb, duplicate_pair_details)`` on PASS parts only."""
    active = _cfg_active_macids(cfg)
    use_bt = "bt" in active
    use_zb = "zb" in active
    bt_pfx, bt_inv = _prepare_macid_rules("bt", cfg) if use_bt else ((), frozenset())
    zb_pfx, zb_inv = _prepare_macid_rules("zb", cfg) if use_zb else ((), frozenset())

    pair_uids: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    bt_uids: Dict[str, Set[str]] = defaultdict(set)
    zb_uids: Dict[str, Set[str]] = defaultdict(set)

    for r in all_rows or ():
        if "pass_fail" in r and str(r.get("pass_fail") or "").strip().upper() != "PASS":
            continue
        u_raw = r.get("uid")
        u_val = str(u_raw).strip() if u_raw is not None else ""
        if not u_val or u_val == "NaN" or "NaN" in u_val or u_val == INVALID_UID:
            continue

        b_val = ""
        if use_bt:
            b_raw = r.get("bt")
            b_val = str(b_raw).strip() if b_raw is not None else ""
            if not _is_valid_macid_compiled(b_val, bt_pfx, bt_inv):
                b_val = ""

        z_val = ""
        if use_zb:
            z_raw = r.get("zb")
            z_val = str(z_raw).strip() if z_raw is not None else ""
            if not _is_valid_macid_compiled(z_val, zb_pfx, zb_inv):
                z_val = ""

        if not b_val and not z_val:
            continue

        pair_uids[(b_val, z_val)].add(u_val)
        if b_val:
            bt_uids[b_val].add(u_val)
        if z_val:
            zb_uids[z_val].add(u_val)

    dup_bt: Dict[str, List[str]] = (
        {b: sorted(u, key=_uid_sort_key) for b, u in bt_uids.items() if len(u) > 1}
        if use_bt
        else {}
    )
    dup_zb: Dict[str, List[str]] = (
        {z: sorted(u, key=_uid_sort_key) for z, u in zb_uids.items() if len(u) > 1}
        if use_zb
        else {}
    )

    dup_bt_set: Set[str] = set(dup_bt.keys())
    dup_zb_set: Set[str] = set(dup_zb.keys())
    if dup_bt_map:
        dup_bt_set.update(str(k).strip() for k in dup_bt_map.keys() if k)
    if dup_zb_map:
        dup_zb_set.update(str(k).strip() for k in dup_zb_map.keys() if k)

    selected_pairs: List[Tuple[str, str]] = []
    selected_pair_set: Set[Tuple[str, str]] = set()
    covered_bt: Set[str] = set()
    covered_zb: Set[str] = set()

    # 1. Include every unique (BT, ZB) pair that has multiple assigned UIDs
    for pair_key, u_set in pair_uids.items():
        if len(u_set) > 1:
            selected_pairs.append(pair_key)
            selected_pair_set.add(pair_key)
            if pair_key[0]:
                covered_bt.add(pair_key[0])
            if pair_key[1]:
                covered_zb.add(pair_key[1])

    # 2. Fallback: if a duplicate BT or ZB MACID was split across different pairs with 1 UID each,
    # include those pairs so every duplicate MACID is represented in the worksheet.
    uncovered_bt = dup_bt_set - covered_bt
    uncovered_zb = dup_zb_set - covered_zb
    if uncovered_bt or uncovered_zb:
        for pair_key in pair_uids.keys():
            if pair_key not in selected_pair_set and (
                (pair_key[0] and pair_key[0] in uncovered_bt)
                or (pair_key[1] and pair_key[1] in uncovered_zb)
            ):
                selected_pairs.append(pair_key)
                selected_pair_set.add(pair_key)

    hex_cache: Dict[str, str] = {}

    def _hex_cached(m_val: str) -> str:
        res = hex_cache.get(m_val)
        if res is None:
            res = macid_decimal_to_hex(m_val)
            hex_cache[m_val] = res
        return res

    details: List[Dict[str, Any]] = []
    for b_val, z_val in selected_pairs:
        sorted_uids = sorted(pair_uids[(b_val, z_val)], key=_uid_sort_key)
        bt_hex = _hex_cached(b_val) if b_val else "N/A"
        zb_hex = _hex_cached(z_val) if z_val else "N/A"
        details.append({
            "bt": b_val,
            "bt_hex": bt_hex,
            "zb": z_val,
            "zb_hex": zb_hex,
            "is_dup_bt": bool(b_val and (b_val in dup_bt_set or len(sorted_uids) > 1)),
            "is_dup_zb": bool(z_val and (z_val in dup_zb_set or len(sorted_uids) > 1)),
            "num_assigned_uids": len(sorted_uids),
            "assigned_uids": sorted_uids,
            "assigned_uids_str": ", ".join(sorted_uids),
        })

    # Synthetic fallback when all_rows is empty but dup_bt_map/dup_zb_map were passed directly
    if not details and (dup_bt_map or dup_zb_map):
        b_items = sorted((dup_bt_map or {}).items())
        z_items = sorted((dup_zb_map or {}).items())
        if len(b_items) == len(z_items) and b_items and all(
            sorted(b_u, key=_uid_sort_key) == sorted(z_u, key=_uid_sort_key)
            for (_, b_u), (_, z_u) in zip(b_items, z_items)
        ):
            for (b_val, b_u), (z_val, _) in zip(b_items, z_items):
                sorted_uids = [str(u) for u in sorted(b_u, key=_uid_sort_key)]
                details.append({
                    "bt": b_val,
                    "bt_hex": _hex_cached(b_val),
                    "zb": z_val,
                    "zb_hex": _hex_cached(z_val),
                    "is_dup_bt": True,
                    "is_dup_zb": True,
                    "num_assigned_uids": len(sorted_uids),
                    "assigned_uids": sorted_uids,
                    "assigned_uids_str": ", ".join(sorted_uids),
                })
        else:
            for b_val, b_u in b_items:
                sorted_uids = [str(u) for u in sorted(b_u, key=_uid_sort_key)]
                details.append({
                    "bt": b_val,
                    "bt_hex": _hex_cached(b_val),
                    "zb": "",
                    "zb_hex": "N/A",
                    "is_dup_bt": True,
                    "is_dup_zb": False,
                    "num_assigned_uids": len(sorted_uids),
                    "assigned_uids": sorted_uids,
                    "assigned_uids_str": ", ".join(sorted_uids),
                })
            for z_val, z_u in z_items:
                sorted_uids = [str(u) for u in sorted(z_u, key=_uid_sort_key)]
                details.append({
                    "bt": "",
                    "bt_hex": "N/A",
                    "zb": z_val,
                    "zb_hex": _hex_cached(z_val),
                    "is_dup_bt": False,
                    "is_dup_zb": True,
                    "num_assigned_uids": len(sorted_uids),
                    "assigned_uids": sorted_uids,
                    "assigned_uids_str": ", ".join(sorted_uids),
                })

    details.sort(key=lambda d: (d["bt_hex"], d["zb_hex"], d["assigned_uids_str"]))
    return dup_bt, dup_zb, details


def compute_duplicate_macid_pairs(
    all_rows: Sequence[Dict[str, Any]],
    dup_bt_map: Optional[Dict[str, Sequence[str]]] = None,
    dup_zb_map: Optional[Dict[str, Sequence[str]]] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Build duplicate MACID details grouped by unique (BT MACID, ZB MACID) pair.

    For BT-only devices the key is (BT, ""), for ZB-only devices ("", ZB).
    Each unique pair that has multiple assigned UIDs (or belongs to a duplicate BT/ZB
    MACID split across pairs) is returned as a separate row dict with:
      - bt, bt_hex, zb, zb_hex
      - is_dup_bt, is_dup_zb
      - num_assigned_uids, assigned_uids, assigned_uids_str
    """
    _, _, details = _compute_duplicate_macid_summary(
        all_rows,
        cfg=cfg,
        dup_bt_map=dup_bt_map,
        dup_zb_map=dup_zb_map,
    )
    return details


def _extract_macid_prefix(hex_val: str, prefix_bytes: int = 3) -> str:
    """Return the uppercase colon-separated leading ``prefix_bytes`` octets of ``hex_val``."""
    s = str(hex_val or "").strip().upper()
    if not s:
        return ""
    octets = [o for o in s.split(":") if o]
    if not octets:
        return ""
    take = min(max(int(prefix_bytes), 1), len(octets))
    return ":".join(octets[:take])


def _is_prefix_explicitly_allowed(prefix_hex: str, configured: Optional[Sequence[str]]) -> bool:
    """Return True when ``prefix_hex`` matches an allowlisted prefix on an octet boundary."""
    p_obs = str(prefix_hex or "").strip().upper()
    if not p_obs or not configured:
        return False
    for raw_c in configured:
        c_norm = _normalize_macid_hex(raw_c) or str(raw_c or "").strip().upper()
        if not c_norm:
            continue
        if (
            p_obs == c_norm
            or p_obs.startswith(c_norm + ":")
            or c_norm.startswith(p_obs + ":")
        ):
            return True
    return False


def format_macid_prefix_outlier_text(
    profile: Optional[Dict[str, Any]],
    fallback_outliers: Optional[Sequence[Dict[str, Any]]] = None,
) -> str:
    """Format concise Layer 2 outlier summary string, e.g. 'BT: 06:80:FC [1 unit / 0.02%]'."""
    parts: List[str] = []
    by_type = (profile or {}).get("by_type") if isinstance(profile, dict) else None
    if isinstance(by_type, dict) and by_type:
        for mtype in MACID_TYPES:
            tdata = by_type.get(mtype)
            if not isinstance(tdata, dict):
                continue
            outliers = tdata.get("outliers") or []
            if not outliers:
                continue
            short_lbl = MACID_SHORT_LABELS.get(mtype, mtype.upper())
            items = []
            for ol in outliers:
                cnt = int(ol.get("count") or 0)
                pct = float(ol.get("pct") or 0.0)
                u_word = "unit" if cnt == 1 else "units"
                items.append(f"{ol.get('prefix', '')} [{cnt:,} {u_word} / {pct:.2f}%]")
            if items:
                parts.append(f"{short_lbl}: {', '.join(items)}")
        return "; ".join(parts)

    if fallback_outliers:
        grouped: Dict[str, List[str]] = defaultdict(list)
        for ol in fallback_outliers:
            mtype = str(ol.get("macid_type") or "").strip().lower()
            short_lbl = MACID_SHORT_LABELS.get(mtype, str(ol.get("label") or "MACID"))
            cnt = int(ol.get("count") or 0)
            pct = float(ol.get("pct") or 0.0)
            u_word = "unit" if cnt == 1 else "units"
            grouped[short_lbl].append(f"{ol.get('prefix', '')} [{cnt:,} {u_word} / {pct:.2f}%]")
        return "; ".join(f"{lbl}: {', '.join(vals)}" for lbl, vals in grouped.items() if vals)
    return ""


def format_macid_prefix_outlier_banner(
    result: Optional[Dict[str, Any]],
    max_lines: int = 10,
) -> Optional[Dict[str, Any]]:
    """Build banner styling and bullet lines when Layer 2 MACID prefix outliers exist."""
    if not isinstance(result, dict):
        return None
    outlier_cnt = int(result.get("macid_prefix_outlier_count") or 0)
    outliers = result.get("macid_prefix_outliers") or []
    if outlier_cnt <= 0 or not outliers:
        return None

    mode = str(result.get("macid_prefix_outlier_mode") or "fail").strip().lower()
    is_fail = (mode == "fail")
    title = (
        "⛔ FAIL: MACID Prefix Outlier(s) detected:"
        if is_fail
        else "⚠️ WARNING: MACID Prefix Outlier(s) detected (non-blocking):"
    )
    lines: List[str] = [title]
    shown = 0
    remaining = 0

    for ol in outliers:
        lbl = str(ol.get("label") or ol.get("macid_type") or "MACID")
        pfx = str(ol.get("prefix") or "")
        cnt = int(ol.get("count") or 0)
        pct = float(ol.get("pct") or 0.0)
        hint = str(ol.get("boundary_hint") or "")
        u_word = "unit" if cnt == 1 else "units"
        samples = ol.get("samples") or []
        if shown >= max_lines:
            remaining += max(1, len(samples))
            continue
        if samples:
            take = samples[: max(1, max_lines - shown)]
            remaining += len(samples) - len(take)
            uids_txt = ", ".join(str(s.get("uid") or "N/A") for s in take)
            hexes_txt = ", ".join(str(s.get("macid_hex") or "") for s in take)
            line = f"  • {lbl} prefix {pfx} — {cnt:,} {u_word} ({pct:.2f}%) | UID: {uids_txt} | {hexes_txt}"
            shown += len(take)
        else:
            line = f"  • {lbl} prefix {pfx} — {cnt:,} {u_word} ({pct:.2f}%)"
            shown += 1
        if hint:
            line += f" [{hint}]"
        lines.append(line)

    if remaining > 0:
        lines.append(f"  …and {remaining:,} more")

    return {
        "is_fail": is_fail,
        "bg": "#F8D7DA" if is_fail else "#FFF3CD",
        "border": "#F5C2C7" if is_fail else "#FFEEBA",
        "fg": "#721C24" if is_fail else "#856404",
        "lines": lines,
    }


def compute_macid_prefix_profile(
    pass_rows: Sequence[Dict[str, Any]],
    cfg: Optional[Dict[str, Any]] = None,
    *,
    prefix_bytes: int = 3,
    min_pass_units: int = 50,
    max_outlier_count: int = 2,
    max_outlier_pct: float = 0.5,
    mode: str = "fail",
) -> Dict[str, Any]:
    """Profile active BT/ZB MACID prefixes across PASS parts and detect minority outliers.

    Pure O(N) pass over ``pass_rows`` (PASS units only):
    - Validates active MACIDs with configured rules (exclusions + optional allowlist).
    - Deduplicates by ``(uid or wxy or '#seq', macid_hex)`` so retested units are counted once.
    - Classifies sorted prefixes as Primary Pool, Rollover Pool, OUTLIER (Option B OR rule:
      ``count <= max_outlier_count or pct < max_outlier_pct`` when ``evaluated``), or
      Observed (Not Evaluated).
    """
    norm_mode = str(mode or "fail").strip().lower()
    if norm_mode not in ("fail", "warn", "disabled"):
        norm_mode = "fail"
    try:
        p_bytes = 2 if int(prefix_bytes) == 2 else 3
    except (TypeError, ValueError):
        p_bytes = 3
    try:
        min_units = max(1, int(min_pass_units))
    except (TypeError, ValueError):
        min_units = 50
    try:
        max_cnt = max(0, int(max_outlier_count))
    except (TypeError, ValueError):
        max_cnt = 2
    try:
        max_pct = float(max_outlier_pct)
        if not (0.0 <= max_pct <= 50.0):
            max_pct = 0.5
    except (TypeError, ValueError):
        max_pct = 0.5

    has_bt, has_zb = _resolve_active_macid_flags(cfg)
    active_types = tuple(m for m, flag in (("bt", has_bt), ("zb", has_zb)) if flag)

    good_rows: List[Dict[str, Any]] = [
        r for r in (pass_rows or ())
        if isinstance(r, dict) and ("pass_fail" not in r or str(r.get("pass_fail") or "").strip().upper() == "PASS")
    ]
    n_good_rows = len(good_rows)

    by_type: Dict[str, Dict[str, Any]] = {}
    all_outliers: List[Dict[str, Any]] = []
    outlier_unit_keys: Set[str] = set()
    outlier_macids_by_type: Dict[str, Set[str]] = {m: set() for m in active_types}
    any_rollover = False

    for mtype in active_types:
        pfx_rules, inv_norm = _prepare_macid_rules(mtype, cfg)
        seen_keys: Set[Tuple[str, str]] = set()
        hex_cache: Dict[str, str] = {}
        buckets: Dict[str, Dict[str, Any]] = {}
        prefix_unit_keys: Dict[str, Set[str]] = defaultdict(set)

        for seq, row in enumerate(good_rows, start=1):
            raw_val = row.get(mtype)
            val_str = str(raw_val).strip() if raw_val is not None else ""
            if not val_str or not _is_valid_macid_compiled(val_str, pfx_rules, inv_norm):
                continue

            mhex = hex_cache.get(val_str)
            if mhex is None:
                mhex = _normalize_macid_hex(val_str)
                hex_cache[val_str] = mhex
            if not mhex:
                continue

            u_raw = row.get("uid")
            u_str = str(u_raw).strip() if u_raw is not None else ""
            if not u_str or u_str == INVALID_UID or "NaN" in u_str:
                u_str = ""

            w_raw = row.get("wxy")
            w_str = str(w_raw).strip() if w_raw is not None else ""
            if not w_str or "NaN" in w_str:
                w_str = ""

            unit_id = u_str or w_str or f"#{seq}"
            dedup_key = (unit_id, mhex)
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            prefix_hex = _extract_macid_prefix(mhex, p_bytes)
            if not prefix_hex:
                continue

            prefix_unit_keys[prefix_hex].add(unit_id)
            bucket = buckets.get(prefix_hex)
            if bucket is None:
                bucket = {
                    "prefix": prefix_hex,
                    "count": 0,
                    "first_seq": seq,
                    "last_seq": seq,
                    "samples": [],
                    "macid_hexes": set(),
                }
                buckets[prefix_hex] = bucket

            bucket["count"] += 1
            if seq < bucket["first_seq"]:
                bucket["first_seq"] = seq
            if seq > bucket["last_seq"]:
                bucket["last_seq"] = seq
            bucket["macid_hexes"].add(mhex)
            if len(bucket["samples"]) < 50:
                bucket["samples"].append({
                    "uid": u_str,
                    "wxy": w_str,
                    "macid_hex": mhex,
                    "panel": str(row.get("panel") or ""),
                    "part_id": str(row.get("part_id") or ""),
                    "seq": seq,
                })

        total_units = sum(b["count"] for b in buckets.values())
        sorted_buckets = sorted(buckets.values(), key=lambda b: (-b["count"], b["prefix"]))
        evaluated = (norm_mode != "disabled") and (total_units >= min_units) and (len(sorted_buckets) > 1)

        entries: List[Dict[str, Any]] = []
        type_outliers: List[Dict[str, Any]] = []
        type_rollover = False

        for idx, b in enumerate(sorted_buckets):
            cnt = b["count"]
            pct = round((cnt / total_units) * 100.0, 4) if total_units > 0 else 0.0
            allowed = _is_prefix_explicitly_allowed(b["prefix"], pfx_rules)

            if idx == 0:
                classification = "Primary Pool"
            elif evaluated and not allowed and (cnt <= max_cnt or pct < max_pct):
                classification = "OUTLIER"
            elif evaluated or allowed:
                classification = "Rollover Pool"
                type_rollover = True
            else:
                classification = "Observed (Not Evaluated)"

            boundary_hint = ""
            if classification == "OUTLIER":
                if b["first_seq"] <= 5 or b["last_seq"] >= max(1, n_good_rows - 4):
                    boundary_hint = "Lot boundary (possible pool rollover)"
                else:
                    boundary_hint = "Mid-lot (possible corrupted/stray MACID)"

            entry = {
                "prefix": b["prefix"],
                "count": cnt,
                "pct": pct,
                "classification": classification,
                "first_seq": b["first_seq"],
                "last_seq": b["last_seq"],
                "boundary_hint": boundary_hint,
                "samples": list(b["samples"]),
            }
            entries.append(entry)

            if classification == "OUTLIER":
                type_outliers.append(entry)
                outlier_macids_by_type[mtype].update(b["macid_hexes"])
                outlier_unit_keys.update(prefix_unit_keys[b["prefix"]])
                tagged = dict(entry)
                tagged["macid_type"] = mtype
                tagged["label"] = CHECK_LABELS.get(mtype, f"{mtype.upper()} MACID")
                all_outliers.append(tagged)

        if type_rollover:
            any_rollover = True

        if not entries:
            summary_str = "None"
        else:
            part_strs = []
            for e in entries:
                tag = ""
                if e["classification"] == "OUTLIER":
                    tag = " [OUTLIER]"
                elif e["classification"] == "Rollover Pool":
                    tag = " [Rollover]"
                part_strs.append(f"{e['prefix']} ({e['count']:,} / {e['pct']:.2f}%{tag})")
            summary_str = ", ".join(part_strs)

        by_type[mtype] = {
            "label": CHECK_LABELS.get(mtype, f"{mtype.upper()} MACID"),
            "total_units": total_units,
            "evaluated": evaluated,
            "rollover_detected": type_rollover,
            "entries": entries,
            "outliers": type_outliers,
            "outlier_unit_count": sum(o["count"] for o in type_outliers),
            "summary_str": summary_str,
        }

    return {
        "mode": norm_mode,
        "prefix_bytes": p_bytes,
        "min_pass_units": min_units,
        "max_count": max_cnt,
        "max_pct": max_pct,
        "by_type": by_type,
        "outliers": all_outliers,
        "outlier_prefix_count": len(all_outliers),
        "outlier_unit_count": len(outlier_unit_keys),
        "rollover_detected": any_rollover,
        "outlier_macids_by_type": outlier_macids_by_type,
    }


def parse_panel_sg406_parts(
    input_paths: Sequence[str],
    panel_name: str,
    sg406_cfg: Dict[str, Any],
    format_coord_fn: Callable[[object], str],
    part_pass_fail_fn: Callable[[Dict[str, object]], str],
    resolve_worker_count_fn: Callable[[ Any, int], int],
    logger: LogFunc = None,
    progress_callback: ProgressFunc = None,
    progress_start: float = 0.0,
    progress_end: float = 1.0,
    max_workers: Optional[int] = None,
    pre_parsed: Optional[Dict[str, object]] = None,
    expected_lot_id: str = "",
    strict_validation: bool = False,
    truncated_files_out: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, object]:
    """Single-pass panel extraction for all 18 SG406 test parameters (WXY, UID, BT, ZB)."""
    rows: List[Dict[str, object]] = []
    total_counts: Dict[str, int] = defaultdict(int)
    _file_sizes: List[int] = []
    for _p in input_paths:
        try:
            _file_sizes.append(max(os.path.getsize(_p), 1))
        except OSError:
            _file_sizes.append(1)
    total_bytes = sum(_file_sizes) or 1
    processed_bytes = 0
    span = max(progress_end - progress_start, 0.0)

    wxy_tests = sg406_cfg["wxy"]
    wxy_wafer = int(wxy_tests["wafer"])
    wxy_x = int(wxy_tests["x"])
    wxy_y = int(wxy_tests["y"])
    uid_tests: Tuple[int, ...] = tuple(
        int(t) for t in (sg406_cfg.get("uid_tests") or _as_test_tuple(sg406_cfg.get("uid")))
    )
    bt_tests: Tuple[int, ...] = tuple(int(t) for t in (sg406_cfg.get("bt") or ()))
    zb_tests: Tuple[int, ...] = tuple(int(t) for t in (sg406_cfg.get("zb") or ()))
    has_bt = bool(bt_tests)
    has_zb = bool(zb_tests)
    single_uid = len(uid_tests) == 1
    uid_test0 = uid_tests[0] if uid_tests else None
    filter_tests: Set[int] = set(sg406_cfg["all_tests"])

    pass_count = 0
    fail_count = 0
    missing_wxy = 0
    missing_uid = 0
    missing_bt = 0
    missing_zb = 0
    mir_lot_id = ""

    status_text = f"Parsing {panel_name}: {_count_files_text(len(input_paths))}"

    def _row_panel_for(source: str) -> str:
        row_panel = panel_name
        if panel_name == "RETEST":
            match = _RJ_RE.search(source)
            if match:
                num = int(match.group(1))
                if 11 <= (num % 100) <= 13:
                    suffix = 'th'
                else:
                    suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(num % 10, 'th')
                row_panel = f"{num}{suffix} RETEST"
        elif panel_name == "QC":
            match = _RJ_RE.search(source)
            if match:
                num = int(match.group(1))
                row_panel = f"QC {num + 1}A"
            else:
                row_panel = "QC 1A"
        return row_panel

    def _accumulate(index: int, input_path: str, parsed, counts) -> None:
        nonlocal pass_count, fail_count, missing_wxy, missing_uid, missing_bt, missing_zb, mir_lot_id
        source = os.path.basename(input_path)
        row_panel = _row_panel_for(source)

        if not mir_lot_id and isinstance(parsed, dict):
            raw_mir_lot = str((parsed.get("MIR") or {}).get("LOT_ID") or "").strip().upper()
            if raw_mir_lot:
                mir_lot_id = normalize_lot_id_for_match(raw_mir_lot) or raw_mir_lot

        diag = parsed.get("DIAGNOSTICS") or {}
        integrity = parsed.get("INTEGRITY") or {}
        is_trunc = bool(
            integrity.get("is_truncated")
            or diag.get("is_truncated")
            or diag.get("truncated")
            or not integrity.get("ended_at_eof", True)
            or not integrity.get("last_record_was_mrr", True)
        )
        if is_trunc:
            t_info = {
                "filename": source,
                "filepath": input_path,
                "panel": panel_name,
                "ended_at_eof": integrity.get("ended_at_eof", diag.get("ended_at_eof", False)),
                "last_record_was_mrr": integrity.get("last_record_was_mrr", diag.get("last_record_was_mrr", False)),
                "pir_without_prr": integrity.get("pir_without_prr", diag.get("pir_without_prr", 0)),
                "prr_count": integrity.get("prr_count", diag.get("prr_count", len(parsed.get("PARTS", [])))),
                "pcr_part_cnt": integrity.get("pcr_part_cnt", diag.get("pcr_part_cnt")),
            }
            if truncated_files_out is not None:
                truncated_files_out.append(t_info)
            _emit_log(
                f"[WARNING] TRUNCATED STDF FILE in {panel_name}: '{t_info['filename']}'! "
                f"ended_at_eof={t_info['ended_at_eof']}, last_record_was_mrr={t_info['last_record_was_mrr']}, "
                f"pir_without_prr={t_info['pir_without_prr']}, PRR count={t_info['prr_count']} "
                f"vs PCR PART_CNT={t_info['pcr_part_cnt']}. Partial data recovered.",
                True, logger,
            )

        diag = parsed.get("DIAGNOSTICS") if isinstance(parsed, dict) else None
        if isinstance(diag, dict) and not diag.get("_logged"):
            for w in (diag.get("warnings") or []):
                _emit_log(w, True, logger)
            for e in (diag.get("errors") or []):
                _emit_log(e, True, logger)
            diag["_logged"] = True

        if strict_validation and isinstance(parsed, dict):
            enforce_strict_analysis_validation(parsed, input_path)

        for rec_name, count in (counts or {}).items():
            total_counts[rec_name] += count

        parts = parsed.get("PARTS") or []
        results_list = parsed.get("RESULTS") or []
        rows_append = rows.append
        _fmt_c = format_coord_fn

        for part_index, part in enumerate(parts):
            results = results_list[part_index] if part_index < len(results_list) else {}
            res_get = results.get

            pf = part_pass_fail_fn(part)
            if pf == "PASS":
                pass_count += 1
            else:
                fail_count += 1

            # 1. WXY
            wafer_str = _fmt_c(res_get(wxy_wafer))
            x_str = _fmt_c(res_get(wxy_x))
            y_str = _fmt_c(res_get(wxy_y))
            wxy = f"{wafer_str}_{x_str}_{y_str}" if wafer_str and x_str and y_str else ""
            if wafer_str == "NaN" or x_str == "NaN" or y_str == "NaN":
                missing_wxy += 1

            # 2. UID (single test or multiple tests joined with '_')
            if single_uid:
                uid_str = _fmt_c(res_get(uid_test0))
            else:
                uid_str = "_".join([_fmt_c(res_get(t)) for t in uid_tests])
            if "NaN" in uid_str:
                missing_uid += 1

            # 3. BT MACID (any number of parameters, only when configured)
            if has_bt:
                b_parts = [_fmt_c(res_get(t)) for t in bt_tests]
                bt_str = "_".join(b_parts)
                if "NaN" in b_parts:
                    missing_bt += 1
            else:
                bt_str = ""

            # 4. ZB MACID (any number of parameters, only when configured)
            if has_zb:
                z_parts = [_fmt_c(res_get(t)) for t in zb_tests]
                zb_str = "_".join(z_parts)
                if "NaN" in z_parts:
                    missing_zb += 1
            else:
                zb_str = ""

            raw_test_t = part.get("TEST_T")
            raw_hb = part.get("HARD_BIN")
            raw_sb = part.get("SOFT_BIN")

            rows_append({
                "panel": row_panel,
                "source_file": source,
                "part_id": part.get("PART_ID") or f"PART_{len(rows) + 1}",
                "site_num": part.get("SITE_NUM", ""),
                "test_t": "" if raw_test_t is None else raw_test_t,
                "hard_bin": "" if raw_hb is None else raw_hb,
                "soft_bin": "" if raw_sb is None else raw_sb,
                "pass_fail": pf,
                "wafer": wafer_str,
                "x": x_str,
                "y": y_str,
                "wxy": wxy,
                "uid": uid_str,
                "bt": bt_str,
                "zb": zb_str,
            })

    def _emit_summary_log() -> None:
        summary_parts = [
            f"{panel_name} summary: total={len(rows):,}",
            f"PASS={pass_count:,}",
            f"FAIL={fail_count:,}",
            f"missing WXY={missing_wxy:,}",
            f"missing UID={missing_uid:,}",
        ]
        if has_bt:
            summary_parts.append(f"missing BT={missing_bt:,}")
        if has_zb:
            summary_parts.append(f"missing ZB={missing_zb:,}")
        _emit_log(", ".join(summary_parts), True, logger)

    # -- Cross-panel fast path: rows-only assembly from pre-parsed results ------
    if pre_parsed is not None:
        for index, input_path in enumerate(input_paths, start=1):
            _emit_log(f"\n[{panel_name}] [{index}/{len(input_paths)}] Processing {input_path}", True, logger)
            parsed = pre_parsed.get(input_path)
            if parsed is None:
                parsed, _c, _ = parse_stdf_file(
                    input_path=input_path, filter_tests=filter_tests,
                    verbose=False, logger=logger, progress_callback=None,
                    expected_lot_id=expected_lot_id, strict_validation=strict_validation,
                )
            _accumulate(index, input_path, parsed, {})
            _emit_log(f"{panel_name} parts accumulated: {len(rows):,}", True, logger)
        _emit_summary_log()
        return {
            "panel": panel_name, "rows": rows, "total_parts": len(rows),
            "pass_count": pass_count, "fail_count": fail_count,
            "missing_wxy": missing_wxy, "missing_uid": missing_uid,
            "missing_bt": missing_bt, "missing_zb": missing_zb,
            "counts": dict(total_counts), "skipped_ptr": 0, "mir_lot_id": mir_lot_id,
        }

    workers = resolve_worker_count_fn(max_workers, len(input_paths))
    used_parallel = False

    if workers > 1:
        filter_list = tuple(sorted(filter_tests))
        tasks = [(p, filter_list, expected_lot_id) for p in input_paths]
        parsed_by_path: Dict[str, object] = {}
        try:
            _emit_log(f"[{panel_name}] Parsing {len(input_paths)} file(s) in parallel using {workers} worker process(es).", True, logger)
            done_bytes = 0
            with ProcessPoolExecutor(max_workers=workers) as ex:
                future_to_path = {ex.submit(_parse_stdf_worker, t): t[0] for t in tasks}
                for fut in as_completed(future_to_path):
                    fpath, parsed = fut.result()
                    parsed_by_path[fpath] = parsed
                    try:
                        done_bytes += max(os.path.getsize(fpath), 1)
                    except OSError:
                        done_bytes += 1
                    if progress_callback:
                        progress_callback(progress_start + span * (done_bytes / total_bytes), status_text)
            used_parallel = True
        except Exception as exc:
            _emit_log(f"[{panel_name}] Parallel parsing unavailable ({exc}); falling back to sequential.", True, logger)
            rows.clear()
            total_counts.clear()
            pass_count = fail_count = missing_wxy = missing_uid = missing_bt = missing_zb = 0
            used_parallel = False

        if used_parallel:
            for index, input_path in enumerate(input_paths, start=1):
                _emit_log(f"\n[{panel_name}] [{index}/{len(input_paths)}] Processing {input_path}", True, logger)
                _accumulate(index, input_path, parsed_by_path[input_path], {})
                _emit_log(f"{panel_name} parts accumulated: {len(rows):,}", True, logger)

    if not used_parallel:
        for index, input_path in enumerate(input_paths, start=1):
            file_size = _file_sizes[index - 1]
            _emit_log(f"\n[{panel_name}] [{index}/{len(input_paths)}] Processing {input_path}", True, logger)

            def file_progress(current_bytes: int, file_total: int, base=processed_bytes, fsz=file_size):
                if not progress_callback:
                    return
                total_for_file = file_total if file_total > 0 else fsz
                fraction = progress_start + span * ((base + min(max(current_bytes, 0), total_for_file)) / total_bytes)
                progress_callback(fraction, status_text)

            parsed, counts, _ = parse_stdf_file(
                input_path=input_path, filter_tests=filter_tests,
                verbose=False, logger=logger, progress_callback=file_progress,
                expected_lot_id=expected_lot_id, strict_validation=strict_validation,
            )
            _accumulate(index, input_path, parsed, counts)

            processed_bytes += file_size
            if progress_callback:
                progress_callback(progress_start + span * (processed_bytes / total_bytes), status_text)
            _emit_log(f"{panel_name} parts accumulated: {len(rows):,}", True, logger)

    _emit_summary_log()
    return {
        "panel": panel_name, "rows": rows, "total_parts": len(rows),
        "pass_count": pass_count, "fail_count": fail_count,
        "missing_wxy": missing_wxy, "missing_uid": missing_uid,
        "missing_bt": missing_bt, "missing_zb": missing_zb,
        "counts": dict(total_counts), "skipped_ptr": 0, "mir_lot_id": mir_lot_id,
    }


def _analyze_single_identifier(
    id_key: str,
    fp_rows: List[Dict[str, Any]],
    retest_rows: List[Dict[str, Any]],
    qc_rows: List[Dict[str, Any]],
    fp_pass_count: int,
    retest_pass_count: int,
    fp_actual: int,
    total_actual: int,
) -> Dict[str, Any]:
    """Run C-array string-interned deduplication, retest reconciliation, and QC verification
    for a single identifier key ('wxy', 'uid', 'bt', or 'zb')."""
    _id_map: Dict[str, int] = {}
    _id_to_val: List[str] = [""]  # index 0 = unused sentinel

    def _get_id(val: str) -> int:
        try:
            return _id_map[val]
        except KeyError:
            idx = len(_id_to_val)
            _id_map[val] = idx
            _id_to_val.append(val)
            return idx

    # 1. Pre-extract retest rows -> C-level arrays
    rt_ids = _CArray('i')
    rt_pass = bytearray()
    for _row in retest_rows:
        _val = _row.get(id_key)
        if _is_valid_identifier(_val, id_key):
            rt_ids.append(_get_id(_val))
            rt_pass.append(_row["pass_fail"] == "PASS")
        else:
            rt_ids.append(0)
            rt_pass.append(0)

    # 2. Pre-extract FP rows -> C-level arrays
    fp_ids = _CArray('i')
    fp_pf = bytearray()
    fp_hbin = _CArray('i')
    for _row in fp_rows:
        _val = _row.get(id_key)
        if _is_valid_identifier(_val, id_key):
            fp_ids.append(_get_id(_val))
            fp_pf.append(_row["pass_fail"] == "PASS")
        else:
            fp_ids.append(0)
            fp_pf.append(0)
        _hb = _row.get("hard_bin")
        fp_hbin.append(_hb if isinstance(_hb, int) else -1)

    # 3. Single retest pass: build retest ID sets
    retest_all_ids: Set[int] = set()
    retest_pass_ids: Set[int] = set()
    for _id, _is_pass in zip(rt_ids, rt_pass):
        if _id:
            retest_all_ids.add(_id)
            if _is_pass:
                retest_pass_ids.add(_id)

    # 4. Single FP pass: duplicate detection + fp_pass build + reject count
    _fp_len = len(fp_rows)
    _rt_len = len(retest_rows)
    _fp_dup_hits = _CArray('i', bytes(4 * _fp_len))
    _rt_dup_hits = _CArray('i', bytes(4 * _rt_len))
    _rt_retest_dup_hits = _CArray('i', bytes(4 * _rt_len))

    fp_pass_ids: Set[int] = set()
    head_pass_ids: Set[int] = set()
    duplicate_guid = 0
    duplicate_list: List[Tuple[str, int]] = []

    retest_duplicate_guid = 0
    retest_duplicate_list: List[Tuple[str, int]] = []
    retest_pass_seen_ids: Set[int] = set()

    _fp_duplicate_labels: List[str] = [NO_DUPLICATE_LABEL] * _fp_len
    _rt_duplicate_labels: List[str] = [NO_DUPLICATE_LABEL] * _rt_len

    _fp_source_idx_by_id: Dict[int, int] = {}
    _fp_first_idx_by_id: Dict[int, int] = {}
    _fp_dup_ids: Set[int] = set()
    fp_datalog_duplicate_count = 0
    fp_datalog_duplicate_list: List[Tuple[str, int]] = []
    _rt_source_idx_by_id: Dict[int, int] = {}

    fp_rejects_not_in_retest = 0

    _fp_add = fp_pass_ids.add
    _h_add = head_pass_ids.add
    _h_dis = head_pass_ids.discard
    _dup_app = duplicate_list.append
    _fp_dl_dup_app = fp_datalog_duplicate_list.append
    _rt_dup_app = retest_duplicate_list.append
    _rt_pass_seen_add = retest_pass_seen_ids.add
    _rt_pass_seen_dis = retest_pass_seen_ids.discard

    _i = 0
    for _id, _is_pass, _hb in zip(fp_ids, fp_pf, fp_hbin):
        if _id:
            _pn = _i + 1
            _first_fp_i = _fp_first_idx_by_id.get(_id)
            if _first_fp_i is not None:
                fp_datalog_duplicate_count += 1
                _fp_dl_dup_app((_id_to_val[_id], _pn))
                _fp_dup_ids.add(_id)
                _fp_dup_hits[_i] = _pn
                _fp_duplicate_labels[_i] = FP_DUPLICATE_LABEL
                if _fp_duplicate_labels[_first_fp_i] == NO_DUPLICATE_LABEL:
                    _fp_duplicate_labels[_first_fp_i] = FP_ORIGINAL_LABEL
            else:
                _fp_first_idx_by_id[_id] = _i

            if _id in fp_pass_ids and _id in head_pass_ids:
                duplicate_guid += 1
                _dup_app((_id_to_val[_id], _pn))
                _fp_dup_hits[_i] = _pn
                _fp_duplicate_labels[_i] = FP_DUPLICATE_LABEL
                _src_i = _fp_source_idx_by_id.get(_id)
                if _src_i is not None and _fp_duplicate_labels[_src_i] == NO_DUPLICATE_LABEL:
                    _fp_duplicate_labels[_src_i] = FP_ORIGINAL_LABEL
            if _is_pass:
                _fp_add(_id)
                _h_add(_id)
                _fp_source_idx_by_id[_id] = _i
            else:
                _h_dis(_id)
                _fp_source_idx_by_id.pop(_id, None)
                if _hb != 3 and _id not in retest_all_ids:
                    fp_rejects_not_in_retest += 1
        _i += 1

    # 5. Single RETEST pass: FP-original duplicate + RETEST duplicate detection
    _i = 0
    for _id, _is_pass in zip(rt_ids, rt_pass):
        if _id:
            _pn = _fp_len + _i + 1
            if _id in retest_pass_seen_ids:
                retest_duplicate_guid += 1
                _rt_dup_app((_id_to_val[_id], _pn))
                _rt_retest_dup_hits[_i] = _pn
                _rt_duplicate_labels[_i] = RETEST_DUPLICATE_LABEL
                _src_i = _rt_source_idx_by_id.get(_id)
                if _src_i is not None:
                    if _rt_duplicate_labels[_src_i] == NO_DUPLICATE_LABEL:
                        _rt_duplicate_labels[_src_i] = RETEST_ORIGINAL_LABEL
                    elif _rt_duplicate_labels[_src_i] == FP_DUPLICATE_LABEL:
                        _rt_duplicate_labels[_src_i] = f"{FP_DUPLICATE_LABEL} / {RETEST_ORIGINAL_LABEL}"
                    elif _rt_duplicate_labels[_src_i] == RETEST_DUPLICATE_LABEL:
                        _rt_duplicate_labels[_src_i] = f"{RETEST_DUPLICATE_LABEL} / {RETEST_ORIGINAL_LABEL}"
            elif _id in fp_pass_ids and _id in head_pass_ids:
                duplicate_guid += 1
                _dup_app((_id_to_val[_id], _pn))
                _rt_dup_hits[_i] = _pn
                _rt_duplicate_labels[_i] = FP_DUPLICATE_LABEL
                _src_i = _fp_source_idx_by_id.get(_id)
                if _src_i is not None and _fp_duplicate_labels[_src_i] == NO_DUPLICATE_LABEL:
                    _fp_duplicate_labels[_src_i] = FP_ORIGINAL_LABEL

            if _is_pass:
                _h_add(_id)
                _rt_pass_seen_add(_id)
                _rt_source_idx_by_id[_id] = _i
            else:
                _h_dis(_id)
                _rt_pass_seen_dis(_id)
                _rt_source_idx_by_id.pop(_id, None)
        _i += 1

    # 6. Write back row annotations
    is_wxy = (id_key == "wxy")
    for _row, _hit, _id, _is_pass, _hb, _label in zip(
        fp_rows, _fp_dup_hits, fp_ids, fp_pf, fp_hbin, _fp_duplicate_labels
    ):
        _rej_in_rt = ("YES" if _id in retest_all_ids else "NO") if (not _is_pass and _hb != 3 and _id) else "N/A"
        _is_fp_dl_dup = (_id in _fp_dup_ids) if _id else False
        _row[f"is_total_duplicate_{id_key}"] = _label
        _row[f"is_fp_datalog_duplicate_{id_key}"] = _is_fp_dl_dup
        _row[f"reject_in_retest_{id_key}"] = _rej_in_rt
        if is_wxy:
            _row["datalog"] = _row["source_file"]
            _row["duplicate_wxy"] = (_id_to_val[_id], _hit) if _hit else "N/A"
            _row["is_duplicate"] = FP_DUPLICATE_LABEL if _hit else NO_DUPLICATE_LABEL
            _row["retest_duplicate_wxy"] = "N/A"
            _row["is_retest_duplicate"] = NO_DUPLICATE_LABEL
            _row["is_total_duplicate"] = _label
            _row["duplicate_label"] = _label
            _row["is_fp_datalog_duplicate"] = _is_fp_dl_dup
            _row["reject_in_retest"] = _rej_in_rt

    for _row, _hit, _rt_hit, _id, _label in zip(
        retest_rows, _rt_dup_hits, _rt_retest_dup_hits, rt_ids, _rt_duplicate_labels
    ):
        _row[f"is_total_duplicate_{id_key}"] = _label
        _row[f"is_fp_datalog_duplicate_{id_key}"] = False
        _row[f"reject_in_retest_{id_key}"] = "N/A"
        if is_wxy:
            _row["datalog"] = _row["source_file"]
            _row["duplicate_wxy"] = (_id_to_val[_id], _hit) if _hit else "N/A"
            _row["is_duplicate"] = FP_DUPLICATE_LABEL if _hit else NO_DUPLICATE_LABEL
            _row["retest_duplicate_wxy"] = (_id_to_val[_id], _rt_hit) if _rt_hit else "N/A"
            _row["is_retest_duplicate"] = RETEST_DUPLICATE_LABEL if _rt_hit else NO_DUPLICATE_LABEL
            _row["is_total_duplicate"] = _label
            _row["duplicate_label"] = _label
            _row["reject_in_retest"] = "N/A"

    # 7. QC analysis
    total_good_ids = fp_pass_ids | retest_pass_ids
    qc_ids = _CArray(
        'i',
        (_get_id(_r.get(id_key)) if _is_valid_identifier(_r.get(id_key), id_key) else 0 for _r in qc_rows),
    )
    qc_seen_ids: Set[int] = set()
    qc_unique_ids: Set[int] = set()
    qc_duplicate_guid = 0
    _qc_seen_add = qc_seen_ids.add
    _qc_unique_add = qc_unique_ids.add

    for _id in qc_ids:
        if not _id:
            continue
        if _id in qc_seen_ids:
            qc_duplicate_guid += 1
        else:
            _qc_seen_add(_id)
            _qc_unique_add(_id)

    qc_sampling_good_in_prev_op = len(qc_unique_ids & total_good_ids)
    qc_failed_or_missing_prev_op_ids = qc_unique_ids - total_good_ids
    qc_failed_or_missing_prev_op_qty = len(qc_failed_or_missing_prev_op_ids)
    qc_failed_or_missing_prev_op_list = sorted(_id_to_val[_id] for _id in qc_failed_or_missing_prev_op_ids)

    qc_failed_set = set(qc_failed_or_missing_prev_op_ids)
    for _row, _id in zip(qc_rows, qc_ids):
        _row[f"is_qc_fail_prev_step_{id_key}"] = (_id in qc_failed_set) if _id else False

    total_duplicate_guid = duplicate_guid + retest_duplicate_guid
    calculated_fp_qty = fp_pass_count - duplicate_guid
    delta = calculated_fp_qty - fp_actual

    total_pass_parts = fp_pass_count + retest_pass_count
    total_good = total_pass_parts - total_duplicate_guid
    total_delta = total_good - total_actual

    has_fp_duplicate = fp_datalog_duplicate_count > 0
    check1_result = "PASS" if (total_delta >= 0 and not has_fp_duplicate) else "FAIL"
    check2_result = "PASS" if total_duplicate_guid <= 72 else "FAIL"
    check4_result = "PASS" if qc_failed_or_missing_prev_op_qty == 0 else "FAIL"

    status = "PASS" if (delta >= 0 and check4_result == "PASS" and not has_fp_duplicate) else "FAIL"
    total_status = "PASS" if (total_delta >= 0 and check4_result == "PASS" and not has_fp_duplicate) else "FAIL"

    return {
        "key": id_key,
        "label": CHECK_LABELS[id_key],
        "duplicate_guid": duplicate_guid,
        "duplicate_list": duplicate_list,
        "fp_datalog_duplicate_count": fp_datalog_duplicate_count,
        "fp_datalog_duplicate_list": fp_datalog_duplicate_list,
        "retest_duplicate_guid": retest_duplicate_guid,
        "retest_duplicate_list": retest_duplicate_list,
        "total_duplicate_guid": total_duplicate_guid,
        "fp_rejects_not_in_retest": fp_rejects_not_in_retest,
        "qc_sampling_good_in_prev_op": qc_sampling_good_in_prev_op,
        "qc_duplicate_guid": qc_duplicate_guid,
        "qc_failed_or_missing_prev_op_qty": qc_failed_or_missing_prev_op_qty,
        "qc_failed_or_missing_prev_op_list": qc_failed_or_missing_prev_op_list,
        "calculated_first_pass_qty": calculated_fp_qty,
        "delta_vs_fp_actual_good": delta,
        "total_good": total_good,
        "delta_vs_total_actual_good": total_delta,
        "has_fp_duplicate": has_fp_duplicate,
        "check1_result": check1_result,
        "check2_result": check2_result,
        "check4_result": check4_result,
        "status": status,
        "total_good_status": total_status,
    }


def analyze_sg406_guid_data(
    first_pass_paths: Sequence[str],
    retest_paths: Sequence[str],
    qc_paths: Sequence[str],
    lot_id: str,
    mpc_text: str,
    fp_actual: int,
    total_actual: int,
    mpc_wxy_map: Dict[str, Dict[str, int]],
    format_coord_fn: Callable[[object], str],
    part_pass_fail_fn: Callable[[Dict[str, object]], str],
    resolve_worker_count_fn: Callable[[Any, int], int],
    logger: LogFunc = None,
    progress_callback: ProgressFunc = None,
    max_workers: Optional[int] = None,
) -> Dict[str, Any]:
    """Multi-Identifier (WXY + UID + BT MACID and/or ZB MACID) analysis pipeline."""
    sg406_cfg = resolve_macid_device_config(mpc_text, mpc_wxy_map)
    wxy_tests = sg406_cfg["wxy"]
    has_bt = bool(sg406_cfg.get("has_bt", True))
    has_zb = bool(sg406_cfg.get("has_zb", True))
    mask_key = str(sg406_cfg["mask"])
    active_lbls = ["WXY", "UID"] + (["BT"] if has_bt else []) + (["ZB"] if has_zb else [])
    wxy_source = f"MPC prefix {mask_key} (Multi-Parameter: {' + '.join(active_lbls)})"

    _emit_log("=" * 80, True, logger)
    _emit_log(
        f"Starting {mask_key} Multi-Identifier STDF Analysis ({' + '.join(active_lbls)})",
        True, logger,
    )
    if lot_id:
        _emit_log(f"Lot ID: {lot_id}", True, logger)
    _emit_log(f"MPC: {mpc_text}", True, logger)
    _emit_log(f"First Pass Actual Good QTY: {fp_actual:,}", True, logger)
    _emit_log(f"Total Actual Good QTY: {total_actual:,}", True, logger)
    _pfx_map = sg406_cfg.get("macid_prefixes") or {}
    _pfx_parts = []
    if has_bt:
        _bp = _pfx_map.get("bt") or ()
        _pfx_parts.append(f"BT={','.join(_bp) if _bp else 'Disabled'}")
    if has_zb:
        _zp = _pfx_map.get("zb") or ()
        _pfx_parts.append(f"ZB={','.join(_zp) if _zp else 'Disabled'}")
    _emit_log(
        f"{mask_key} Tests resolved: Wafer={wxy_tests['wafer']}, X={wxy_tests['x']}, Y={wxy_tests['y']} | "
        f"UID={list(sg406_cfg.get('uid_tests', (sg406_cfg['uid'],)))} | "
        f"BT={list(sg406_cfg['bt'])} | ZB={list(sg406_cfg['zb'])} | "
        f"MACID Prefix={', '.join(_pfx_parts) if _pfx_parts else 'Disabled'}",
        True, logger,
    )

    # === Cross-panel parallel pre-parse for all 18 tests =======================
    pre_parsed = None
    _all_paths = list(first_pass_paths) + list(retest_paths) + list(qc_paths)
    _combined_workers = resolve_worker_count_fn(max_workers, len(_all_paths))
    if _combined_workers > 1:
        _filter_list = tuple(sorted(sg406_cfg["all_tests"]))
        _seen = set()
        _unique_paths = [p for p in _all_paths if not (p in _seen or _seen.add(p))]
        try:
            _sizes = {}
            for _p in _unique_paths:
                try:
                    _sizes[_p] = max(os.path.getsize(_p), 1)
                except OSError:
                    _sizes[_p] = 1

            _biggest = max(_unique_paths, key=lambda _p: _sizes[_p])
            _pool_paths = [_p for _p in _unique_paths if _p != _biggest]
            _pool_workers = max(1, min(_combined_workers, len(_pool_paths))) if _pool_paths else 1
            _big_name = os.path.basename(_biggest)
            _big_total = _sizes[_biggest]

            _emit_log(
                f"Parsing {len(_unique_paths)} file(s) across ALL panels "
                f"(FIRST PASS + RETEST + QC): {len(_pool_paths)} smaller file(s) in "
                f"{_pool_workers} worker process(es) + the largest datalog "
                f"('{_big_name}') in the main process with live progress.",
                True, logger,
            )

            def _big_progress(_cur, _tot, _bt=_big_total):
                if progress_callback:
                    _t = _tot if _tot and _tot > 0 else _bt
                    _frac = (min(max(_cur, 0), _t) / _t) if _t else 1.0
                    progress_callback(0.15 + 0.77 * _frac, f"Parsing largest datalog: {_big_name}")

            pre_parsed = {}
            _filter_set = set(_filter_list)
            if _pool_paths:
                with ProcessPoolExecutor(max_workers=_pool_workers) as _ex:
                    _futs = {_ex.submit(_parse_stdf_worker, (_p, _filter_list, lot_id)): _p for _p in _pool_paths}
                    _big_parsed, _bc, _ = parse_stdf_file(
                        input_path=_biggest, filter_tests=_filter_set,
                        verbose=False, logger=None, progress_callback=_big_progress,
                        expected_lot_id=lot_id, strict_validation=False,
                    )
                    pre_parsed[_biggest] = _big_parsed
                    for _fut in as_completed(_futs):
                        _path, _parsed = _fut.result()
                        pre_parsed[_path] = _parsed
            else:
                _big_parsed, _bc, _ = parse_stdf_file(
                    input_path=_biggest, filter_tests=_filter_set,
                    verbose=False, logger=None, progress_callback=_big_progress,
                    expected_lot_id=lot_id, strict_validation=False,
                )
                pre_parsed[_biggest] = _big_parsed

            if progress_callback:
                progress_callback(0.92, "Finished parsing all panels")
        except Exception as _exc:
            _emit_log(f"Cross-panel parallel parse failed ({_exc}); using per-panel parsing.", True, logger)
            pre_parsed = None

    truncated_files: List[Dict[str, Any]] = []

    fp_data = parse_panel_sg406_parts(
        first_pass_paths, "FIRST PASS", sg406_cfg, format_coord_fn, part_pass_fail_fn,
        resolve_worker_count_fn, logger, progress_callback,
        0.15, 0.55, max_workers=max_workers, pre_parsed=pre_parsed,
        expected_lot_id=lot_id, strict_validation=True,
        truncated_files_out=truncated_files,
    )
    retest_data = parse_panel_sg406_parts(
        retest_paths, "RETEST", sg406_cfg, format_coord_fn, part_pass_fail_fn,
        resolve_worker_count_fn, logger, progress_callback,
        0.55, 0.90, max_workers=max_workers, pre_parsed=pre_parsed,
        expected_lot_id=lot_id, strict_validation=True,
        truncated_files_out=truncated_files,
    )
    qc_data = parse_panel_sg406_parts(
        qc_paths, "QC", sg406_cfg, format_coord_fn, part_pass_fail_fn,
        resolve_worker_count_fn, logger, progress_callback,
        0.90, 0.97, max_workers=max_workers, pre_parsed=pre_parsed,
        expected_lot_id=lot_id, strict_validation=True,
        truncated_files_out=truncated_files,
    )

    had_pre_parsed = pre_parsed is not None
    if pre_parsed is not None:
        # Release raw STDF parse dicts immediately after row extraction to cut peak RAM
        pre_parsed.clear()
        pre_parsed = None

    if progress_callback and had_pre_parsed:
        active_ids_lbl = ", ".join(
            ["WXY", "UID"] + (["BT"] if has_bt else []) + (["ZB"] if has_zb else [])
        )
        progress_callback(0.95, f"Analyzing duplicates and QC ({active_ids_lbl})")

    fp_rows = fp_data["rows"]
    retest_rows = retest_data["rows"]
    qc_rows = qc_data["rows"]
    fp_pass_count = int(fp_data["pass_count"])
    retest_pass_count = int(retest_data["pass_count"])
    total_pass_parts = fp_pass_count + retest_pass_count

    # 1. Standard WXY analysis (retains C-array high speed analysis)
    wxy_m = _analyze_single_identifier(
        id_key="wxy",
        fp_rows=fp_rows,
        retest_rows=retest_rows,
        qc_rows=qc_rows,
        fp_pass_count=fp_pass_count,
        retest_pass_count=retest_pass_count,
        fp_actual=fp_actual,
        total_actual=total_actual,
    )

    # 2. Single-pass MACID duplicate analysis (PASS parts only; FAIL parts excluded):
    # Each active BT MACID and/or ZB MACID among PASS units should be uniquely assigned to one UID only.
    fp_good_rows = [r for r in fp_rows if r.get("pass_fail") == "PASS"]
    rt_good_rows = [r for r in retest_rows if r.get("pass_fail") == "PASS"]
    qc_good_rows = [r for r in qc_rows if r.get("pass_fail") == "PASS"]
    dup_bt, dup_zb, dup_macid_pairs = _compute_duplicate_macid_summary(
        itertools.chain(fp_good_rows, rt_good_rows, qc_good_rows),
        cfg=sg406_cfg,
    )
    duplicate_macid_count = len(dup_bt) + len(dup_zb)

    # 3. Good Unique Count tally (active parameters: WXY vs UID vs BT MACID and/or ZB MACID) on PASS rows.
    gu_fp = compute_good_unique_discrepancy(fp_good_rows, cfg=sg406_cfg)
    gu_total = compute_good_unique_discrepancy(fp_good_rows + rt_good_rows, cfg=sg406_cfg)
    fp_good_unique_disc = int(gu_fp["discrepancy"])
    total_good_unique_disc = int(gu_total["discrepancy"])
    good_unique_pairs: List[str] = []
    for _p in list(gu_fp["pairs"]) + list(gu_total["pairs"]):
        if _p not in good_unique_pairs:
            good_unique_pairs.append(_p)
    has_fp_gu_disc = fp_good_unique_disc > 0
    has_total_gu_disc = total_good_unique_disc > 0

    # 4. Layer 2: Automatic BT/ZB MACID prefix profiling & statistical outlier detection (PASS parts only).
    from core import config as app_config
    _pcfg = app_config.get_macid_prefix_profile_config()
    prefix_profile = compute_macid_prefix_profile(
        fp_good_rows + rt_good_rows,
        cfg=sg406_cfg,
        prefix_bytes=_pcfg["prefix_bytes"],
        min_pass_units=_pcfg["min_pass_units"],
        max_outlier_count=_pcfg["max_count"],
        max_outlier_pct=_pcfg["max_pct"],
        mode=_pcfg["mode"],
    )
    prefix_outlier_mode = str(prefix_profile.get("mode") or "fail").strip().lower()
    prefix_outlier_count = int(prefix_profile.get("outlier_unit_count") or 0)
    has_prefix_outlier_fail = (prefix_outlier_mode == "fail" and prefix_outlier_count > 0)

    # Status & Criteria
    has_fp_wxy_dup = (wxy_m["fp_datalog_duplicate_count"] > 0)
    has_dup_macid = (duplicate_macid_count > 0)
    check2_result = wxy_m["check2_result"]
    check4_result = wxy_m["check4_result"]

    check1_result = "PASS" if (
        wxy_m["delta_vs_total_actual_good"] >= 0 and not has_fp_wxy_dup and not has_dup_macid
        and not has_fp_gu_disc and not has_total_gu_disc and not has_prefix_outlier_fail
    ) else "FAIL"

    status = "PASS" if (
        wxy_m["delta_vs_fp_actual_good"] >= 0 and check4_result == "PASS" and not has_fp_wxy_dup
        and not has_dup_macid and not has_fp_gu_disc and not has_prefix_outlier_fail
    ) else "FAIL"
    if total_actual == 0 and (has_dup_macid or has_total_gu_disc or has_prefix_outlier_fail):
        total_status = "FAIL"
    elif total_actual == 0:
        total_status = "SKIPPED"
    else:
        total_status = "PASS" if (
            wxy_m["delta_vs_total_actual_good"] >= 0 and check4_result == "PASS" and not has_fp_wxy_dup
            and not has_dup_macid and not has_total_gu_disc and not has_prefix_outlier_fail
        ) else "FAIL"

    _emit_log("-" * 80, True, logger)
    _emit_log(
        f"[WXY] FP Datalog Duplicate: {wxy_m['fp_datalog_duplicate_count']:,} | "
        f"FP Duplicate Good: {wxy_m['duplicate_guid']:,} | Total Duplicate Good: {wxy_m['total_duplicate_guid']:,} | "
        f"Calc FP Good: {wxy_m['calculated_first_pass_qty']:,} (Delta: {wxy_m['delta_vs_fp_actual_good']:,}) | "
        f"Total Good: {wxy_m['total_good']:,} (Delta: {wxy_m['delta_vs_total_actual_good']:,}) | "
        f"QC Good Prev Op: {wxy_m['qc_sampling_good_in_prev_op']:,} | QC Fail/Missing Prev Op: {wxy_m['qc_failed_or_missing_prev_op_qty']:,}",
        True, logger,
    )
    _dup_parts = []
    if has_bt:
        _dup_parts.append(f"BT duplicates: {len(dup_bt):,}")
    if has_zb:
        _dup_parts.append(f"ZB duplicates: {len(dup_zb):,}")
    _emit_log(
        f"[{mask_key} MACID] Duplicate MACID count: {duplicate_macid_count:,} "
        f"({', '.join(_dup_parts)})",
        True, logger,
    )
    for _scope, _gu in (("FP", gu_fp), ("Total", gu_total)):
        _c = _gu["counts"]
        _c_str = " | ".join(f"{k}={int(v):,}" for k, v in _c.items())
        _emit_log(
            f"[{mask_key} Good Unique Count - {_scope}] {_c_str} | "
            f"Discrepancy={_gu['discrepancy']:,}"
            + (f" ({', '.join(_gu['pairs'])})" if _gu["pairs"] else ""),
            True, logger,
        )
        for _pd in _gu["pair_details"]:
            if _pd["discrepancy"] > 0:
                _emit_log(
                    f"    - {_pd['pair']}: count diff={_pd['count_diff']:,}, "
                    f"GOOD rows with only one valid value={_pd['one_side_invalid_rows']:,}, "
                    f"values mapped to >1 counterpart={_pd['multi_mapped_values']:,}",
                    True, logger,
                )
    _mode_lbl = prefix_outlier_mode.upper()
    _emit_log(
        f"[{mask_key} MACID Prefix Profile] Mode={_mode_lbl} "
        f"({prefix_profile['prefix_bytes']}-byte prefix, "
        f"min_units={prefix_profile['min_pass_units']}, "
        f"outlier: <={prefix_profile['max_count']} units OR <{prefix_profile['max_pct']:.2f}%)",
        True, logger,
    )
    for _mt, _tdata in (prefix_profile.get("by_type") or {}).items():
        _emit_log(
            f"  - {_tdata['label']} ({_tdata['total_units']:,} unique PASS): {_tdata['summary_str']}",
            True, logger,
        )
    if prefix_profile.get("rollover_detected"):
        _emit_log(
            "  [INFO] Multi-pool MACID prefix rollover detected (valid pool transition; no hold triggered).",
            True, logger,
        )
    for _ol in prefix_profile.get("outliers") or ():
        _lvl = "[FAIL]" if _mode_lbl == "FAIL" else "[WARNING]"
        _samp_strs = [
            f"UID={s.get('uid') or 'N/A'} {s.get('macid_hex') or ''} ({s.get('panel') or ''})"
            for s in (_ol.get("samples") or ())[:10]
        ]
        if len(_ol.get("samples") or ()) > 10:
            _samp_strs.append(f"...+{len(_ol['samples']) - 10} more")
        _emit_log(
            f"  {_lvl} {_ol['label']} prefix {_ol['prefix']}: "
            f"{_ol['count']:,} unit(s) ({_ol['pct']:.2f}%) | {_ol['boundary_hint']} | "
            f"{', '.join(_samp_strs)}",
            True, logger,
        )
    _emit_log(f"First Pass Result: {status}  |  Total Good Result: {total_status}", True, logger)
    _emit_log(f"FTDC Check 1: {check1_result}  |  Check 2: {check2_result}  |  Check 4: {check4_result}", True, logger)

    if progress_callback:
        progress_callback(1.0, "Analysis completed")

    mir_lot_id = (
        fp_data.get("mir_lot_id")
        or retest_data.get("mir_lot_id")
        or qc_data.get("mir_lot_id")
        or lot_id
    )

    metrics_by_key = {
        "wxy": wxy_m,
    }

    res_dict: Dict[str, Any] = {
        "is_sg406": True,
        "has_bt": has_bt,
        "has_zb": has_zb,
        "sg406_tests": sg406_cfg,
        "sg406_metrics": metrics_by_key,
        "status": status,
        "lot_id": lot_id,
        "mir_lot_id": mir_lot_id,
        "mpc": mpc_text,
        "wxy_tests": wxy_tests,
        "wxy_source": wxy_source,
        "first_pass": fp_data,
        "retest": retest_data,
        "qc": qc_data,
        # Standard WXY keys
        "duplicate_guid": wxy_m["duplicate_guid"],
        "duplicate_wxy_list": wxy_m["duplicate_list"],
        "fp_datalog_duplicate_count": wxy_m["fp_datalog_duplicate_count"],
        "fp_datalog_duplicate_wxy_list": wxy_m["fp_datalog_duplicate_list"],
        "retest_duplicate_guid": wxy_m["retest_duplicate_guid"],
        "retest_duplicate_wxy_list": wxy_m["retest_duplicate_list"],
        "total_duplicate_guid": wxy_m["total_duplicate_guid"],
        "fp_rejects_not_in_retest": wxy_m["fp_rejects_not_in_retest"],
        "qc_sampling_good_in_prev_op": wxy_m["qc_sampling_good_in_prev_op"],
        "qc_duplicate_guid": wxy_m["qc_duplicate_guid"],
        "first_pass_total_parts": fp_data["total_parts"],
        "first_pass_pass_count": fp_pass_count,
        "retest_pass_count": retest_pass_count,
        "calculated_first_pass_qty": wxy_m["calculated_first_pass_qty"],
        "first_pass_actual_good_qty": fp_actual,
        "delta_vs_fp_actual_good": wxy_m["delta_vs_fp_actual_good"],
        "total_pass_parts": total_pass_parts,
        "total_good": wxy_m["total_good"],
        "total_actual_good_qty": total_actual,
        "delta_vs_total_actual_good": wxy_m["delta_vs_total_actual_good"],
        "total_good_status": total_status,
        "check1_result": check1_result,
        "check2_result": check2_result,
        "check4_result": check4_result,
        "qc_failed_or_missing_prev_op_qty": wxy_m["qc_failed_or_missing_prev_op_qty"],
        "qc_failed_or_missing_prev_op_wxy": wxy_m["qc_failed_or_missing_prev_op_list"],
        "truncated_files": truncated_files,
        # SG406 Duplicate MACID
        "duplicate_macid": duplicate_macid_count,
        "duplicate_macid_count": duplicate_macid_count,
        "duplicate_bt_macid": len(dup_bt),
        "duplicate_bt_macid_count": len(dup_bt),
        "duplicate_zb_macid": len(dup_zb),
        "duplicate_zb_macid_count": len(dup_zb),
        "duplicate_bt_macids": dup_bt,
        "duplicate_zb_macids": dup_zb,
        "duplicate_macid_pairs": dup_macid_pairs,
        # SG406 Good Unique Count tally (WXY vs UID vs BT MACID vs ZB MACID)
        "fp_good_unique_discrepancy": fp_good_unique_disc,
        "total_good_unique_discrepancy": total_good_unique_disc,
        "fp_good_unique_counts": gu_fp["counts"],
        "total_good_unique_counts": gu_total["counts"],
        "fp_good_unique_pair_details": gu_fp["pair_details"],
        "total_good_unique_pair_details": gu_total["pair_details"],
        "good_unique_discrepancy_pairs": good_unique_pairs,
        # Layer 2: Automatic MACID prefix profiling & outlier detection
        "macid_prefix_profile": prefix_profile,
        "macid_prefix_outliers": prefix_profile["outliers"],
        "macid_prefix_outlier_count": prefix_outlier_count,
        "macid_prefix_outlier_mode": prefix_outlier_mode,
        "macid_prefix_rollover_detected": bool(prefix_profile["rollover_detected"]),
        # Backward-compatibility aliases
        "wxy_duplicate_guid": wxy_m["duplicate_guid"],
        "wxy_total_duplicate_guid": wxy_m["total_duplicate_guid"],
        "wxy_calculated_first_pass_qty": wxy_m["calculated_first_pass_qty"],
        "wxy_delta_vs_fp_actual_good": wxy_m["delta_vs_fp_actual_good"],
        "wxy_total_good": wxy_m["total_good"],
        "wxy_delta_vs_total_actual_good": wxy_m["delta_vs_total_actual_good"],
        "wxy_qc_sampling_good_in_prev_op": wxy_m["qc_sampling_good_in_prev_op"],
        "wxy_fp_rejects_not_in_retest": wxy_m["fp_rejects_not_in_retest"],
        "wxy_check1_result": check1_result,
        "wxy_check2_result": check2_result,
        "wxy_check4_result": check4_result,
    }

    remarks = compute_sg406_remarks(res_dict)
    res_dict["remarks"] = remarks
    _emit_log(f"Remarks: {remarks}", True, logger)
    return res_dict


def _resolve_active_macid_flags(
    cfg_or_result: Optional[Dict[str, Any]] = None,
    has_bt: Optional[bool] = None,
    has_zb: Optional[bool] = None,
) -> Tuple[bool, bool]:
    """Determine (has_bt, has_zb) flags from explicit args, result dict, or config dict."""
    if has_bt is not None and has_zb is not None:
        b_flag, z_flag = bool(has_bt), bool(has_zb)
        return (b_flag, z_flag) if (b_flag or z_flag) else (True, True)

    b_res: Optional[bool] = has_bt
    z_res: Optional[bool] = has_zb
    if isinstance(cfg_or_result, dict):
        sub_cfg = cfg_or_result.get("sg406_tests")
        src = sub_cfg if isinstance(sub_cfg, dict) else cfg_or_result
        if b_res is None:
            if "has_bt" in cfg_or_result:
                b_res = bool(cfg_or_result["has_bt"])
            elif "has_bt" in src:
                b_res = bool(src["has_bt"])
            elif "bt" in src:
                b_res = bool(src.get("bt"))
        if z_res is None:
            if "has_zb" in cfg_or_result:
                z_res = bool(cfg_or_result["has_zb"])
            elif "has_zb" in src:
                z_res = bool(src["has_zb"])
            elif "zb" in src:
                z_res = bool(src.get("zb"))

        if b_res is None and z_res is None:
            gu_c = cfg_or_result.get("fp_good_unique_counts") or cfg_or_result.get("total_good_unique_counts")
            if isinstance(gu_c, dict) and gu_c:
                has_b_key = ("BT MACID" in gu_c) or ("bt" in gu_c)
                has_z_key = ("ZB MACID" in gu_c) or ("zb" in gu_c)
                if has_b_key or has_z_key:
                    b_res = has_b_key
                    z_res = has_z_key

    b_final = True if b_res is None else bool(b_res)
    z_final = True if z_res is None else bool(z_res)
    if not b_final and not z_final:
        return True, True
    return b_final, z_final


def get_duplicate_macid_label(
    cfg_or_result: Optional[Dict[str, Any]] = None,
    has_bt: Optional[bool] = None,
    has_zb: Optional[bool] = None,
) -> str:
    """Return dynamic Duplicate MACID row label showing only active MACID parameters."""
    b_flag, z_flag = _resolve_active_macid_flags(cfg_or_result, has_bt=has_bt, has_zb=has_zb)
    if b_flag and z_flag:
        return "Duplicate MACID (BT / ZB)"
    if b_flag:
        return "Duplicate MACID (BT)"
    return "Duplicate MACID (ZB)"


def get_unique_valid_good_label(
    cfg_or_result: Optional[Dict[str, Any]] = None,
    has_bt: Optional[bool] = None,
    has_zb: Optional[bool] = None,
) -> str:
    """Return dynamic Unique Valid Good row label showing only active parameters."""
    b_flag, z_flag = _resolve_active_macid_flags(cfg_or_result, has_bt=has_bt, has_zb=has_zb)
    parts = ["WXY", "UID"]
    if b_flag:
        parts.append("BT")
    if z_flag:
        parts.append("ZB")
    return f"Unique Valid Good ({' / '.join(parts)})"


def compute_sg406_remarks(result: Dict[str, Any]) -> str:
    """Compute remarks for multi-identifier (WXY + UID + BT/ZB MACID) analysis.

    If multiple failure violations occur (e.g. QC failure, duplicate WXY, duplicate MACID),
    all violations are included in Remarks.
    """
    violations: List[str] = []
    has_bt, has_zb = _resolve_active_macid_flags(result)

    # 1. Check 4: QC units untested/rejected in previous FT operation
    check4_result = str(result.get("check4_result") or "").strip().upper()
    qc_failed_qty = int(result.get("qc_failed_or_missing_prev_op_qty") or 0)
    if check4_result == "FAIL" or qc_failed_qty > 0:
        count = qc_failed_qty if qc_failed_qty > 0 else 1
        violations.append(
            f"There are {count} QC units that are either UNTESTED/TESTED REJECT "
            "from previous FT operation. Please hold lot for review and assessment."
        )

    # 2. Check for duplicated WXY coordinate(s) in FIRST PASS datalog
    fp_dup_cnt = int(result.get("fp_datalog_duplicate_count") or 0)
    if fp_dup_cnt > 0:
        violations.append(
            "Duplicate WXY coordinate(s) observed during FIRST PASS. Please hold lot for review and assessment."
        )

    # 3. Check for Duplicate MACID (assigned to multiple UIDs)
    dup_bt_cnt = int(result.get("duplicate_bt_macid") or result.get("duplicate_bt_macid_count") or 0) if has_bt else 0
    dup_zb_cnt = int(result.get("duplicate_zb_macid") or result.get("duplicate_zb_macid_count") or 0) if has_zb else 0
    if dup_bt_cnt > 0 and dup_zb_cnt > 0:
        violations.append(
            f"Duplicate BT MACID ({dup_bt_cnt}) & Duplicate ZB MACID ({dup_zb_cnt}) were observed. "
            "Please hold lot and inform Product Owner for review and assessment."
        )
    elif dup_bt_cnt > 0:
        violations.append(
            f"Duplicate BT MACID ({dup_bt_cnt}) was observed. "
            "Please hold lot and inform Product Owner for review and assessment."
        )
    elif dup_zb_cnt > 0:
        violations.append(
            f"Duplicate ZB MACID ({dup_zb_cnt}) was observed. "
            "Please hold lot and inform Product Owner for review and assessment."
        )
    else:
        dup_macid_cnt = int(result.get("duplicate_macid") or result.get("duplicate_macid_count") or 0)
        if dup_macid_cnt > 0:
            violations.append(
                f"Duplicate MACID ({dup_macid_cnt}) was observed. "
                "Please hold lot and inform Product Owner for review and assessment."
            )

    # 4. Good Unique Count tally (WXY vs UID vs active MACIDs)
    fp_gu = int(result.get("fp_good_unique_discrepancy") or 0)
    tot_gu = int(result.get("total_good_unique_discrepancy") or 0)
    if fp_gu > 0 or tot_gu > 0:
        gu_pairs = [str(p) for p in (result.get("good_unique_discrepancy_pairs") or []) if str(p).strip()]
        default_vs = ["WXY", "UID"] + (["BT MACID"] if has_bt else []) + (["ZB MACID"] if has_zb else [])
        pairs_txt = ", ".join(gu_pairs) if gu_pairs else " vs ".join(default_vs)
        violations.append(
            f"Discrepancy was observed in Good Unique Count of ({pairs_txt}). "
            "Please hold and inform Product Owner for review and assessment."
        )

    # 5. Layer 2: MACID prefix outlier detection
    prefix_outlier_cnt = int(result.get("macid_prefix_outlier_count") or 0)
    prefix_outlier_mode = str(result.get("macid_prefix_outlier_mode") or "fail").strip().lower()
    prefix_outlier_txt = ""
    if prefix_outlier_cnt > 0:
        prefix_outlier_txt = format_macid_prefix_outlier_text(
            result.get("macid_prefix_profile"),
            result.get("macid_prefix_outliers"),
        )
        if not prefix_outlier_txt:
            u_word = "unit" if prefix_outlier_cnt == 1 else "units"
            prefix_outlier_txt = f"{prefix_outlier_cnt:,} {u_word}"
        if prefix_outlier_mode == "fail":
            violations.append(
                f"MACID prefix outlier(s) detected ({prefix_outlier_txt}). "
                "Please hold lot and inform Product Owner for review and assessment."
            )

    if violations:
        if prefix_outlier_cnt > 0 and prefix_outlier_mode == "warn":
            violations.append(f"[Prefix Warning] MACID prefix outlier(s) detected ({prefix_outlier_txt}).")
        return " ".join(violations)

    # Check FP and Total Good status combination for remaining cases
    fp_status = str(result.get("status") or "").strip().upper()
    tot_status = str(result.get("total_good_status") or "").strip().upper()

    fp_pass = (fp_status == "PASS")
    tot_pass = (tot_status == "PASS")

    if fp_pass and tot_pass:
        if prefix_outlier_cnt > 0 and prefix_outlier_mode == "warn":
            return (
                f"PASS with Warning: MACID prefix outlier(s) detected ({prefix_outlier_txt}). "
                "Please review Valid Good Count Cross-Check sheet."
            )
        return "No abnormalities or discrepancies were observed."
    elif fp_pass and not tot_pass:
        base_msg = "There is abnormality or discrepancy observed in RETEST process. Please check."
    elif not fp_pass and tot_pass:
        base_msg = "There is abnormality or discrepancy observed in FIRST PASS process. Please check."
    else:
        base_msg = "There was an anomaly during lot processing. Please hold lot for review and assessment."
    if prefix_outlier_cnt > 0 and prefix_outlier_mode == "warn":
        return f"{base_msg} [Prefix Warning] MACID prefix outlier(s) detected ({prefix_outlier_txt})."
    return base_msg


def _fmt_gu_counts(counts: Any) -> str:
    if not isinstance(counts, dict) or not counts:
        return "N/A"
    return " / ".join(f"{k}={int(v):,}" for k, v in counts.items())


def format_duplicate_macid_row(
    dup_bt: Any,
    dup_zb: Any = None,
    has_bt: bool = True,
    has_zb: bool = True,
) -> str:
    """Format duplicate BT and/or ZB MACID counts:
    - Both active: '{dup_bt} / {dup_zb}'
    - BT only:     '{dup_bt}'
    - ZB only:     '{dup_zb}'
    """
    try:
        bt_c = int(float(str(dup_bt or 0).replace(",", "")))
    except (ValueError, TypeError):
        bt_c = 0
    try:
        zb_c = int(float(str(dup_zb or 0).replace(",", "")))
    except (ValueError, TypeError):
        zb_c = 0
    if has_bt and has_zb:
        return f"{bt_c:,} / {zb_c:,}"
    if has_bt:
        return f"{bt_c:,}"
    if has_zb:
        return f"{zb_c:,}"
    return f"{bt_c:,} / {zb_c:,}"


def format_unique_valid_good_count_row(
    counts: Any,
    fallback_val: Any = 0,
    has_bt: Optional[bool] = None,
    has_zb: Optional[bool] = None,
) -> str:
    """Format unique valid good counts in slash format for active parameters only:
    - Both active: '{wxy} / {uid} / {bt} / {zb}'
    - BT only:     '{wxy} / {uid} / {bt}'
    - ZB only:     '{wxy} / {uid} / {zb}'
    """
    try:
        fb = int(float(str(fallback_val or 0).replace(",", "")))
    except (ValueError, TypeError):
        fb = 0
    if isinstance(counts, dict) and counts:
        if has_bt is None and has_zb is None:
            has_b_key = ("BT MACID" in counts) or ("bt" in counts)
            has_z_key = ("ZB MACID" in counts) or ("zb" in counts)
            if has_b_key or has_z_key:
                has_bt = has_b_key
                has_zb = has_z_key
        wxy_c = int(counts.get("WXY", fb) or 0)
        uid_c = int(counts.get("UID", fb) or 0)
        bt_c = int(counts.get("BT MACID", fb) or 0)
        zb_c = int(counts.get("ZB MACID", fb) or 0)
    else:
        wxy_c = uid_c = bt_c = zb_c = fb
    b_flag = True if has_bt is None else bool(has_bt)
    z_flag = True if has_zb is None else bool(has_zb)
    if not b_flag and not z_flag:
        b_flag = z_flag = True
    vals = [f"{wxy_c:,}", f"{uid_c:,}"]
    if b_flag:
        vals.append(f"{bt_c:,}")
    if z_flag:
        vals.append(f"{zb_c:,}")
    return " / ".join(vals)


def build_sg406_summary_and_details(
    result: Dict[str, Any],
    hard_bin_equals_fn: Callable[[Dict[str, object], int], bool],
    soft_bin_equals_fn: Callable[[Dict[str, object], int], bool],
):
    """Build summary_sections and detail_rows for multi-identifier analysis."""
    cache_sig = (
        True,
        result.get("status"),
        result.get("total_good_status"),
        result.get("remarks"),
        result.get("check1_result"),
        result.get("check2_result"),
        result.get("check4_result"),
        result.get("first_pass_pass_count"),
        result.get("total_pass_parts"),
        result.get("macid_prefix_outlier_count"),
        result.get("macid_prefix_outlier_mode"),
    )
    cached = result.get("_cached_summary_and_details")
    if isinstance(cached, tuple) and len(cached) == 2 and cached[0] == cache_sig:
        return cached[1]

    def panel_data(panel_key: str) -> Dict[str, object]:
        data = result.get(panel_key) or {}
        return data if isinstance(data, dict) else {}

    def panel_rows(panel_key: str) -> List[Dict[str, object]]:
        rows = panel_data(panel_key).get("rows") or []
        return rows if isinstance(rows, list) else []

    fp_rows = panel_rows("first_pass")
    retest_rows = panel_rows("retest")

    fp_hard_bin3_rejects = sum(
        1 for r in fp_rows if r.get("pass_fail") == "FAIL" and hard_bin_equals_fn(r, 3)
    )
    retest_bin90 = sum(1 for r in retest_rows if soft_bin_equals_fn(r, 90))

    fp_status = result.get("status", "")
    total_status = result.get("total_good_status") or "SKIPPED"
    qc_info = panel_data("qc")
    fp_info = panel_data("first_pass")
    rt_info = panel_data("retest")
    has_bt, has_zb = _resolve_active_macid_flags(result)
    dup_bt = (result.get("duplicate_bt_macid", result.get("duplicate_bt_macid_count", 0)) or 0) if has_bt else 0
    dup_zb = (result.get("duplicate_zb_macid", result.get("duplicate_zb_macid_count", 0)) or 0) if has_zb else 0
    dup_macid = result.get("duplicate_macid", result.get("duplicate_macid_count", dup_bt + dup_zb)) or 0

    fp_good_wo_dup = result.get("wxy_calculated_first_pass_qty", result.get("calculated_first_pass_qty"))
    tot_good_wo_dup = result.get("wxy_total_good", result.get("total_good"))
    dup_macid_lbl = get_duplicate_macid_label(result, has_bt=has_bt, has_zb=has_zb)
    uniq_good_lbl = get_unique_valid_good_label(result, has_bt=has_bt, has_zb=has_zb)
    dup_macid_str = format_duplicate_macid_row(dup_bt, dup_zb, has_bt=has_bt, has_zb=has_zb)
    fp_gu_str = format_unique_valid_good_count_row(
        result.get("fp_good_unique_counts"), fp_good_wo_dup, has_bt=has_bt, has_zb=has_zb
    )
    tot_gu_str = format_unique_valid_good_count_row(
        result.get("total_good_unique_counts"), tot_good_wo_dup, has_bt=has_bt, has_zb=has_zb
    )

    summary_sections = [
        (
            "FIRST PASS",
            [
                ("First Pass STDF Good", result.get("first_pass_pass_count")),
                ("Duplicate Good UID", result.get("wxy_duplicate_guid", result.get("duplicate_guid", 0))),
                ("First Pass Good w/o Duplicate UID", fp_good_wo_dup),
                ("First Pass Actual Good", result.get("first_pass_actual_good_qty")),
                ("Difference between Good w/o Duplicate vs FP Actual Good", result.get("wxy_delta_vs_fp_actual_good", result.get("delta_vs_fp_actual_good"))),
                ("Total QC Units Good At Prev. Operation", result.get("wxy_qc_sampling_good_in_prev_op", result.get("qc_sampling_good_in_prev_op"))),
                (dup_macid_lbl, dup_macid_str),
                (uniq_good_lbl, fp_gu_str),
                ("Result", fp_status),
            ],
        ),
        (
            "TOTAL (FP + RETEST)",
            [
                ("STDF Total Good", result.get("total_pass_parts")),
                ("Duplicate Good UID", result.get("wxy_total_duplicate_guid", result.get("total_duplicate_guid", 0))),
                ("Total Good w/o Duplicate UID", tot_good_wo_dup),
                ("Actual Total Good", result.get("total_actual_good_qty")),
                ("Difference between Good w/o Duplicate vs Actual Total Good", result.get("wxy_delta_vs_total_actual_good", result.get("delta_vs_total_actual_good"))),
                ("Total QC Tested Good units", qc_info.get("pass_count")),
                (dup_macid_lbl, dup_macid_str),
                (uniq_good_lbl, tot_gu_str),
                ("Result", total_status),
            ],
        ),
    ]

    sg406_tests = result.get("sg406_tests") or {}
    if not sg406_tests and result.get("mpc"):
        try:
            sg406_tests = resolve_macid_device_config(str(result.get("mpc")))
        except ValueError:
            sg406_tests = {}
    wxy_tests = result.get("wxy_tests") or (sg406_tests.get("wxy") if isinstance(sg406_tests, dict) else None) or {}
    remarks = str(result.get("remarks") or compute_sg406_remarks(result))

    uid_tests_seq = tuple(sg406_tests.get("uid_tests") or _as_test_tuple(sg406_tests.get("uid")))
    if len(uid_tests_seq) == 1:
        uid_tests_val: Any = uid_tests_seq[0]
    elif uid_tests_seq:
        uid_tests_val = ", ".join(str(t) for t in uid_tests_seq)
    else:
        uid_tests_val = ""
    bt_tests_str = ", ".join(str(t) for t in sg406_tests.get("bt", ())) if has_bt else "N/A"
    zb_tests_str = ", ".join(str(t) for t in sg406_tests.get("zb", ())) if has_zb else "N/A"
    active_gu_hdr = " / ".join(["WXY", "UID"] + (["BT MACID"] if has_bt else []) + (["ZB MACID"] if has_zb else []))

    detail_rows = [
        ("General", "Lot ID", result.get("lot_id")),
        ("General", "MPC", result.get("mpc")),
        ("General", "Remarks", remarks),
        ("General", "Wafer Test Number", wxy_tests.get("wafer") if isinstance(wxy_tests, dict) else ""),
        ("General", "X Test Number", wxy_tests.get("x") if isinstance(wxy_tests, dict) else ""),
        ("General", "Y Test Number", wxy_tests.get("y") if isinstance(wxy_tests, dict) else ""),
        ("General", "UID Test Number", uid_tests_val),
    ]
    if has_bt:
        detail_rows.append(("General", "BT MACID Test Numbers", bt_tests_str))
    if has_zb:
        detail_rows.append(("General", "ZB MACID Test Numbers", zb_tests_str))

    detail_rows.append(("FT", "Duplicate Good WXY", result.get("wxy_total_duplicate_guid", result.get("total_duplicate_guid"))))
    if has_bt:
        detail_rows.append(("FT", "Duplicate BT MACID", dup_bt))
    if has_zb:
        detail_rows.append(("FT", "Duplicate ZB MACID", dup_zb))

    detail_rows.extend([
        ("FT", f"FP Good Unique Count ({active_gu_hdr})", _fmt_gu_counts(result.get("fp_good_unique_counts"))),
        ("FT", "FP Good Unique Count Discrepancy", result.get("fp_good_unique_discrepancy", 0) or 0),
        ("FT", f"Total Good Unique Count ({active_gu_hdr})", _fmt_gu_counts(result.get("total_good_unique_counts"))),
        ("FT", "Total Good Unique Count Discrepancy", result.get("total_good_unique_discrepancy", 0) or 0),
        ("FT", "Good Unique Count Discrepant Pair(s)", ", ".join(result.get("good_unique_discrepancy_pairs") or []) or "None"),
        ("FT", "FP rejects not in RETEST", result.get("wxy_fp_rejects_not_in_retest", result.get("fp_rejects_not_in_retest"))),
        ("FT", "BIN3 NO RETEST", fp_hard_bin3_rejects),
        ("FT", "FIRST PASS Total parts", fp_info.get("total_parts")),
        ("FT", "FIRST PASS PASS parts", fp_info.get("pass_count")),
        ("FT", "FIRST PASS FAIL parts", fp_info.get("fail_count")),
        ("FT", "FIRST PASS Missing WXY", fp_info.get("missing_wxy")),
        ("FT", "FIRST PASS Missing UID", fp_info.get("missing_uid")),
    ])
    if has_bt:
        detail_rows.append(("FT", "FIRST PASS Missing BT MACID", fp_info.get("missing_bt")))
    if has_zb:
        detail_rows.append(("FT", "FIRST PASS Missing ZB MACID", fp_info.get("missing_zb")))

    detail_rows.extend([
        ("FT", "RETEST Total parts", rt_info.get("total_parts")),
        ("FT", "RETEST PASS parts", rt_info.get("pass_count")),
        ("FT", "RETEST FAIL parts", rt_info.get("fail_count")),
        ("FT", "RETEST Missing WXY", rt_info.get("missing_wxy")),
        ("FT", "RETEST Missing UID", rt_info.get("missing_uid")),
    ])
    if has_bt:
        detail_rows.append(("FT", "RETEST Missing BT MACID", rt_info.get("missing_bt")))
    if has_zb:
        detail_rows.append(("FT", "RETEST Missing ZB MACID", rt_info.get("missing_zb")))

    detail_rows.extend([
        ("QC", "Total parts", qc_info.get("total_parts")),
        ("QC", "PASS parts", qc_info.get("pass_count")),
        ("QC", "FAIL parts", qc_info.get("fail_count")),
        ("QC", "Missing WXY", qc_info.get("missing_wxy")),
        ("QC", "Missing UID", qc_info.get("missing_uid")),
    ])
    if has_bt:
        detail_rows.append(("QC", "Missing BT MACID", qc_info.get("missing_bt")))
    if has_zb:
        detail_rows.append(("QC", "Missing ZB MACID", qc_info.get("missing_zb")))

    detail_rows.extend([
        ("QC", "Duplicate WXY within QC", result.get("wxy_qc_duplicate_guid", result.get("qc_duplicate_guid"))),
        ("QC", "QC Sampling units good in previous operation", result.get("wxy_qc_sampling_good_in_prev_op", result.get("qc_sampling_good_in_prev_op"))),
        ("FT", "Calculated First Pass QTY", result.get("wxy_calculated_first_pass_qty", result.get("calculated_first_pass_qty"))),
        ("FT", "Total PASS parts", result.get("total_pass_parts")),
        ("FT", "Total Good", result.get("wxy_total_good", result.get("total_good"))),
    ])
    prefix_profile = result.get("macid_prefix_profile")
    if isinstance(prefix_profile, dict):
        by_type = prefix_profile.get("by_type") or {}
        if has_bt and "bt" in by_type:
            detail_rows.append(("FT", "Observed BT MACID Prefix(es)", by_type["bt"].get("summary_str") or "None"))
        if has_zb and "zb" in by_type:
            detail_rows.append(("FT", "Observed ZB MACID Prefix(es)", by_type["zb"].get("summary_str") or "None"))
        outlier_txt = format_macid_prefix_outlier_text(prefix_profile, result.get("macid_prefix_outliers"))
        detail_rows.append(("FT", "MACID Prefix Outlier(s)", outlier_txt or "None"))

    check1 = result.get("check1_result", "N/A")
    check2 = result.get("check2_result", "N/A")
    check4 = result.get("check4_result", "N/A")

    ftdc_criteria = {
        "is_sg406": True,
        "has_bt": has_bt,
        "has_zb": has_zb,
        "check1": check1,
        "check2": check2,
        "check4": check4,
        "fp_status": fp_status,
        "total_status": total_status,
        "remarks": remarks,
        "duplicate_bt_macid": dup_bt,
        "duplicate_zb_macid": dup_zb,
        "duplicate_macid": dup_bt + dup_zb,
        "sg406_metrics": result.get("sg406_metrics") or {},
        "result_ref": result,
    }

    extra = {"fp_hard_bin3_rejects": fp_hard_bin3_rejects, "retest_bin90": retest_bin90}
    out = (summary_sections, detail_rows, fp_status, total_status, extra, ftdc_criteria)
    result["_cached_summary_and_details"] = (cache_sig, out)
    return out


def build_sg406_ftdc_criteria_text(ftdc_criteria: dict) -> List[str]:
    """Build the [FTDC Criteria] section lines for multi-identifier analysis."""
    check1 = ftdc_criteria.get("check1", "N/A")
    check2 = ftdc_criteria.get("check2", "N/A")
    check4 = ftdc_criteria.get("check4", "N/A")
    fp_status = ftdc_criteria.get("fp_status", "")
    total_status = ftdc_criteria.get("total_status", "")
    remarks = ftdc_criteria.get("remarks", "")
    res = ftdc_criteria.get("result_ref") or {}
    has_bt, has_zb = _resolve_active_macid_flags(
        res,
        has_bt=ftdc_criteria.get("has_bt"),
        has_zb=ftdc_criteria.get("has_zb"),
    )
    active_vs_str = " vs ".join(["WXY", "UID"] + (["BT MACID"] if has_bt else []) + (["ZB MACID"] if has_zb else []))

    lines = ["[FTDC Criteria]"]
    if remarks:
        lines.append(f"Remarks: {remarks}")
        lines.append("")

    # Check 1
    lines.append(
        "[Check 1] - If Total Actual Good QTY is greater than Total Tested Good w/o Duplicate "
        "(or if any internal duplicate is observed in FIRST PASS, if Duplicate MACID is observed, "
        f"or if any discrepancy in Good Unique Count of {active_vs_str} is observed), "
        "then result will be 'FAIL'. Otherwise, result will be 'PASS'."
    )
    lines.append(f"[Result: {check1}]")
    fp_dup = res.get("fp_datalog_duplicate_count", 0)
    d_fp = res.get("delta_vs_fp_actual_good", 0)
    d_tot = res.get("delta_vs_total_actual_good", 0)
    dup_bt = res.get("duplicate_bt_macid", res.get("duplicate_bt_macid_count", 0)) or 0
    dup_zb = res.get("duplicate_zb_macid", res.get("duplicate_zb_macid_count", 0)) or 0
    lines.append(
        f"  - WXY: {fp_dup} duplicate unit(s) in FIRST PASS datalog | "
        f"FP variance: {d_fp} | TOTAL variance: {d_tot}"
    )
    if has_bt:
        lines.append(
            f"  - Duplicate BT MACID: {dup_bt} duplicate BT MACID(s) observed"
        )
    if has_zb:
        lines.append(
            f"  - Duplicate ZB MACID: {dup_zb} duplicate ZB MACID(s) observed"
        )
    for _scope, _dk, _ck in (
        ("FP", "fp_good_unique_discrepancy", "fp_good_unique_counts"),
        ("Total", "total_good_unique_discrepancy", "total_good_unique_counts"),
    ):
        lines.append(
            f"  - Good Unique Count Discrepancy ({_scope}): {int(res.get(_dk) or 0)} "
            f"[{_fmt_gu_counts(res.get(_ck))}]"
        )
    _gu_pairs = res.get("good_unique_discrepancy_pairs") or []
    if _gu_pairs:
        lines.append(f"  - Discrepant pair(s): {', '.join(_gu_pairs)}")
    lines.append("")

    # Check 2
    lines.append("[Check 2] - If duplicate good units exceed 72 units, then result will be 'FAIL'.")
    lines.append(f"[Result: {check2}]")
    tot_dup = res.get("total_duplicate_guid", res.get("wxy_total_duplicate_guid", 0)) or 0
    lines.append(f"  - Total duplicate good units = {tot_dup} (limit <= 72)")
    lines.append("")

    # Check 4
    lines.append("[Check 4] - If all QC samples are confirmed FT good parts, then result will be 'PASS'. Otherwise, it will be 'FAIL'.")
    lines.append(f"[Result: {check4}]")
    fail_qty = res.get("qc_failed_or_missing_prev_op_qty", 0)
    fail_list = res.get("qc_failed_or_missing_prev_op_wxy") or []
    if fail_qty and int(fail_qty) > 0:
        lines.append(f"  - {fail_qty} unit(s) in QC did not PASS in previous operation ({', '.join(fail_list)})")
    else:
        lines.append(f"  - All QC units confirmed PASS in previous operation.")

    prefix_profile = res.get("macid_prefix_profile")
    if isinstance(prefix_profile, dict):
        pp_mode = str(prefix_profile.get("mode") or res.get("macid_prefix_outlier_mode") or "fail").strip().upper()
        pp_ol_cnt = int(prefix_profile.get("outlier_unit_count") or res.get("macid_prefix_outlier_count") or 0)
        if pp_mode == "DISABLED":
            pp_res = "DISABLED"
        elif pp_ol_cnt > 0:
            pp_res = "FAIL" if pp_mode == "FAIL" else "WARNING"
        else:
            pp_res = "PASS"
        lines.append("")
        lines.append(
            f"[MACID Prefix Profile (Layer 2)] - Profiles unique PASS BT/ZB {prefix_profile.get('prefix_bytes', 3)}-byte prefixes; "
            f"flags un-allowlisted minority prefixes when unique PASS units >= {prefix_profile.get('min_pass_units', 50)} "
            f"and (count <= {prefix_profile.get('max_count', 2)} or share < {float(prefix_profile.get('max_pct', 0.5)):.2f}%). "
            f"Mode: {pp_mode}."
        )
        lines.append(f"[Result: {pp_res}]")
        for _mt, _tdata in (prefix_profile.get("by_type") or {}).items():
            lines.append(
                f"  - {_tdata.get('label', _mt.upper())} ({int(_tdata.get('total_units') or 0):,} unique PASS): "
                f"{_tdata.get('summary_str') or 'None'}"
            )
        for _ol in (prefix_profile.get("outliers") or ()):
            _samps = ", ".join(
                f"UID={s.get('uid') or 'N/A'} {s.get('macid_hex') or ''} ({s.get('panel') or ''})"
                for s in (_ol.get("samples") or ())[:10]
            )
            lines.append(
                f"  - OUTLIER {_ol.get('label', '')}: {_ol.get('prefix', '')} "
                f"({_ol.get('count', 0):,} unit(s) / {float(_ol.get('pct') or 0.0):.2f}%) | "
                f"{_ol.get('boundary_hint', '')}"
                + (f" | {_samps}" if _samps else "")
            )

    if fp_status == "FAIL" or total_status == "FAIL":
        lines.append("")
        lines.append("These are the possible reasons for Check 1 and/or Check 4 Fail:")
        lines.append("1) Corrupted STDF - Please double check each STDF, retrieve all corrupted datalog.")
        lines.append("")
        lines.append("2) Wrong/Incomplete STDF files attached - Kindly make sure you attached the previous FT step stdf and all RETESTs stdf. Also, if the datalog/summary was cut off, then attach the continuation of that STDF/datalog.")
        lines.append("")
        lines.append("3) Duplicate WXY in FIRST PASS or Duplicate MACID assigned to multiple UIDs - Please verify and confirm if there are duplicate WXYs or duplicate MACIDs.")
        lines.append("")
        lines.append(f"4) Discrepancy in Good Unique Count of {active_vs_str} - A GOOD unit has a missing/invalid UID or MACID, or one UID is linked to more than one MACID (or vice versa). Please inform Product Owner.")
        lines.append("")
        lines.append("5) Incorrect Actual Good QTY input - Kindly double check the ACTUAL GOOD QUANTITY, perform recount if possible.")
        if int(res.get("macid_prefix_outlier_count") or 0) > 0:
            lines.append("")
            lines.append("6) MACID prefix outlier - a PASS unit carries a MACID prefix not matching the lot's MACID pool(s). Please inform Product Owner.")

    return lines


def resolve_summary_metric_style(
    metric: str,
    value: Any,
    result: Dict[str, Any],
    *,
    is_total: bool = False,
    fmt_fn: Optional[Callable[[Any], str]] = None,
) -> Tuple[str, str]:
    """Return ``(formatted_value_str, foreground_hex_color)`` for a summary metric row.

    Shared by both the Tkinter result popup and programmatic Pillow result image renderer.
    """
    val_str = fmt_fn(value) if fmt_fn is not None else ("" if value is None else str(value))
    val_fg = "#000000"

    if metric == "Result":
        text = str(val_str or "").strip().upper()
        if text == "PASS":
            val_fg = "#008000"
        elif text == "FAIL":
            val_fg = "#C00000"
        elif text == "SKIPPED":
            val_fg = "#6C757D"
        else:
            val_fg = "#000000"
    elif "Difference between Good" in metric:
        if value is None or str(value).strip().upper() in ("N/A", ""):
            val_str = "N/A"
            val_fg = "#6C757D"
        else:
            try:
                num_val = float(str(value).replace(",", ""))
                val_str = f"{int(num_val):,}" if num_val.is_integer() else f"{num_val:,.2f}"
                val_fg = "#008000" if num_val >= 0 else "#C00000"
            except (ValueError, TypeError):
                val_fg = "#6C757D"
    elif metric.startswith("Duplicate MACID"):
        nums = [int(n) for n in re.findall(r"\d+", str(val_str).replace(",", ""))]
        has_dup = (
            any(n > 0 for n in nums)
            or (result.get("duplicate_bt_macid", 0) or 0) > 0
            or (result.get("duplicate_zb_macid", 0) or 0) > 0
            or (result.get("duplicate_macid", 0) or 0) > 0
        )
        val_fg = "#C00000" if has_dup else "#008000"
    elif metric.startswith("Unique Valid Good"):
        nums = [int(n) for n in re.findall(r"\d+", str(val_str).replace(",", ""))]
        disc_key = "total_good_unique_discrepancy" if is_total else "fp_good_unique_discrepancy"
        disc_val = int(result.get(disc_key, 0) or 0)
        has_disc = disc_val > 0 or len(set(nums)) > 1
        val_fg = "#C00000" if has_disc else "#008000"
    elif metric in ("Duplicate BT MACID", "Duplicate ZB MACID") or metric.startswith("Discrepancy in Good Unique Count"):
        try:
            num_val = int(float(str(value or 0).replace(",", "")))
        except (ValueError, TypeError):
            num_val = 0
        val_fg = "#C00000" if num_val > 0 else "#008000"

    return val_str, val_fg


def write_sg406_result_excel(
    result: Dict[str, Any],
    save_path: str,
    fmt_fn: Callable[[Any], str],
    to_num_fn: Callable[[Any], Any],
    col_tracker_cls: Any,
    hard_bin_equals_fn: Callable[[Dict[str, object], int], bool],
    soft_bin_equals_fn: Callable[[Dict[str, object], int], bool],
    progress_callback: Optional[Callable[[float, str], None]] = None,
):
    """Write SG406 analysis result to an Excel workbook with standard 2-column comparative layout."""
    import xlsxwriter

    if progress_callback:
        progress_callback(0.05, "Initializing SG406 Excel workbook…")

    wb = xlsxwriter.Workbook(save_path, {'constant_memory': False, 'strings_to_urls': False})

    hdr_fmt = wb.add_format({
        'bold': True, 'font_color': '#FFFFFF', 'bg_color': '#4472C4',
        'align': 'center', 'valign': 'vcenter',
        'border': 1,
    })
    subhdr_fmt = wb.add_format({
        'bold': True, 'bg_color': '#EDF2F7',
        'align': 'center', 'valign': 'vcenter',
        'border': 1,
    })
    border_fmt = wb.add_format({'border': 1, 'valign': 'vcenter'})
    center_border_fmt = wb.add_format({'border': 1, 'align': 'center', 'valign': 'vcenter'})
    green_center_fmt = wb.add_format({
        'border': 1, 'bg_color': '#90EE90', 'bold': True,
        'align': 'center', 'valign': 'vcenter',
    })
    red_center_fmt = wb.add_format({
        'border': 1, 'bg_color': '#FF5050', 'bold': True,
        'align': 'center', 'valign': 'vcenter',
    })
    center_fmt = wb.add_format({'align': 'center', 'valign': 'vcenter'})

    summary_sections, detail_rows, _, _, extra, _ = build_sg406_summary_and_details(
        result, hard_bin_equals_fn, soft_bin_equals_fn
    )

    # ══════════════════════════════════════════════════════════════════════
    # 1. Summary worksheet
    # ══════════════════════════════════════════════════════════════════════
    ws_sum = wb.add_worksheet("Summary")
    sum_tracker = col_tracker_cls()

    general_rows = [(m, v) for s, m, v in detail_rows if s == "General" and m != "Remarks"]

    row = 0
    ws_sum.merge_range(row, 0, row, 1, "GENERAL", hdr_fmt)
    sum_tracker.track(0, "GENERAL")
    row += 1

    for metric, value in general_rows:
        formatted = fmt_fn(value)
        ws_sum.write(row, 0, metric, border_fmt)
        ws_sum.write(row, 1, formatted, border_fmt)
        sum_tracker.track(0, metric)
        sum_tracker.track(1, formatted)
        row += 1

    start_row_for_panels = row + 2

    fp_status = str(result.get("status", "") or "").strip().upper()
    tot_status = str(result.get("total_good_status") or "SKIPPED").strip().upper()

    fp_stdf = result.get("first_pass_pass_count")
    fp_dup = result.get("wxy_duplicate_guid", result.get("duplicate_guid", 0))
    if fp_dup is None:
        fp_dup = 0
    fp_good_wo_dup = result.get("wxy_calculated_first_pass_qty", result.get("calculated_first_pass_qty"))
    fp_actual = result.get("first_pass_actual_good_qty")
    fp_diff = result.get("wxy_delta_vs_fp_actual_good")
    if fp_diff is None:
        fp_diff = result.get("delta_vs_fp_actual_good")
    if fp_diff is None and fp_good_wo_dup is not None and fp_actual is not None:
        try:
            fp_diff = int(fp_good_wo_dup) - int(fp_actual)
        except (ValueError, TypeError):
            pass
    fp_qc = result.get("wxy_qc_sampling_good_in_prev_op", result.get("qc_sampling_good_in_prev_op"))

    tot_stdf = result.get("total_pass_parts")
    tot_dup = result.get("wxy_total_duplicate_guid", result.get("total_duplicate_guid", 0))
    if tot_dup is None:
        tot_dup = 0
    tot_good_wo_dup = result.get("wxy_total_good", result.get("total_good"))
    tot_actual = result.get("total_actual_good_qty")
    tot_diff = result.get("wxy_delta_vs_total_actual_good")
    if tot_diff is None:
        tot_diff = result.get("delta_vs_total_actual_good")
    if tot_diff is None and tot_good_wo_dup is not None and tot_actual is not None and tot_status != "SKIPPED":
        try:
            tot_diff = int(tot_good_wo_dup) - int(tot_actual)
        except (ValueError, TypeError):
            pass
    qc_pass = (result.get("qc") or {}).get("pass_count") if isinstance(result.get("qc"), dict) else None

    has_bt, has_zb = _resolve_active_macid_flags(result)
    sg406_cfg = result.get("sg406_tests") if isinstance(result.get("sg406_tests"), dict) else None
    dup_bt = (result.get("duplicate_bt_macid", result.get("duplicate_bt_macid_count", 0)) or 0) if has_bt else 0
    dup_zb = (result.get("duplicate_zb_macid", result.get("duplicate_zb_macid_count", 0)) or 0) if has_zb else 0
    dup_macid = result.get("duplicate_macid", result.get("duplicate_macid_count", dup_bt + dup_zb)) or 0
    fp_good_uniq_disc = result.get("fp_good_unique_discrepancy", 0) or 0
    tot_good_uniq_disc = result.get("total_good_unique_discrepancy", 0) or 0
    dup_macid_lbl = get_duplicate_macid_label(result, has_bt=has_bt, has_zb=has_zb)
    uniq_good_lbl = get_unique_valid_good_label(result, has_bt=has_bt, has_zb=has_zb)
    dup_macid_str = format_duplicate_macid_row(dup_bt, dup_zb, has_bt=has_bt, has_zb=has_zb)
    fp_gu_str = format_unique_valid_good_count_row(
        result.get("fp_good_unique_counts"), fp_good_wo_dup, has_bt=has_bt, has_zb=has_zb
    )
    tot_gu_str = format_unique_valid_good_count_row(
        result.get("total_good_unique_counts"), tot_good_wo_dup, has_bt=has_bt, has_zb=has_zb
    )

    fp_matrix_rows = [
        ("First Pass STDF Good", fp_stdf, "normal"),
        ("Duplicate Good UID", fp_dup, "normal"),
        ("First Pass Good w/o Duplicate UID", fp_good_wo_dup, "normal"),
        ("First Pass Actual Good", fp_actual, "normal"),
        ("Difference between Good w/o Duplicate vs FP Actual Good", fp_diff, "diff"),
        ("Total QC Units Good At Prev. Operation", fp_qc, "normal"),
        (dup_macid_lbl, dup_macid_str, "macid_status_fail" if (dup_bt + dup_zb + dup_macid) > 0 else "macid_status_pass"),
        (uniq_good_lbl, fp_gu_str, "macid_status_fail" if fp_good_uniq_disc > 0 else "macid_status_pass"),
        ("Result", fp_status, "result"),
    ]

    tot_matrix_rows = [
        ("STDF Total Good", tot_stdf, "normal"),
        ("Duplicate Good UID", tot_dup, "normal"),
        ("Total Good w/o Duplicate UID", tot_good_wo_dup, "normal"),
        ("Actual Total Good", tot_actual, "normal"),
        ("Difference between Good w/o Duplicate vs Actual Total Good", tot_diff, "diff"),
        ("Total QC Tested Good units", qc_pass, "normal"),
        (dup_macid_lbl, dup_macid_str, "macid_status_fail" if (dup_bt + dup_zb + dup_macid) > 0 else "macid_status_pass"),
        (uniq_good_lbl, tot_gu_str, "macid_status_fail" if tot_good_uniq_disc > 0 else "macid_status_pass"),
        ("Result", tot_status, "result"),
    ]

    tables = [
        (0, "FIRST PASS", fp_matrix_rows, fp_status, False),
        (3, "TOTAL (FP + RETEST)", tot_matrix_rows, tot_status, True),
    ]

    for c_start, tbl_title, rows_data, overall_st, is_tot in tables:
        # Table Header (aligned with FTDC App Pop-up Result Window)
        ws_sum.merge_range(start_row_for_panels, c_start, start_row_for_panels, c_start + 1, tbl_title, hdr_fmt)
        sum_tracker.track(c_start, tbl_title)

        # Table Data Rows
        for r_offset, (m_label, val, r_type) in enumerate(rows_data, start=1):
            curr_r = start_row_for_panels + r_offset
            # Metric label cell: standard border, NO highlight
            ws_sum.write(curr_r, c_start, m_label, border_fmt)
            sum_tracker.track(c_start, m_label)

            # Highlighting applies ONLY to Difference, MACID/Unique status, and Result
            if r_type == "diff":
                if val is None or str(val).strip().upper() in ("N/A", ""):
                    v_str = "N/A"
                    cell_fmt = center_border_fmt
                else:
                    try:
                        num = float(str(val).replace(',', ''))
                        v_str = f"{int(num):,}" if num.is_integer() else f"{num:,.2f}"
                        cell_fmt = green_center_fmt if num >= 0 else red_center_fmt
                    except (ValueError, TypeError):
                        v_str = fmt_fn(val)
                        cell_fmt = center_border_fmt
            elif r_type == "result":
                res_str = str(val or "").strip().upper()
                v_str = res_str
                if res_str == "PASS":
                    cell_fmt = green_center_fmt
                elif res_str == "FAIL":
                    cell_fmt = red_center_fmt
                else:
                    cell_fmt = center_border_fmt
            elif r_type == "macid_status_fail":
                v_str = str(val or "")
                cell_fmt = red_center_fmt
            elif r_type == "macid_status_pass":
                v_str = str(val or "")
                cell_fmt = green_center_fmt
            else:
                # Normal rows (STDF Good, Duplicate Good UID, Good w/o Duplicate UID, Actual Good, QC Good): NO highlight!
                v_str = fmt_fn(val)
                cell_fmt = center_border_fmt

            ws_sum.write(curr_r, c_start + 1, v_str, cell_fmt)
            sum_tracker.track(c_start + 1, v_str)

    # Spacer column between tables (Col C = index 2)
    ws_sum.set_column(2, 2, 4)

    # Remarks row spanning across columns B to E (cols 1 to 4)
    remarks_text = str(result.get("remarks") or compute_sg406_remarks(result))
    remarks_row = start_row_for_panels + len(fp_matrix_rows) + 2
    ws_sum.write(remarks_row, 0, "Remarks:", border_fmt)
    if remarks_text.startswith("PASS with Warning:"):
        amber_remarks_fmt = wb.add_format({
            'border': 1, 'bg_color': '#FFF3CD', 'font_color': '#856404',
            'bold': True, 'valign': 'vcenter',
        })
        ws_sum.merge_range(remarks_row, 1, remarks_row, 4, remarks_text, amber_remarks_fmt)
    else:
        ws_sum.merge_range(remarks_row, 1, remarks_row, 4, remarks_text, border_fmt)
    sum_tracker.track(0, "Remarks:")
    sum_tracker.apply(ws_sum, max_width=85)

    if progress_callback:
        progress_callback(0.15, "Writing SG406 FT Total worksheet…")

    # ══════════════════════════════════════════════════════════════════════
    # 2. FT Total worksheet
    # ══════════════════════════════════════════════════════════════════════
    fp_panel = result.get("first_pass") or {}
    rt_panel = result.get("retest") or {}
    fp_rows_list = fp_panel.get("rows") or []
    rt_rows_list = rt_panel.get("rows") or []
    ft_all_rows = list(fp_rows_list) + list(rt_rows_list)

    qc_panel = result.get("qc") or {}
    qc_rows_list = qc_panel.get("rows") or []
    total_excel_rows = max(len(ft_all_rows) + len(qc_rows_list), 1)
    written_rows = 0

    # Build WXY lookup for FT (preserving PASS entries over later FAIL retests)
    ft_wxy_lookup: Dict[str, Dict[str, Any]] = {}
    for part_num, r_obj in enumerate(ft_all_rows, start=1):
        pf_val = r_obj.get("pass_fail", "")
        panel_val = r_obj.get("panel", "")
        id_val = r_obj.get("wxy")
        if _is_valid_identifier(id_val, "wxy"):
            existing = ft_wxy_lookup.get(id_val)
            if existing is None or existing.get("result") != "PASS" or pf_val == "PASS":
                ft_wxy_lookup[id_val] = {
                    "result": pf_val,
                    "part_num": part_num,
                    "datalog": panel_val,
                }

    dup_bt_map = result.get("duplicate_bt_macids") or {}
    dup_zb_map = result.get("duplicate_zb_macids") or {}

    hex_cache: Dict[str, str] = {}

    def _macid_hex_cached(val: Any) -> str:
        key = "" if val is None else str(val)
        cached = hex_cache.get(key)
        if cached is None:
            cached = macid_decimal_to_hex(val)
            hex_cache[key] = cached
        return cached

    ft_headers = [
        "Total Parts", "Part ID", "Site", "Test Time (ms)", "Hard Bin", "Soft Bin",
        "Pass/Fail", "Wafer (W)", "X", "Y", "WXY (W_X_Y)",
        "UID",
    ]
    if has_bt:
        ft_headers.extend(["BT MACID", "BT MACID HEX"])
    if has_zb:
        ft_headers.extend(["ZB MACID", "ZB MACID HEX"])
    ft_headers.extend([
        "Datalog",
        "Duplicate WXY", "Duplicate MACID Flag",
        "Reject In Retest",
    ])

    qc_headers = [
        "Total Parts", "Part ID", "Site", "Test Time (ms)", "Hard Bin", "Soft Bin",
        "Pass/Fail", "Wafer (W)", "X", "Y", "WXY (W_X_Y)",
        "UID",
    ]
    if has_bt:
        qc_headers.extend(["BT MACID", "BT MACID HEX"])
    if has_zb:
        qc_headers.extend(["ZB MACID", "ZB MACID HEX"])
    qc_headers.extend([
        "Datalog",
        "Result in previous FT step", "Part Number in previous FT step", "Datalog in previous FT step",
    ])

    row_get = dict.get
    dup_bt_has = dup_bt_map.__contains__
    dup_zb_has = dup_zb_map.__contains__
    lk_wxy_get = ft_wxy_lookup.get

    def _build_ft_row_values(part_num: int, r_obj: Dict[str, Any]) -> Tuple[Any, ...]:
        bt_val = row_get(r_obj, "bt", "") if has_bt else ""
        zb_val = row_get(r_obj, "zb", "") if has_zb else ""
        is_pass = str(row_get(r_obj, "pass_fail", "") or "").strip().upper() == "PASS"
        is_dup_bt = is_pass and has_bt and dup_bt_has(bt_val)
        is_dup_zb = is_pass and has_zb and dup_zb_has(zb_val)
        if is_dup_bt and is_dup_zb:
            mac_flag = "Duplicate BT & ZB MACID"
        elif is_dup_bt:
            mac_flag = "Duplicate BT MACID"
        elif is_dup_zb:
            mac_flag = "Duplicate ZB MACID"
        else:
            mac_flag = "No Duplicate"

        dup_wxy_lbl = row_get(r_obj, "is_total_duplicate_wxy", row_get(r_obj, "is_total_duplicate", "No Duplicate"))
        rej_rt_lbl = row_get(r_obj, "reject_in_retest_wxy", row_get(r_obj, "reject_in_retest", "N/A"))
        if has_bt and has_zb:
            return (
                part_num,
                row_get(r_obj, "part_id", ""),
                row_get(r_obj, "site_num", ""),
                row_get(r_obj, "test_t", ""),
                row_get(r_obj, "hard_bin", ""),
                row_get(r_obj, "soft_bin", ""),
                row_get(r_obj, "pass_fail", ""),
                row_get(r_obj, "wafer", ""),
                row_get(r_obj, "x", ""),
                row_get(r_obj, "y", ""),
                row_get(r_obj, "wxy", ""),
                row_get(r_obj, "uid", ""),
                bt_val,
                _macid_hex_cached(bt_val),
                zb_val,
                _macid_hex_cached(zb_val),
                row_get(r_obj, "panel", ""),
                dup_wxy_lbl,
                mac_flag,
                rej_rt_lbl,
            )
        vals: List[Any] = [
            part_num,
            row_get(r_obj, "part_id", ""),
            row_get(r_obj, "site_num", ""),
            row_get(r_obj, "test_t", ""),
            row_get(r_obj, "hard_bin", ""),
            row_get(r_obj, "soft_bin", ""),
            row_get(r_obj, "pass_fail", ""),
            row_get(r_obj, "wafer", ""),
            row_get(r_obj, "x", ""),
            row_get(r_obj, "y", ""),
            row_get(r_obj, "wxy", ""),
            row_get(r_obj, "uid", ""),
        ]
        if has_bt:
            vals.extend((bt_val, _macid_hex_cached(bt_val)))
        if has_zb:
            vals.extend((zb_val, _macid_hex_cached(zb_val)))
        vals.extend((
            row_get(r_obj, "panel", ""),
            dup_wxy_lbl,
            mac_flag,
            rej_rt_lbl,
        ))
        return tuple(vals)

    def _build_qc_row_values(part_num: int, r_obj: Dict[str, Any]) -> Tuple[Any, ...]:
        wxy = row_get(r_obj, "wxy", "")
        w_info = lk_wxy_get(wxy, {})
        if has_bt and has_zb:
            bt = row_get(r_obj, "bt", "")
            zb = row_get(r_obj, "zb", "")
            return (
                part_num,
                row_get(r_obj, "part_id", ""),
                row_get(r_obj, "site_num", ""),
                row_get(r_obj, "test_t", ""),
                row_get(r_obj, "hard_bin", ""),
                row_get(r_obj, "soft_bin", ""),
                row_get(r_obj, "pass_fail", ""),
                row_get(r_obj, "wafer", ""),
                row_get(r_obj, "x", ""),
                row_get(r_obj, "y", ""),
                wxy,
                row_get(r_obj, "uid", ""),
                bt,
                _macid_hex_cached(bt),
                zb,
                _macid_hex_cached(zb),
                row_get(r_obj, "panel", ""),
                w_info.get("result", "N/A"),
                w_info.get("part_num", "N/A"),
                w_info.get("datalog", "N/A"),
            )
        vals: List[Any] = [
            part_num,
            row_get(r_obj, "part_id", ""),
            row_get(r_obj, "site_num", ""),
            row_get(r_obj, "test_t", ""),
            row_get(r_obj, "hard_bin", ""),
            row_get(r_obj, "soft_bin", ""),
            row_get(r_obj, "pass_fail", ""),
            row_get(r_obj, "wafer", ""),
            row_get(r_obj, "x", ""),
            row_get(r_obj, "y", ""),
            wxy,
            row_get(r_obj, "uid", ""),
        ]
        if has_bt:
            bt = row_get(r_obj, "bt", "")
            vals.extend((bt, _macid_hex_cached(bt)))
        if has_zb:
            zb = row_get(r_obj, "zb", "")
            vals.extend((zb, _macid_hex_cached(zb)))
        vals.extend((
            row_get(r_obj, "panel", ""),
            w_info.get("result", "N/A"),
            w_info.get("part_num", "N/A"),
            w_info.get("datalog", "N/A"),
        ))
        return tuple(vals)

    if ft_all_rows:
        ws_ft = wb.add_worksheet("FT Total")
        ft_tracker = col_tracker_cls()

        col0_stats = [
            ("Duplicate Good WXY", result.get("wxy_total_duplicate_guid", result.get("total_duplicate_guid"))),
        ]
        if has_bt:
            col0_stats.append(("Duplicate BT MACID", dup_bt))
        if has_zb:
            col0_stats.append(("Duplicate ZB MACID", dup_zb))
        col0_stats.extend([
            ("Rejects not in RETEST", result.get("wxy_fp_rejects_not_in_retest", result.get("fp_rejects_not_in_retest"))),
            ("BIN3 NO RETEST", extra.get("fp_hard_bin3_rejects")),
            ("RETEST SBIN90", extra.get("retest_bin90")),
        ])

        col3_stats = [
            ("FIRST PASS Total Parts", fp_panel.get("total_parts")),
            ("FIRST PASS Good Parts", fp_panel.get("pass_count")),
            ("FIRST PASS Fail Parts", fp_panel.get("fail_count")),
            ("FIRST PASS No WXY", fp_panel.get("missing_wxy")),
            ("FIRST PASS No UID", fp_panel.get("missing_uid")),
        ]
        if has_bt:
            col3_stats.append(("FIRST PASS No BT MACID", fp_panel.get("missing_bt")))
        if has_zb:
            col3_stats.append(("FIRST PASS No ZB MACID", fp_panel.get("missing_zb")))

        col6_stats = [
            ("RETEST Total Parts", rt_panel.get("total_parts")),
            ("RETEST Good Parts", rt_panel.get("pass_count")),
            ("RETEST Fail Parts", rt_panel.get("fail_count")),
            ("RETEST No WXY", rt_panel.get("missing_wxy")),
            ("RETEST No UID", rt_panel.get("missing_uid")),
        ]
        if has_bt:
            col6_stats.append(("RETEST No BT MACID", rt_panel.get("missing_bt")))
        if has_zb:
            col6_stats.append(("RETEST No ZB MACID", rt_panel.get("missing_zb")))

        ft_stats_groups = [
            (0, 1, col0_stats),
            (3, 4, col3_stats),
            (6, 7, col6_stats),
        ]

        max_stats_len = 0
        for col_m, col_v, stats_rows in ft_stats_groups:
            if len(stats_rows) > max_stats_len:
                max_stats_len = len(stats_rows)
            ws_ft.write(0, col_m, "Metric", hdr_fmt)
            ws_ft.write(0, col_v, "Value", hdr_fmt)
            ft_tracker.track(col_m, "Metric")
            ft_tracker.track(col_v, "Value")
            for r_idx, (metric, value) in enumerate(stats_rows, start=1):
                num_val = to_num_fn(value)
                ws_ft.write(r_idx, col_m, metric)
                ws_ft.write(r_idx, col_v, num_val)
                ft_tracker.track(col_m, metric)
                ft_tracker.track(col_v, num_val)

        parts_start_row = max_stats_len + 2
        ws_ft.write_row(parts_start_row, 0, ft_headers, hdr_fmt)
        ft_tracker.track_row(0, ft_headers)

        write_ft_row = ws_ft.write_row
        for part_num, r_obj in enumerate(ft_all_rows, start=1):
            r = parts_start_row + part_num
            values = _build_ft_row_values(part_num, r_obj)
            write_ft_row(r, 0, values, center_fmt)
            if part_num <= 100:
                ft_tracker.track_row(0, values)
            written_rows += 1
            if progress_callback and (written_rows & 255) == 0:
                frac = 0.15 + 0.55 * (written_rows / total_excel_rows)
                progress_callback(frac, f"Writing FT Total rows ({part_num:,}/{len(ft_all_rows):,})…")

        ft_tracker.apply(ws_ft)

    # ══════════════════════════════════════════════════════════════════════
    # 3. QC Total worksheet
    # ══════════════════════════════════════════════════════════════════════
    if qc_rows_list:
        if progress_callback:
            progress_callback(0.70, "Writing SG406 QC Total worksheet…")
        ws_qc = wb.add_worksheet("QC Total")
        qc_tracker = col_tracker_cls()

        qc_detail_rows = [(m, v) for s, m, v in detail_rows if s == "QC"]
        if qc_detail_rows:
            ws_qc.write(0, 0, "Metric", hdr_fmt)
            ws_qc.write(0, 1, "Value", hdr_fmt)
            qc_tracker.track(0, "Metric")
            qc_tracker.track(1, "Value")
            for r_idx, (metric, value) in enumerate(qc_detail_rows, start=1):
                num_val = to_num_fn(value)
                ws_qc.write(r_idx, 0, metric)
                ws_qc.write(r_idx, 1, num_val)
                qc_tracker.track(0, metric)
                qc_tracker.track(1, num_val)
            qc_parts_start_row = len(qc_detail_rows) + 2
        else:
            qc_parts_start_row = 0

        ws_qc.write_row(qc_parts_start_row, 0, qc_headers, hdr_fmt)
        qc_tracker.track_row(0, qc_headers)

        write_qc_row = ws_qc.write_row
        for part_num, r_obj in enumerate(qc_rows_list, start=1):
            r = qc_parts_start_row + part_num
            values = _build_qc_row_values(part_num, r_obj)
            write_qc_row(r, 0, values, center_fmt)
            if part_num <= 100:
                qc_tracker.track_row(0, values)
            written_rows += 1
            if progress_callback and (written_rows & 255) == 0:
                frac = 0.15 + 0.55 * (written_rows / total_excel_rows)
                progress_callback(frac, f"Writing QC Total rows ({part_num:,}/{len(qc_rows_list):,})…")

        qc_tracker.apply(ws_qc)

    # ══════════════════════════════════════════════════════════════════════
    # 4. Duplicate GUID First Pass worksheet
    # ══════════════════════════════════════════════════════════════════════
    if progress_callback:
        progress_callback(0.75, "Writing Duplicate GUID First Pass worksheet…")
    fp_dup_rows = [
        r for r in fp_rows_list
        if r.get("is_fp_datalog_duplicate") or r.get("is_fp_datalog_duplicate_wxy") or r.get("is_fp_datalog_duplicate_any")
    ]

    ws_fp_dup = wb.add_worksheet("Duplicate GUID First Pass")
    fp_dup_tracker = col_tracker_cls()
    ws_fp_dup.write_row(0, 0, ft_headers, hdr_fmt)
    fp_dup_tracker.track_row(0, ft_headers)

    write_fp_dup_row = ws_fp_dup.write_row
    for part_num, r_obj in enumerate(fp_dup_rows, start=1):
        values = _build_ft_row_values(part_num, r_obj)
        write_fp_dup_row(part_num, 0, values, center_fmt)
        if part_num <= 100:
            fp_dup_tracker.track_row(0, values)
    fp_dup_tracker.apply(ws_fp_dup)

    # ══════════════════════════════════════════════════════════════════════
    # 5. QC units FAIL in prev FT Step worksheet
    # ══════════════════════════════════════════════════════════════════════
    qc_fail_prev_rows = [
        r for r in qc_rows_list
        if r.get("is_qc_fail_prev_step") or r.get("is_qc_fail_prev_step_wxy")
    ]

    ws_qc_fail = wb.add_worksheet("QC units FAIL in prev FT Step")
    qc_fail_tracker = col_tracker_cls()
    ws_qc_fail.write_row(0, 0, qc_headers, hdr_fmt)
    qc_fail_tracker.track_row(0, qc_headers)

    write_qc_fail_row = ws_qc_fail.write_row
    for part_num, r_obj in enumerate(qc_fail_prev_rows, start=1):
        values = _build_qc_row_values(part_num, r_obj)
        write_qc_fail_row(part_num, 0, values, center_fmt)
        if part_num <= 100:
            qc_fail_tracker.track_row(0, values)
    qc_fail_tracker.apply(ws_qc_fail)

    # ══════════════════════════════════════════════════════════════════════
    # 6. Duplicate MACID Details worksheet
    # ══════════════════════════════════════════════════════════════════════
    dup_mac_hex_fmt = wb.add_format({
        'bg_color': '#FFC7CE',
        'font_color': '#9C0006',
        'align': 'center',
        'valign': 'vcenter',
        'border': 1,
    })

    dup_mac_pairs = result.get("duplicate_macid_pairs")
    if not dup_mac_pairs:
        dup_mac_pairs = compute_duplicate_macid_pairs(
            itertools.chain(ft_all_rows, qc_rows_list), dup_bt_map, dup_zb_map, cfg=sg406_cfg
        )

    ws_dup_mac = wb.add_worksheet("Duplicate MACID Details")
    mac_tracker = col_tracker_cls()
    if has_bt and has_zb:
        mac_hdrs = [
            "BT MACID HEX",
            "ZB MACID HEX",
            "Number of UIDs per BT & ZB MACID pair",
            "Assigned UIDS per BT & ZB MACID pair",
        ]
    elif has_bt:
        mac_hdrs = [
            "BT MACID HEX",
            "Number of UIDs per BT MACID",
            "Assigned UIDS per BT MACID",
        ]
    else:
        mac_hdrs = [
            "ZB MACID HEX",
            "Number of UIDs per ZB MACID",
            "Assigned UIDS per ZB MACID",
        ]
    ws_dup_mac.write_row(0, 0, mac_hdrs, hdr_fmt)
    mac_tracker.track_row(0, mac_hdrs)

    for m_row, pair_info in enumerate(dup_mac_pairs, start=1):
        bt_hex_v = str(pair_info.get("bt_hex") or "N/A")
        zb_hex_v = str(pair_info.get("zb_hex") or "N/A")
        num_uids_v = int(pair_info.get("num_assigned_uids") or 0)
        uids_str_v = str(pair_info.get("assigned_uids_str") or "")
        is_dup_bt_cell = bool(pair_info.get("is_dup_bt"))
        is_dup_zb_cell = bool(pair_info.get("is_dup_zb"))

        if has_bt and has_zb:
            ws_dup_mac.write(m_row, 0, bt_hex_v, dup_mac_hex_fmt if is_dup_bt_cell else center_border_fmt)
            ws_dup_mac.write(m_row, 1, zb_hex_v, dup_mac_hex_fmt if is_dup_zb_cell else center_border_fmt)
            ws_dup_mac.write(m_row, 2, num_uids_v, center_border_fmt)
            ws_dup_mac.write(m_row, 3, uids_str_v, center_border_fmt)
            mac_tracker.track_row(0, (bt_hex_v, zb_hex_v, num_uids_v, uids_str_v))
        elif has_bt:
            ws_dup_mac.write(m_row, 0, bt_hex_v, dup_mac_hex_fmt if is_dup_bt_cell else center_border_fmt)
            ws_dup_mac.write(m_row, 1, num_uids_v, center_border_fmt)
            ws_dup_mac.write(m_row, 2, uids_str_v, center_border_fmt)
            mac_tracker.track_row(0, (bt_hex_v, num_uids_v, uids_str_v))
        else:
            ws_dup_mac.write(m_row, 0, zb_hex_v, dup_mac_hex_fmt if is_dup_zb_cell else center_border_fmt)
            ws_dup_mac.write(m_row, 1, num_uids_v, center_border_fmt)
            ws_dup_mac.write(m_row, 2, uids_str_v, center_border_fmt)
            mac_tracker.track_row(0, (zb_hex_v, num_uids_v, uids_str_v))

    mac_tracker.apply(ws_dup_mac, max_width=85)

    # ══════════════════════════════════════════════════════════════════════
    # 7. Valid Good Count Cross-Check worksheet
    # ══════════════════════════════════════════════════════════════════════
    if progress_callback:
        progress_callback(0.82, "Writing Valid Good Count Cross-Check worksheet…")
    ws_gu = wb.add_worksheet("Valid Good Count Cross-Check")
    gu_tracker = col_tracker_cls()

    fp_gu_counts = result.get("fp_good_unique_counts")
    tot_gu_counts = result.get("total_good_unique_counts")
    fp_gu_pairs = result.get("fp_good_unique_pair_details")
    tot_gu_pairs = result.get("total_good_unique_pair_details")

    fp_good_rows_list = [r for r in fp_rows_list if r.get("pass_fail") == "PASS"]
    rt_good_rows_list = [r for r in rt_rows_list if r.get("pass_fail") == "PASS"]
    total_good_rows_list = fp_good_rows_list + rt_good_rows_list

    if not fp_gu_counts or fp_gu_pairs is None:
        gu_fp_calc = compute_good_unique_discrepancy(fp_good_rows_list, cfg=sg406_cfg) if fp_good_rows_list else None
        fp_gu_counts = fp_gu_counts or (gu_fp_calc["counts"] if gu_fp_calc else {})
        if fp_gu_pairs is None:
            fp_gu_pairs = gu_fp_calc["pair_details"] if gu_fp_calc else []
    if not tot_gu_counts or tot_gu_pairs is None:
        gu_tot_calc = compute_good_unique_discrepancy(total_good_rows_list, cfg=sg406_cfg) if total_good_rows_list else None
        tot_gu_counts = tot_gu_counts or (gu_tot_calc["counts"] if gu_tot_calc else {})
        if tot_gu_pairs is None:
            tot_gu_pairs = gu_tot_calc["pair_details"] if gu_tot_calc else []

    def _cnt_val(counts_map: Dict[str, Any], k_label: str, k_short: str, fallback: Any) -> int:
        for k in (k_label, k_short, k_short.lower(), k_label.lower()):
            if k in counts_map and counts_map[k] is not None:
                try:
                    return int(counts_map[k])
                except (ValueError, TypeError):
                    pass
        try:
            return int(fallback or 0)
        except (ValueError, TypeError):
            return 0

    fp_c_wxy = _cnt_val(fp_gu_counts, "WXY", "wxy", fp_good_wo_dup)
    fp_c_uid = _cnt_val(fp_gu_counts, "UID", "uid", fp_good_wo_dup)
    fp_c_bt = _cnt_val(fp_gu_counts, "BT MACID", "bt", fp_good_wo_dup)
    fp_c_zb = _cnt_val(fp_gu_counts, "ZB MACID", "zb", fp_good_wo_dup)

    tot_c_wxy = _cnt_val(tot_gu_counts, "WXY", "wxy", tot_good_wo_dup)
    tot_c_uid = _cnt_val(tot_gu_counts, "UID", "uid", tot_good_wo_dup)
    tot_c_bt = _cnt_val(tot_gu_counts, "BT MACID", "bt", tot_good_wo_dup)
    tot_c_zb = _cnt_val(tot_gu_counts, "ZB MACID", "zb", tot_good_wo_dup)

    fp_disc_pairs_str = ", ".join(
        p["pair"] for p in fp_gu_pairs if int(p.get("discrepancy") or 0) > 0
    ) or ("None" if int(fp_good_uniq_disc) == 0 else ", ".join(result.get("good_unique_discrepancy_pairs") or []))
    tot_disc_pairs_str = ", ".join(
        p["pair"] for p in tot_gu_pairs if int(p.get("discrepancy") or 0) > 0
    ) or ("None" if int(tot_good_uniq_disc) == 0 else ", ".join(result.get("good_unique_discrepancy_pairs") or []))

    # Section 1: Summary Comparison Table (Cols 0..2)
    gu_sum_hdrs = ["Parameter / Check Metric", "FIRST PASS", "TOTAL (FP + RETEST)"]
    ws_gu.write_row(0, 0, gu_sum_hdrs, hdr_fmt)
    gu_tracker.track_row(0, gu_sum_hdrs)

    gu_sum_rows = [
        ("STDF Good Parts (PASS)", to_num_fn(fp_stdf), to_num_fn(tot_stdf), "normal"),
        ("Unique Valid Good WXY", fp_c_wxy, tot_c_wxy, "normal"),
        ("Unique Valid Good UID", fp_c_uid, tot_c_uid, "normal"),
    ]
    if has_bt:
        gu_sum_rows.append(("Unique Valid Good BT MACID", fp_c_bt, tot_c_bt, "normal"))
    if has_zb:
        gu_sum_rows.append(("Unique Valid Good ZB MACID", fp_c_zb, tot_c_zb, "normal"))
    gu_sum_rows.extend([
        (uniq_good_lbl, fp_gu_str, tot_gu_str, "status"),
        ("Discrepancy Count", int(fp_good_uniq_disc), int(tot_good_uniq_disc), "disc"),
        ("Discrepant Parameter Pair(s)", fp_disc_pairs_str, tot_disc_pairs_str, "normal"),
        ("Cross-Check Status", "FAIL" if int(fp_good_uniq_disc) > 0 else "PASS", "FAIL" if int(tot_good_uniq_disc) > 0 else "PASS", "status"),
    ])
    for r_i, (m_lbl, fp_v, tot_v, r_kind) in enumerate(gu_sum_rows, start=1):
        ws_gu.write(r_i, 0, m_lbl, border_fmt)
        if r_kind in ("disc", "status"):
            fp_fmt = red_center_fmt if int(fp_good_uniq_disc) > 0 else green_center_fmt
            tot_fmt = red_center_fmt if int(tot_good_uniq_disc) > 0 else green_center_fmt
        else:
            fp_fmt = center_border_fmt
            tot_fmt = center_border_fmt
        ws_gu.write(r_i, 1, fp_v, fp_fmt)
        ws_gu.write(r_i, 2, tot_v, tot_fmt)
        gu_tracker.track_row(0, (m_lbl, fp_v, tot_v))

    # Section 2: Pairwise Cross-Check Breakdown Table (Cols 4..10)
    pair_hdrs = [
        "Scope", "Parameter Pair", "Count Diff",
        "Good Rows w/ Missing/Invalid ID", "Multi-Mapped IDs",
        "Pair Discrepancy", "Status",
    ]
    ws_gu.write_row(0, 4, pair_hdrs, hdr_fmt)
    gu_tracker.track_row(4, pair_hdrs)
    p_row_idx = 1
    for scope_lbl, p_list in (("FIRST PASS", fp_gu_pairs), ("TOTAL (FP + RETEST)", tot_gu_pairs)):
        for p_item in p_list:
            p_disc = int(p_item.get("discrepancy") or 0)
            p_st = "FAIL" if p_disc > 0 else "PASS"
            p_st_fmt = red_center_fmt if p_disc > 0 else green_center_fmt
            p_vals = (
                scope_lbl,
                str(p_item.get("pair") or ""),
                int(p_item.get("count_diff") or 0),
                int(p_item.get("one_side_invalid_rows") or 0),
                int(p_item.get("multi_mapped_values") or 0),
                p_disc,
            )
            ws_gu.write_row(p_row_idx, 4, p_vals, center_border_fmt)
            ws_gu.write(p_row_idx, 10, p_st, p_st_fmt)
            gu_tracker.track_row(4, p_vals + (p_st,))
            p_row_idx += 1

    # Section 3: MACID Prefix Distribution & Outlier Profile (Layer 2)
    amber_center_fmt = wb.add_format({
        'border': 1, 'bg_color': '#FFF3CD', 'font_color': '#856404', 'bold': True,
        'align': 'center', 'valign': 'vcenter',
    })
    amber_row_fmt = wb.add_format({
        'border': 1, 'bg_color': '#FFF3CD', 'font_color': '#856404',
        'align': 'center', 'valign': 'vcenter',
    })
    rollover_center_fmt = wb.add_format({
        'border': 1, 'bg_color': '#E8F1FD', 'font_color': '#1A4E8A',
        'align': 'center', 'valign': 'vcenter',
    })

    prefix_profile = result.get("macid_prefix_profile")
    if not isinstance(prefix_profile, dict) and total_good_rows_list:
        try:
            from core import config as app_config
            _pcfg = app_config.get_macid_prefix_profile_config()
            prefix_profile = compute_macid_prefix_profile(
                total_good_rows_list,
                cfg=sg406_cfg,
                prefix_bytes=_pcfg["prefix_bytes"],
                min_pass_units=_pcfg["min_pass_units"],
                max_outlier_count=_pcfg["max_count"],
                max_outlier_pct=_pcfg["max_pct"],
                mode=_pcfg["mode"],
            )
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            logging.warning("Skipping Layer 2 MACID Prefix table in Excel: %s", exc)
            prefix_profile = None

    prefix_start = max(len(gu_sum_rows) + 2, p_row_idx + 2)
    prefix_rows_written = 0
    pp_mode = "fail"
    pp_prefix_bytes = 3
    outlier_macids_by_type: Dict[str, Set[str]] = {}

    if isinstance(prefix_profile, dict):
        pp_mode = str(prefix_profile.get("mode") or result.get("macid_prefix_outlier_mode") or "fail").strip().lower()
        pp_prefix_bytes = int(prefix_profile.get("prefix_bytes") or 3)
        outlier_macids_by_type = prefix_profile.get("outlier_macids_by_type") or {}
        prefix_hdrs = [
            "MACID Type", "Prefix", "Unique PASS Units", "Share (%)",
            "Classification", "Position / Boundary Hint", "Outlier UID(s) & MACID HEX(s)",
        ]
        ws_gu.write_row(prefix_start, 0, prefix_hdrs, hdr_fmt)
        gu_tracker.track_row(0, prefix_hdrs)
        prefix_rows_written = 1

        by_type_map = prefix_profile.get("by_type") or {}
        for mtype in MACID_TYPES:
            tdata = by_type_map.get(mtype)
            if not isinstance(tdata, dict):
                continue
            m_label = str(tdata.get("label") or CHECK_LABELS.get(mtype, mtype.upper()))
            for entry in tdata.get("entries") or ():
                cls_str = str(entry.get("classification") or "")
                hint_str = str(entry.get("boundary_hint") or "") or "-"
                if cls_str == "OUTLIER":
                    samples = entry.get("samples") or ()
                    ol_samp_txt = ", ".join(
                        f"{s.get('uid') or 'N/A'} ({s.get('macid_hex') or ''})"
                        for s in samples[:10]
                    )
                    if len(samples) > 10:
                        ol_samp_txt += f", ...+{len(samples) - 10} more"
                    cls_fmt = red_center_fmt if pp_mode == "fail" else amber_center_fmt
                elif cls_str == "Rollover Pool":
                    ol_samp_txt = "-"
                    cls_fmt = rollover_center_fmt
                else:
                    ol_samp_txt = "-"
                    cls_fmt = center_border_fmt

                pct_str = f"{float(entry.get('pct') or 0.0):.2f}%"
                p_row_vals = (
                    m_label,
                    str(entry.get("prefix") or ""),
                    int(entry.get("count") or 0),
                    pct_str,
                    cls_str,
                    hint_str,
                    ol_samp_txt,
                )
                curr_pr = prefix_start + prefix_rows_written
                ws_gu.write(curr_pr, 0, p_row_vals[0], center_border_fmt)
                ws_gu.write(curr_pr, 1, p_row_vals[1], center_border_fmt)
                ws_gu.write(curr_pr, 2, p_row_vals[2], center_border_fmt)
                ws_gu.write(curr_pr, 3, p_row_vals[3], center_border_fmt)
                ws_gu.write(curr_pr, 4, p_row_vals[4], cls_fmt)
                ws_gu.write(curr_pr, 5, p_row_vals[5], center_border_fmt)
                ws_gu.write(curr_pr, 6, p_row_vals[6], cls_fmt if cls_str == "OUTLIER" else center_border_fmt)
                gu_tracker.track_row(0, p_row_vals)
                prefix_rows_written += 1

        if prefix_rows_written == 1:
            empty_pr = ("N/A", "None", 0, "0.00%", "No valid PASS MACIDs", "-", "-")
            ws_gu.write_row(prefix_start + 1, 0, empty_pr, center_border_fmt)
            gu_tracker.track_row(0, empty_pr)
            prefix_rows_written += 1

    # Section 4: Row-by-Row Good Units (PASS) Tracking & Cross-Check Table
    good_table_start = prefix_start + (prefix_rows_written + 2 if prefix_rows_written > 0 else 0)
    gu_unit_hdrs = [
        "Good Part #", "Part ID", "Site", "Datalog",
        "WXY (W_X_Y)", "UID",
    ]
    if has_bt:
        gu_unit_hdrs.extend(["BT MACID", "BT MACID HEX"])
    if has_zb:
        gu_unit_hdrs.extend(["ZB MACID", "ZB MACID HEX"])
    gu_unit_hdrs.extend(["Valid WXY", "Valid UID"])
    if has_bt:
        gu_unit_hdrs.append("Valid BT MACID")
    if has_zb:
        gu_unit_hdrs.append("Valid ZB MACID")
    gu_unit_hdrs.extend(["Unique Good WXY", "Unique Good UID"])
    if has_bt:
        gu_unit_hdrs.append("Unique Good BT MACID")
    if has_zb:
        gu_unit_hdrs.append("Unique Good ZB MACID")
    gu_unit_hdrs.extend(["Cross-Check Status", "Cross-Check Remarks"])
    ws_gu.write_row(good_table_start, 0, gu_unit_hdrs, hdr_fmt)
    gu_tracker.track_row(0, gu_unit_hdrs)

    # Pre-scan bidirectional mappings across all GOOD rows ONCE and cache identifier validity per row
    active_keys_labels = active_good_unique_params(sg406_cfg)
    bt_rules = _prepare_macid_rules("bt", sg406_cfg) if has_bt else None
    zb_rules = _prepare_macid_rules("zb", sg406_cfg) if has_zb else None
    good_id_maps: Dict[Tuple[str, str], Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
    precomputed_good_rows: List[Tuple[Dict[str, Any], Dict[str, str], Dict[str, bool]]] = []

    def _is_valid_active_id(val: str, key: str) -> bool:
        if not val:
            return False
        if key == "bt" and bt_rules is not None:
            return _is_valid_macid_compiled(val, bt_rules[0], bt_rules[1])
        if key == "zb" and zb_rules is not None:
            return _is_valid_macid_compiled(val, zb_rules[0], zb_rules[1])
        return _is_valid_identifier(val, key, sg406_cfg)

    for g_obj in total_good_rows_list:
        g_vals = {k: str(g_obj.get(k) or "").strip() for k, _ in active_keys_labels}
        g_valid = {k: _is_valid_active_id(g_vals[k], k) for k, _ in active_keys_labels}
        precomputed_good_rows.append((g_obj, g_vals, g_valid))
        for k1, _ in active_keys_labels:
            if not g_valid[k1]:
                continue
            v1 = g_vals[k1]
            for k2, _ in active_keys_labels:
                if k1 != k2 and g_valid[k2]:
                    good_id_maps[(k1, k2)][v1].add(g_vals[k2])

    # Identify only those (k1, k2, v1) or (k2, k1, v2) entries that map to > 1 counterpart
    has_any_multi_map = any(
        len(mapped_set) > 1
        for pair_map in good_id_maps.values()
        for mapped_set in pair_map.values()
    )

    bt_outlier_set = outlier_macids_by_type.get("bt") or set()
    zb_outlier_set = outlier_macids_by_type.get("zb") or set()

    seen_good_ids: Dict[str, Set[str]] = {k: set() for k, _ in active_keys_labels}
    write_gu_row = ws_gu.write_row
    for g_idx, (g_obj, val_map, ok_map) in enumerate(precomputed_good_rows, start=1):
        curr_gu_row = good_table_start + g_idx
        wxy_v = val_map.get("wxy", "")
        uid_v = val_map.get("uid", "")
        bt_v = val_map.get("bt", "") if has_bt else ""
        zb_v = val_map.get("zb", "") if has_zb else ""
        bt_hex_v = _macid_hex_cached(bt_v) if has_bt else ""
        zb_hex_v = _macid_hex_cached(zb_v) if has_zb else ""

        uniq_state: Dict[str, str] = {}
        row_issues: List[str] = []

        for k, lbl in active_keys_labels:
            if not ok_map[k]:
                uniq_state[k] = "INVALID"
                row_issues.append(f"Invalid {lbl}")
            elif val_map[k] not in seen_good_ids[k]:
                seen_good_ids[k].add(val_map[k])
                uniq_state[k] = "Unique (1st Good)"
            else:
                uniq_state[k] = "Duplicate Good"

        valid_states = {uniq_state[k] for k, _ in active_keys_labels if ok_map[k]}
        if "Unique (1st Good)" in valid_states and "Duplicate Good" in valid_states:
            uniq_lbls = [lbl for k, lbl in active_keys_labels if uniq_state[k] == "Unique (1st Good)"]
            dup_lbls = [lbl for k, lbl in active_keys_labels if uniq_state[k] == "Duplicate Good"]
            row_issues.append(
                f"Unique/Duplicate Mismatch (Unique: {', '.join(uniq_lbls)} vs Duplicate: {', '.join(dup_lbls)})"
            )

        if has_any_multi_map:
            for k1, lbl1 in active_keys_labels:
                if not ok_map[k1]:
                    continue
                for k2, lbl2 in active_keys_labels:
                    if k1 < k2 and ok_map[k2]:
                        mapped_set = good_id_maps[(k1, k2)].get(val_map[k1], set())
                        if len(mapped_set) > 1:
                            row_issues.append(f"{lbl1} mapped to {len(mapped_set)} {lbl2}s")
                        rev_set = good_id_maps[(k2, k1)].get(val_map[k2], set())
                        if len(rev_set) > 1:
                            row_issues.append(f"{lbl2} mapped to {len(rev_set)} {lbl1}s")

        has_other_issue = bool(row_issues)
        row_has_prefix_outlier = False
        if has_bt and bt_hex_v and bt_hex_v in bt_outlier_set:
            row_has_prefix_outlier = True
            row_issues.append(f"BT MACID Prefix Outlier ({_extract_macid_prefix(bt_hex_v, pp_prefix_bytes)})")
        if has_zb and zb_hex_v and zb_hex_v in zb_outlier_set:
            row_has_prefix_outlier = True
            row_issues.append(f"ZB MACID Prefix Outlier ({_extract_macid_prefix(zb_hex_v, pp_prefix_bytes)})")

        has_issue = bool(row_issues)
        if has_other_issue or (row_has_prefix_outlier and pp_mode == "fail"):
            row_status = "DISCREPANCY"
            row_cell_fmt = dup_mac_hex_fmt
        elif row_has_prefix_outlier and pp_mode == "warn":
            row_status = "WARNING"
            row_cell_fmt = amber_row_fmt
        else:
            row_status = "OK"
            row_cell_fmt = center_fmt
        row_remarks = "; ".join(row_issues) if has_issue else "OK"

        gu_vals_list: List[Any] = [
            g_idx,
            g_obj.get("part_id", ""),
            g_obj.get("site_num", ""),
            g_obj.get("panel", ""),
            wxy_v,
            uid_v,
        ]
        if has_bt:
            gu_vals_list.extend((bt_v, bt_hex_v))
        if has_zb:
            gu_vals_list.extend((zb_v, zb_hex_v))
        gu_vals_list.extend((
            "VALID" if ok_map["wxy"] else "INVALID",
            "VALID" if ok_map["uid"] else "INVALID",
        ))
        if has_bt:
            gu_vals_list.append("VALID" if ok_map["bt"] else "INVALID")
        if has_zb:
            gu_vals_list.append("VALID" if ok_map["zb"] else "INVALID")
        gu_vals_list.extend((uniq_state["wxy"], uniq_state["uid"]))
        if has_bt:
            gu_vals_list.append(uniq_state["bt"])
        if has_zb:
            gu_vals_list.append(uniq_state["zb"])
        gu_vals_list.extend((row_status, row_remarks))

        gu_row_values = tuple(gu_vals_list)
        write_gu_row(curr_gu_row, 0, gu_row_values, row_cell_fmt)
        if g_idx <= 100 or has_issue:
            gu_tracker.track_row(0, gu_row_values)

    gu_tracker.apply(ws_gu, max_width=85)

    if progress_callback:
        progress_callback(0.85, "Finalizing SG406 Excel workbook…")
    wb.close()
    if progress_callback:
        progress_callback(1.0, "Excel workbook generated")
