"""
STDF (Standard Test Data Format) Parser Module
===============================================
Handles the binary parsing of STDF files and scanning of tests.
"""

import gzip
import logging
import mmap
import os
import re
import shutil
import struct
import tempfile
import zlib
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# Precompiled little/big-endian header unpackers used before FAR endianness is known.
_HEADER_LEN = struct.Struct("<H").unpack_from
_HEADER_LEN_BE = struct.Struct(">H").unpack_from
_ASCII_BYTES = bytes

STDF_UNSET_U4 = 0xFFFFFFFF

RECORD_TYPES = {
    (0, 10): "FAR",
    (1, 10): "MIR",
    (1, 20): "MRR",
    (1, 40): "HBR",
    (1, 50): "SBR",
    (1, 80): "SDR",
    (5, 10): "PIR",
    (5, 20): "PRR",
    (15, 10): "PTR",
}

_KNOWN_RECORDS = frozenset(
    {
        (0, 10), (0, 20),
        (1, 10), (1, 20), (1, 30), (1, 40), (1, 50), (1, 60), (1, 62), (1, 63), (1, 70), (1, 80),
        (2, 10), (2, 20), (2, 30),
        (5, 10), (5, 20),
        (10, 30),
        (15, 10), (15, 15), (15, 20),
        (20, 10), (20, 20),
        (50, 10), (50, 30),
    }
)

_TEST_TOKEN_RE = re.compile(r"^(\d+)(?:_|$)")

BASE_METADATA_FIELD_MAP = [
    ("Source File", "SOURCE_FILE"),
    ("Setup Time", "SETUP_T_TEXT"),
    ("Start Time", "START_T_TEXT"),
    ("Finish Time", "FINISH_T_TEXT"),
    ("Lot ID", "LOT_ID"),
    ("Product", "PART_TYP"),
    ("Program", "JOB_NAM"),
    ("Program Revision", "JOB_REV"),
    ("Test Code", "TEST_COD"),
    ("Operation Frequency", "OPER_FRQ"),
    ("Temperature", "TST_TEMP"),
    ("Spec Name", "SPEC_NAM"),
    ("Operator", "OPER_NAM"),
    ("Tester Name", "NODE_NAM"),
    ("Tester Type", "TSTR_TYP"),
    ("Handler ID", "HAND_ID"),
    ("Handler Type", "HAND_TYP"),
    ("Site Count", "SITE_CNT_TEXT"),
    ("LB ID", "LOAD_ID"),
    ("DIB ID", "DIB_ID"),
    ("Exec Type", "EXEC_TYP"),
    ("Exec Version", "EXEC_VER"),
]

SDR_METADATA_FIELD_MAP = [
    ("Handler ID", "HAND_ID"),
    ("Handler Type", "HAND_TYP"),
    ("Site Count", "SITE_CNT_TEXT"),
    ("LB ID", "LOAD_ID"),
    ("DIB ID", "DIB_ID"),
]

LogFunc = Optional[Callable[[str], None]]
ProgressFunc = Optional[Callable[[float, str], None]]
ByteProgressFunc = Optional[Callable[[int, int], None]]

# Per-drive-letter cache of "is this a remote drive?" so GetDriveTypeW is called
# at most once per drive per process.
_DRIVE_REMOTE_CACHE: Dict[str, bool] = {}


def _is_network_path(filepath: str) -> bool:
    """Return True if filepath resides on a UNC share or mapped remote drive."""
    if not filepath:
        return False
    norm = filepath.replace("/", "\\")
    if norm.startswith("\\\\"):
        return True
    if os.name == "nt":
        drive = os.path.splitdrive(os.path.abspath(filepath))[0]
        if drive:
            drive_key = drive.upper()
            cached = _DRIVE_REMOTE_CACHE.get(drive_key)
            if cached is not None:
                return cached
            is_remote = False
            try:
                import ctypes

                DRIVE_REMOTE = 4
                dtype = ctypes.windll.kernel32.GetDriveTypeW(drive + "\\")
                is_remote = dtype == DRIVE_REMOTE
                _DRIVE_REMOTE_CACHE[drive_key] = is_remote
            except Exception as exc:
                logging.warning("Failed to query drive type for '%s': %s", filepath, exc)
            return is_remote
    return False


