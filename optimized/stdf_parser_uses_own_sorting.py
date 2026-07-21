"""
Optimized STDF binary parser.

Fixes applied vs v25:
- B1: Inline PTR parsing (no dict per record)
- B2: Pre-compiled struct.Struct objects
- B3: Direct loop instead of generator for parse_stdf_file
- B4: Integer-based record dispatch
- B5: PART_FLG stored as int
- B12: Removed unused records() method
- B19: Removed unused test_meta_get binding
- B20: Fixed endian capture bug (re-capture after FAR)
"""
import mmap
import os
import struct
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

LogFunc = Optional[Callable[[str], None]]
ProgressFunc = Optional[Callable[[float, str], None]]
ByteProgressFunc = Optional[Callable[[int, int], None]]

# Record type/sub constants for integer dispatch
_FAR = (0, 10)
_MIR = (1, 10)
_MRR = (1, 20)
_PCR = (1, 30)
_SDR = (1, 80)
_PIR = (5, 10)
_PRR = (5, 20)
_PTR = (15, 10)

RECORD_TYPES = {
    _FAR: "FAR", _MIR: "MIR", _MRR: "MRR", _PCR: "PCR", _SDR: "SDR",
    _PIR: "PIR", _PRR: "PRR", _PTR: "PTR",
}

# Pre-build the set of known record type tuples for fast membership test
_KNOWN_RECORDS = frozenset(RECORD_TYPES.keys())


def _count_files_text(count: int) -> str:
    return f"{count} file" if count == 1 else f"{count} files"


def _path_modified_sort_key(path: str) -> Tuple[float, str]:
    try:
        modified_time = os.path.getmtime(path)
    except OSError:
        modified_time = float("inf")
    return modified_time, os.path.basename(path).lower()


def sort_paths_by_modified(input_paths: Sequence[str]) -> List[str]:
    return sorted(list(input_paths or []), key=_path_modified_sort_key)


def _format_stdf_timestamp(value) -> str:
    try:
        if value in (None, ""):
            return ""
        timestamp = int(value)
        if timestamp <= 0:
            return ""
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        if dt.second:
            return f"{dt.month}/{dt.day}/{dt.year} {dt.hour}:{dt.minute:02d}:{dt.second:02d}"
        return f"{dt.month}/{dt.day}/{dt.year} {dt.hour}:{dt.minute:02d}"
    except Exception:
        return ""


def _format_limit_text(value) -> str:
    try:
        return f"{value:.6g}" if value == value else "n/a"
    except Exception:
        return "n/a"


def _emit_log(message: str, verbose: bool, logger: LogFunc):
    if logger:
        logger(message)
    elif verbose:
        print(message)


