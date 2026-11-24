"""
Excel Output Module for STDF Converter
======================================
Handles combining multiple parsed STDF datasets and writing them to Excel.
"""

import os
import tempfile
import threading
import zipfile
from collections import OrderedDict, defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import xlsxwriter
import xlsxwriter.workbook
import xlsxwriter.xmlwriter

# ─── MONKEY-PATCHES FOR EXCEL WRITE OPTIMIZATION ───
# Overriding ZipFile to use compression level 1 (fastest) instead of default 6.
class _FastZipFile(zipfile.ZipFile):
    def __init__(self, *args, **kwargs):
        if "compresslevel" not in kwargs:
            kwargs["compresslevel"] = 1
        super().__init__(*args, **kwargs)


xlsxwriter.workbook.ZipFile = _FastZipFile

# Overriding XML writer setup to use a larger write buffer (1 MB) for temp XML files.
_orig_set_xml_writer = xlsxwriter.xmlwriter.XMLwriter._set_xml_writer


def _fast_set_xml_writer(self, filename) -> None:
    if isinstance(filename, str):
        self.internal_fh = True
        self.fh = open(filename, "w", encoding="utf-8", buffering=1024 * 1024)
    else:
        _orig_set_xml_writer(self, filename)


xlsxwriter.xmlwriter.XMLwriter._set_xml_writer = _fast_set_xml_writer


def add_manual_merge(ws, r1, c1, r2, c2) -> None:
    """Manually add a merged range to Worksheet's internal structures to support
    merges in constant_memory mode without sequential cell write conflicts.
    """
    from xlsxwriter.utility import xl_range

    cell_range = xl_range(r1, c1, r2, c2)
    ws.merge.append([r1, c1, r2, c2])
    for row in range(r1, r2 + 1):
        for col in range(c1, c2 + 1):
            ws.merged_cells[(row, col)] = cell_range


# ───────────────────────────────────────────────────


def _get_col_name(col_idx: int) -> str:
    """Convert a zero-based column index to Excel column letters (A, B, AA, etc.)."""
    name = ""
    while col_idx >= 0:
        name = chr(col_idx % 26 + 65) + name
        col_idx = col_idx // 26 - 1
    return name


# Import necessary parsing utilities and constants from stdf_parser
try:
    from apps.stdf2sum.stdf_parser import (
        BASE_METADATA_FIELD_MAP,
        LogFunc,
        ProgressFunc,
        _emit_log,
        _summarize_sdr_records,
        build_default_output_prefix,
        parse_stdf_file,
    )
except ImportError:
    from stdf_parser import (
        BASE_METADATA_FIELD_MAP,
        LogFunc,
        ProgressFunc,
        _emit_log,
        _summarize_sdr_records,
        build_default_output_prefix,
        parse_stdf_file,
    )

_PARALLEL_PROCESS_MIN_BYTES = 8 * 1024 * 1024
_PARSE_CACHE_MAX_ENTRIES = 16
_PARSE_CACHE: "OrderedDict[Tuple[Any, ...], Tuple[Dict[str, Any], Dict[str, int], int]]" = OrderedDict()
_PARSE_CACHE_LOCK = threading.Lock()


def clear_parse_cache() -> None:
    """Clear the bounded in-memory STDF parse cache."""
    with _PARSE_CACHE_LOCK:
        _PARSE_CACHE.clear()


def _build_parse_cache_key(
    input_path: str,
    filter_tests: Optional[Set[int]],
) -> Optional[Tuple[Any, ...]]:
    try:
        st = os.stat(input_path)
    except OSError:
        return None
    mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
    filter_key = frozenset(filter_tests) if filter_tests is not None else None
    return (os.path.abspath(input_path), int(st.st_size), int(mtime_ns), filter_key)


def _clone_cached_parse(
    entry: Tuple[Dict[str, Any], Dict[str, int], int],
) -> Tuple[Dict[str, Any], Dict[str, int], int]:
    """Return a caller-safe copy of a cached parse result.

    PARTS/HBR/SBR rows are shallow-copied because callers mutate ``SOURCE_FILE``
    on them, while immutable RESULTS and TEST_META dicts are shared read-only.
    """
    parsed, counts, skipped_ptr = entry
    cloned = {
        "PARTS": [dict(p) for p in parsed["PARTS"]],
        "RESULTS": parsed["RESULTS"],
        "TEST_META": parsed["TEST_META"],
        "MIR": dict(parsed.get("MIR") or {}),
        "MRR": dict(parsed.get("MRR") or {}),
        "SDR": [dict(r) for r in (parsed.get("SDR") or [])],
        "HBR": [dict(r) for r in (parsed.get("HBR") or [])],
        "SBR": [dict(r) for r in (parsed.get("SBR") or [])],
    }
    return cloned, dict(counts), skipped_ptr


def _parse_stdf_cached(
    input_path: str,
    filter_tests: Optional[Set[int]] = None,
    verbose: bool = True,
    logger: LogFunc = None,
    progress_callback: Any = None,
) -> Tuple[Dict[str, Any], Dict[str, int], int]:
    """Parse an STDF file or return a cached parse when path+size+mtime+filter match."""
    cache_key = _build_parse_cache_key(input_path, filter_tests)
    if cache_key is not None:
        with _PARSE_CACHE_LOCK:
            cached = _PARSE_CACHE.get(cache_key)
            if cached is not None:
                _PARSE_CACHE.move_to_end(cache_key)
                if logger:
                    logger(f"Using cached parse for: {input_path}")
                if progress_callback:
                    try:
                        sz = cache_key[1]
                        progress_callback(sz, sz)
                    except Exception:
                        pass
                return _clone_cached_parse(cached)

    parsed, counts, skipped_ptr = parse_stdf_file(
        input_path=input_path,
        filter_tests=filter_tests,
        verbose=verbose,
        logger=logger,
        progress_callback=progress_callback,
    )
    if cache_key is not None:
        snapshot = _clone_cached_parse((parsed, dict(counts), skipped_ptr))
        with _PARSE_CACHE_LOCK:
            _PARSE_CACHE[cache_key] = snapshot
            _PARSE_CACHE.move_to_end(cache_key)
            while len(_PARSE_CACHE) > _PARSE_CACHE_MAX_ENTRIES:
                _PARSE_CACHE.popitem(last=False)
    return parsed, counts, skipped_ptr


def _parse_one_file(args: Tuple[str, Optional[Set[int]]]) -> Dict:
    """Parse a single STDF file into a self-contained, picklable result dict."""
    input_path, filter_tests = args
    parsed, counts, skipped_ptr = parse_stdf_file(
        input_path=input_path,
        filter_tests=filter_tests,
        verbose=False,
        logger=None,
        progress_callback=None,
    )
    source_name = os.path.basename(input_path)
    for part_row in parsed["PARTS"]:
        part_row["SOURCE_FILE"] = source_name
    for hbr_row in parsed.get("HBR", []):
        hbr_row["SOURCE_FILE"] = source_name
    for sbr_row in parsed.get("SBR", []):
        sbr_row["SOURCE_FILE"] = source_name
    return {
        "source_name": source_name,
        "parts": parsed["PARTS"],
        "results": parsed["RESULTS"],
        "test_meta": parsed["TEST_META"],
        "mir": parsed.get("MIR") or {},
        "mrr": parsed.get("MRR") or {},
        "sdr": parsed.get("SDR") or [],
        "hbr": parsed.get("HBR") or [],
        "sbr": parsed.get("SBR") or [],
        "counts": dict(counts),
        "skipped_ptr": skipped_ptr,
    }