class STDFReader:
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
        self._os_fd = None
        self._win_handle = None
        self._temp_local_path = None
        self._cn_cache: Dict[bytes, str] = {}
        # Endian-dependent compiled struct formats, refreshed once FAR is read.
        self._prepare_endian_formats()

    @property
    def buffer(self):
        return self._mm

    def _prepare_endian_formats(self):
        """Compile the endian-dependent struct formats once endianness is known."""
        e = self.endian
        self._s_H = struct.Struct(e + "H")
        self._s_I = struct.Struct(e + "I")
        self._s_f = struct.Struct(e + "f")
        self._s_ff = struct.Struct(e + "ff")
        self._s_HH = struct.Struct(e + "HH")
        self._s_HI = struct.Struct(e + "HI")
        self._s_hdr = struct.Struct(e + "HBB")
        self._s_ptr_head = struct.Struct(e + "IBBxxf")

        self._u2_unpack = self._s_H.unpack_from
        self._u4_unpack = self._s_I.unpack_from
        self._u4_at = self._s_I.unpack_from
        self._f4_unpack = self._s_f.unpack_from
        self._ff_unpack = self._s_ff.unpack_from
        self._hh_unpack = self._s_HH.unpack_from
        self._hi_unpack = self._s_HI.unpack_from
        self._hdr_unpack = self._s_hdr.unpack_from
        self._ptr_head_unpack = self._s_ptr_head.unpack_from

    def _detect_and_apply_far_endian(self, data, offset: int = 0, limit: Optional[int] = None) -> Tuple[bool, int]:
        """Inspect a FAR (0, 10) record at offset, set endianness, and return (is_far, next_offset)."""
        limit = self._limit_for(data, limit)
        if offset + 4 > limit:
            return False, offset
        rec_typ = data[offset + 2]
        rec_sub = data[offset + 3]
        if rec_typ != 0 or rec_sub != 10:
            return False, offset
        len_le = _HEADER_LEN(data, offset)[0]
        len_be = _HEADER_LEN_BE(data, offset)[0]
        far_len = 2 if (len_le == 2 or len_be == 2) else min(len_le, len_be)
        body_start = offset + 4
        body_end = body_start + far_len
        if body_end > limit:
            return True, limit
        if far_len >= 1:
            cpu_typ = data[body_start]
            if cpu_typ == 1 or (len_be == 2 and len_le != 2 and cpu_typ != 2 and cpu_typ != 0):
                self.endian = ">"
            else:
                self.endian = "<"
            self._prepare_endian_formats()
        return True, body_end

    def _load_gzip(self, path: str):
        """Transparent gzip (.gz) support - decompress into memory."""
        try:
            with gzip.open(path, "rb") as gz_file:
                data = gz_file.read()
            self._mm = data
            self.file_size = len(data)
        except (gzip.BadGzipFile, EOFError, zlib.error, OSError):
            self._mm = b""
            self.file_size = 0
        return self

    def _release_os_handles(self):
        """Close mmap / fd / file handles opened by __enter__ (staging copy kept)."""
        if self._mm is not None and hasattr(self._mm, "close"):
            try:
                self._mm.close()
            except Exception as exc:
                logging.warning("Failed to close mmap for '%s': %s", self.filepath, exc)
        self._mm = None
        if self._os_fd is not None:
            try:
                os.close(self._os_fd)
            except OSError as exc:
                logging.warning("Failed to close file descriptor for '%s': %s", self.filepath, exc)
            self._os_fd = None
            self._win_handle = None
        if self._file is not None:
            try:
                self._file.close()
            except OSError as exc:
                logging.warning("Failed to close file handle for '%s': %s", self.filepath, exc)
            self._file = None

    def __enter__(self):
        self._file = None
        self._os_fd = None
        self._win_handle = None
        self._temp_local_path = None
        self._mm = None
        self._last_progress_bytes = -1
        self._cn_cache = {}
        self.endian = "<"
        self._prepare_endian_formats()

        target_path = self.filepath
        is_remote = bool(target_path) and _is_network_path(target_path)

        # Guard mmap against network shares by staging to a local temporary file first
        if is_remote and os.path.isfile(target_path):
            try:
                suffix = ".gz" if target_path.lower().endswith(".gz") else ".stdf"
                tmp_fd, tmp_path = tempfile.mkstemp(prefix="stdf2sum_", suffix=suffix)
                os.close(tmp_fd)
                shutil.copy2(target_path, tmp_path)
                self._temp_local_path = tmp_path
                target_path = tmp_path
            except Exception:
                self._temp_local_path = None

        if target_path and str(target_path).lower().endswith(".gz") and os.path.isfile(target_path):
            return self._load_gzip(target_path)

        allow_mmap = (not is_remote) or (self._temp_local_path is not None)

        if os.name == "nt" and allow_mmap:
            try:
                import ctypes
                import msvcrt

                GENERIC_READ = 0x80000000
                FILE_SHARE_READ = 0x00000001
                OPEN_EXISTING = 3
                FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000

                handle = ctypes.windll.kernel32.CreateFileW(
                    os.path.abspath(target_path),
                    GENERIC_READ,
                    FILE_SHARE_READ,
                    None,
                    OPEN_EXISTING,
                    FILE_FLAG_SEQUENTIAL_SCAN,
                    None,
                )
                if handle != -1 and handle != 0:
                    self._win_handle = handle
                    self._os_fd = msvcrt.open_osfhandle(handle, 0)
            except Exception:
                self._win_handle = None
                self._os_fd = None

        if self._os_fd is not None:
            try:
                self.file_size = os.fstat(self._os_fd).st_size
            except OSError:
                self.file_size = 0
            if self.file_size > 0 and allow_mmap:
                try:
                    self._mm = mmap.mmap(self._os_fd, 0, access=mmap.ACCESS_READ)
                except (ValueError, OSError):
                    self._mm = None
            if self._mm is None and self.file_size > 0:
                self._file = open(target_path, "rb")
        else:
            self._file = open(target_path, "rb")
            try:
                self.file_size = os.fstat(self._file.fileno()).st_size
            except OSError:
                self.file_size = 0
            if self.file_size > 0 and allow_mmap:
                try:
                    self._mm = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
                except (ValueError, OSError):
                    self._mm = None

        # Gzip magic-byte detection for files without a '.gz' extension
        if self.file_size >= 2:
            magic = b""
            if self._mm is not None:
                magic = bytes(self._mm[:2])
            elif self._file is not None:
                try:
                    magic = self._file.read(2)
                    self._file.seek(0)
                except OSError:
                    magic = b""
            if magic == b"\x1f\x8b":
                self._release_os_handles()
                return self._load_gzip(target_path)

        return self

    def __exit__(self, *args):
        self._release_os_handles()
        if self._temp_local_path:
            try:
                os.remove(self._temp_local_path)
            except OSError as exc:
                logging.warning("Failed to remove temporary STDF copy '%s': %s", self._temp_local_path, exc)
            self._temp_local_path = None

    def _report_progress(self, current_bytes: int):
        if not self.progress_callback:
            return
        total = self.file_size
        if total <= 0:
            self.progress_callback(current_bytes, total)
            return
        current_bytes = max(0, min(current_bytes, total))
        if current_bytes != total and self._last_progress_bytes >= 0 and current_bytes - self._last_progress_bytes < self._progress_step:
            return
        self._last_progress_bytes = current_bytes
        self.progress_callback(current_bytes, total)

    def _limit_for(self, data, limit=None):
        return len(data) if limit is None else limit

    def _read_u1_at(self, data, offset, limit=None):
        limit = self._limit_for(data, limit)
        if offset + 1 > limit:
            raise IndexError
        return data[offset], offset + 1

    def _read_u2_at(self, data, offset, limit=None):
        limit = self._limit_for(data, limit)
        if offset + 2 > limit:
            raise IndexError
        return self._u2_unpack(data, offset)[0], offset + 2

    def _read_u4_at(self, data, offset, limit=None):
        limit = self._limit_for(data, limit)
        if offset + 4 > limit:
            raise IndexError
        return self._u4_unpack(data, offset)[0], offset + 4

    def _read_c1_at(self, data, offset, limit=None):
        limit = self._limit_for(data, limit)
        if offset + 1 > limit:
            raise IndexError
        return bytes(data[offset : offset + 1]).decode("latin-1", errors="replace"), offset + 1

    def _read_cn_at(self, data, offset, limit=None):
        limit = self._limit_for(data, limit)
        if offset + 1 > limit:
            raise IndexError
        length = data[offset]
        offset += 1
        if offset + length > limit:
            raise IndexError
        raw = bytes(data[offset : offset + length])
        cached = self._cn_cache.get(raw)
        if cached is None:
            cached = raw.decode("latin-1", errors="replace")
            self._cn_cache[raw] = cached
        return cached, offset + length

    def record_spans(self):
        if self._mm is None:
            f, first = self._file, True
            if f is None:
                return
            _progress_step = self._progress_step
            _last_pb = -1
            _hdr_unpack = self._hdr_unpack
            _filter_tests = self.filter_tests
            _unpack_u4 = self._u4_unpack
            while True:
                header = f.read(4)
                if len(header) < 4:
                    break
                rec_typ, rec_sub = header[2], header[3]
                if first:
                    first = False
                    if (rec_typ, rec_sub) == (0, 10):
                        len_le = _HEADER_LEN(header, 0)[0]
                        len_be = _HEADER_LEN_BE(header, 0)[0]
                        rec_len = 2 if (len_le == 2 or len_be == 2) else min(len_le, len_be)
                        body = f.read(rec_len)
                        if len(body) < rec_len:
                            break
                        if len(body) >= 1 and (body[0] == 1 or (len_be == 2 and len_le != 2 and body[0] not in (0, 2))):
                            self.endian = ">"
                            self._prepare_endian_formats()
                            _hdr_unpack = self._hdr_unpack
                            _unpack_u4 = self._u4_unpack
                        pos = f.tell()
                        if pos - _last_pb >= _progress_step:
                            _last_pb = pos
                            self._report_progress(pos)
                        yield rec_typ, rec_sub, body, 0, len(body)
                        continue
                rec_len = self._u2_unpack(header, 0)[0]
                body = f.read(rec_len)
                if len(body) < rec_len:
                    break
                pos = f.tell()
                if pos - _last_pb >= _progress_step:
                    _last_pb = pos
                    self._report_progress(pos)
                if rec_typ == 15 and rec_sub == 10 and _filter_tests is not None and rec_len >= 4:
                    if _unpack_u4(body, 0)[0] not in _filter_tests:
                        yield rec_typ, rec_sub, None, 0, 0
                        continue
                yield rec_typ, rec_sub, body, 0, len(body)
            self._report_progress(self.file_size)
            return

        mm = self._mm
        file_size = self.file_size
        offset = 0
        _progress_step = self._progress_step
        _last_pb = -1
        _filter_tests = self.filter_tests

        if file_size >= 4 and mm[2] == 0 and mm[3] == 10:
            is_far, next_off = self._detect_and_apply_far_endian(mm, 0, file_size)
            if is_far:
                if next_off <= 4:
                    self._report_progress(file_size)
                    return
                offset = next_off
                if offset - _last_pb >= _progress_step:
                    _last_pb = offset
                    self._report_progress(offset)
                yield 0, 10, mm, 4, next_off

        _hdr_unpack = self._hdr_unpack
        _unpack_u4 = self._u4_unpack
        while offset + 4 <= file_size:
            rec_len, rec_typ, rec_sub = _hdr_unpack(mm, offset)
            body_start = offset + 4
            body_end = body_start + rec_len
            if body_end > file_size:
                break
            if rec_typ == 15 and rec_sub == 10 and _filter_tests is not None:
                if rec_len >= 4:
                    test_num = _unpack_u4(mm, body_start)[0]
                    if test_num not in _filter_tests:
                        offset = body_end
                        if offset - _last_pb >= _progress_step:
                            _last_pb = offset
                            self._report_progress(offset)
                        yield rec_typ, rec_sub, None, 0, 0
                        continue
            offset = body_end
            if offset - _last_pb >= _progress_step:
                _last_pb = offset
                self._report_progress(offset)
            yield rec_typ, rec_sub, mm, body_start, body_end
        self._report_progress(file_size)

    def records(self):
        for rec_typ, rec_sub, data, start, end in self.record_spans():
            if data is None:
                yield rec_typ, rec_sub, None
            elif start == 0 and end == len(data):
                yield rec_typ, rec_sub, data
            else:
                yield rec_typ, rec_sub, bytes(data[start:end])

    def parse_mir_compact(self, data, start: int = 0, end: Optional[int] = None):
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
            fields["LOT_ID"], offset = self._read_cn_at(data, offset, end)
            fields["PART_TYP"], offset = self._read_cn_at(data, offset, end)
            fields["NODE_NAM"], offset = self._read_cn_at(data, offset, end)
            fields["TSTR_TYP"], offset = self._read_cn_at(data, offset, end)
            fields["JOB_NAM"], offset = self._read_cn_at(data, offset, end)
            fields["JOB_REV"], offset = self._read_cn_at(data, offset, end)
            fields["SBLOT_ID"], offset = self._read_cn_at(data, offset, end)
            fields["OPER_NAM"], offset = self._read_cn_at(data, offset, end)
            fields["EXEC_TYP"], offset = self._read_cn_at(data, offset, end)
            fields["EXEC_VER"], offset = self._read_cn_at(data, offset, end)
            fields["TEST_COD"], offset = self._read_cn_at(data, offset, end)
            fields["TST_TEMP"], offset = self._read_cn_at(data, offset, end)
            fields["USER_TXT"], offset = self._read_cn_at(data, offset, end)
            fields["AUX_FILE"], offset = self._read_cn_at(data, offset, end)
            fields["PKG_TYP"], offset = self._read_cn_at(data, offset, end)
            fields["FAMLY_ID"], offset = self._read_cn_at(data, offset, end)
            fields["DATE_COD"], offset = self._read_cn_at(data, offset, end)
            fields["FACIL_ID"], offset = self._read_cn_at(data, offset, end)
            fields["FLOOR_ID"], offset = self._read_cn_at(data, offset, end)
            fields["PROC_ID"], offset = self._read_cn_at(data, offset, end)
            fields["OPER_FRQ"], offset = self._read_cn_at(data, offset, end)
            fields["SPEC_NAM"], offset = self._read_cn_at(data, offset, end)
            fields["SPEC_VER"], offset = self._read_cn_at(data, offset, end)
            fields["FLOW_ID"], offset = self._read_cn_at(data, offset, end)
            fields["SETUP_ID"], offset = self._read_cn_at(data, offset, end)
            fields["DSGN_REV"], offset = self._read_cn_at(data, offset, end)
            fields["ENG_ID"], offset = self._read_cn_at(data, offset, end)
            fields["ROM_COD"], offset = self._read_cn_at(data, offset, end)
            fields["SERL_NUM"], offset = self._read_cn_at(data, offset, end)
            fields["SUPR_NAM"], offset = self._read_cn_at(data, offset, end)
        except (IndexError, struct.error):
            pass
        if fields.get("SETUP_T") not in (None, ""):
            fields["SETUP_T_TEXT"] = _format_stdf_timestamp(fields.get("SETUP_T"))
        if fields.get("START_T") not in (None, ""):
            fields["START_T_TEXT"] = _format_stdf_timestamp(fields.get("START_T"))
        return fields or None

    def parse_mrr_compact(self, data, start: int = 0, end: Optional[int] = None):
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

    def parse_sdr_compact(self, data, start: int = 0, end: Optional[int] = None):
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
            fields["SITE_NUM_LIST_TEXT"] = ", ".join(str(site_num) for site_num in site_numbers)
            fields["HAND_TYP"], offset = self._read_cn_at(data, offset, end)
            fields["HAND_ID"], offset = self._read_cn_at(data, offset, end)
            fields["CARD_TYP"], offset = self._read_cn_at(data, offset, end)
            fields["CARD_ID"], offset = self._read_cn_at(data, offset, end)
            fields["LOAD_TYP"], offset = self._read_cn_at(data, offset, end)
            fields["LOAD_ID"], offset = self._read_cn_at(data, offset, end)
            fields["DIB_TYP"], offset = self._read_cn_at(data, offset, end)
            fields["DIB_ID"], offset = self._read_cn_at(data, offset, end)
            fields["CABL_TYP"], offset = self._read_cn_at(data, offset, end)
            fields["CABL_ID"], offset = self._read_cn_at(data, offset, end)
            fields["CONT_TYP"], offset = self._read_cn_at(data, offset, end)
            fields["CONT_ID"], offset = self._read_cn_at(data, offset, end)
            fields["LASR_TYP"], offset = self._read_cn_at(data, offset, end)
            fields["LASR_ID"], offset = self._read_cn_at(data, offset, end)
            fields["EXTR_TYP"], offset = self._read_cn_at(data, offset, end)
            fields["EXTR_ID"], offset = self._read_cn_at(data, offset, end)
        except (IndexError, struct.error):
            pass
        return fields or None

    def parse_pir_compact(self, data, start: int = 0, end: Optional[int] = None):
        end = self._limit_for(data, end)
        if start + 2 > end:
            return None
        return {"HEAD_NUM": data[start], "SITE_NUM": data[start + 1]}

    def parse_prr_compact(self, data, start: int = 0, end: Optional[int] = None):
        end = self._limit_for(data, end)
        if start + 18 > end:
            return None
        try:
            head_num = data[start]
            site_num = data[start + 1]
            part_flg = data[start + 2]
            hard_bin, soft_bin = self._hh_unpack(data, start + 5)
            test_t = self._u4_unpack(data, start + 13)[0]
            offset = start + 17
            part_id_len = data[offset]
            offset += 1
            if offset + part_id_len > end:
                raise IndexError
            part_id = bytes(data[offset : offset + part_id_len]).decode("latin-1", errors="replace")
        except (IndexError, struct.error):
            return None
        return {
            "HEAD_NUM": head_num,
            "SITE_NUM": site_num,
            "PART_FLG": part_flg,
            "HARD_BIN": hard_bin,
            "SOFT_BIN": soft_bin,
            "TEST_T": test_t,
            "PART_ID": part_id,
        }

    def parse_ptr_compact(
        self,
        data,
        include_meta: bool = False,
        start: int = 0,
        end: Optional[int] = None,
    ):
        end = self._limit_for(data, end)
        if start + 12 > end:
            return None
        try:
            test_num, head_num, site_num, result = self._ptr_head_unpack(data, start)
        except (IndexError, struct.error):
            return None
        fields = {
            "TEST_NUM": test_num,
            "HEAD_NUM": head_num,
            "SITE_NUM": site_num,
            "RESULT": result,
        }
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
            raw_txt = bytes(data[offset : offset + test_txt_len])
            test_txt = self._cn_cache.get(raw_txt)
            if test_txt is None:
                test_txt = raw_txt.decode("latin-1", errors="replace")
                self._cn_cache[raw_txt] = test_txt
            offset += test_txt_len
            if offset >= end:
                raise IndexError
            alarm_id_len = data[offset]
            offset += 1 + alarm_id_len
            if offset + 4 > end:
                raise IndexError
            offset += 4
            if offset + 8 <= end:
                lo_limit, hi_limit = self._ff_unpack(data, offset)
                offset += 8
            elif offset + 4 <= end:
                lo_limit = self._f4_unpack(data, offset)[0]
                offset += 4
            if offset < end:
                units_len = data[offset]
                offset += 1
                if offset + units_len > end:
                    raise IndexError
                raw_units = bytes(data[offset : offset + units_len])
                units = self._cn_cache.get(raw_units)
                if units is None:
                    units = raw_units.decode("latin-1", errors="replace")
                    self._cn_cache[raw_units] = units
        except (IndexError, struct.error):
            test_txt = test_txt or ""
            units = units or ""
        fields.update(
            {
                "TEST_TXT": test_txt,
                "LO_LIMIT": lo_limit,
                "HI_LIMIT": hi_limit,
                "UNITS": units,
            }
        )
        return fields

    def parse_hbr_compact(self, data, start: int = 0, end: Optional[int] = None):
        end = self._limit_for(data, end)
        if start + 8 > end:
            return None
        try:
            head_num = data[start]
            site_num = data[start + 1]
            hbin_num, hbin_cnt = self._hi_unpack(data, start + 2)
            hbin_pf = " "
            hbin_nam = ""
            if start + 9 <= end:
                hbin_pf = bytes(data[start + 8 : start + 9]).decode("latin-1", errors="replace")
                offset = start + 9
                if offset < end:
                    hbin_nam, offset = self._read_cn_at(data, offset, end)
        except (IndexError, struct.error):
            return None
        return {
            "HEAD_NUM": head_num,
            "SITE_NUM": site_num,
            "HBIN_NUM": hbin_num,
            "HBIN_CNT": hbin_cnt,
            "HBIN_PF": hbin_pf,
            "HBIN_NAM": hbin_nam,
        }

    def parse_sbr_compact(self, data, start: int = 0, end: Optional[int] = None):
        end = self._limit_for(data, end)
        if start + 8 > end:
            return None
        try:
            head_num = data[start]
            site_num = data[start + 1]
            sbin_num, sbin_cnt = self._hi_unpack(data, start + 2)
            sbin_pf = " "
            sbin_nam = ""
            if start + 9 <= end:
                sbin_pf = bytes(data[start + 8 : start + 9]).decode("latin-1", errors="replace")
                offset = start + 9
                if offset < end:
                    sbin_nam, offset = self._read_cn_at(data, offset, end)
        except (IndexError, struct.error):
            return None
        return {
            "HEAD_NUM": head_num,
            "SITE_NUM": site_num,
            "SBIN_NUM": sbin_num,
            "SBIN_CNT": sbin_cnt,
            "SBIN_PF": sbin_pf,
            "SBIN_NAM": sbin_nam,
        }


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