class STDFReader:
    """Memory-mapped STDF reader with pre-compiled struct objects."""

    def __init__(
        self,
        filepath: str,
        filter_tests: Optional[Set[int]] = None,
        progress_callback: ByteProgressFunc = None,
    ):
        self.filepath = filepath
        self.endian = "<"
        self.filter_tests = filter_tests
        self.progress_callback = progress_callback
        self.file_size = 0
        self._last_progress_bytes = -1
        self._progress_step = 1048576
        self._file = None
        self._mm = None
        self._cn_cache = {}
        # Pre-compiled structs (updated after endian detection)
        self._s_H = struct.Struct("<H")
        self._s_I = struct.Struct("<I")
        self._s_f = struct.Struct("<f")
        self._s_HH = struct.Struct("<HH")

    def _rebuild_structs(self):
        """Rebuild struct objects after endian is detected."""
        e = self.endian
        self._s_H = struct.Struct(e + "H")
        self._s_I = struct.Struct(e + "I")
        self._s_f = struct.Struct(e + "f")
        self._s_HH = struct.Struct(e + "HH")

    @property
    def buffer(self):
        return self._mm

    def __enter__(self):
        self._file = open(self.filepath, "rb")
        try:
            self.file_size = os.path.getsize(self.filepath)
        except OSError:
            self.file_size = 0
        if self.file_size > 0:
            try:
                self._mm = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
            except (ValueError, OSError):
                self._mm = None
        self._last_progress_bytes = -1
        self._cn_cache = {}
        return self

    def __exit__(self, *args):
        if self._mm is not None:
            self._mm.close()
            self._mm = None
        if self._file:
            self._file.close()
            self._file = None

    def _report_progress(self, current_bytes: int):
        if not self.progress_callback:
            return
        self.progress_callback(min(current_bytes, self.file_size), self.file_size)

    def record_spans(self):
        """Yield (rec_typ, rec_sub, data, body_start, body_end) for each record."""
        if self._mm is None:
            f = self._file
            first = True
            while True:
                header = f.read(4)
                if len(header) < 4:
                    break
                rec_len = struct.unpack("<H", header[0:2])[0]
                rec_typ, rec_sub = header[2], header[3]
                body = f.read(rec_len)
                if len(body) < rec_len:
                    break
                if first:
                    if rec_typ == 0 and rec_sub == 10 and len(body) >= 1:
                        self.endian = ">" if body[0] == 1 else "<"
                        self._rebuild_structs()
                    first = False
                self._report_progress(f.tell())
                yield rec_typ, rec_sub, body, 0, len(body)
            self._report_progress(self.file_size)
            return

        mm = self._mm
        file_size = self.file_size
        offset = 0
        first = True
        filter_tests = self.filter_tests
        s_H_unpack = struct.Struct("<H").unpack_from
        
        has_cb = self.progress_callback is not None
        p_step = self._progress_step
        next_p = p_step
        s_I_unpack = self._s_I.unpack_from

        while offset + 4 <= file_size:
            rec_len = s_H_unpack(mm, offset)[0]
            rec_typ = mm[offset + 2]
            rec_sub = mm[offset + 3]
            body_start = offset + 4
            body_end = body_start + rec_len
            if body_end > file_size:
                break

            if first:
                if rec_typ == 0 and rec_sub == 10 and rec_len >= 1:
                    self.endian = ">" if mm[body_start] == 1 else "<"
                    self._rebuild_structs()
                    s_I_unpack = self._s_I.unpack_from
                first = False
                offset = body_end
                if has_cb and offset >= next_p:
                    self._report_progress(offset)
                    next_p = offset + p_step
                yield rec_typ, rec_sub, mm, body_start, body_end
                continue

            # Fast PTR filter skip
            if rec_typ == 15 and rec_sub == 10 and filter_tests is not None:
                if rec_len >= 4:
                    if s_I_unpack(mm, body_start)[0] not in filter_tests:
                        offset = body_end
                        if has_cb and offset >= next_p:
                            self._report_progress(offset)
                            next_p = offset + p_step
                        yield rec_typ, rec_sub, None, 0, 0
                        continue

            offset = body_end
            if has_cb and offset >= next_p:
                self._report_progress(offset)
                next_p = offset + p_step
            yield rec_typ, rec_sub, mm, body_start, body_end
        self._report_progress(file_size)

    def _limit_for(self, data, limit=None):
        return len(data) if limit is None else limit

    def _read_u1_at(self, data, offset, limit):
        if offset + 1 > limit:
            raise IndexError
        return data[offset], offset + 1

    def _read_u2_at(self, data, offset, limit):
        if offset + 2 > limit:
            raise IndexError
        return self._s_H.unpack_from(data, offset)[0], offset + 2

    def _read_u4_at(self, data, offset, limit):
        if offset + 4 > limit:
            raise IndexError
        return self._s_I.unpack_from(data, offset)[0], offset + 4

    def _read_c1_at(self, data, offset, limit):
        if offset + 1 > limit:
            raise IndexError
        return bytes(data[offset:offset + 1]).decode("latin-1", errors="replace"), offset + 1

    def _read_cn_at(self, data, offset, limit):
        if offset + 1 > limit:
            raise IndexError
        length = data[offset]
        offset += 1
        if offset + length > limit:
            raise IndexError
        raw = bytes(data[offset:offset + length])
        cached = self._cn_cache.get(raw)
        if cached is None:
            cached = raw.decode("latin-1", errors="replace")
            self._cn_cache[raw] = cached
        return cached, offset + length

    def parse_mir_compact(self, data, start=0, end=None):
        fields = {}
        offset = start
        end = self._limit_for(data, end)
        try:
            fields["SETUP_T"], offset = self._read_u4_at(data, offset, end)
            fields["START_T"], offset = self._read_u4_at(data, offset, end)
            fields["STAT_NUM"], offset = self._read_u1_at(data, offset, end)
            fields["MODE_COD"], offset = self._read_c1_at(data, offset, end)
            fields["RTST_COD"], offset = self._read_c1_at(data, offset, end)
            fields["PROT_COD"], offset = self._read_c1_at(data, offset, end)
            fields["BURN_TIM"], offset = self._read_u2_at(data, offset, end)
            fields["CMOD_COD"], offset = self._read_c1_at(data, offset, end)
            for key in ("LOT_ID", "PART_TYP", "NODE_NAM", "TSTR_TYP", "JOB_NAM",
                        "JOB_REV", "SBLOT_ID", "OPER_NAM", "EXEC_TYP", "EXEC_VER",
                        "TEST_COD", "TST_TEMP", "USER_TXT", "AUX_FILE", "PKG_TYP",
                        "FAMLY_ID", "DATE_COD", "FACIL_ID", "FLOOR_ID", "PROC_ID",
                        "OPER_FRQ", "SPEC_NAM", "SPEC_VER", "FLOW_ID", "SETUP_ID",
                        "DSGN_REV", "ENG_ID", "ROM_COD", "SERL_NUM", "SUPR_NAM"):
                fields[key], offset = self._read_cn_at(data, offset, end)
        except (IndexError, struct.error):
            pass
        if fields.get("SETUP_T") not in (None, ""):
            fields["SETUP_T_TEXT"] = _format_stdf_timestamp(fields.get("SETUP_T"))
        if fields.get("START_T") not in (None, ""):
            fields["START_T_TEXT"] = _format_stdf_timestamp(fields.get("START_T"))
        return fields or None

    def parse_mrr_compact(self, data, start=0, end=None):
        fields = {}
        offset = start
        end = self._limit_for(data, end)
        try:
            fields["FINISH_T"], offset = self._read_u4_at(data, offset, end)
            fields["DISP_COD"], offset = self._read_c1_at(data, offset, end)
            fields["USR_DESC"], offset = self._read_cn_at(data, offset, end)
            fields["EXC_DESC"], offset = self._read_cn_at(data, offset, end)
        except (IndexError, struct.error):
            pass
        if fields.get("FINISH_T") not in (None, ""):
            fields["FINISH_T_TEXT"] = _format_stdf_timestamp(fields.get("FINISH_T"))
        return fields or None

    def parse_pcr_compact(self, data, start=0, end=None):
        """Parse a Part Count Record (PCR)."""
        fields = {}
        offset = start
        end = self._limit_for(data, end)
        try:
            fields["HEAD_NUM"], offset = self._read_u1_at(data, offset, end)
            fields["SITE_NUM"], offset = self._read_u1_at(data, offset, end)
            fields["PART_CNT"], offset = self._read_u4_at(data, offset, end)
            fields["RTST_CNT"], offset = self._read_u4_at(data, offset, end)
            fields["ABRT_CNT"], offset = self._read_u4_at(data, offset, end)
            fields["GOOD_CNT"], offset = self._read_u4_at(data, offset, end)
            fields["FUNC_CNT"], offset = self._read_u4_at(data, offset, end)
        except (IndexError, struct.error):
            pass
        return fields or None

    def parse_sdr_compact(self, data, start=0, end=None):
        fields = {}
        offset = start
        end = self._limit_for(data, end)
        try:
            fields["HEAD_NUM"], offset = self._read_u1_at(data, offset, end)
            fields["SITE_GRP"], offset = self._read_u1_at(data, offset, end)
            fields["SITE_CNT"], offset = self._read_u1_at(data, offset, end)
            site_numbers = []
            for _ in range(fields["SITE_CNT"]):
                site_num, offset = self._read_u1_at(data, offset, end)
                site_numbers.append(site_num)
            fields["SITE_NUM_LIST"] = site_numbers
            fields["SITE_NUM_LIST_TEXT"] = ", ".join(str(s) for s in site_numbers)
            for key in ("HAND_TYP", "HAND_ID", "CARD_TYP", "CARD_ID", "LOAD_TYP",
                        "LOAD_ID", "DIB_TYP", "DIB_ID", "CABL_TYP", "CABL_ID",
                        "CONT_TYP", "CONT_ID", "LASR_TYP", "LASR_ID", "EXTR_TYP",
                        "EXTR_ID"):
                fields[key], offset = self._read_cn_at(data, offset, end)
        except (IndexError, struct.error):
            pass
        return fields or None

    def parse_pir_compact(self, data, start=0, end=None):
        end = self._limit_for(data, end)
        if start + 2 > end:
            return None
        return {"HEAD_NUM": data[start], "SITE_NUM": data[start + 1]}

    def parse_prr_compact(self, data, start=0, end=None):
        end = self._limit_for(data, end)
        if start + 18 > end:
            return None
        try:
            head_num = data[start]
            site_num = data[start + 1]
            part_flg = data[start + 2]  # B5: store as int, not hex string
            hard_bin = self._s_H.unpack_from(data, start + 5)[0]
            soft_bin = self._s_H.unpack_from(data, start + 7)[0]
            
            # --- NEW: Extract TEST_T (4-byte unsigned int at offset 13) ---
            test_t = self._s_I.unpack_from(data, start + 13)[0]
            
            offset = start + 17
            part_id_len = data[offset]
            offset += 1
            if offset + part_id_len > end:
                raise IndexError
            part_id = bytes(data[offset:offset + part_id_len]).decode("latin-1", errors="replace")
        except (IndexError, struct.error):
            return None
        return {
            "HEAD_NUM": head_num, "SITE_NUM": site_num, "PART_FLG": part_flg,
            "HARD_BIN": hard_bin, "SOFT_BIN": soft_bin, 
            "TEST_T": test_t,  # <-- ADDED TEST_T HERE
            "PART_ID": part_id,
        }

    def parse_ptr_compact(self, data, include_meta=False, start=0, end=None):
        end = self._limit_for(data, end)
        if start + 12 > end:
            return None
        try:
            test_num = self._s_I.unpack_from(data, start)[0]
            head_num = data[start + 4]
            site_num = data[start + 5]
            result = self._s_f.unpack_from(data, start + 8)[0]
        except (IndexError, struct.error):
            return None
        fields = {"TEST_NUM": test_num, "HEAD_NUM": head_num, "SITE_NUM": site_num, "RESULT": result}
        if not include_meta:
            return fields
        test_txt = ""
        lo_limit = float("nan")
        hi_limit = float("nan")
        units = ""
        offset = start + 12
        try:
            if offset >= end:
                raise IndexError
            test_txt_len = data[offset]
            offset += 1
            if offset + test_txt_len > end:
                raise IndexError
            test_txt = bytes(data[offset:offset + test_txt_len]).decode("latin-1", errors="replace")
            offset += test_txt_len
            if offset >= end:
                raise IndexError
            alarm_id_len = data[offset]
            offset += 1 + alarm_id_len
            if offset + 4 > end:
                raise IndexError
            offset += 4  # skip OPT_FLAG + padding
            if offset + 4 <= end:
                lo_limit = self._s_f.unpack_from(data, offset)[0]
                offset += 4
            if offset + 4 <= end:
                hi_limit = self._s_f.unpack_from(data, offset)[0]
                offset += 4
            if offset < end:
                units_len = data[offset]
                offset += 1
                if offset + units_len > end:
                    raise IndexError
                units = bytes(data[offset:offset + units_len]).decode("latin-1", errors="replace")
        except (IndexError, struct.error):
            test_txt = test_txt or ""
            units = units or ""
        fields.update({"TEST_TXT": test_txt, "LO_LIMIT": lo_limit, "HI_LIMIT": hi_limit, "UNITS": units})
        return fields


