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


def _gui_path_filename_sort_key(path: str):
    """Return gui_app._path_filename_sort_key(path) lazily.

    The actual timestamp ordering rule is intentionally owned by gui_app.py so
    the GUI panel order and backend parsing order cannot drift apart. The
    import is lazy to avoid circular-import issues while gui_app imports this
    parser module during application startup.
    """
    try:
        from gui_app import _path_filename_sort_key
    except Exception as exc:
        # Fallback only for the case where gui_app.py is executed as __main__
        # instead of imported as gui_app.
        import sys
        main_key = getattr(sys.modules.get("__main__"), "_path_filename_sort_key", None)
        if callable(main_key):
            return main_key(path)
        raise RuntimeError(
            "Unable to import gui_app._path_filename_sort_key. "
            "Run the application as the optimized package or ensure gui_app.py "
            "is available before sorting STDF paths."
        ) from exc
    return _path_filename_sort_key(path)


def _path_modified_sort_key(path: str):
    """Backward-compatible alias for older callers.

    Historical name retained, but the sort key now delegates to
    gui_app._path_filename_sort_key instead of using Date Modified.
    """
    return _gui_path_filename_sort_key(path)


def sort_paths_by_filename_timestamp(input_paths: Sequence[str]) -> List[str]:
    """Sort STDF paths using the shared GUI timestamp-from-filename rule."""
    return sorted(list(input_paths or []), key=_gui_path_filename_sort_key)


def sort_paths_by_modified(input_paths: Sequence[str]) -> List[str]:
    """Backward-compatible wrapper using the shared GUI timestamp sort key."""
    return sort_paths_by_filename_timestamp(input_paths)


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
        self._file = None
        self._os_fd = None
        self._win_handle = None

        if os.name == "nt":
            try:
                import ctypes, msvcrt
                # FILE_FLAG_SEQUENTIAL_SCAN (0x08000000) tells Windows Kernel Cache Manager
                # to aggressively pre-fetch contiguous disk pages into RAM via DMA ahead of CPU page faults.
                GENERIC_READ = 0x80000000
                FILE_SHARE_READ = 0x00000001
                OPEN_EXISTING = 3
                FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000

                handle = ctypes.windll.kernel32.CreateFileW(
                    os.path.abspath(self.filepath),
                    GENERIC_READ,
                    FILE_SHARE_READ,
                    None,
                    OPEN_EXISTING,
                    FILE_FLAG_SEQUENTIAL_SCAN,
                    None
                )
                if handle != -1 and handle != 0:
                    self._win_handle = handle
                    self._os_fd = msvcrt.open_osfhandle(handle, 0)
            except Exception:
                self._win_handle = None
                self._os_fd = None

        if self._os_fd is not None:
            try:
                self.file_size = os.path.getsize(self.filepath)
            except OSError:
                self.file_size = 0
            if self.file_size > 0:
                try:
                    self._mm = mmap.mmap(self._os_fd, 0, access=mmap.ACCESS_READ)
                except (ValueError, OSError):
                    self._mm = None
        else:
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
        if self._os_fd is not None:
            try:
                os.close(self._os_fd)
            except OSError:
                pass
            self._os_fd = None
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
                rec_typ = header[2]
                rec_sub = header[3]
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

            # Zero-yield PTR filter skip: advance offset directly at C-level without tracking counters
            if rec_typ == 15 and rec_sub == 10 and filter_tests is not None:
                if rec_len >= 4:
                    if s_I_unpack(mm, body_start)[0] not in filter_tests:
                        offset = body_end
                        if has_cb and offset >= next_p:
                            self._report_progress(offset)
                            next_p = offset + p_step
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