def _prepare_scanned_test_row(test_row: Dict) -> Dict:
    lo_text = _format_limit_text(test_row.get("LO_LIMIT"))
    hi_text = _format_limit_text(test_row.get("HI_LIMIT"))
    search_text = " ".join(
        [
            str(test_row.get("TEST_NUM", "")),
            str(test_row.get("TEST_TXT", "")),
            str(test_row.get("UNITS", "")),
            lo_text,
            hi_text,
        ]
    ).lower()
    test_row["_LO_LIMIT_TEXT"] = lo_text
    test_row["_HI_LIMIT_TEXT"] = hi_text
    test_row["_SEARCH_TEXT"] = search_text
    return test_row


def _find_record_stream_sync(mm, search_start: int, file_size: int, s_hdr_unpack) -> Optional[int]:
    """Find the first valid STDF record boundary at or after search_start by verifying
    a chain of consecutive known STDF record headers."""
    max_probe = min(file_size - 4, search_start + 65536)
    known = _KNOWN_RECORDS
    for cand in range(search_start, max_probe + 1):
        pos = cand
        steps = 0
        valid = True
        while pos + 4 <= file_size and steps < 10:
            rlen, rtyp, rsub = s_hdr_unpack(mm, pos)
            if (rtyp, rsub) not in known:
                valid = False
                break
            if rtyp == 0 and rsub == 10 and pos != 0:
                valid = False
                break
            nxt = pos + 4 + rlen
            if nxt > file_size:
                valid = False
                break
            pos = nxt
            steps += 1
            if pos == file_size:
                break
        if valid and (steps >= 8 or pos == file_size):
            return cand
    return None