def _prepare_scanned_test_row(test_row: Dict) -> Dict:
    lo_text = _format_limit_text(test_row.get("LO_LIMIT"))
    hi_text = _format_limit_text(test_row.get("HI_LIMIT"))
    search_text = " ".join([
        str(test_row.get("TEST_NUM", "")), str(test_row.get("TEST_TXT", "")),
        str(test_row.get("UNITS", "")), lo_text, hi_text,
    ]).lower()
    test_row["_LO_LIMIT_TEXT"] = lo_text
    test_row["_HI_LIMIT_TEXT"] = hi_text
    test_row["_SEARCH_TEXT"] = search_text
    return test_row


def scan_test_list(filepath: str, progress_callback: ByteProgressFunc = None) -> List[Dict]:
    tests: Dict[int, Dict] = {}
    with STDFReader(filepath, progress_callback=progress_callback) as parser:
        for rec_typ, rec_sub, data, start, end in parser.record_spans():
            if data is None:
                continue
            if rec_typ == 15 and rec_sub == 10 and end - start >= 4:
                test_num = parser._s_I.unpack_from(data, start)[0]
                if test_num not in tests:
                    fields = parser.parse_ptr_compact(data, include_meta=True, start=start, end=end) or {}
                    tests[test_num] = {
                        "TEST_NUM": test_num, "TEST_TXT": fields.get("TEST_TXT", ""),
                        "LO_LIMIT": fields.get("LO_LIMIT", float("nan")),
                        "HI_LIMIT": fields.get("HI_LIMIT", float("nan")),
                        "UNITS": fields.get("UNITS", ""),
                    }
    return [_prepare_scanned_test_row(row) for row in sorted(tests.values(), key=lambda r: r["TEST_NUM"])]