def scan_test_list(
    filepath: str,
    progress_callback: ByteProgressFunc = None,
    full_scan: bool = False,
    stable_parts: int = 3,
) -> List[Dict]:
    """Return the unique PTR test list (TEST_NUM/TXT/limits/units) for a file.

    Performance note
    ----------------
    In a real datalog the complete set of PTR test numbers (and the only PTR
    records that carry full metadata: TEST_TXT / LO_LIMIT / HI_LIMIT / UNITS)
    appears in the FIRST tested part. Every later part just repeats the same
    test numbers as 12-byte result-only PTRs. The previous implementation still
    walked all ~N-million PTRs through the generator, so a 2,300-test x
    10-15k-part file spent ~18 s re-confirming test numbers it already had.

    This version:
      * inlines the record walk (no generator, single '<HBB' header decode), and
      * applies a CONSERVATIVE early-exit: it keeps scanning until `stable_parts`
        CONSECUTIVE complete parts (PIR..PRR) have each added ZERO new test
        numbers, then stops. Requiring several stable full parts in a row makes
        it safe against parts that fail/terminate early (those only have FEWER
        tests, never new ones).

    Set `full_scan=True` to force a complete end-to-end scan (identical to the
    old behavior) if a program is ever suspected of introducing brand-new test
    numbers only in later parts.
    """
    tests: Dict[int, Dict] = {}
    with STDFReader(filepath, progress_callback=progress_callback) as parser:
        mm = parser._mm
        if mm is None:
            # Streaming fallback (non-mmap): keep the simple full generator walk.
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

        file_size = parser.file_size
        has_cb = progress_callback is not None
        p_step = parser._progress_step
        report = parser._report_progress

        offset = 0
        if file_size >= 5 and mm[2] == 0 and mm[3] == 10:
            parser.endian = ">" if mm[4] == 1 else "<"
            parser._rebuild_structs()
            far_len = struct.Struct(parser.endian + "H").unpack_from(mm, 0)[0]
            offset = 4 + far_len

        s_I_unpack = parser._s_I.unpack_from
        s_hdr_unpack = struct.Struct(parser.endian + "HBB").unpack_from
        parse_ptr = parser.parse_ptr_compact
        next_p = offset + p_step

        stable_run = 0            # consecutive complete parts with no new tests
        count_at_part_start = 0   # len(tests) captured at the last PIR

        while offset + 4 <= file_size:
            rec_len, rec_typ, rec_sub = s_hdr_unpack(mm, offset)
            body_start = offset + 4
            body_end = body_start + rec_len
            if body_end > file_size:
                break

            if rec_typ == 15 and rec_sub == 10:
                if rec_len >= 4:
                    test_num = s_I_unpack(mm, body_start)[0]
                    if test_num not in tests:
                        fields = parse_ptr(mm, include_meta=True, start=body_start, end=body_end) or {}
                        tests[test_num] = {
                            "TEST_NUM": test_num, "TEST_TXT": fields.get("TEST_TXT", ""),
                            "LO_LIMIT": fields.get("LO_LIMIT", float("nan")),
                            "HI_LIMIT": fields.get("HI_LIMIT", float("nan")),
                            "UNITS": fields.get("UNITS", ""),
                        }
            elif rec_typ == 5:
                if rec_sub == 10:      # PIR = start of a part
                    count_at_part_start = len(tests)
                elif rec_sub == 20:    # PRR = end of a complete part
                    if not full_scan and tests:
                        if len(tests) == count_at_part_start:
                            stable_run += 1
                            if stable_run >= stable_parts:
                                if has_cb:
                                    report(file_size)
                                break
                        else:
                            stable_run = 0

            offset = body_end
            if has_cb and offset >= next_p:
                report(offset); next_p = offset + p_step
        else:
            if has_cb:
                report(file_size)

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
    if filter_tests is not None:
        _emit_log(f"Reading STDF : {input_path}", verbose, logger)
    else:
        _emit_log(f"Reading STDF : {input_path}  (all PTR tests)", verbose, logger)

    parts: List[Dict] = []
    part_results: List[Dict[int, float]] = []
    mir_info: Dict[str, object] = {}
    mrr_info: Dict[str, object] = {}
    open_slot: Dict[int, int] = {}

    with STDFReader(input_path, filter_tests=filter_tests, progress_callback=progress_callback) as reader:
        # Local bindings for hot-path methods
        parse_mir = reader.parse_mir_compact
        parse_mrr = reader.parse_mrr_compact
        parse_pir = reader.parse_pir_compact
        parse_prr = reader.parse_prr_compact
        open_slot_get = open_slot.get
        open_slot_pop = open_slot.pop
        parts_append = parts.append
        part_results_append = part_results.append

        mm = reader._mm
        if mm is not None:
            # B3 (now actually applied): fully inlined single-loop walk.
            # The prior "optimized" build STILL consumed the record_spans()
            # generator, so every one of the ~N-million records incurred a
            # generator resume + 5-tuple allocation, PLUS a second per-record
            # `endian_captured` branch in this consumer. For a 2,300-test x
            # 10-15k-part datalog that is 20-35M generator round-trips.
            #
            # This inlines the record walk directly into the parse loop:
            #   * header (rec_len, rec_typ, rec_sub) decoded in ONE '<HBB'
            #     struct call instead of 1 unpack + 2 mmap byte-index reads
            #   * endian/FAR handled ONCE before the loop (hoisted out)
            #   * no generator, no per-record tuple, no per-record endian check
            # Output is identical to the generator path (verified by regression).
            file_size = reader.file_size
            has_cb = progress_callback is not None
            p_step = reader._progress_step
            report = reader._report_progress

            offset = 0
            if file_size >= 5 and mm[2] == 0 and mm[3] == 10:
                reader.endian = ">" if mm[4] == 1 else "<"
                reader._rebuild_structs()
                far_len = struct.Struct(reader.endian + "H").unpack_from(mm, 0)[0]
                offset = 4 + far_len

            s_I_unpack = reader._s_I.unpack_from
            s_f_unpack = reader._s_f.unpack_from
            s_hdr_unpack = struct.Struct(reader.endian + "HBB").unpack_from
            next_p = offset + p_step

            while offset + 4 <= file_size:
                rec_len, rec_typ, rec_sub = s_hdr_unpack(mm, offset)
                body_start = offset + 4
                body_end = body_start + rec_len
                if body_end > file_size:
                    break

                if rec_typ == 15 and rec_sub == 10:
                    if rec_len >= 12:
                        test_num = s_I_unpack(mm, body_start)[0]
                        if filter_tests is not None and test_num not in filter_tests:
                            offset = body_end
                            if has_cb and offset >= next_p:
                                report(offset); next_p = offset + p_step
                            continue
                        head_num = mm[body_start + 4]
                        site_num = mm[body_start + 5]
                        result_val = s_f_unpack(mm, body_start + 8)[0]
                        part_idx = open_slot_get((head_num << 8) | site_num, -1)
                        if 0 <= part_idx < len(part_results):
                            part_results[part_idx][test_num] = result_val
                    offset = body_end
                    if has_cb and offset >= next_p:
                        report(offset); next_p = offset + p_step
                    continue

                if rec_typ == 5:
                    if rec_sub == 10:  # PIR
                        if body_start + 2 <= body_end:
                            head = mm[body_start]
                            site = mm[body_start + 1]
                            part_idx = len(parts)
                            open_slot[(head << 8) | site] = part_idx
                            parts_append({"PART_IDX": part_idx, "HAS_PRR": False})
                            part_results_append({})
                    elif rec_sub == 20:  # PRR
                        fields = parse_prr(mm, start=body_start, end=body_end)
                        if fields:
                            slot_key = (fields["HEAD_NUM"] << 8) | fields["SITE_NUM"]
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
                            fields = parse_mir(mm, start=body_start, end=body_end)
                            if fields:
                                mir_info.update(fields)
                    elif rec_sub == 20:  # MRR
                        if not mrr_info:
                            fields = parse_mrr(mm, start=body_start, end=body_end)
                            if fields:
                                mrr_info.update(fields)

                offset = body_end
                if has_cb and offset >= next_p:
                    report(offset); next_p = offset + p_step
            if has_cb:
                report(file_size)
        else:
            # Fallback: streaming (non-mmap) path via the record generator.
            endian_captured = False
            for rec_typ, rec_sub, data, start, end in reader.record_spans():
                if not endian_captured and rec_typ == 0 and rec_sub == 10:
                    endian_captured = True
                    s_I = reader._s_I
                    s_f = reader._s_f
                    continue
                if not endian_captured:
                    endian_captured = True
                    s_I = reader._s_I
                    s_f = reader._s_f
                if rec_typ == 15 and rec_sub == 10:
                    if data is not None and end - start >= 12:
                        try:
                            test_num = s_I.unpack_from(data, start)[0]
                            head_num = data[start + 4]
                            site_num = data[start + 5]
                            result_val = s_f.unpack_from(data, start + 8)[0]
                        except (struct.error, IndexError):
                            continue
                        part_idx = open_slot_get((head_num << 8) | site_num, -1)
                        if 0 <= part_idx < len(part_results):
                            part_results[part_idx][test_num] = result_val
                    continue
                if data is None:
                    continue
                if rec_typ == 5:
                    if rec_sub == 10:
                        if start + 2 > end:
                            continue
                        head = data[start]
                        site = data[start + 1]
                        part_idx = len(parts)
                        open_slot[(head << 8) | site] = part_idx
                        parts_append({"PART_IDX": part_idx, "HAS_PRR": False})
                        part_results_append({})
                    elif rec_sub == 20:
                        fields = parse_prr(data, start=start, end=end)
                        if not fields:
                            continue
                        slot_key = (fields["HEAD_NUM"] << 8) | fields["SITE_NUM"]
                        part_idx = open_slot_pop(slot_key, -1)
                        if part_idx < 0:
                            part_idx = len(parts)
                            parts_append({"PART_IDX": part_idx, "HAS_PRR": False})
                            part_results_append({})
                        fields["PART_IDX"] = part_idx
                        fields["HAS_PRR"] = True
                        parts[part_idx].update(fields)
                elif rec_typ == 1:
                    if rec_sub == 10:
                        if not mir_info:
                            fields = parse_mir(data, start=start, end=end)
                            if fields:
                                mir_info.update(fields)
                    elif rec_sub == 20:
                        if not mrr_info:
                            fields = parse_mrr(data, start=start, end=end)
                            if fields:
                                mrr_info.update(fields)

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

    return {
        "PARTS": filtered_parts, "RESULTS": filtered_results,
        "TEST_META": {}, "MIR": mir_info, "MRR": mrr_info, "SDR": [],
    }, {}, 0


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

    Fast Reverse Lookup: MRR (type=1, sub=20) and summary PCR (type=1, sub=30)
    are written at the very end of an STDF file. Uses memory-mapped rfind() to
    locate MRR/PCR in < 0.1ms, bypassing megabytes of PTR records.
    Falls back to a full sequential scan if reverse lookup is not applicable.
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

                # Fast reverse lookup: scan the last 512KB for MRR (type=1, sub=20 -> b'\x01\x14')
                search_start = max(0, file_size - 512 * 1024)
                mrr_idx = mm.rfind(b"\x01\x14", search_start)
                if mrr_idx >= 2:
                    mrr_offset = mrr_idx - 2
                    rec_len = s_H_unpack(mm, mrr_offset)[0]
                    body_start = mrr_offset + 4
                    body_end = body_start + rec_len
                    if body_end <= file_size and rec_len < 1000:
                        result["mrr"] = reader.parse_mrr_compact(mm, start=body_start, end=body_end)

                # Fast reverse lookup: scan the last 512KB for summary PCR (type=1, sub=30 -> b'\x01\x1e')
                pcr_idx = mm.rfind(b"\x01\x1e", search_start)
                if pcr_idx >= 2:
                    pcr_offset = pcr_idx - 2
                    rec_len = s_H_unpack(mm, pcr_offset)[0]
                    body_start = pcr_offset + 4
                    body_end = body_start + rec_len
                    if body_end <= file_size and rec_len < 1000:
                        pcr = reader.parse_pcr_compact(mm, start=body_start, end=body_end)
                        if pcr and pcr.get("HEAD_NUM") == 255 and pcr.get("SITE_NUM") == 0:
                            result["pcr"] = pcr

                # If MRR was successfully found via reverse lookup, return immediately!
                if result["mrr"] is not None:
                    return result

                # Fallback: sequential scan if reverse lookup did not locate MRR
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
                            break  # MRR is the final record; stop scanning!
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
                        break  # MRR is final record
                    elif rec_sub == 30:  # PCR
                        pcr = reader.parse_pcr_compact(data, start=start, end=end)
                        if pcr and pcr.get("HEAD_NUM") == 255 and pcr.get("SITE_NUM") == 0:
                            result["pcr"] = pcr
    except Exception:
        pass
    return result


# -- Parallel-parsing worker (module-level so it is picklable for spawn) -------
# ProcessPoolExecutor on Windows uses the 'spawn' start method, which pickles
# the target callable BY REFERENCE (module + qualname) and re-imports its
# module in each child. Keeping this worker at module scope in stdf_parser
# (which has NO mask_wxy_map.json dependency at import time) means a spawned
# child only needs to import this lightweight parser module -- it will NOT try
# to load guid_analysis / the JSON, so worker startup can never fail on a
# missing/relocated JSON inside a Nuitka onefile bundle.
def _parse_stdf_worker(task):
    """Parse one STDF file in a child process.

    task = (input_path, filter_list) where filter_list is a tuple of ints or
    None (tuple instead of a set purely for stable, compact pickling).
    Returns (input_path, parsed_dict). Because the WXY analyze path filters to
    just 3 test numbers, `parsed` is tiny, so the inter-process transfer cost is
    negligible relative to the parse itself.
    """
    input_path, filter_list = task
    filter_tests = set(filter_list) if filter_list is not None else None
    parsed, _, _ = parse_stdf_file(
        input_path, filter_tests=filter_tests, verbose=False,
        logger=None, progress_callback=None,
    )
    return input_path, parsed