def scan_test_list(
    filepath: str,
    progress_callback: ByteProgressFunc = None,
    full_scan: bool = False,
    stable_parts: int = 5,
) -> List[Dict]:
    """Return the unique PTR test list (TEST_NUM/TXT/limits/units) for a file.

    For files > 16 MB (unless full_scan=True), stops head scanning after
    consecutive stable good/fail parts and jumps to the 2 MB tail window + TSR
    (10, 30) synopsis records to verify no middle-only tests were missed.
    """
    tests: Dict[int, Dict] = {}
    with STDFReader(filepath, progress_callback=progress_callback) as parser:
        mm = parser._mm
        if mm is None:
            for rec_typ, rec_sub, data, start, end in parser.record_spans():
                if data is None:
                    continue
                if rec_typ == 15 and rec_sub == 10 and end - start >= 4:
                    test_num = parser._u4_unpack(data, start)[0]
                    if test_num not in tests:
                        fields = parser.parse_ptr_compact(data, include_meta=True, start=start, end=end) or {}
                        tests[test_num] = {
                            "TEST_NUM": test_num,
                            "TEST_TXT": fields.get("TEST_TXT", ""),
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
        if file_size >= 4 and mm[2] == 0 and mm[3] == 10:
            _, offset = parser._detect_and_apply_far_endian(mm, 0, file_size)

        s_I_unpack = parser._u4_unpack
        s_hdr_unpack = parser._hdr_unpack
        parse_ptr = parser.parse_ptr_compact
        next_p = offset + p_step

        LARGE_FILE_THRESHOLD = 16 * 1024 * 1024
        use_early_exit = (not full_scan) and (file_size > LARGE_FILE_THRESHOLD)
        min_good_stable = max(stable_parts, 5)

        stable_good_run = 0
        stable_fail_run = 0
        fail_parts_seen = 0
        count_at_part_start = 0
        stopped_early = False
        head_stop_offset = 0

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
                            "TEST_NUM": test_num,
                            "TEST_TXT": fields.get("TEST_TXT", ""),
                            "LO_LIMIT": fields.get("LO_LIMIT", float("nan")),
                            "HI_LIMIT": fields.get("HI_LIMIT", float("nan")),
                            "UNITS": fields.get("UNITS", ""),
                        }
            elif rec_typ == 10 and rec_sub == 30:  # TSR
                if rec_len >= 20:
                    t_typ = mm[body_start + 2]
                    t_num = s_I_unpack(mm, body_start + 3)[0]
                    exec_cnt = s_I_unpack(mm, body_start + 7)[0]
                    if exec_cnt > 0 and exec_cnt != STDF_UNSET_U4 and t_typ in (ord("P"), ord("M"), ord(" ")):
                        tnam_len = mm[body_start + 19]
                        tnam = ""
                        if body_start + 20 + tnam_len <= body_end:
                            tnam = bytes(mm[body_start + 20 : body_start + 20 + tnam_len]).decode("latin-1", errors="replace")
                        if t_num not in tests:
                            tests[t_num] = {
                                "TEST_NUM": t_num,
                                "TEST_TXT": tnam,
                                "LO_LIMIT": float("nan"),
                                "HI_LIMIT": float("nan"),
                                "UNITS": "",
                            }
                        elif tnam and not tests[t_num].get("TEST_TXT"):
                            tests[t_num]["TEST_TXT"] = tnam
            elif rec_typ == 5:
                if rec_sub == 10:
                    count_at_part_start = len(tests)
                elif rec_sub == 20 and use_early_exit and tests and rec_len >= 3:
                    part_flg = mm[body_start + 2]
                    is_good = not (part_flg & 0x08)
                    added_new = len(tests) > count_at_part_start
                    count_at_part_start = len(tests)

                    if is_good:
                        stable_good_run = 0 if added_new else (stable_good_run + 1)
                    else:
                        fail_parts_seen += 1
                        stable_fail_run = 0 if added_new else (stable_fail_run + 1)

                    if stable_good_run >= min_good_stable and (fail_parts_seen == 0 or stable_fail_run >= 2):
                        stopped_early = True
                        head_stop_offset = body_end
                        break

            offset = body_end
            if has_cb and offset >= next_p:
                report(offset)
                next_p = offset + p_step

        if stopped_early and head_stop_offset < file_size:
            tail_window = 2 * 1024 * 1024
            tail_search_start = max(head_stop_offset, file_size - tail_window)
            sync_off = (
                head_stop_offset
                if tail_search_start == head_stop_offset
                else _find_record_stream_sync(mm, tail_search_start, file_size, s_hdr_unpack)
            )
            saw_tsr = False
            missing_from_head: Set[int] = set()

            if sync_off is not None:
                t_off = sync_off
                while t_off + 4 <= file_size:
                    rec_len, rec_typ, rec_sub = s_hdr_unpack(mm, t_off)
                    b_start = t_off + 4
                    b_end = b_start + rec_len
                    if b_end > file_size:
                        break
                    if rec_typ == 15 and rec_sub == 10 and rec_len >= 4:
                        test_num = s_I_unpack(mm, b_start)[0]
                        if test_num not in tests or test_num in missing_from_head:
                            fields = parse_ptr(mm, include_meta=True, start=b_start, end=b_end) or {}
                            tests[test_num] = {
                                "TEST_NUM": test_num,
                                "TEST_TXT": fields.get("TEST_TXT", ""),
                                "LO_LIMIT": fields.get("LO_LIMIT", float("nan")),
                                "HI_LIMIT": fields.get("HI_LIMIT", float("nan")),
                                "UNITS": fields.get("UNITS", ""),
                            }
                            missing_from_head.discard(test_num)
                    elif rec_typ == 10 and rec_sub == 30 and rec_len >= 20:
                        saw_tsr = True
                        t_typ = mm[b_start + 2]
                        t_num = s_I_unpack(mm, b_start + 3)[0]
                        exec_cnt = s_I_unpack(mm, b_start + 7)[0]
                        if exec_cnt > 0 and exec_cnt != STDF_UNSET_U4 and t_typ in (ord("P"), ord("M"), ord(" ")):
                            tnam_len = mm[b_start + 19]
                            tnam = ""
                            if b_start + 20 + tnam_len <= b_end:
                                tnam = bytes(mm[b_start + 20 : b_start + 20 + tnam_len]).decode("latin-1", errors="replace")
                            if t_num not in tests:
                                tests[t_num] = {
                                    "TEST_NUM": t_num,
                                    "TEST_TXT": tnam,
                                    "LO_LIMIT": float("nan"),
                                    "HI_LIMIT": float("nan"),
                                    "UNITS": "",
                                }
                                missing_from_head.add(t_num)
                            elif tnam and not tests[t_num].get("TEST_TXT"):
                                tests[t_num]["TEST_TXT"] = tnam
                    t_off = b_end

            if not saw_tsr or missing_from_head:
                m_off = head_stop_offset
                m_limit = sync_off if (sync_off is not None and saw_tsr) else file_size
                while m_off + 4 <= m_limit and (not saw_tsr or missing_from_head):
                    rec_len, rec_typ, rec_sub = s_hdr_unpack(mm, m_off)
                    b_start = m_off + 4
                    b_end = b_start + rec_len
                    if b_end > file_size:
                        break
                    if rec_typ == 15 and rec_sub == 10 and rec_len >= 4:
                        test_num = s_I_unpack(mm, b_start)[0]
                        if test_num not in tests or test_num in missing_from_head:
                            fields = parse_ptr(mm, include_meta=True, start=b_start, end=b_end) or {}
                            prev_txt = tests[test_num].get("TEST_TXT", "") if test_num in tests else ""
                            tests[test_num] = {
                                "TEST_NUM": test_num,
                                "TEST_TXT": fields.get("TEST_TXT", "") or prev_txt,
                                "LO_LIMIT": fields.get("LO_LIMIT", float("nan")),
                                "HI_LIMIT": fields.get("HI_LIMIT", float("nan")),
                                "UNITS": fields.get("UNITS", ""),
                            }
                            missing_from_head.discard(test_num)
                    m_off = b_end

        if has_cb:
            report(file_size)

    return [_prepare_scanned_test_row(row) for row in sorted(tests.values(), key=lambda r: r["TEST_NUM"])]