def parse_filter_values(typed_tests_text: str, selected_tests_text: str,
                        range_from: str, range_to: str) -> Optional[Set[int]]:
    filter_tests: Set[int] = set()
    typed_tests_text = typed_tests_text.strip()
    selected_tests_text = selected_tests_text.strip()
    range_from = range_from.strip()
    range_to = range_to.strip()
    for tests_text in (typed_tests_text, selected_tests_text):
        if tests_text:
            normalized = tests_text.replace(",", " ")
            for token in normalized.split():
                filter_tests.add(int(token))
    if range_from or range_to:
        if not range_from or not range_to:
            raise ValueError("Both Range From and Range To must be filled in.")
        lo, hi = int(range_from), int(range_to)
        if lo > hi:
            raise ValueError("Range From cannot be greater than Range To.")
        filter_tests.update(range(lo, hi + 1))
    return filter_tests or None


def parse_stdf_file(
    input_path: str,
    filter_tests: Optional[Set[int]] = None,
    verbose: bool = True,
    logger: LogFunc = None,
    progress_callback: ByteProgressFunc = None,
) -> Tuple[Dict[str, object], Dict[str, int], int]:
    """
    Optimized STDF parser. Key differences from v25:
    - B1: PTR hot path inlined — no dict creation per PTR
    - B2: Pre-compiled struct objects
    - B4: Integer-based dispatch (PTR checked first since it's 99%+ of records)
    - B9: Inline completion tracking (no post-parse filter)
    - B20: Endian re-captured after FAR detection
    """
    counts: Dict[str, int] = defaultdict(int)
    skipped_ptr = 0
    if filter_tests is not None:
        _emit_log(f"Reading STDF : {input_path}", verbose, logger)
        _emit_log(f"PTR filter   : {sorted(filter_tests)}", verbose, logger)
    else:
        _emit_log(f"Reading STDF : {input_path}  (all PTR tests)", verbose, logger)

    parts: List[Dict] = []
    part_results: List[Dict[int, float]] = []
    test_meta: Dict[int, Dict] = {}
    mir_info: Dict[str, object] = {}
    mrr_info: Dict[str, object] = {}
    sdr_records: List[Dict[str, object]] = []
    open_slot: Dict[Tuple[int, int], int] = {}

    with STDFReader(input_path, filter_tests=filter_tests, progress_callback=progress_callback) as reader:
        # Local bindings for hot-path methods
        parse_mir = reader.parse_mir_compact
        parse_mrr = reader.parse_mrr_compact
        parse_sdr = reader.parse_sdr_compact
        parse_pir = reader.parse_pir_compact
        parse_ptr = reader.parse_ptr_compact
        parse_prr = reader.parse_prr_compact
        open_slot_get = open_slot.get
        open_slot_pop = open_slot.pop
        parts_append = parts.append
        part_results_append = part_results.append
        sdr_append = sdr_records.append

        endian_captured = False

        for rec_typ, rec_sub, data, start, end in reader.record_spans():
            # B20: Re-capture structs after FAR sets endian
            if not endian_captured and rec_typ == 0 and rec_sub == 10:
                endian_captured = True
                # reader._rebuild_structs() already called in record_spans
                # Re-bind local struct references
                s_I = reader._s_I
                s_f = reader._s_f
                continue
            if not endian_captured:
                endian_captured = True
                s_I = reader._s_I
                s_f = reader._s_f

            # B4: Check PTR first (most common record by far)
            if rec_typ == 15 and rec_sub == 10:
                counts["PTR"] = counts.get("PTR", 0) + 1
                if data is None:
                    skipped_ptr += 1
                    continue
                # B1: Inline PTR parsing — no dict creation
                if end - start >= 12:
                    try:
                        test_num = s_I.unpack_from(data, start)[0]
                        head_num = data[start + 4]
                        site_num = data[start + 5]
                        result_val = s_f.unpack_from(data, start + 8)[0]
                    except (struct.error, IndexError):
                        continue
                    part_idx = open_slot_get((head_num, site_num), -1)
                    if 0 <= part_idx < len(part_results):
                        part_results[part_idx][test_num] = result_val
                    if test_num not in test_meta:
                        fields = parse_ptr(data, include_meta=True, start=start, end=end)
                        if fields:
                            test_meta[test_num] = {"TEST_TXT": fields.get("TEST_TXT", "")}
                continue

            # Non-PTR records (rare)
            rec_key = (rec_typ, rec_sub)
            if rec_key not in _KNOWN_RECORDS:
                continue
            rec_name = RECORD_TYPES[rec_key]
            counts[rec_name] = counts.get(rec_name, 0) + 1
            if data is None:
                continue

            if rec_typ == 5:
                if rec_sub == 10:  # PIR
                    if start + 2 > end:
                        continue
                    head = data[start]
                    site = data[start + 1]
                    part_idx = len(parts)
                    open_slot[(head, site)] = part_idx
                    parts_append({"PART_IDX": part_idx, "HAS_PRR": False})
                    part_results_append({})
                elif rec_sub == 20:  # PRR
                    fields = parse_prr(data, start=start, end=end)
                    if not fields:
                        continue
                    slot_key = (fields["HEAD_NUM"], fields["SITE_NUM"])
                    part_idx = open_slot_pop(slot_key, -1)
                    if part_idx < 0:
                        part_idx = len(parts)
                        parts_append({"PART_IDX": part_idx, "HAS_PRR": False})
                        part_results_append({})
                    fields["PART_IDX"] = part_idx
                    fields["HAS_PRR"] = True
                    parts[part_idx].update(fields)
            elif rec_typ == 1:
                if rec_sub == 10:  # MIR
                    if not mir_info:
                        fields = parse_mir(data, start=start, end=end)
                        if fields:
                            mir_info.update(fields)
                elif rec_sub == 20:  # MRR
                    if not mrr_info:
                        fields = parse_mrr(data, start=start, end=end)
                        if fields:
                            mrr_info.update(fields)
                elif rec_sub == 80:  # SDR
                    fields = parse_sdr(data, start=start, end=end)
                    if fields:
                        sdr_append(fields)

    # B9: Inline filter — no separate _filter_complete_parts() call
    filtered_parts: List[Dict] = []
    filtered_results: List[Dict[int, float]] = []
    dropped = 0
    for part, result in zip(parts, part_results):
        if part.get("HAS_PRR"):
            filtered_parts.append(part)
            filtered_results.append(result)
        else:
            dropped += 1

    if dropped:
        _emit_log(f"Dropped {dropped:,} inferred/incomplete part(s) without PRR.", verbose, logger)

    kept = counts.get("PTR", 0) - skipped_ptr
    if filter_tests is not None:
        _emit_log(f"PTR kept     : {kept:,}  /  skipped: {skipped_ptr:,}", verbose, logger)
    _emit_log(f"Records      : {dict(counts)}", verbose, logger)

    return {
        "PARTS": filtered_parts, "RESULTS": filtered_results,
        "TEST_META": test_meta, "MIR": mir_info, "MRR": mrr_info, "SDR": sdr_records,
    }, counts, skipped_ptr