def _compute_summary_aggregations(
    parts: List[Dict],
    metadata_blocks: List[Dict[str, object]],
    hbr_records: Optional[List[Dict]],
    sbr_records: Optional[List[Dict]],
) -> Dict[str, Any]:
    """Compute all Summary, Bin Mapping, and synthesized HBR/SBR data in ONE pass over parts."""
    existing_hbr = {
        (r.get("SOURCE_FILE", ""), r.get("HEAD_NUM", 0), r.get("SITE_NUM", 0), r.get("HBIN_NUM", 0))
        for r in (hbr_records or ())
    }
    existing_sbr = {
        (r.get("SOURCE_FILE", ""), r.get("HEAD_NUM", 0), r.get("SITE_NUM", 0), r.get("SBIN_NUM", 0))
        for r in (sbr_records or ())
    }
    hbr_to_add: Dict[Tuple, Dict] = {}
    sbr_to_add: Dict[Tuple, Dict] = {}

    total_parts = len(parts)
    passed_parts = 0
    hbin_counts: Dict[int, int] = defaultdict(int)
    sbin_counts: Dict[int, int] = defaultdict(int)
    sbin_site_counts: Dict[int, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
    part_sites: Set[int] = set()
    sbin_to_hbin: Dict[int, int] = {}
    bin_map: Dict[Tuple[int, int], int] = {}

    for part in parts:
        part_get = part.get
        sf = part_get("SOURCE_FILE", "")
        head = part_get("HEAD_NUM", 0)
        site = part_get("SITE_NUM", 0)
        is_fail = bool(part_get("PART_FLG", 0) & 0x08)
        if not is_fail:
            passed_parts += 1
        pf_char = "F" if is_fail else "P"

        if site is not None:
            part_sites.add(site)

        hbin = part_get("HARD_BIN")
        if hbin is not None:
            hbin_counts[hbin] += 1
            hkey = (sf, head, site, hbin)
            if hkey not in existing_hbr:
                entry = hbr_to_add.get(hkey)
                if entry is None:
                    hbr_to_add[hkey] = {
                        "SOURCE_FILE": sf,
                        "HEAD_NUM": head,
                        "SITE_NUM": site,
                        "HBIN_NUM": hbin,
                        "HBIN_CNT": 1,
                        "HBIN_PF": pf_char,
                        "HBIN_NAM": "",
                    }
                else:
                    entry["HBIN_CNT"] += 1

        sbin = part_get("SOFT_BIN")
        if sbin is not None:
            sbin_counts[sbin] += 1
            if site is not None:
                sbin_site_counts[sbin][site] += 1
            skey = (sf, head, site, sbin)
            if skey not in existing_sbr:
                entry = sbr_to_add.get(skey)
                if entry is None:
                    sbr_to_add[skey] = {
                        "SOURCE_FILE": sf,
                        "HEAD_NUM": head,
                        "SITE_NUM": site,
                        "SBIN_NUM": sbin,
                        "SBIN_CNT": 1,
                        "SBIN_PF": pf_char,
                        "SBIN_NAM": "",
                    }
                else:
                    entry["SBIN_CNT"] += 1

            if hbin is not None:
                sbin_to_hbin[sbin] = hbin
                bm_key = (sbin, hbin)
                bin_map[bm_key] = bin_map.get(bm_key, 0) + 1

    merged_hbr = list(hbr_records or ())
    if hbr_to_add:
        merged_hbr.extend(hbr_to_add.values())

    merged_sbr = list(sbr_records or ())
    if sbr_to_add:
        merged_sbr.extend(sbr_to_add.values())

    failed_parts = total_parts - passed_parts
    sorted_hbins = sorted(hbin_counts.keys(), key=lambda h: hbin_counts[h], reverse=True)
    sorted_sbins = sorted(sbin_counts.keys(), key=lambda s: sbin_counts[s], reverse=True)
    top_10_sbins = sorted_sbins[:10]
    top_20_sbins = sorted_sbins[:20]

    sdr_sites: Set[int] = set()
    for mb in metadata_blocks:
        for sdr in mb.get("SDR", []):
            sdr_sites.update(sdr.get("SITE_NUM_LIST", []))
    if not sdr_sites:
        sdr_sites = part_sites
    sorted_sites = sorted(sdr_sites) if sdr_sites else [1]
    min_site = sorted_sites[0]
    max_site = sorted_sites[-1]
    site_list = list(range(min_site, max_site + 1))
    num_sites = len(site_list)
    site_blocks = [site_list[i : i + 8] for i in range(0, num_sites, 8)]

    sbin_info: Dict[int, Dict] = {}
    for sbr in merged_sbr:
        snum = sbr.get("SBIN_NUM")
        if snum is not None:
            existing = sbin_info.get(snum)
            if existing is None or (existing.get("SBIN_CNT", 0) == 0 and sbr.get("SBIN_CNT", 0) > 0):
                sbin_info[snum] = {
                    "SBIN_NAM": sbr.get("SBIN_NAM", ""),
                    "SBIN_PF": sbr.get("SBIN_PF", ""),
                    "SBIN_CNT": sbr.get("SBIN_CNT", 0),
                }

    hbin_info: Dict[int, Dict] = {}
    for hbr in merged_hbr:
        hnum = hbr.get("HBIN_NUM")
        if hnum is not None and hnum not in hbin_info:
            hbin_info[hnum] = {
                "HBIN_NAM": hbr.get("HBIN_NAM", ""),
                "HBIN_PF": hbr.get("HBIN_PF", ""),
            }

    return {
        "total_parts": total_parts,
        "passed_parts": passed_parts,
        "failed_parts": failed_parts,
        "hbin_counts": hbin_counts,
        "sorted_hbins": sorted_hbins,
        "sbin_counts": sbin_counts,
        "sbin_site_counts": sbin_site_counts,
        "sorted_sbins": sorted_sbins,
        "top_10_sbins": top_10_sbins,
        "top_20_sbins": top_20_sbins,
        "sbin_to_hbin": sbin_to_hbin,
        "bin_map": bin_map,
        "site_list": site_list,
        "num_sites": num_sites,
        "site_blocks": site_blocks,
        "hbr_records": merged_hbr,
        "sbr_records": merged_sbr,
        "sbin_info": sbin_info,
        "hbin_info": hbin_info,
    }


def _estimate_string_width(s: str) -> float:
    if not s:
        return 0.0
    width = 0.0
    for char in s:
        if char in "ilI1.,!|:;t ":
            width += 0.5
        elif char in "wmWMyqdopbg":
            width += 1.2
        elif char.isupper():
            width += 1.1
        else:
            width += 0.95
    return width


class STDFMultiConverter:
    def __init__(
        self,
        input_paths: List[str],
        output_prefix: Optional[str] = None,
        verbose: bool = True,
        filter_tests: Optional[Set[int]] = None,
        logger: LogFunc = None,
        progress_callback: ProgressFunc = None,
    ):
        self.input_paths = input_paths
        self.output_prefix = output_prefix or self._default_output_prefix(input_paths)
        self.verbose = verbose
        self.filter_tests = filter_tests
        self.logger = logger
        self.progress_callback = progress_callback

    @staticmethod
    def _default_output_prefix(input_paths: List[str]) -> str:
        return build_default_output_prefix(input_paths)

    def _log(self, msg: str):
        _emit_log(msg, self.verbose, self.logger)

    def _build_metadata_info(self, metadata_block: Dict[str, object]) -> Dict[str, object]:
        merged_info: Dict[str, object] = {"SOURCE_FILE": metadata_block.get("SOURCE_FILE", "")}
        merged_info.update(metadata_block.get("MIR") or {})
        merged_info.update(metadata_block.get("MRR") or {})
        merged_info.update(_summarize_sdr_records(metadata_block.get("SDR") or []))
        return merged_info

    def convert(self) -> List[str]:
        all_parts: List[Dict] = []
        all_part_results: List[Dict[int, float]] = []
        all_test_meta: Dict[int, Dict] = {}
        metadata_blocks: List[Dict[str, object]] = []
        all_hbr: List[Dict] = []
        all_sbr: List[Dict] = []
        total_counts: Dict[str, int] = defaultdict(int)
        total_skipped_ptr = 0
        total_bytes = sum(max(os.path.getsize(path), 1) for path in self.input_paths if os.path.exists(path)) or 1
        processed_bytes = 0

        num_files = len(self.input_paths)
        use_process_pool = num_files >= 2 and total_bytes >= _PARALLEL_PROCESS_MIN_BYTES
        use_thread_pool = num_files >= 2 and not use_process_pool

        if not use_process_pool and not use_thread_pool:
            for index, input_path in enumerate(self.input_paths, start=1):
                self._log(f"\n[{index}/{num_files}] Processing {input_path}")
                file_size = max(os.path.getsize(input_path), 1) if os.path.exists(input_path) else 1

                def file_progress(current_bytes: int, file_total: int, base=processed_bytes, source_name=os.path.basename(input_path)):
                    if not self.progress_callback:
                        return
                    total_for_file = file_total if file_total > 0 else file_size
                    overall = (base + min(max(current_bytes, 0), total_for_file)) / total_bytes
                    self.progress_callback(overall, f"Converting: {source_name}")

                parsed, counts, skipped_ptr = _parse_stdf_cached(
                    input_path=input_path,
                    filter_tests=self.filter_tests,
                    verbose=self.verbose,
                    logger=self.logger,
                    progress_callback=file_progress,
                )
                source_name = os.path.basename(input_path)
                self._merge_parsed(
                    parsed, counts, skipped_ptr, source_name,
                    all_parts, all_part_results, all_test_meta, metadata_blocks, all_hbr, all_sbr, total_counts,
                )
                total_skipped_ptr += skipped_ptr
                processed_bytes += file_size
                if self.progress_callback:
                    self.progress_callback(processed_bytes / total_bytes, f"Converting: {source_name}")
                self._log(f"Parts accumulated: {len(all_parts):,}")
        elif use_thread_pool:
            self._log(f"\nParsing {num_files} files in parallel (thread pool) ...")
            results_by_path: Dict[str, Tuple[Dict[str, Any], Dict[str, int], int]] = {}
            try:
                max_workers = min(4, num_files)
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {
                        executor.submit(_parse_stdf_cached, path, self.filter_tests, False, None, None): path
                        for path in self.input_paths
                    }
                    done_count = 0
                    for future in as_completed(futures):
                        path = futures[future]
                        results_by_path[path] = future.result()
                        done_count += 1
                        if self.progress_callback:
                            self.progress_callback(done_count / num_files, f"Converting: {os.path.basename(path)}")
                for path in self.input_paths:
                    parsed, counts, skipped_ptr = results_by_path[path]
                    source_name = os.path.basename(path)
                    self._merge_parsed(
                        parsed, counts, skipped_ptr, source_name,
                        all_parts, all_part_results, all_test_meta, metadata_blocks, all_hbr, all_sbr, total_counts,
                    )
                    total_skipped_ptr += skipped_ptr
                    self._log(f"[{len(metadata_blocks)}/{num_files}] {source_name} -> parts accumulated: {len(all_parts):,}")
            except Exception as exc:
                self._log(f"Threaded parse fallback ({exc}); running serial ...")
                all_parts.clear(); all_part_results.clear(); all_test_meta.clear()
                metadata_blocks.clear(); all_hbr.clear(); all_sbr.clear()
                total_counts.clear(); total_skipped_ptr = 0
                for index, input_path in enumerate(self.input_paths, start=1):
                    self._log(f"\n[{index}/{num_files}] Processing {input_path}")
                    parsed, counts, skipped = _parse_stdf_cached(
                        input_path=input_path,
                        filter_tests=self.filter_tests,
                        verbose=self.verbose,
                        logger=self.logger,
                    )
                    source_name = os.path.basename(input_path)
                    self._merge_parsed(
                        parsed, counts, skipped, source_name,
                        all_parts, all_part_results, all_test_meta, metadata_blocks, all_hbr, all_sbr, total_counts,
                    )
                    total_skipped_ptr += skipped
                    self._log(f"Parts accumulated: {len(all_parts):,}")
        else:
            self._log(f"\nParsing {num_files} files in parallel ...")
            results_by_path_proc: Dict[str, Dict] = {}
            try:
                max_workers = min(os.cpu_count() or 2, num_files)
                with ProcessPoolExecutor(max_workers=max_workers) as executor:
                    futures = {
                        executor.submit(_parse_one_file, (path, self.filter_tests)): path
                        for path in self.input_paths
                    }
                    for future in as_completed(futures):
                        path = futures[future]
                        result = future.result()
                        results_by_path_proc[path] = result
                        if self.progress_callback:
                            done = len(results_by_path_proc)
                            self.progress_callback(done / num_files, f"Converting: {result['source_name']}")
                for path in self.input_paths:
                    result = results_by_path_proc[path]
                    self._merge_parsed(
                        result, result["counts"], result["skipped_ptr"], result["source_name"],
                        all_parts, all_part_results, all_test_meta, metadata_blocks, all_hbr, all_sbr, total_counts,
                    )
                    total_skipped_ptr += result["skipped_ptr"]
                    self._log(f"[{len(metadata_blocks)}/{num_files}] {result['source_name']} -> parts accumulated: {len(all_parts):,}")
            except Exception as exc:
                self._log(f"Parallel parse unavailable ({exc}); falling back to serial ...")
                all_parts.clear(); all_part_results.clear(); all_test_meta.clear()
                metadata_blocks.clear(); all_hbr.clear(); all_sbr.clear()
                total_counts.clear(); total_skipped_ptr = 0
                for index, input_path in enumerate(self.input_paths, start=1):
                    self._log(f"\n[{index}/{num_files}] Processing {input_path}")
                    parsed, counts, skipped = _parse_stdf_cached(
                        input_path=input_path,
                        filter_tests=self.filter_tests,
                        verbose=self.verbose,
                        logger=self.logger,
                    )
                    source_name = os.path.basename(input_path)
                    self._merge_parsed(
                        parsed, counts, skipped, source_name,
                        all_parts, all_part_results, all_test_meta, metadata_blocks, all_hbr, all_sbr, total_counts,
                    )
                    total_skipped_ptr += skipped
                    self._log(f"Parts accumulated: {len(all_parts):,}")

        if self.filter_tests is not None:
            kept = total_counts.get("PTR", 0) - total_skipped_ptr
            self._log(f"\nTOTAL PTR kept : {kept:,}  /  skipped: {total_skipped_ptr:,}")
        self._log(f"TOTAL Records  : {dict(total_counts)}")
        if self.progress_callback:
            self.progress_callback(0.98, "Writing Excel")
        return self._write_combined_xlsx(all_parts, all_part_results, all_test_meta, metadata_blocks, all_hbr, all_sbr)

    @staticmethod
    def _merge_parsed(
        parsed,
        counts,
        skipped_ptr,
        source_name,
        all_parts,
        all_part_results,
        all_test_meta,
        metadata_blocks,
        all_hbr,
        all_sbr,
        total_counts,
    ) -> None:
        """Fold one file's parsed result into the shared accumulator collections."""
        if "parts" in parsed:
            parts = parsed["parts"]
            results = parsed["results"]
            test_meta = parsed["test_meta"]
            mir = parsed["mir"]
            mrr = parsed["mrr"]
            sdr = parsed["sdr"]
            hbr = parsed["hbr"]
            sbr = parsed["sbr"]
        else:
            parts = parsed["PARTS"]
            results = parsed["RESULTS"]
            test_meta = parsed["TEST_META"]
            mir = parsed.get("MIR") or {}
            mrr = parsed.get("MRR") or {}
            sdr = parsed.get("SDR") or []
            hbr = parsed.get("HBR") or []
            sbr = parsed.get("SBR") or []
            for part_row in parts:
                part_row["SOURCE_FILE"] = source_name
            for hbr_row in hbr:
                hbr_row["SOURCE_FILE"] = source_name
            for sbr_row in sbr:
                sbr_row["SOURCE_FILE"] = source_name

        for rec_name, count in counts.items():
            total_counts[rec_name] += count
        metadata_blocks.append(
            {
                "SOURCE_FILE": source_name,
                "MIR": dict(mir),
                "MRR": dict(mrr),
                "SDR": [dict(record) for record in sdr],
            }
        )
        for test_num, meta in test_meta.items():
            existing = all_test_meta.get(test_num)
            if existing is None or not existing.get("TEST_TXT"):
                all_test_meta[test_num] = meta
        all_hbr.extend(hbr)
        all_sbr.extend(sbr)
        all_parts.extend(parts)
        all_part_results.extend(results)

    def _write_summary_worksheet(
        self,
        workbook: xlsxwriter.Workbook,
        agg: Dict[str, Any],
        metadata_blocks: List[Dict[str, object]],
    ):
        """Write the formatted Summary worksheet into `workbook` using precomputed `agg`."""
        summary_ws = workbook.add_worksheet("Summary")
        summary_ws.set_margins(left=0.7, right=0.0, top=0.5, bottom=0.5)
        summary_ws.fit_to_pages(1, 0)

        title_fmt = workbook.add_format({
            "font_name": "Calibri", "font_size": 22, "bold": True,
            "align": "center", "valign": "vcenter", "border": 5,
        })
        norm_7_fmt = workbook.add_format({
            "font_name": "Aptos Display", "font_size": 7, "align": "center", "valign": "vcenter",
        })
        norm_7_wrap_fmt = workbook.add_format({
            "font_name": "Aptos Display", "font_size": 7, "align": "center", "valign": "vcenter", "text_wrap": True,
        })
        bold_7_fmt = workbook.add_format({
            "font_name": "Aptos Display", "font_size": 7, "bold": True,
            "align": "center", "valign": "vcenter", "text_wrap": True,
        })

        th_fmt = workbook.add_format({
            "font_name": "Aptos Display", "font_size": 7, "bold": True,
            "border": 1, "align": "center", "valign": "vcenter",
        })
        th_wrap_fmt = workbook.add_format({
            "font_name": "Aptos Display", "font_size": 7, "bold": True,
            "border": 1, "align": "center", "valign": "vcenter", "text_wrap": True,
        })
        td_fmt = workbook.add_format({
            "font_name": "Aptos Display", "font_size": 7, "border": 1, "align": "center", "valign": "vcenter",
        })
        td_pct_fmt = workbook.add_format({
            "font_name": "Aptos Display", "font_size": 7, "border": 1,
            "align": "center", "valign": "vcenter", "num_format": "0.00%",
        })
        td_bold_fmt = workbook.add_format({
            "font_name": "Aptos Display", "font_size": 7, "bold": True,
            "border": 1, "align": "center", "valign": "vcenter",
        })
        td_bold_wrap_fmt = workbook.add_format({
            "font_name": "Aptos Display", "font_size": 7, "bold": True,
            "border": 1, "align": "center", "valign": "vcenter", "text_wrap": True,
        })

        th_top_fmt = workbook.add_format({
            "font_name": "Aptos Display", "font_size": 7, "bold": True,
            "align": "center", "valign": "vcenter", "top": 1, "bottom": 1, "text_wrap": True,
        })
        td_both_fmt = workbook.add_format({
            "font_name": "Aptos Display", "font_size": 7, "align": "center", "valign": "vcenter", "top": 1, "bottom": 1,
        })
        td_bold_both_fmt = workbook.add_format({
            "font_name": "Aptos Display", "font_size": 7, "bold": True,
            "align": "center", "valign": "vcenter", "top": 1, "bottom": 1, "text_wrap": True,
        })
        td_sbin_name_both_fmt = workbook.add_format({
            "font_name": "Aptos Display", "font_size": 7, "align": "left", "valign": "vcenter", "top": 1, "bottom": 1,
        })
        td_top_fmt = workbook.add_format({
            "font_name": "Aptos Display", "font_size": 7, "align": "center", "valign": "vcenter", "top": 1,
        })
        td_bold_top_fmt = workbook.add_format({
            "font_name": "Aptos Display", "font_size": 7, "bold": True,
            "align": "center", "valign": "vcenter", "top": 1, "text_wrap": True,
        })
        td_sbin_name_top_fmt = workbook.add_format({
            "font_name": "Aptos Display", "font_size": 7, "align": "left", "valign": "vcenter", "top": 1,
        })
        td_bottom_fmt = workbook.add_format({
            "font_name": "Aptos Display", "font_size": 7, "align": "center", "valign": "vcenter", "bottom": 1,
        })
        td_bold_bottom_fmt = workbook.add_format({
            "font_name": "Aptos Display", "font_size": 7, "bold": True,
            "align": "center", "valign": "vcenter", "bottom": 1, "text_wrap": True,
        })
        td_sbin_name_bottom_fmt = workbook.add_format({
            "font_name": "Aptos Display", "font_size": 7, "align": "left", "valign": "vcenter", "bottom": 1,
        })
        td_noborder_fmt = workbook.add_format({
            "font_name": "Aptos Display", "font_size": 7, "align": "center", "valign": "vcenter",
        })
        td_bold_noborder_fmt = workbook.add_format({
            "font_name": "Aptos Display", "font_size": 7, "bold": True,
            "align": "center", "valign": "vcenter", "text_wrap": True,
        })
        td_sbin_name_noborder_fmt = workbook.add_format({
            "font_name": "Aptos Display", "font_size": 7, "align": "left", "valign": "vcenter",
        })

        col_a_fmt = workbook.add_format({"text_wrap": True})
        summary_ws.set_column(0, 0, 6.33, col_a_fmt)

        metadata_info: Dict[str, object] = {}
        if metadata_blocks:
            metadata_info_blocks = [self._build_metadata_info(mb) for mb in metadata_blocks]
            if metadata_info_blocks:
                metadata_info = dict(metadata_info_blocks[0])
                source_files = [mb.get("SOURCE_FILE", "") for mb in metadata_blocks if mb.get("SOURCE_FILE")]
                if source_files:
                    metadata_info["SOURCE_FILE"] = ", ".join(source_files)

        summary_ws.set_column(2, 2, 5)
        summary_ws.set_column(3, 4, 7)
        summary_ws.set_column(7, 7, 5)
        summary_ws.set_column(8, 8, 7)
        summary_ws.set_column(9, 9, 5)
        summary_ws.set_column(10, 10, 5)

        left_metadata_map = {
            2: ("Source File", "SOURCE_FILE"),
            3: ("", ""),
            4: ("Setup Time", "SETUP_T_TEXT"),
            5: ("Start Time", "START_T_TEXT"),
            6: ("Finish Time", "FINISH_T_TEXT"),
            7: ("Lot ID", "LOT_ID"),
            8: ("Product", "PART_TYP"),
            9: ("Program", "JOB_NAM"),
            10: ("Program Revision", "JOB_REV"),
            11: ("Test Code", "TEST_COD"),
            12: ("Operation", "OPER_FRQ"),
            13: ("Operator", "OPER_NAM"),
        }
        right_metadata_map = {
            2: ("Exec Type", "EXEC_TYP"),
            3: ("Exec Version", "EXEC_VER"),
            4: ("Temperature", "TST_TEMP"),
            5: ("Tester Name", "NODE_NAM"),
            6: ("Tester Type", "TSTR_TYP"),
            7: ("Handler ID", "HAND_ID"),
            8: ("Handler Type", "HAND_TYP"),
            9: ("Site Count", "SITE_CNT_TEXT"),
            10: ("LB ID", "LOAD_ID"),
            11: ("DIB ID", "DIB_ID"),
            12: ("Spec Name", "SPEC_NAM"),
            13: ("", ""),
        }

        summary_ws.merge_range("A1:K1", "LOT SUMMARY", title_fmt)
        summary_ws.set_row(0, 30)

        for r in range(2, 14):
            lbl_l, key_l = left_metadata_map[r]
            if key_l == "SOURCE_FILE":
                summary_ws.write(r, 0, lbl_l, bold_7_fmt)
                summary_ws.write(r, 1, metadata_info.get(key_l, ""), norm_7_wrap_fmt)
                summary_ws.write(r, 2, "", norm_7_wrap_fmt)
                summary_ws.write(r, 3, "", norm_7_wrap_fmt)
            elif r == 3:
                summary_ws.write(r, 0, "", bold_7_fmt)
                summary_ws.write(r, 1, "", norm_7_wrap_fmt)
                summary_ws.write(r, 2, "", norm_7_wrap_fmt)
                summary_ws.write(r, 3, "", norm_7_wrap_fmt)
            elif lbl_l:
                summary_ws.write(r, 0, lbl_l, bold_7_fmt)
                summary_ws.write(r, 1, metadata_info.get(key_l, ""), norm_7_fmt)

            lbl_r, key_r = right_metadata_map[r]
            if key_r == "SPEC_NAM":
                summary_ws.write(r, 5, lbl_r, bold_7_fmt)
                summary_ws.write(r, 6, metadata_info.get(key_r, ""), norm_7_wrap_fmt)
                for c in range(7, 11):
                    summary_ws.write(r, c, "", norm_7_wrap_fmt)
            elif r == 13:
                summary_ws.write(r, 5, "", bold_7_fmt)
                for c in range(6, 11):
                    summary_ws.write(r, c, "", norm_7_wrap_fmt)
            elif lbl_r:
                summary_ws.write(r, 5, lbl_r, bold_7_fmt)
                summary_ws.write(r, 6, metadata_info.get(key_r, ""), norm_7_fmt)

        add_manual_merge(summary_ws, 2, 0, 3, 0)
        add_manual_merge(summary_ws, 2, 1, 3, 3)
        add_manual_merge(summary_ws, 12, 5, 13, 5)
        add_manual_merge(summary_ws, 12, 6, 13, 10)

        total_parts = agg["total_parts"]
        passed_parts = agg["passed_parts"]
        failed_parts = agg["failed_parts"]
        hbin_counts = agg["hbin_counts"]
        sorted_hbins = agg["sorted_hbins"]
        sbin_counts = agg["sbin_counts"]
        sbin_site_counts = agg["sbin_site_counts"]
        top_10_sbins = agg["top_10_sbins"]
        top_20_sbins = agg["top_20_sbins"]
        sbin_to_hbin = agg["sbin_to_hbin"]
        sbin_info = agg["sbin_info"]
        site_list = agg["site_list"]
        num_sites = agg["num_sites"]
        site_blocks = agg["site_blocks"]

        current_row = 15
        summary_ws.write(current_row, 0, "", th_wrap_fmt)
        summary_ws.write(current_row, 1, "Total", th_fmt)
        summary_ws.write(current_row, 2, "Total %", th_fmt)
        summary_ws.write(current_row, 5, "HW Bin", th_wrap_fmt)
        summary_ws.write(current_row, 6, "Total", th_fmt)
        summary_ws.write(current_row, 7, "Total %", th_fmt)

        yield_val = (passed_parts / total_parts) if total_parts > 0 else 0.0
        fail_pct = (failed_parts / total_parts) if total_parts > 0 else 0.0
        pass_pct = (passed_parts / total_parts) if total_parts > 0 else 0.0

        total_rows_data = [
            ("Tested", total_parts, 1.0, td_pct_fmt),
            ("Passed", passed_parts, pass_pct, td_pct_fmt),
            ("Failed", failed_parts, fail_pct, td_pct_fmt),
            ("Yield", yield_val, "", td_fmt),
        ]

        max_rows_p1 = max(4, len(sorted_hbins))
        for r_idx in range(max_rows_p1):
            r = current_row + 1 + r_idx
            if r_idx < 4:
                label, val1, val2, val2_fmt = total_rows_data[r_idx]
                summary_ws.write(r, 0, label, td_bold_wrap_fmt)
                summary_ws.write(r, 1, val1, td_pct_fmt if label == "Yield" else td_fmt)
                summary_ws.write(r, 2, val2, val2_fmt)
            if r_idx < len(sorted_hbins):
                hbin = sorted_hbins[r_idx]
                cnt = hbin_counts[hbin]
                pct = (cnt / total_parts) if total_parts > 0 else 0.0
                summary_ws.write(r, 5, hbin, td_bold_wrap_fmt)
                summary_ws.write(r, 6, cnt, td_fmt)
                summary_ws.write(r, 7, pct, td_pct_fmt)

        max_row_part1 = current_row + max_rows_p1
        p2_row_idx = max_row_part1 + 2

        for block_idx, block_sites in enumerate(site_blocks):
            if block_idx > 0:
                p2_row_idx += 1
            block_num_sites = len(block_sites)
            summary_ws.merge_range(
                p2_row_idx, 0, p2_row_idx, block_num_sites + 1, "Top 10 Software Bin Site Information", th_fmt
            )
            summary_ws.write(p2_row_idx + 1, 0, "SW Bin", th_wrap_fmt)
            for idx, site in enumerate(block_sites):
                summary_ws.write(p2_row_idx + 1, 1 + idx, f"Site {site}", th_fmt)
            summary_ws.write(
                p2_row_idx + 1, block_num_sites + 1, "Total", th_wrap_fmt if block_num_sites + 1 == 5 else th_fmt
            )

            p2_row_idx += 2
            for sbin in top_10_sbins:
                summary_ws.write(p2_row_idx, 0, f"Bin {sbin}", td_bold_wrap_fmt)
                site_map = sbin_site_counts[sbin]
                for idx, site in enumerate(block_sites):
                    summary_ws.write(p2_row_idx, 1 + idx, site_map.get(site, 0), td_fmt)
                summary_ws.write(
                    p2_row_idx,
                    block_num_sites + 1,
                    sbin_counts[sbin],
                    td_bold_wrap_fmt if block_num_sites + 1 == 5 else td_bold_fmt,
                )
                p2_row_idx += 1
            p2_row_idx += 1

        left_data = [
            {
                "hbin": sbin_to_hbin.get(sbin, 0),
                "sbin": sbin,
                "count": sbin_counts[sbin],
                "name": sbin_info.get(sbin, {}).get("SBIN_NAM", ""),
            }
            for sbin in top_20_sbins[:10]
        ]
        left_data.sort(key=lambda x: -x["count"])

        right_data = [
            {
                "hbin": sbin_to_hbin.get(sbin, 0),
                "sbin": sbin,
                "count": sbin_counts[sbin],
                "name": sbin_info.get(sbin, {}).get("SBIN_NAM", ""),
            }
            for sbin in top_20_sbins[10:20]
        ]
        right_data.sort(key=lambda x: -x["count"])

        p3_start_row = p2_row_idx + 1

        def _border_flags(data_list):
            n = len(data_list)
            first_flags = [i == 0 or data_list[i]["hbin"] != data_list[i - 1]["hbin"] for i in range(n)]
            last_flags = [i == n - 1 or data_list[i]["hbin"] != data_list[i + 1]["hbin"] for i in range(n)]
            return first_flags, last_flags

        left_first, left_last = _border_flags(left_data)
        right_first, right_last = _border_flags(right_data)

        summary_ws.write(p3_start_row, 0, "HW Bin", th_top_fmt)
        summary_ws.write(p3_start_row, 1, "SW Bin", th_top_fmt)
        summary_ws.write(p3_start_row, 2, "Count", th_top_fmt)
        summary_ws.write(p3_start_row, 3, "SBin Name", th_top_fmt)
        summary_ws.write(p3_start_row, 5, "HW Bin", th_top_fmt)
        summary_ws.write(p3_start_row, 6, "SW Bin", th_top_fmt)
        summary_ws.write(p3_start_row, 7, "Count", th_top_fmt)
        summary_ws.write(p3_start_row, 8, "SBin Name", th_top_fmt)

        fmt_quads = {
            (True, True): (td_bold_both_fmt, td_both_fmt, td_both_fmt, td_sbin_name_both_fmt),
            (True, False): (td_bold_top_fmt, td_top_fmt, td_top_fmt, td_sbin_name_top_fmt),
            (False, True): (td_bold_bottom_fmt, td_bottom_fmt, td_bottom_fmt, td_sbin_name_bottom_fmt),
            (False, False): (td_bold_noborder_fmt, td_noborder_fmt, td_noborder_fmt, td_sbin_name_noborder_fmt),
        }

        max_p3_rows = max(len(left_data), len(right_data)) if (left_data or right_data) else 0
        for r_idx in range(max_p3_rows):
            r = p3_start_row + 1 + r_idx
            if r_idx < len(left_data):
                item = left_data[r_idx]
                is_first, is_last = left_first[r_idx], left_last[r_idx]
                hbin_fmt, sbin_fmt, cnt_fmt, name_fmt = fmt_quads[(is_first, is_last)]
                summary_ws.write(r, 0, item["hbin"] if is_first else "", hbin_fmt)
                summary_ws.write(r, 1, f"Bin {item['sbin']}", sbin_fmt)
                summary_ws.write(r, 2, item["count"], cnt_fmt)
                summary_ws.write(r, 3, item["name"], name_fmt)

            if r_idx < len(right_data):
                item = right_data[r_idx]
                is_first, is_last = right_first[r_idx], right_last[r_idx]
                hbin_fmt, sbin_fmt, cnt_fmt, name_fmt = fmt_quads[(is_first, is_last)]
                summary_ws.write(r, 5, item["hbin"] if is_first else "", hbin_fmt)
                summary_ws.write(r, 6, f"Bin {item['sbin']}", sbin_fmt)
                summary_ws.write(r, 7, item["count"], cnt_fmt)
                summary_ws.write(r, 8, item["name"], name_fmt)

        def _get_merge_runs(data_list):
            runs = []
            i = 0
            n = len(data_list)
            while i < n:
                run_start = i
                while i < n - 1 and data_list[i + 1]["hbin"] == data_list[run_start]["hbin"]:
                    i += 1
                runs.append((run_start, i))
                i += 1
            return runs

        for run_start, run_end in _get_merge_runs(left_data):
            if run_start < run_end:
                add_manual_merge(summary_ws, p3_start_row + 1 + run_start, 0, p3_start_row + 1 + run_end, 0)
        for run_start, run_end in _get_merge_runs(right_data):
            if run_start < run_end:
                add_manual_merge(summary_ws, p3_start_row + 1 + run_start, 5, p3_start_row + 1 + run_end, 5)

        max_summary_row = p3_start_row + max_p3_rows
        summary_ws.print_area(0, 0, max_summary_row, 10)

        col_b_strings = [
            str(metadata_info.get(key_l, ""))
            for r in range(2, 14)
            for _, key_l in (left_metadata_map[r],)
            if key_l and key_l != "SOURCE_FILE"
        ]
        col_b_strings.extend(
            ["Total", str(total_parts), str(passed_parts), str(failed_parts), f"{yield_val:.2%}"]
        )
        col_b_strings.append(f"Site {site_list[0]}" if site_list else "Site 1")
        for sbin in top_10_sbins:
            col_b_strings.append(str(sbin_site_counts[sbin].get(site_list[0], 0) if site_list else 0))
        col_b_strings.append("SW Bin")
        col_b_strings.extend(f"Bin {item['sbin']}" for item in left_data)
        raw_b_width = max(_estimate_string_width(s) for s in col_b_strings) + 1 if col_b_strings else 10
        summary_ws.set_column(1, 1, max(0.88 * raw_b_width - 3.6, 5.0))

        col_f_strings = [
            lbl_r for r in range(2, 14) for lbl_r, _ in (right_metadata_map[r],) if lbl_r
        ]
        col_f_strings.append("HW Bin")
        col_f_strings.extend(str(hbin) for hbin in sorted_hbins)
        if num_sites >= 5:
            col_f_strings.append(f"Site {site_list[4]}")
            col_f_strings.extend(str(sbin_site_counts[sbin].get(site_list[4], 0)) for sbin in top_10_sbins)
        col_f_strings.append("HW Bin")
        col_f_strings.extend(str(item["hbin"]) for item in right_data)
        raw_f_width = max(_estimate_string_width(s) for s in col_f_strings) + 1 if col_f_strings else 12
        col_f_fmt = workbook.add_format({"text_wrap": True})
        summary_ws.set_column(5, 5, max(0.88 * raw_f_width - 3.6, 5.0), col_f_fmt)

        col_g_strings = [
            str(metadata_info.get(key_r, ""))
            for r in range(2, 14)
            for _, key_r in (right_metadata_map[r],)
            if key_r and key_r != "SPEC_NAM"
        ]
        col_g_strings.append("Total")
        col_g_strings.extend(str(hbin_counts[hbin]) for hbin in sorted_hbins)
        if num_sites >= 6:
            col_g_strings.append(f"Site {site_list[5]}")
            col_g_strings.extend(str(sbin_site_counts[sbin].get(site_list[5], 0)) for sbin in top_10_sbins)
        col_g_strings.append("SW Bin")
        col_g_strings.extend(f"Bin {item['sbin']}" for item in right_data)
        raw_g_width = max(_estimate_string_width(s) for s in col_g_strings) + 1 if col_g_strings else 10
        summary_ws.set_column(6, 6, max(0.88 * raw_g_width - 3.6, 5.0))
        return summary_ws

    def _write_combined_xlsx(
        self,
        parts: List[Dict],
        part_results: List[Dict[int, float]],
        test_meta: Dict[int, Dict],
        metadata_blocks: List[Dict[str, object]],
        hbr_records: Optional[List[Dict]] = None,
        sbr_records: Optional[List[Dict]] = None,
    ) -> List[str]:
        output_files: List[str] = []
        if not parts:
            self._log("No part rows found. No Excel file created.")
            return output_files

        # Single-pass aggregation across all parts & bins
        agg = _compute_summary_aggregations(parts, metadata_blocks, hbr_records, sbr_records)
        hbr_records = agg["hbr_records"]
        sbr_records = agg["sbr_records"]

        sorted_tnums = sorted(test_meta)
        _PREFIX8 = ["", "", "", "", "", "", "", ""]
        test_columns = []
        row_tnum = ["Test Number"] + _PREFIX8[1:]
        row_hilim = ["High Limit"] + _PREFIX8[1:]
        row_lolim = ["Low Limit"] + _PREFIX8[1:]
        row_units = ["Unit"] + _PREFIX8[1:]
        col_append = test_columns.append
        tnum_append = row_tnum.append
        hi_append = row_hilim.append
        lo_append = row_lolim.append
        u_append = row_units.append
        for test_num in sorted_tnums:
            meta = test_meta[test_num]
            test_txt = str(meta.get("TEST_TXT", "")).strip()
            col_append(test_txt if test_txt else str(test_num))
            tnum_append(test_num)
            hi_val = meta.get("HI_LIMIT")
            hi_append("n/a" if (hi_val is None or hi_val != hi_val) else hi_val)
            lo_val = meta.get("LO_LIMIT")
            lo_append("n/a" if (lo_val is None or lo_val != lo_val) else lo_val)
            u_val = meta.get("UNITS")
            u_append(u_val if u_val else "")
        ordered_cols = [
            "SOURCE_FILE",
            "PART_ID",
            "HEAD_NUM",
            "SITE_NUM",
            "TEST_T",
            "HARD_BIN",
            "SOFT_BIN",
            "PASS_FAIL",
        ] + test_columns

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        output_path = f"{self.output_prefix}_{timestamp}.xlsx"
        workbook = xlsxwriter.Workbook(output_path, {"constant_memory": True})
        workbook.use_zip64()
        try:
            header_fmt = workbook.add_format({"bold": True, "bg_color": "#4472C4", "font_color": "#FFFFFF"})
            meta_label_fmt = workbook.add_format({"bold": True, "bg_color": "#D9D9D9", "align": "left"})
            meta_val_fmt = workbook.add_format({"bg_color": "#F2F2F2", "align": "center"})

            # ── Summary worksheet ──
            self._write_summary_worksheet(workbook, agg, metadata_blocks)

            # ── Data worksheet ──
            data_ws = workbook.add_worksheet("Data")
            num_cols = len(ordered_cols)

            for header_row_idx, (label, values) in enumerate(
                (
                    ("Test Number", row_tnum),
                    ("High Limit", row_hilim),
                    ("Low Limit", row_lolim),
                    ("Unit", row_units),
                )
            ):
                data_ws.write(header_row_idx, 0, label, meta_label_fmt)
                for col_idx in range(1, 8):
                    data_ws.write(header_row_idx, col_idx, "", meta_val_fmt)
                for col_idx in range(8, num_cols):
                    value = values[col_idx] if col_idx < len(values) else ""
                    data_ws.write(header_row_idx, col_idx, value, meta_val_fmt)

            for col_idx in range(num_cols):
                data_ws.write(4, col_idx, ordered_cols[col_idx], header_fmt)

            data_ws.freeze_panes(5, 0)
            fixed_widths = [20, 12, 10, 10, 10, 10, 10, 10]
            for i, w in enumerate(fixed_widths):
                data_ws.set_column(i, i, w)
            if num_cols > len(fixed_widths):
                data_ws.set_column(len(fixed_widths), num_cols - 1, 14)

            total_parts = max(len(parts), 1)
            progress_callback = self.progress_callback
            test_num_to_index = {test_num: idx for idx, test_num in enumerate(sorted_tnums)}
            test_num_to_index_get = test_num_to_index.get
            num_tests = len(sorted_tnums)
            if len(part_results) == len(parts):
                part_results_iter = part_results
            else:
                part_results_iter = [part_results[index] if index < len(part_results) else {} for index in range(len(parts))]

            # Flush headers (row 4 flushed, previous_row=5)
            data_ws._write_single_row(5)

            col_names = [_get_col_name(c) for c in range(8 + num_tests)]
            test_col_names = col_names[8:]
            col_tnum_pairs = list(zip(test_col_names, sorted_tnums))
            r_strs = [str(i) for i in range(6, total_parts + 6)]
            escaped_source_files: Dict[str, str] = {}

            fh_write = data_ws.fh.write
            _ninf = float("-inf")
            _pinf = float("inf")
            use_sparse_check = num_tests > 64
            sparse_threshold = num_tests >> 2
            span_end_str = str(8 + num_tests)

            chunk: List[str] = []
            chunk_append = chunk.append

            for part_idx, (part, results), r_str in zip(range(1, len(parts) + 1), zip(parts, part_results_iter), r_strs):
                chunk_append(f'    <row r="{r_str}" spans="1:{span_end_str}">\n')

                part_get = part.get
                flag_value = part_get("PART_FLG", 0)

                sf = part_get("SOURCE_FILE", "")
                escaped_sf = escaped_source_files.get(sf)
                if escaped_sf is None:
                    escaped_sf = sf.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    escaped_source_files[sf] = escaped_sf
                if escaped_sf:
                    chunk_append(f'      <c r="A{r_str}" t="inlineStr"><is><t>{escaped_sf}</t></is></c>\n')

                val = part_get("PART_ID")
                if val:
                    if "&" in val or "<" in val or ">" in val:
                        val_str = val.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    else:
                        val_str = val
                    chunk_append(f'      <c r="B{r_str}" t="inlineStr"><is><t>{val_str}</t></is></c>\n')
                else:
                    chunk_append(f'      <c r="B{r_str}" t="inlineStr"><is><t>PART_{part_idx}</t></is></c>\n')

                val = part_get("HEAD_NUM")
                if val is not None and val != "":
                    chunk_append(f'      <c r="C{r_str}"><v>{val}</v></c>\n')

                val = part_get("SITE_NUM")
                if val is not None and val != "":
                    chunk_append(f'      <c r="D{r_str}"><v>{val}</v></c>\n')

                val = part_get("TEST_T")
                if val is not None and val != "":
                    if type(val) is float:
                        chunk_append(f'      <c r="E{r_str}"><v>{val:.16G}</v></c>\n')
                    else:
                        chunk_append(f'      <c r="E{r_str}"><v>{val}</v></c>\n')

                val = part_get("HARD_BIN")
                if val is not None and val != "":
                    chunk_append(f'      <c r="F{r_str}"><v>{val}</v></c>\n')

                val = part_get("SOFT_BIN")
                if val is not None and val != "":
                    chunk_append(f'      <c r="G{r_str}"><v>{val}</v></c>\n')

                pf_str = "FAIL" if (flag_value & 0x08) else "PASS"
                chunk_append(f'      <c r="H{r_str}" t="inlineStr"><is><t>{pf_str}</t></is></c>\n')

                if results:
                    if use_sparse_check and len(results) < sparse_threshold:
                        present = []
                        for tnum, value in results.items():
                            if value is not None:
                                cidx = test_num_to_index_get(tnum)
                                if cidx is not None:
                                    present.append((cidx, value))
                        present.sort()
                        for cidx, value in present:
                            col_name = test_col_names[cidx]
                            try:
                                if _ninf < value < _pinf:
                                    chunk_append(f'      <c r="{col_name}{r_str}"><v>{value:.16G}</v></c>\n')
                            except TypeError:
                                val_str = str(value)
                                if "&" in val_str or "<" in val_str or ">" in val_str:
                                    val_str = val_str.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                                chunk_append(f'      <c r="{col_name}{r_str}" t="inlineStr"><is><t>{val_str}</t></is></c>\n')
                    else:
                        results_get = results.get
                        for col_name, test_num in col_tnum_pairs:
                            value = results_get(test_num)
                            if value is not None:
                                try:
                                    if _ninf < value < _pinf:
                                        chunk_append(f'      <c r="{col_name}{r_str}"><v>{value:.16G}</v></c>\n')
                                except TypeError:
                                    val_str = str(value)
                                    if "&" in val_str or "<" in val_str or ">" in val_str:
                                        val_str = val_str.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                                    chunk_append(f'      <c r="{col_name}{r_str}" t="inlineStr"><is><t>{val_str}</t></is></c>\n')

                chunk_append("    </row>\n")

                if len(chunk) >= 32768:
                    fh_write("".join(chunk))
                    chunk.clear()

                if progress_callback and ((part_idx % 2000 == 0) or part_idx == total_parts):
                    write_fraction = part_idx / total_parts
                    progress_callback(0.98 + (0.02 * write_fraction), "Writing Excel")

            if chunk:
                fh_write("".join(chunk))

            data_ws.previous_row = total_parts + 5
            data_ws.dim_rowmax = total_parts + 4
            data_ws.dim_colmax = 8 + num_tests - 1
            data_ws.autofilter(4, 0, len(parts) + 4, num_cols - 1)

            # ── Hard Bins worksheet ──
            if hbr_records:
                hbr_ws = workbook.add_worksheet("Hard Bins")
                hbr_headers = ["SOURCE_FILE", "HEAD_NUM", "SITE_NUM", "HBIN_NUM", "HBIN_CNT", "HBIN_PF", "HBIN_NAM"]
                for col_idx, h in enumerate(hbr_headers):
                    hbr_ws.write(0, col_idx, h, header_fmt)
                hbr_ws.freeze_panes(1, 0)
                hbr_ws.set_column(0, 0, 20)
                hbr_ws.set_column(1, 6, 12)
                sorted_hbr = sorted(hbr_records, key=lambda r: (str(r.get("SOURCE_FILE", "")), r.get("HBIN_NUM", 0)))
                for row_idx, hbr in enumerate(sorted_hbr, start=1):
                    hbr_ws.write_row(
                        row_idx,
                        0,
                        (
                            hbr.get("SOURCE_FILE", ""),
                            hbr.get("HEAD_NUM", ""),
                            hbr.get("SITE_NUM", ""),
                            hbr.get("HBIN_NUM", ""),
                            hbr.get("HBIN_CNT", ""),
                            hbr.get("HBIN_PF", ""),
                            hbr.get("HBIN_NAM", ""),
                        ),
                    )
                hbr_ws.autofilter(0, 0, len(hbr_records), len(hbr_headers) - 1)

            # ── Soft Bins worksheet ──
            if sbr_records:
                sbr_ws = workbook.add_worksheet("Soft Bins")
                sbr_headers = ["SOURCE_FILE", "HEAD_NUM", "SITE_NUM", "SBIN_NUM", "SBIN_CNT", "SBIN_PF", "SBIN_NAM"]
                for col_idx, h in enumerate(sbr_headers):
                    sbr_ws.write(0, col_idx, h, header_fmt)
                sbr_ws.freeze_panes(1, 0)
                sbr_ws.set_column(0, 0, 20)
                sbr_ws.set_column(1, 6, 12)
                sorted_sbr = sorted(sbr_records, key=lambda r: (str(r.get("SOURCE_FILE", "")), r.get("SBIN_NUM", 0)))
                for row_idx, sbr in enumerate(sorted_sbr, start=1):
                    sbr_ws.write_row(
                        row_idx,
                        0,
                        (
                            sbr.get("SOURCE_FILE", ""),
                            sbr.get("HEAD_NUM", ""),
                            sbr.get("SITE_NUM", ""),
                            sbr.get("SBIN_NUM", ""),
                            sbr.get("SBIN_CNT", ""),
                            sbr.get("SBIN_PF", ""),
                            sbr.get("SBIN_NAM", ""),
                        ),
                    )
                sbr_ws.autofilter(0, 0, len(sbr_records), len(sbr_headers) - 1)

            # ── Test Parameters worksheet ──
            if test_meta:
                tp_ws = workbook.add_worksheet("Test Parameters")
                tp_headers = ["TEST_NUM", "TEST_TXT"]
                for col_idx, h in enumerate(tp_headers):
                    tp_ws.write(0, col_idx, h, header_fmt)
                tp_ws.freeze_panes(1, 0)
                tp_ws.set_column(0, 0, 14)
                tp_ws.set_column(1, 1, 44)
                for row_idx, test_num in enumerate(sorted_tnums, start=1):
                    tp_ws.write(row_idx, 0, test_num)
                    tp_ws.write(row_idx, 1, test_meta[test_num].get("TEST_TXT", ""))
                tp_ws.autofilter(0, 0, len(sorted_tnums), len(tp_headers) - 1)

            # ── Bin Mapping worksheet (reuses single-pass bin_map/sbin_info/hbin_info) ──
            bin_map = agg["bin_map"]
            if bin_map:
                sbin_info = agg["sbin_info"]
                hbin_info = agg["hbin_info"]

                bm_ws = workbook.add_worksheet("Bin Mapping")
                bm_headers = ["SBIN_NUM", "SBIN_NAM", "HBIN_NUM", "HBIN_NAM", "BIN_PF", "PART_COUNT"]
                for col_idx, h in enumerate(bm_headers):
                    bm_ws.write(0, col_idx, h, header_fmt)
                bm_ws.freeze_panes(1, 0)
                bm_ws.set_column(0, 0, 12)
                bm_ws.set_column(1, 1, 24)
                bm_ws.set_column(2, 2, 12)
                bm_ws.set_column(3, 3, 24)
                bm_ws.set_column(4, 4, 10)
                bm_ws.set_column(5, 5, 14)

                sorted_mappings = sorted(bin_map.keys(), key=lambda x: (x[0], x[1]))
                for row_idx, (sbin, hbin) in enumerate(sorted_mappings, start=1):
                    s_info = sbin_info.get(sbin, {})
                    h_info = hbin_info.get(hbin, {})
                    bin_pf = s_info.get("SBIN_PF", "") or h_info.get("HBIN_PF", "")
                    bm_ws.write_row(
                        row_idx,
                        0,
                        (
                            sbin,
                            s_info.get("SBIN_NAM", ""),
                            hbin,
                            h_info.get("HBIN_NAM", ""),
                            bin_pf,
                            bin_map[(sbin, hbin)],
                        ),
                    )
                bm_ws.autofilter(0, 0, len(sorted_mappings), len(bm_headers) - 1)
        finally:
            workbook.close()

        if self.progress_callback:
            self.progress_callback(1.0, "Writing Excel complete")
        output_files.append(output_path)
        self._log(f"Wrote {len(parts):,} combined parts to {output_path}")

        pdf_path = output_path.replace(".xlsx", ".pdf")
        if self._export_summary_to_pdf(output_path, pdf_path, summary_data=(agg, metadata_blocks)):
            output_files.append(pdf_path)

        return output_files

    def _export_summary_to_pdf(
        self,
        xlsx_path: str,
        pdf_path: str,
        summary_data: Optional[Tuple[Dict[str, Any], List[Dict[str, object]]]] = None,
    ) -> bool:
        """Export the Summary worksheet to PDF via Excel COM automation.

        When `summary_data` is supplied, writes a tiny summary-only temporary
        workbook (~6 KB) so Excel COM opens in <0.5s without loading huge Data sheets.
        """
        try:
            import pythoncom
            import win32com.client
        except ImportError:
            self._log("Warning: win32com is not installed. Install pywin32 to enable PDF export: pip install pywin32")
            return False

        self._log(f"Exporting Summary worksheet to PDF: {pdf_path}")
        excel = None
        wb = None
        tmp_summary_xlsx: Optional[str] = None
        com_initialized = False
        try:
            source_xlsx = xlsx_path
            if summary_data is not None:
                agg, metadata_blocks = summary_data
                fd, tmp_summary_xlsx = tempfile.mkstemp(prefix="stdf2sum_pdf_", suffix=".xlsx")
                os.close(fd)
                tmp_wb = xlsxwriter.Workbook(tmp_summary_xlsx, {"constant_memory": True})
                try:
                    self._write_summary_worksheet(tmp_wb, agg, metadata_blocks)
                finally:
                    tmp_wb.close()
                source_xlsx = tmp_summary_xlsx

            pythoncom.CoInitialize()
            com_initialized = True

            excel = win32com.client.Dispatch("Excel.Application")
            excel.Visible = False
            excel.DisplayAlerts = False
            excel.ScreenUpdating = False

            abs_xlsx = os.path.abspath(source_xlsx)
            abs_pdf = os.path.abspath(pdf_path)
            wb = excel.Workbooks.Open(abs_xlsx, ReadOnly=True, UpdateLinks=0)

            ws = None
            for sheet in wb.Worksheets:
                if sheet.Name == "Summary":
                    ws = sheet
                    break

            if ws is None:
                self._log("Error exporting PDF: 'Summary' worksheet not found in workbook.")
                return False

            try:
                ws.PageSetup.TopMargin = excel.InchesToPoints(0.5)
                ws.PageSetup.BottomMargin = excel.InchesToPoints(0.5)
                ws.PageSetup.LeftMargin = excel.InchesToPoints(0.7)
                ws.PageSetup.RightMargin = excel.InchesToPoints(0.0)
                ws.PageSetup.Zoom = False
                ws.PageSetup.FitToPagesWide = 1
                ws.PageSetup.FitToPagesTall = False
            except Exception as e_margin:
                self._log(f"Warning: Failed to set custom margins/page setup: {e_margin}")

            ws.ExportAsFixedFormat(
                Type=0,
                Filename=abs_pdf,
                Quality=0,
                IncludeDocProperties=True,
                IgnorePrintAreas=False,
                OpenAfterPublish=False,
            )

            self._log(f"PDF Summary exported successfully to {pdf_path}")
            return True
        except Exception as e:
            self._log(f"Warning: Failed to export Summary PDF: {e}")
            import traceback

            self._log(traceback.format_exc())
            return False
        finally:
            try:
                if wb is not None:
                    wb.Close(SaveChanges=False)
                if excel is not None:
                    excel.Quit()
                if com_initialized:
                    pythoncom.CoUninitialize()
            except Exception:
                pass
            if tmp_summary_xlsx and os.path.exists(tmp_summary_xlsx):
                try:
                    os.remove(tmp_summary_xlsx)
                except OSError:
                    pass


def convert_multiple_stdf_to_single_csv(
    input_paths: List[str],
    output_prefix: Optional[str] = None,
    filter_tests: Optional[Set[int]] = None,
    verbose: bool = True,
    logger: LogFunc = None,
    progress_callback: ProgressFunc = None,
) -> List[str]:
    """Main conversion entry point for converting one or more STDF files
    to a single Excel (.xlsx) workbook containing Summary and Data worksheets.
    """
    converter = STDFMultiConverter(
        input_paths=input_paths,
        output_prefix=output_prefix,
        verbose=verbose,
        filter_tests=filter_tests,
        logger=logger,
        progress_callback=progress_callback,
    )
    return converter.convert()