def _emit_log(message: str, verbose: bool, logger: LogFunc):
    if logger:
        logger(message)
    elif verbose:
        print(message)


def _safe_filename_stem(file_path: str) -> str:
    stem = os.path.splitext(os.path.basename(file_path))[0].strip()
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in stem)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "stdf"


def build_default_output_prefix(input_paths: List[str]) -> str:
    if not input_paths:
        return "combined_stdf"
    if len(input_paths) == 1:
        first_dir = os.path.dirname(input_paths[0]) or "."
        return os.path.join(first_dir, _safe_filename_stem(input_paths[0]))
    first_dir = os.path.dirname(input_paths[0]) or "."
    stems = [_safe_filename_stem(path) for path in input_paths]
    if len(stems) == 2:
        combined_name = f"{stems[0]}_{stems[1]}"
    else:
        combined_name = f"{stems[0]}_{stems[1]}_etc"
    return os.path.join(first_dir, combined_name)


def parse_stdf_file(
    input_path: str,
    filter_tests: Optional[Set[int]] = None,
    verbose: bool = True,
    logger: LogFunc = None,
    progress_callback: ByteProgressFunc = None,
) -> Tuple[Dict[str, object], Dict[str, int], int]:
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
    hbr_records: List[Dict[str, object]] = []
    sbr_records: List[Dict[str, object]] = []
    open_slot: Dict[int, int] = {}

    with STDFReader(input_path, filter_tests=filter_tests, progress_callback=progress_callback) as reader:
        parse_mir = reader.parse_mir_compact
        parse_mrr = reader.parse_mrr_compact
        parse_sdr = reader.parse_sdr_compact
        parse_ptr = reader.parse_ptr_compact
        parse_prr = reader.parse_prr_compact
        parse_hbr = reader.parse_hbr_compact
        parse_sbr = reader.parse_sbr_compact
        open_slot_get = open_slot.get
        open_slot_pop = open_slot.pop
        parts_append = parts.append
        part_results_append = part_results.append
        sdr_append = sdr_records.append
        hbr_append = hbr_records.append
        sbr_append = sbr_records.append

        mm = reader._mm
        if mm is not None:
            file_size = reader.file_size
            has_cb = progress_callback is not None
            p_step = reader._progress_step
            report = reader._report_progress

            cnt_far = 0
            cnt_mir = 0
            cnt_mrr = 0
            cnt_hbr = 0
            cnt_sbr = 0
            cnt_sdr = 0
            cnt_pir = 0
            cnt_prr = 0
            cnt_ptr = 0

            offset = 0
            if file_size >= 4 and mm[2] == 0 and mm[3] == 10:
                is_far, next_off = reader._detect_and_apply_far_endian(mm, 0, file_size)
                if is_far:
                    cnt_far = 1
                    offset = next_off

            hdr_unpack = reader._hdr_unpack
            u4_unpack = reader._u4_unpack
            f4_unpack = reader._f4_unpack
            ptr_head_unpack = reader._ptr_head_unpack
            next_p = offset + p_step

            n_part_results = 0
            last_slot_key = -1
            last_part_res: Optional[Dict[int, float]] = None

            while offset + 4 <= file_size:
                rec_len, rec_typ, rec_sub = hdr_unpack(mm, offset)
                body_start = offset + 4
                body_end = body_start + rec_len
                if body_end > file_size:
                    break

                # PTR (15, 10) fast path first -- ~99% of all records
                if rec_typ == 15 and rec_sub == 10:
                    cnt_ptr += 1
                    if rec_len >= 12:
                        if filter_tests is not None:
                            test_num = u4_unpack(mm, body_start)[0]
                            if test_num not in filter_tests:
                                skipped_ptr += 1
                                offset = body_end
                                if has_cb and offset >= next_p:
                                    report(offset)
                                    next_p = offset + p_step
                                continue
                            head_num = mm[body_start + 4]
                            site_num = mm[body_start + 5]
                            result = f4_unpack(mm, body_start + 8)[0]
                        else:
                            test_num, head_num, site_num, result = ptr_head_unpack(mm, body_start)

                        slot_key = (head_num << 8) | site_num
                        if slot_key == last_slot_key and last_part_res is not None:
                            last_part_res[test_num] = result
                        else:
                            part_idx = open_slot_get(slot_key, -1)
                            if 0 <= part_idx < n_part_results:
                                res_dict = part_results[part_idx]
                                res_dict[test_num] = result
                                last_slot_key = slot_key
                                last_part_res = res_dict

                        if test_num not in test_meta:
                            meta_fields = parse_ptr(mm, include_meta=True, start=body_start, end=body_end)
                            if meta_fields:
                                test_meta[test_num] = {
                                    "TEST_TXT": meta_fields.get("TEST_TXT", ""),
                                    "LO_LIMIT": meta_fields.get("LO_LIMIT", float("nan")),
                                    "HI_LIMIT": meta_fields.get("HI_LIMIT", float("nan")),
                                    "UNITS": meta_fields.get("UNITS", ""),
                                }
                    offset = body_end
                    if has_cb and offset >= next_p:
                        report(offset)
                        next_p = offset + p_step
                    continue

                if rec_typ == 5:
                    if rec_sub == 10:  # PIR
                        cnt_pir += 1
                        if body_start + 2 <= body_end:
                            slot_key = (mm[body_start] << 8) | mm[body_start + 1]
                            part_idx = n_part_results
                            open_slot[slot_key] = part_idx
                            parts_append({"PART_IDX": part_idx, "HAS_PRR": False})
                            res_dict = {}
                            part_results_append(res_dict)
                            n_part_results += 1
                            last_slot_key = slot_key
                            last_part_res = res_dict
                    elif rec_sub == 20:  # PRR
                        cnt_prr += 1
                        fields = parse_prr(mm, start=body_start, end=body_end)
                        if fields:
                            slot_key = (fields["HEAD_NUM"] << 8) | fields["SITE_NUM"]
                            part_idx = open_slot_pop(slot_key, -1)
                            if slot_key == last_slot_key:
                                last_slot_key = -1
                                last_part_res = None
                            if part_idx < 0:
                                part_idx = n_part_results
                                parts_append({"PART_IDX": part_idx, "HAS_PRR": False})
                                part_results_append({})
                                n_part_results += 1
                            fields["PART_IDX"] = part_idx
                            fields["HAS_PRR"] = True
                            parts[part_idx].update(fields)
                elif rec_typ == 1:
                    if rec_sub == 10:  # MIR
                        cnt_mir += 1
                        if not mir_info:
                            fields = parse_mir(mm, start=body_start, end=body_end)
                            if fields:
                                mir_info.update(fields)
                    elif rec_sub == 20:  # MRR
                        cnt_mrr += 1
                        if not mrr_info:
                            fields = parse_mrr(mm, start=body_start, end=body_end)
                            if fields:
                                mrr_info.update(fields)
                    elif rec_sub == 40:  # HBR
                        cnt_hbr += 1
                        fields = parse_hbr(mm, start=body_start, end=body_end)
                        if fields:
                            hbr_append(fields)
                    elif rec_sub == 50:  # SBR
                        cnt_sbr += 1
                        fields = parse_sbr(mm, start=body_start, end=body_end)
                        if fields:
                            sbr_append(fields)
                    elif rec_sub == 80:  # SDR
                        cnt_sdr += 1
                        fields = parse_sdr(mm, start=body_start, end=body_end)
                        if fields:
                            sdr_append(fields)

                offset = body_end
                if has_cb and offset >= next_p:
                    report(offset)
                    next_p = offset + p_step

            if has_cb:
                report(file_size)

            for _name, _val in (
                ("FAR", cnt_far),
                ("MIR", cnt_mir),
                ("MRR", cnt_mrr),
                ("HBR", cnt_hbr),
                ("SBR", cnt_sbr),
                ("SDR", cnt_sdr),
                ("PIR", cnt_pir),
                ("PRR", cnt_prr),
                ("PTR", cnt_ptr),
            ):
                if _val:
                    counts[_name] = _val
        else:
            # Streaming fallback when mmap is unavailable
            record_iter = reader.record_spans()
            try:
                rec_typ, rec_sub, data, start, end = next(record_iter)
            except StopIteration:
                rec_typ = -1
            else:
                if rec_typ == 0 and rec_sub == 10:
                    counts["FAR"] += 1
            ptr_head_unpack = reader._ptr_head_unpack
            n_part_results = 0
            while rec_typ != -1:
                if rec_typ == 15 and rec_sub == 10:
                    counts["PTR"] += 1
                    if data is not None:
                        if end - start >= 12:
                            try:
                                test_num, head_num, site_num, result = ptr_head_unpack(data, start)
                            except (IndexError, struct.error):
                                pass
                            else:
                                part_idx = open_slot_get((head_num << 8) | site_num, -1)
                                if 0 <= part_idx < n_part_results:
                                    part_results[part_idx][test_num] = result
                                if test_num not in test_meta:
                                    meta_fields = parse_ptr(data, include_meta=True, start=start, end=end)
                                    if meta_fields:
                                        test_meta[test_num] = {
                                            "TEST_TXT": meta_fields.get("TEST_TXT", ""),
                                            "LO_LIMIT": meta_fields.get("LO_LIMIT", float("nan")),
                                            "HI_LIMIT": meta_fields.get("HI_LIMIT", float("nan")),
                                            "UNITS": meta_fields.get("UNITS", ""),
                                        }
                    else:
                        skipped_ptr += 1
                elif rec_typ == 5 and rec_sub == 10:  # PIR
                    counts["PIR"] += 1
                    if start + 2 <= end:
                        slot_key = (data[start] << 8) | data[start + 1]
                        part_idx = len(parts)
                        open_slot[slot_key] = part_idx
                        parts_append({"PART_IDX": part_idx, "HAS_PRR": False})
                        part_results_append({})
                        n_part_results += 1
                elif rec_typ == 5 and rec_sub == 20:  # PRR
                    counts["PRR"] += 1
                    if data is not None:
                        fields = parse_prr(data, start=start, end=end)
                        if fields:
                            slot_key = (fields["HEAD_NUM"] << 8) | fields["SITE_NUM"]
                            part_idx = open_slot_pop(slot_key, -1)
                            if part_idx < 0:
                                part_idx = len(parts)
                                parts_append({"PART_IDX": part_idx, "HAS_PRR": False})
                                part_results_append({})
                                n_part_results += 1
                            fields["PART_IDX"] = part_idx
                            fields["HAS_PRR"] = True
                            parts[part_idx].update(fields)
                elif rec_typ == 1 and rec_sub == 10:  # MIR
                    counts["MIR"] += 1
                    if data is not None and not mir_info:
                        fields = parse_mir(data, start=start, end=end)
                        if fields:
                            mir_info.update(fields)
                elif rec_typ == 1 and rec_sub == 20:  # MRR
                    counts["MRR"] += 1
                    if data is not None and not mrr_info:
                        fields = parse_mrr(data, start=start, end=end)
                        if fields:
                            mrr_info.update(fields)
                elif rec_typ == 1 and rec_sub == 40:  # HBR
                    counts["HBR"] += 1
                    if data is not None:
                        fields = parse_hbr(data, start=start, end=end)
                        if fields:
                            hbr_append(fields)
                elif rec_typ == 1 and rec_sub == 50:  # SBR
                    counts["SBR"] += 1
                    if data is not None:
                        fields = parse_sbr(data, start=start, end=end)
                        if fields:
                            sbr_append(fields)
                elif rec_typ == 1 and rec_sub == 80:  # SDR
                    counts["SDR"] += 1
                    if data is not None:
                        fields = parse_sdr(data, start=start, end=end)
                        if fields:
                            sdr_append(fields)

                try:
                    rec_typ, rec_sub, data, start, end = next(record_iter)
                except StopIteration:
                    break

    parts, part_results, dropped_inferred_parts = _filter_complete_parts(parts, part_results)
    if dropped_inferred_parts:
        _emit_log(
            f"Dropped {dropped_inferred_parts:,} inferred/incomplete part(s) without PRR.",
            verbose,
            logger,
        )
    kept = counts.get("PTR", 0) - skipped_ptr
    if filter_tests is not None:
        _emit_log(f"PTR kept     : {kept:,}  /  skipped: {skipped_ptr:,}", verbose, logger)
    _emit_log(f"Records      : {dict(counts)}", verbose, logger)
    return {
        "PARTS": parts,
        "RESULTS": part_results,
        "TEST_META": test_meta,
        "MIR": mir_info,
        "MRR": mrr_info,
        "SDR": sdr_records,
        "HBR": hbr_records,
        "SBR": sbr_records,
    }, counts, skipped_ptr