def parse_mrr_only(filepath: str) -> Optional[Dict]:
    """Parse only the MRR record from an STDF file for fast integrity checking.

    Scans records sequentially until the MRR (type=1, sub=20) is found, then
    returns its fields dict. Utilizes a fast memory-mapped loop to avoid generator overhead.
    """
    try:
        with STDFReader(filepath) as reader:
            mm = reader._mm
            file_size = reader.file_size
            if mm is not None:
                if file_size >= 5 and mm[2] == 0 and mm[3] == 10:
                    reader.endian = ">" if mm[4] == 1 else "<"
                    reader._rebuild_structs()
                endian = reader.endian
                s_H_unpack = struct.Struct(f"{endian}H").unpack_from
                offset = 0
                while offset + 4 <= file_size:
                    rec_len = s_H_unpack(mm, offset)[0]
                    rec_typ = mm[offset + 2]
                    rec_sub = mm[offset + 3]
                    body_start = offset + 4
                    body_end = body_start + rec_len
                    if body_end > file_size:
                        break
                    if rec_typ == 1 and rec_sub == 20:  # MRR
                        return reader.parse_mrr_compact(mm, start=body_start, end=body_end)
                    offset = body_end
            else:
                for rec_typ, rec_sub, data, start, end in reader.record_spans():
                    if data is None:
                        continue
                    if rec_typ == 1 and rec_sub == 20:  # MRR
                        return reader.parse_mrr_compact(data, start=start, end=end)
    except Exception:
        return None
    return None


def parse_check_summary(filepath: str) -> Dict:
    """Parse MRR and summary PCR from an STDF file for integrity checking.

    Scans the entire file and returns a dict with MRR and PCR.
    Uses a fast custom loop to avoid generator yield overhead.
    """
    result: Dict = {"mrr": None, "pcr": None}
    try:
        with STDFReader(filepath) as reader:
            mm = reader._mm
            file_size = reader.file_size
            if mm is not None:
                if file_size >= 5 and mm[2] == 0 and mm[3] == 10:
                    reader.endian = ">" if mm[4] == 1 else "<"
                    reader._rebuild_structs()
                endian = reader.endian
                s_H_unpack = struct.Struct(f"{endian}H").unpack_from
                offset = 0
                while offset + 4 <= file_size:
                    rec_len = s_H_unpack(mm, offset)[0]
                    rec_typ = mm[offset + 2]
                    rec_sub = mm[offset + 3]
                    body_start = offset + 4
                    body_end = body_start + rec_len
                    if body_end > file_size:
                        break
                    if rec_typ == 1:
                        if rec_sub == 20 and result["mrr"] is None:  # MRR
                            result["mrr"] = reader.parse_mrr_compact(mm, start=body_start, end=body_end)
                        elif rec_sub == 30:  # PCR
                            pcr = reader.parse_pcr_compact(mm, start=body_start, end=body_end)
                            if pcr and pcr.get("HEAD_NUM") == 255 and pcr.get("SITE_NUM") == 0:
                                result["pcr"] = pcr
                    offset = body_end
            else:
                for rec_typ, rec_sub, data, start, end in reader.record_spans():
                    if data is None or rec_typ != 1:
                        continue
                    if rec_sub == 20 and result["mrr"] is None:  # MRR
                        result["mrr"] = reader.parse_mrr_compact(data, start=start, end=end)
                    elif rec_sub == 30:  # PCR
                        pcr = reader.parse_pcr_compact(data, start=start, end=end)
                        if pcr and pcr.get("HEAD_NUM") == 255 and pcr.get("SITE_NUM") == 0:
                            result["pcr"] = pcr
    except Exception:
        pass
    return result