def _filter_complete_parts(parts: List[Dict], part_results: List[Dict[int, float]]) -> Tuple[List[Dict], List[Dict[int, float]], int]:
    filtered_parts: List[Dict] = []
    filtered_results: List[Dict[int, float]] = []
    dropped = 0
    for part, result in zip(parts, part_results):
        if part.get("HAS_PRR"):
            filtered_parts.append(part)
            filtered_results.append(result)
        else:
            dropped += 1
    return filtered_parts, filtered_results, dropped


def _join_unique_nonempty(records: List[Dict[str, object]], key: str) -> str:
    values: List[str] = []
    seen = set()
    for record in records:
        value = str(record.get(key, "")).strip()
        if value and value not in seen:
            seen.add(value)
            values.append(value)
    return " | ".join(values)


def _summarize_sdr_records(sdr_records: List[Dict[str, object]]) -> Dict[str, str]:
    summary = {
        "HAND_ID": "",
        "HAND_TYP": "",
        "LOAD_ID": "",
        "DIB_ID": "",
        "SITE_CNT_TEXT": "",
    }
    if not sdr_records:
        return summary
    unique_sites = []
    seen_sites = set()
    for record in sdr_records:
        for site_num in record.get("SITE_NUM_LIST", []) or []:
            if site_num not in seen_sites:
                seen_sites.add(site_num)
                unique_sites.append(site_num)
    if unique_sites:
        summary["SITE_CNT_TEXT"] = str(len(unique_sites))
    else:
        summary["SITE_CNT_TEXT"] = _join_unique_nonempty(sdr_records, "SITE_CNT")
    summary["HAND_ID"] = _join_unique_nonempty(sdr_records, "HAND_ID")
    summary["HAND_TYP"] = _join_unique_nonempty(sdr_records, "HAND_TYP")
    summary["LOAD_ID"] = _join_unique_nonempty(sdr_records, "LOAD_ID")
    summary["DIB_ID"] = _join_unique_nonempty(sdr_records, "DIB_ID")
    return summary


def parse_filter_values(
    typed_tests_text: str,
    selected_tests_text: str,
    range_from: str,
    range_to: str,
) -> Optional[Set[int]]:
    filter_tests: Set[int] = set()
    typed_tests_text = typed_tests_text.strip()
    selected_tests_text = selected_tests_text.strip()
    range_from = range_from.strip()
    range_to = range_to.strip()
    for tests_text in (typed_tests_text, selected_tests_text):
        if tests_text:
            normalized = tests_text.replace(",", " ")
            for token in normalized.split():
                match = _TEST_TOKEN_RE.match(token)
                if match:
                    filter_tests.add(int(match.group(1)))
                else:
                    filter_tests.add(int(token))
    if range_from or range_to:
        if not range_from or not range_to:
            raise ValueError("Both Range From and Range To must be filled in.")
        lo = int(range_from)
        hi = int(range_to)
        if lo > hi:
            raise ValueError("Range From cannot be greater than Range To.")
        filter_tests.update(range(lo, hi + 1))
    return filter_tests or None
