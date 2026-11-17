"""
Optimized STDF binary parser.

Fixes and Perl stdf.lib reliability features applied:
- B1: Inline PTR parsing (no dict per record)
- B2: Pre-compiled struct.Struct objects
- B3: Direct loop instead of generator for parse_stdf_file
- B4: Integer-based record dispatch
- B5: PART_FLG stored as int
- B12: Removed unused records() method
- B19: Removed unused test_meta_get binding
- B20: Fixed endian capture bug (re-capture after FAR)
- Fix 1 / Perl 1: Strict FAR header validation (FAR first, endianness from FAR length
  cross-checked with CPU_TYP, STDF_VER == 4 validation)
- Fix 2 / Perl 3: Truncation, malformed record, and error context reporting
  (file name, record type, byte offset) logged to Activity Log
- Fix 3: Unified endian-aware record walk (fixes "<H" fallback bug)
- Fix 4 / Perl 5: Unset-value normalization (65535 bins, TEST_T == 0, PCR 0xFFFFFFFF)
  and PTR OPT_FLAG limit validity decoding
- Fix 5 / Perl 8: HBR/SBR decoding and count reconciliation check (PCR vs SBR vs PRR)
- Fix 6: Gzip (.gz) STDF support
- Fix 7: Network-share mmap guard (local temp copy / safe fallback)
- Perl 2: Leftover/extra bytes check on fixed & fully-parsed records (warning in
  Check STDF, error blocking Run Analysis)
- Perl 4: validate_stdf_data() step with reason codes (MISSING_MIR_FIELDS, NO_PARTS,
  NO_MRR, INVALID_LOT, EXTRA_BYTES, etc.) and MIR LOT_ID validation vs user Lot ID
"""
import gzip
import logging
import mmap
import os
import re
import shutil
import struct
import tempfile
import time
import zlib
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

LogFunc = Optional[Callable[[str], None]]
ProgressFunc = Optional[Callable[[float, str], None]]
ByteProgressFunc = Optional[Callable[[int, int], None]]

# Record type/sub constants for integer dispatch
_FAR = (0, 10)
_ATR = (0, 20)
_MIR = (1, 10)
_MRR = (1, 20)
_PCR = (1, 30)
_HBR = (1, 40)
_SBR = (1, 50)
_PMR = (1, 60)
_PGR = (1, 62)
_PLR = (1, 63)
_RDR = (1, 70)
_SDR = (1, 80)
_WIR = (2, 10)
_WRR = (2, 20)
_WCR = (2, 30)
_PIR = (5, 10)
_PRR = (5, 20)
_TSR = (10, 30)
_PTR = (15, 10)
_MPR = (15, 15)
_FTR = (15, 20)
_BPS = (20, 10)
_EPS = (20, 20)
_GDR = (50, 10)
_DTR = (50, 30)

RECORD_TYPES = {
    _FAR: "FAR", _ATR: "ATR", _MIR: "MIR", _MRR: "MRR", _PCR: "PCR",
    _HBR: "HBR", _SBR: "SBR", _PMR: "PMR", _PGR: "PGR", _PLR: "PLR",
    _RDR: "RDR", _SDR: "SDR", _WIR: "WIR", _WRR: "WRR", _WCR: "WCR",
    _PIR: "PIR", _PRR: "PRR", _TSR: "TSR", _PTR: "PTR", _MPR: "MPR",
    _FTR: "FTR", _BPS: "BPS", _EPS: "EPS", _GDR: "GDR", _DTR: "DTR",
}

_KNOWN_RECORDS = frozenset(RECORD_TYPES.keys())

# STDF V4 unset/missing sentinel values (Fix 4 / Perl 5)
STDF_UNSET_U1 = 0xFF          # 255 (except HEAD_NUM=255 which means summary)
STDF_UNSET_U2 = 0xFFFF        # 65535
STDF_UNSET_U4 = 0xFFFFFFFF    # 4294967295
STDF_UNSET_I2 = -32768        # 0x8000

# Named validation reason codes (Perl 4)
REASON_INVALID_FAR = "INVALID_FAR"
REASON_EXTRA_BYTES = "EXTRA_BYTES"
REASON_TRUNCATED_FILE = "TRUNCATED_FILE"
REASON_MALFORMED_RECORD = "MALFORMED_RECORD"
REASON_MISSING_MIR_FIELDS = "MISSING_MIR_FIELDS"
REASON_INVALID_LOT = "INVALID_LOT"
REASON_NO_PARTS = "NO_PARTS"
REASON_NO_MRR = "NO_MRR"
REASON_COUNT_MISMATCH = "COUNT_MISMATCH"

_MAX_DETAILED_DIAGNOSTICS = 50
_RETEST_LOT_SUFFIX_RE = re.compile(r"[._\-\s]+RJ\d+$", re.IGNORECASE)

_MIR_STRING_KEYS = (
    "LOT_ID", "PART_TYP", "NODE_NAM", "TSTR_TYP", "JOB_NAM",
    "JOB_REV", "SBLOT_ID", "OPER_NAM", "EXEC_TYP", "EXEC_VER",
    "TEST_COD", "TST_TEMP", "USER_TXT", "AUX_FILE", "PKG_TYP",
    "FAMLY_ID", "DATE_COD", "FACIL_ID", "FLOOR_ID", "PROC_ID",
    "OPER_FRQ", "SPEC_NAM", "SPEC_VER", "FLOW_ID", "SETUP_ID",
    "DSGN_REV", "ENG_ID", "ROM_COD", "SERL_NUM", "SUPR_NAM",
)



def _count_files_text(count: int) -> str:
    return f"{count} file" if count == 1 else f"{count} files"


_FILENAME_TIMESTAMP_RE = re.compile(r"\d{14}")


def _extract_first_filename_timestamp(path: str) -> str:
    """Return the first valid YYYYMMDDHHMMSS timestamp found in the filename."""
    filename = os.path.basename(os.path.abspath(path))
    for match in _FILENAME_TIMESTAMP_RE.finditer(filename):
        timestamp = match.group(0)
        try:
            datetime.strptime(timestamp, "%Y%m%d%H%M%S")
        except ValueError:
            continue
        return timestamp
    return ""


def _path_filename_sort_key(path: str) -> Tuple[int, str, str, str]:
    """Return a deterministic ascending datalog sort key."""
    abs_path = os.path.abspath(path)
    base_name = os.path.basename(abs_path).casefold()
    timestamp = _extract_first_filename_timestamp(abs_path)
    if timestamp:
        return 0, timestamp, base_name, abs_path.casefold()
    return 1, base_name, "", abs_path.casefold()


def sort_paths_by_filename_timestamp(input_paths: Sequence[str]) -> List[str]:
    """Sort STDF paths using the shared GUI timestamp-from-filename rule."""
    return sorted(list(input_paths or []), key=_path_filename_sort_key)


def _format_stdf_timestamp(value) -> str:
    try:
        if value in (None, ""):
            return ""
        timestamp = int(value)
        if timestamp <= 0 or timestamp == STDF_UNSET_U4:
            return ""
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        if dt.second:
            return f"{dt.month}/{dt.day}/{dt.year} {dt.hour}:{dt.minute:02d}:{dt.second:02d}"
        return f"{dt.month}/{dt.day}/{dt.year} {dt.hour}:{dt.minute:02d}"
    except Exception:
        return ""


def _format_limit_text(value) -> str:
    try:
        return f"{value:.6g}" if value is not None and value == value else "n/a"
    except Exception:
        return "n/a"


def _emit_log(message: str, verbose: bool, logger: LogFunc):
    if logger:
        logger(message)
    elif verbose:
        print(message)


def normalize_lot_id_for_match(lot_id: str) -> str:
    """Normalize a Lot ID for comparison (strips optional .RJ<N> retest suffix)."""
    cleaned = str(lot_id or "").strip().upper()
    if not cleaned:
        return ""
    return _RETEST_LOT_SUFFIX_RE.sub("", cleaned)


def is_lot_id_matching(mir_lot_id: str, expected_lot_id: str) -> bool:
    """Return True if MIR.LOT_ID matches the user-supplied Lot ID."""
    mir_clean = str(mir_lot_id or "").strip().upper()
    exp_clean = str(expected_lot_id or "").strip().upper()
    if not exp_clean:
        return bool(mir_clean)
    if not mir_clean:
        return False
    if mir_clean == exp_clean:
        return True
    return normalize_lot_id_for_match(mir_clean) == normalize_lot_id_for_match(exp_clean)


# Per-drive-letter cache of "is this a remote drive?" so GetDriveTypeW is called
# at most once per drive per process instead of once (or twice) per opened file.
_DRIVE_REMOTE_CACHE: Dict[str, bool] = {}


def _is_network_path(filepath: str) -> bool:
    """Return True if filepath resides on a UNC share or mapped remote drive (Fix 7)."""
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


def _is_gzip_file(filepath: str) -> bool:
    """Return True if filepath is a gzipped STDF file by extension or magic bytes (Fix 6)."""
    if str(filepath or "").lower().endswith(".gz"):
        return True
    try:
        with open(filepath, "rb") as f:
            magic = f.read(2)
        return magic == b"\x1f\x8b"
    except OSError:
        return False


class STDFReader:
    """Memory-mapped / buffered STDF V4 reader with strict header validation and diagnostics."""

    def __init__(
        self,
        filepath: str,
        filter_tests: Optional[Set[int]] = None,
        progress_callback: ByteProgressFunc = None,
    ):
        self.filepath = filepath
        self.filename = os.path.basename(filepath) if filepath else "<memory>"
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
        self._cn_cache = {}

        # Diagnostics & validation state (Perl Features 1, 2, 3)
        self.warnings: List[str] = []
        self.errors: List[str] = []
        self.extra_bytes_count: int = 0
        self.extra_bytes_records: List[Dict[str, Any]] = []
        self.malformed_count: int = 0
        self.truncated: bool = False
        self.far_valid: bool = False
        self.far_info: Optional[Dict[str, int]] = None

        # Pre-compiled structs (updated after endian detection)
        self._s_H = struct.Struct("<H")
        self._s_h = struct.Struct("<h")
        self._s_I = struct.Struct("<I")
        self._s_f = struct.Struct("<f")
        self._s_HH = struct.Struct("<HH")
        self._s_hdr = struct.Struct("<HBB")

    def _rebuild_structs(self):
        """Rebuild struct objects after endian is detected."""
        e = self.endian
        self._s_H = struct.Struct(e + "H")
        self._s_h = struct.Struct(e + "h")
        self._s_I = struct.Struct(e + "I")
        self._s_f = struct.Struct(e + "f")
        self._s_HH = struct.Struct(e + "HH")
        self._s_hdr = struct.Struct(e + "HBB")

    @staticmethod
    def _rec_label(rec_typ: int, rec_sub: int) -> str:
        name = RECORD_TYPES.get((rec_typ, rec_sub))
        if name:
            return f"{rec_typ}-{rec_sub} ({name})"
        return f"{rec_typ}-{rec_sub}"

    def _add_warning(self, rec_typ: int, rec_sub: int, offset: int, message: str):
        if len(self.warnings) < _MAX_DETAILED_DIAGNOSTICS:
            lbl = self._rec_label(rec_typ, rec_sub)
            self.warnings.append(
                f"[WARNING] {self.filename} @ offset {offset} (0x{offset:X}) [parse {lbl}]: {message}"
            )
        elif len(self.warnings) == _MAX_DETAILED_DIAGNOSTICS:
            self.warnings.append(
                f"[WARNING] {self.filename}: Additional warnings suppressed (exceeded {_MAX_DETAILED_DIAGNOSTICS} messages)."
            )

    def _add_error(self, rec_typ: int, rec_sub: int, offset: int, message: str):
        if len(self.errors) < _MAX_DETAILED_DIAGNOSTICS:
            lbl = self._rec_label(rec_typ, rec_sub)
            self.errors.append(
                f"[ERROR] {self.filename} @ offset {offset} (0x{offset:X}) [parse {lbl}]: {message}"
            )
        elif len(self.errors) == _MAX_DETAILED_DIAGNOSTICS:
            self.errors.append(
                f"[ERROR] {self.filename}: Additional errors suppressed (exceeded {_MAX_DETAILED_DIAGNOSTICS} messages)."
            )

    def _flag_extra_bytes(self, rec_typ: int, rec_sub: int, rec_offset: int, leftover: int, rec_len: int):
        self.extra_bytes_count += 1
        if len(self.extra_bytes_records) < _MAX_DETAILED_DIAGNOSTICS:
            self.extra_bytes_records.append({
                "rec_typ": rec_typ,
                "rec_sub": rec_sub,
                "rec_name": RECORD_TYPES.get((rec_typ, rec_sub), f"{rec_typ}-{rec_sub}"),
                "offset": rec_offset,
                "leftover_bytes": leftover,
                "rec_len": rec_len,
            })
        self._add_warning(
            rec_typ,
            rec_sub,
            rec_offset,
            f"Record has {leftover} leftover/extra byte(s) (rec_len={rec_len}, consumed={rec_len - leftover})",
        )

    def _flag_malformed(self, rec_typ: int, rec_sub: int, rec_offset: int, message: str):
        self.malformed_count += 1
        self._add_error(rec_typ, rec_sub, rec_offset, f"Malformed/partial record: {message}")

    def validate_far_header(self, data, offset: int = 0, limit: Optional[int] = None) -> Tuple[bool, int]:
        """Validate the initial FAR record: FAR first, endianness from FAR length field
        cross-checked with CPU_TYP, and STDF_VER == 4 (Fix 1 / Perl 1).

        Returns (is_valid, next_offset).
        """
        limit = self._limit_for(data, limit)
        if limit - offset < 4:
            self.truncated = True
            self.far_valid = False
            self._add_error(0, 10, offset, f"File too short for STDF record header ({limit - offset} byte(s))")
            return False, limit

        rec_typ = data[offset + 2]
        rec_sub = data[offset + 3]
        if rec_typ != 0 or rec_sub != 10:
            self.far_valid = False
            self._add_error(
                rec_typ, rec_sub, offset,
                f"First record must be FAR (0-10), found {self._rec_label(rec_typ, rec_sub)}",
            )
            return False, offset

        len_le = struct.unpack_from("<H", data, offset)[0]
        len_be = struct.unpack_from(">H", data, offset)[0]

        if limit - offset < 6:
            self.truncated = True
            self.far_valid = False
            self._add_error(0, 10, offset, f"FAR record truncated ({limit - offset - 4} body byte(s), need at least 2)")
            return False, limit

        cpu_typ = data[offset + 4]
        stdf_ver = data[offset + 5]

        # Validate CPU_TYP (STDF v4: 0=DEC/VAX LE, 1=Sun/SPARC BE, 2=x86/IBM PC LE)
        if cpu_typ == 1:
            cpu_endian = ">"
        elif cpu_typ in (0, 2):
            cpu_endian = "<"
        else:
            self.far_valid = False
            far_len = 2 if (len_le == 2 or len_be == 2) else min(len_le, len_be)
            self._add_error(
                0, 10, offset,
                f"Invalid CPU_TYP={cpu_typ} in FAR (expected 1 for Big-Endian or 2 for Little-Endian)",
            )
            return False, min(limit, offset + 4 + far_len)

        # Detect endianness from FAR length field (2 bytes) and cross-check with CPU_TYP
        if len_le == 2 and len_be != 2:
            len_endian = "<"
            far_len = len_le
        elif len_be == 2 and len_le != 2:
            len_endian = ">"
            far_len = len_be
        else:
            # FAR length is not 2 in either endian; check using CPU_TYP endian
            len_endian = cpu_endian
            far_len = len_be if cpu_endian == ">" else len_le
            if far_len < 2:
                self.far_valid = False
                self._add_error(
                    0, 10, offset,
                    f"Invalid FAR length field (LE={len_le}, BE={len_be}; expected 2)",
                )
                return False, min(limit, offset + 4 + far_len)

        if len_endian != cpu_endian:
            self.far_valid = False
            self.endian = cpu_endian
            self._rebuild_structs()
            self._add_error(
                0, 10, offset,
                f"FAR endianness mismatch: FAR length indicates '{len_endian}' (LE={len_le}, BE={len_be}) "
                f"but CPU_TYP={cpu_typ} indicates '{cpu_endian}'",
            )
            return False, min(limit, offset + 4 + far_len)

        self.endian = len_endian
        self._rebuild_structs()

        body_end = offset + 4 + far_len
        if body_end > limit:
            self.truncated = True
            self.far_valid = False
            self._add_error(
                0, 10, offset,
                f"FAR record body extends past EOF (rec_len={far_len}, available={limit - offset - 4})",
            )
            return False, limit

        # Validate STDF version (must be 4)
        if stdf_ver != 4:
            self.far_valid = False
            self._add_error(
                0, 10, offset,
                f"Unsupported STDF_VER={stdf_ver} in FAR (expected STDF version 4)",
            )
            return False, body_end

        # Check leftover bytes in FAR (expected exactly 2 bytes: CPU_TYP, STDF_VER)
        if far_len > 2:
            self._flag_extra_bytes(0, 10, offset, far_len - 2, far_len)

        self.far_valid = True
        self.far_info = {"CPU_TYP": cpu_typ, "STDF_VER": stdf_ver, "REC_LEN": far_len}
        return True, body_end

    @property
    def buffer(self):
        return self._mm

    def _load_gzip(self, path: str):
        """Fix 6: Transparent gzip (.gz) support - decompress fully into memory."""
        try:
            with gzip.open(path, "rb") as gz_file:
                data = gz_file.read()
            self._mm = data
            self.file_size = len(data)
        except (gzip.BadGzipFile, EOFError, zlib.error, OSError) as exc:
            self.truncated = True
            self.far_valid = False
            self._add_error(0, 10, 0, f"Corrupted or truncated gzip STDF file: {exc}")
            self._mm = b""
            self.file_size = 0
        return self

    def _release_os_handles(self):
        """Close mmap / fd / file handles opened by __enter__ (temp staging copy is kept)."""
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

        target_path = self.filepath
        # Resolved once per open (drive type is additionally cached per drive letter).
        is_remote = bool(target_path) and _is_network_path(target_path)

        # Fix 7: Guard mmap against network shares by copying to a local temporary file first
        if is_remote and os.path.isfile(target_path):
            try:
                suffix = ".gz" if target_path.lower().endswith(".gz") else ".stdf"
                tmp_fd, tmp_path = tempfile.mkstemp(prefix="pts_stdf_", suffix=suffix)
                os.close(tmp_fd)
                shutil.copy2(target_path, tmp_path)
                self._temp_local_path = tmp_path
                target_path = tmp_path
            except Exception as exc:
                self._add_warning(
                    0, 0, 0,
                    f"Could not stage network file locally ({exc}); falling back to direct stream read.",
                )
                self._temp_local_path = None

        # Fix 6: '.gz' extension -> decompress directly (no need to open/peek the file first)
        if target_path and str(target_path).lower().endswith(".gz") and os.path.isfile(target_path):
            return self._load_gzip(target_path)

        # Only mmap local files (never mmap directly over a remote UNC share if temp copy failed)
        allow_mmap = (not is_remote) or (self._temp_local_path is not None)

        if os.name == "nt" and allow_mmap:
            try:
                import ctypes, msvcrt
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
            # fstat on the already-open descriptor (no extra path-based stat syscall)
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

        # Fix 6: gzip magic-byte detection for files without a '.gz' extension, done on the
        # already-open handle (mmap slice or a 2-byte peek) instead of a separate open/read/close.
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
        if self._mm is not None:
            if hasattr(self._mm, "close"):
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
        if self._file:
            try:
                self._file.close()
            except OSError as exc:
                logging.warning("Failed to close file handle for '%s': %s", self.filepath, exc)
            self._file = None
        if self._temp_local_path:
            try:
                os.remove(self._temp_local_path)
            except OSError as exc:
                logging.warning("Failed to remove temporary STDF copy '%s': %s", self._temp_local_path, exc)
            self._temp_local_path = None

    def _report_progress(self, current_bytes: int):
        if not self.progress_callback:
            return
        self.progress_callback(min(current_bytes, self.file_size), self.file_size)

    def record_spans(self):
        """Unified record generator yielding (rec_typ, rec_sub, data, body_start, body_end).

        Fix 3: Uses validated FAR endianness in both mmap and streaming fallback paths
        (fixing the hardcoded '<H' bug) and flags truncation at EOF.
        """
        if self._mm is None:
            f = self._file
            if f is None:
                return
            offset = 0
            first = True
            while True:
                header = f.read(4)
                if not header:
                    break
                if len(header) < 4:
                    self.truncated = True
                    self._add_error(
                        0, 0, offset,
                        f"Truncated record header at EOF ({len(header)} trailing byte(s) left)",
                    )
                    break

                rec_typ = header[2]
                rec_sub = header[3]
                if first:
                    first = False
                    len_le = struct.unpack("<H", header[0:2])[0]
                    len_be = struct.unpack(">H", header[0:2])[0]
                    far_len = 2 if (len_le == 2 or len_be == 2) else min(len_le, len_be)
                    body = f.read(far_len)
                    raw_far = header + body
                    self.validate_far_header(raw_far, 0, len(raw_far))
                    if len(body) < far_len:
                        break
                    offset += 4 + len(body)
                    self._report_progress(offset)
                    yield rec_typ, rec_sub, body, 0, len(body)
                    continue

                # Use the endian-aware struct rebuilt after FAR validation (fixes '<H' bug)
                rec_len = self._s_H.unpack(header[0:2])[0]
                body = f.read(rec_len)
                if len(body) < rec_len:
                    self.truncated = True
                    self._add_error(
                        rec_typ, rec_sub, offset,
                        f"Truncated record body at EOF (need {rec_len} bytes, only {len(body)} available)",
                    )
                    break
                offset += 4 + rec_len
                self._report_progress(offset)
                yield rec_typ, rec_sub, body, 0, len(body)
            self._report_progress(self.file_size)
            return

        mm = self._mm
        file_size = self.file_size
        if file_size == 0:
            if not self.errors:
                self.truncated = True
                self._add_error(0, 10, 0, "Empty STDF file (0 bytes)")
            return

        far_ok, next_offset = self.validate_far_header(mm, 0, file_size)
        if next_offset > 4 and next_offset <= file_size:
            yield mm[2], mm[3], mm, 4, next_offset
        if not far_ok and next_offset == 0:
            self._report_progress(file_size)
            return

        offset = next_offset
        filter_tests = self.filter_tests
        s_hdr_unpack = self._s_hdr.unpack_from
        s_I_unpack = self._s_I.unpack_from
        has_cb = self.progress_callback is not None
        p_step = self._progress_step
        next_p = offset + p_step

        while offset + 4 <= file_size:
            rec_len, rec_typ, rec_sub = s_hdr_unpack(mm, offset)
            body_start = offset + 4
            body_end = body_start + rec_len
            if body_end > file_size:
                self.truncated = True
                self._add_error(
                    rec_typ, rec_sub, offset,
                    f"Truncated record body at EOF (need {rec_len} bytes, only {file_size - body_start} available)",
                )
                break

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

        if not self.truncated and offset < file_size:
            self.truncated = True
            self._add_error(
                0, 0, offset,
                f"Truncated record header at EOF ({file_size - offset} trailing byte(s) left)",
            )
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

    def parse_mir_compact(self, data, start=0, end=None, rec_offset: Optional[int] = None):
        """Parse Master Information Record (MIR) with partial/malformed and leftover-bytes checks."""
        fields: Dict[str, Any] = {}
        offset = start
        end = self._limit_for(data, end)
        rec_len = end - start
        if rec_offset is None:
            rec_offset = start - 4 if start >= 4 else start

        if rec_len < 15:
            self._flag_malformed(1, 10, rec_offset, f"MIR record too short ({rec_len} bytes < 15 fixed bytes)")

        try:
            fields["SETUP_T"], offset = self._read_u4_at(data, offset, end)
            fields["START_T"], offset = self._read_u4_at(data, offset, end)
            fields["STAT_NUM"], offset = self._read_u1_at(data, offset, end)
            fields["MODE_COD"], offset = self._read_c1_at(data, offset, end)
            fields["RTST_COD"], offset = self._read_c1_at(data, offset, end)
            fields["PROT_COD"], offset = self._read_c1_at(data, offset, end)
            burn_tim, offset = self._read_u2_at(data, offset, end)
            fields["BURN_TIM"] = None if burn_tim == STDF_UNSET_U2 else burn_tim
            fields["CMOD_COD"], offset = self._read_c1_at(data, offset, end)
            for key in _MIR_STRING_KEYS:
                if offset == end:
                    # Legal early termination at optional trailing field boundary
                    break
                if offset + 1 > end or offset + 1 + data[offset] > end:
                    avail = max(0, end - offset - 1)
                    needed = data[offset] if offset < end else 1
                    self._flag_malformed(
                        1, 10, rec_offset,
                        f"MIR field '{key}' truncated (length={needed}, available={avail})",
                    )
                    offset = end
                    break
                fields[key], offset = self._read_cn_at(data, offset, end)
        except (IndexError, struct.error) as exc:
            self._flag_malformed(1, 10, rec_offset, f"MIR truncated mid-field ({exc})")
            offset = end

        if offset < end:
            self._flag_extra_bytes(1, 10, rec_offset, end - offset, rec_len)

        if fields.get("SETUP_T") not in (None, ""):
            fields["SETUP_T_TEXT"] = _format_stdf_timestamp(fields.get("SETUP_T"))
        if fields.get("START_T") not in (None, ""):
            fields["START_T_TEXT"] = _format_stdf_timestamp(fields.get("START_T"))
        return fields or None

    def parse_mrr_compact(self, data, start=0, end=None, rec_offset: Optional[int] = None):
        """Parse Master Results Record (MRR) with leftover-bytes and truncation checks."""
        fields: Dict[str, Any] = {}
        offset = start
        end = self._limit_for(data, end)
        rec_len = end - start
        if rec_offset is None:
            rec_offset = start - 4 if start >= 4 else start

        if rec_len < 4:
            self._flag_malformed(1, 20, rec_offset, f"MRR record too short ({rec_len} bytes < 4)")
            return None

        try:
            fields["FINISH_T"], offset = self._read_u4_at(data, offset, end)
            if offset < end:
                fields["DISP_COD"], offset = self._read_c1_at(data, offset, end)
            for key in ("USR_DESC", "EXC_DESC"):
                if offset == end:
                    break
                if offset + 1 > end or offset + 1 + data[offset] > end:
                    avail = max(0, end - offset - 1)
                    needed = data[offset] if offset < end else 1
                    self._flag_malformed(
                        1, 20, rec_offset,
                        f"MRR field '{key}' truncated (length={needed}, available={avail})",
                    )
                    offset = end
                    break
                fields[key], offset = self._read_cn_at(data, offset, end)
        except (IndexError, struct.error) as exc:
            self._flag_malformed(1, 20, rec_offset, f"MRR truncated mid-field ({exc})")
            offset = end

        if offset < end:
            self._flag_extra_bytes(1, 20, rec_offset, end - offset, rec_len)

        if fields.get("FINISH_T") not in (None, ""):
            fields["FINISH_T_TEXT"] = _format_stdf_timestamp(fields.get("FINISH_T"))
        return fields or None

    def parse_pcr_compact(self, data, start=0, end=None, rec_offset: Optional[int] = None):
        """Parse Part Count Record (PCR) with 0xFFFFFFFF unset normalization and leftover-bytes check."""
        fields: Dict[str, Any] = {}
        offset = start
        end = self._limit_for(data, end)
        rec_len = end - start
        if rec_offset is None:
            rec_offset = start - 4 if start >= 4 else start

        if rec_len < 6:
            self._flag_malformed(1, 30, rec_offset, f"PCR record too short ({rec_len} bytes < 6)")
            return None

        try:
            fields["HEAD_NUM"], offset = self._read_u1_at(data, offset, end)
            fields["SITE_NUM"], offset = self._read_u1_at(data, offset, end)
            for key in ("PART_CNT", "RTST_CNT", "ABRT_CNT", "GOOD_CNT", "FUNC_CNT"):
                if offset == end:
                    fields[key] = None
                    continue
                if offset + 4 > end:
                    self._flag_malformed(
                        1, 30, rec_offset,
                        f"PCR field '{key}' truncated ({end - offset} byte(s) left, need 4)",
                    )
                    offset = end
                    break
                val, offset = self._read_u4_at(data, offset, end)
                fields[key] = None if val == STDF_UNSET_U4 else val
        except (IndexError, struct.error) as exc:
            self._flag_malformed(1, 30, rec_offset, f"PCR truncated ({exc})")
            offset = end

        if offset < end:
            self._flag_extra_bytes(1, 30, rec_offset, end - offset, rec_len)

        return fields or None

    def _parse_bin_record(
        self, rec_sub: int, prefix: str, data, start: int = 0, end: Optional[int] = None,
        rec_offset: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Parse HBR (1, 40) or SBR (1, 50) record with unset normalization and leftover-bytes check."""
        end = self._limit_for(data, end)
        rec_len = end - start
        if rec_offset is None:
            rec_offset = start - 4 if start >= 4 else start

        if rec_len < 8:
            self._flag_malformed(1, rec_sub, rec_offset, f"{prefix}R record too short ({rec_len} bytes < 8)")
            return None

        offset = start
        try:
            head_num, offset = self._read_u1_at(data, offset, end)
            site_num, offset = self._read_u1_at(data, offset, end)
            raw_bin, offset = self._read_u2_at(data, offset, end)
            raw_cnt, offset = self._read_u4_at(data, offset, end)
            bin_pf = " "
            bin_nam = ""
            if offset < end:
                bin_pf, offset = self._read_c1_at(data, offset, end)
            if offset < end:
                if offset + 1 + data[offset] > end:
                    self._flag_malformed(
                        1, rec_sub, rec_offset,
                        f"{prefix}_NAM truncated (len={data[offset]}, available={end - offset - 1})",
                    )
                    offset = end
                else:
                    bin_nam, offset = self._read_cn_at(data, offset, end)
        except (IndexError, struct.error) as exc:
            self._flag_malformed(1, rec_sub, rec_offset, f"{prefix}R truncated ({exc})")
            return None

        if offset < end:
            self._flag_extra_bytes(1, rec_sub, rec_offset, end - offset, rec_len)

        return {
            "HEAD_NUM": head_num,
            "SITE_NUM": site_num,
            f"{prefix}_NUM": None if raw_bin == STDF_UNSET_U2 else raw_bin,
            f"{prefix}_CNT": None if raw_cnt == STDF_UNSET_U4 else raw_cnt,
            f"{prefix}_PF": bin_pf,
            f"{prefix}_NAM": bin_nam,
        }

    def parse_hbr_compact(self, data, start=0, end=None, rec_offset: Optional[int] = None):
        """Parse Hardware Bin Record (HBR)."""
        return self._parse_bin_record(40, "HBIN", data, start, end, rec_offset)

    def parse_sbr_compact(self, data, start=0, end=None, rec_offset: Optional[int] = None):
        """Parse Software Bin Record (SBR)."""
        return self._parse_bin_record(50, "SBIN", data, start, end, rec_offset)

    def parse_pir_compact(self, data, start=0, end=None, rec_offset: Optional[int] = None):
        """Parse Part Information Record (PIR) with leftover-bytes and truncation checks."""
        end = self._limit_for(data, end)
        rec_len = end - start
        if rec_offset is None:
            rec_offset = start - 4 if start >= 4 else start

        if rec_len < 2:
            self._flag_malformed(5, 10, rec_offset, f"PIR record too short ({rec_len} bytes < 2)")
            return None
        if rec_len > 2:
            self._flag_extra_bytes(5, 10, rec_offset, rec_len - 2, rec_len)
        return {"HEAD_NUM": data[start], "SITE_NUM": data[start + 1]}

    def parse_prr_compact(self, data, start=0, end=None, rec_offset: Optional[int] = None):
        """Parse Part Results Record (PRR) with unset normalization (65535 bins, TEST_T==0),
        trailing field parsing (PART_ID, PART_TXT, PART_FIX), and leftover-bytes check."""
        end = self._limit_for(data, end)
        rec_len = end - start
        if rec_offset is None:
            rec_offset = start - 4 if start >= 4 else start

        if rec_len < 17:
            self._flag_malformed(5, 20, rec_offset, f"PRR record too short ({rec_len} bytes < 17)")
            return None

        try:
            head_num = data[start]
            site_num = data[start + 1]
            part_flg = data[start + 2]
            num_test = self._s_H.unpack_from(data, start + 3)[0]
            raw_hbin = self._s_H.unpack_from(data, start + 5)[0]
            raw_sbin = self._s_H.unpack_from(data, start + 7)[0]
            raw_x = self._s_h.unpack_from(data, start + 9)[0]
            raw_y = self._s_h.unpack_from(data, start + 11)[0]
            raw_test_t = self._s_I.unpack_from(data, start + 13)[0]

            # Fix 4 / Perl 5: Normalize 65535 unset bins, -32768 coords, and TEST_T == 0
            hard_bin = None if raw_hbin == STDF_UNSET_U2 else raw_hbin
            soft_bin = None if raw_sbin == STDF_UNSET_U2 else raw_sbin
            x_coord = None if raw_x == STDF_UNSET_I2 else raw_x
            y_coord = None if raw_y == STDF_UNSET_I2 else raw_y
            test_t = None if (raw_test_t == 0 or raw_test_t == STDF_UNSET_U4) else raw_test_t

            offset = start + 17
            part_id = ""
            part_txt = ""

            # 1. PART_ID (C*n, optional if record ends at offset 17)
            if offset < end:
                part_id_len = data[offset]
                offset += 1
                if offset + part_id_len > end:
                    self._flag_malformed(
                        5, 20, rec_offset,
                        f"PRR PART_ID truncated (len={part_id_len}, available={end - offset})",
                    )
                    return None
                part_id = bytes(data[offset:offset + part_id_len]).decode("latin-1", errors="replace")
                offset += part_id_len

            # 2. PART_TXT (C*n, optional trailing field)
            if offset < end:
                part_txt_len = data[offset]
                offset += 1
                if offset + part_txt_len > end:
                    self._flag_malformed(
                        5, 20, rec_offset,
                        f"PRR PART_TXT truncated (len={part_txt_len}, available={end - offset})",
                    )
                    return None
                if part_txt_len:
                    part_txt = bytes(data[offset:offset + part_txt_len]).decode("latin-1", errors="replace")
                offset += part_txt_len

            # 3. PART_FIX (B*n, optional trailing field)
            if offset < end:
                part_fix_len = data[offset]
                offset += 1
                if offset + part_fix_len > end:
                    self._flag_malformed(
                        5, 20, rec_offset,
                        f"PRR PART_FIX truncated (len={part_fix_len}, available={end - offset})",
                    )
                    return None
                offset += part_fix_len

            # Perl 2: Flag leftover/extra bytes after all PRR fields are consumed
            if offset < end:
                self._flag_extra_bytes(5, 20, rec_offset, end - offset, rec_len)
        except (IndexError, struct.error) as exc:
            self._flag_malformed(5, 20, rec_offset, f"PRR truncated ({exc})")
            return None

        return {
            "HEAD_NUM": head_num,
            "SITE_NUM": site_num,
            "PART_FLG": part_flg,
            "NUM_TEST": num_test,
            "HARD_BIN": hard_bin,
            "SOFT_BIN": soft_bin,
            "X_COORD": x_coord,
            "Y_COORD": y_coord,
            "TEST_T": test_t,
            "PART_ID": part_id,
            "PART_TXT": part_txt,
        }

    def parse_ptr_compact(self, data, include_meta=False, start=0, end=None, rec_offset: Optional[int] = None):
        """Parse Parametric Test Record (PTR) with OPT_FLAG limit validity decoding (Fix 4)."""
        end = self._limit_for(data, end)
        rec_len = end - start
        if rec_offset is None:
            rec_offset = start - 4 if start >= 4 else start

        if rec_len < 12:
            self._flag_malformed(15, 10, rec_offset, f"PTR record too short ({rec_len} bytes < 12)")
            return None
        try:
            test_num = self._s_I.unpack_from(data, start)[0]
            head_num = data[start + 4]
            site_num = data[start + 5]
            test_flg = data[start + 6]
            parm_flg = data[start + 7]
            result = self._s_f.unpack_from(data, start + 8)[0]
        except (IndexError, struct.error) as exc:
            self._flag_malformed(15, 10, rec_offset, f"PTR fixed header truncated ({exc})")
            return None

        fields: Dict[str, Any] = {
            "TEST_NUM": test_num,
            "HEAD_NUM": head_num,
            "SITE_NUM": site_num,
            "TEST_FLG": test_flg,
            "PARM_FLG": parm_flg,
            "RESULT": result,
        }
        if not include_meta:
            return fields

        test_txt = ""
        lo_limit = float("nan")
        hi_limit = float("nan")
        units = ""
        opt_flag = 0
        offset = start + 12
        try:
            if offset < end:
                test_txt_len = data[offset]
                offset += 1
                if offset + test_txt_len > end:
                    self._flag_malformed(15, 10, rec_offset, "PTR TEST_TXT truncated")
                    raise IndexError
                test_txt = bytes(data[offset:offset + test_txt_len]).decode("latin-1", errors="replace")
                offset += test_txt_len
            if offset < end:
                alarm_id_len = data[offset]
                offset += 1
                if offset + alarm_id_len > end:
                    self._flag_malformed(15, 10, rec_offset, "PTR ALARM_ID truncated")
                    raise IndexError
                offset += alarm_id_len
            if offset < end:
                opt_flag = data[offset]
                # OPT_FLAG (1B) + RES_SCAL (1B) + LLM_SCAL (1B) + HLM_SCAL (1B)
                if offset + 4 > end:
                    offset = end
                else:
                    offset += 4
                    # Fix 4: Decode OPT_FLAG limit validity bits (STDF V4 spec):
                    # Bit 4 (0x10) & Bit 6 (0x40) => LO_LIMIT invalid / not used (0x50)
                    # Bit 5 (0x20) & Bit 7 (0x80) => HI_LIMIT invalid / not used (0xA0)
                    # (Note: Bits 2 & 3 [0x04, 0x08] apply to LO_SPEC/HI_SPEC, not LO_LIMIT/HI_LIMIT)
                    lo_invalid = bool(opt_flag & 0x50)
                    hi_invalid = bool(opt_flag & 0xA0)
                    if offset + 4 <= end:
                        raw_lo = self._s_f.unpack_from(data, offset)[0]
                        lo_limit = float("nan") if lo_invalid else raw_lo
                        offset += 4
                    if offset + 4 <= end:
                        raw_hi = self._s_f.unpack_from(data, offset)[0]
                        hi_limit = float("nan") if hi_invalid else raw_hi
                        offset += 4
            if offset < end:
                units_len = data[offset]
                offset += 1
                if offset + units_len > end:
                    self._flag_malformed(15, 10, rec_offset, "PTR UNITS truncated")
                    raise IndexError
                units = bytes(data[offset:offset + units_len]).decode("latin-1", errors="replace")
                offset += units_len
            # Optional C_RESFMT, C_LLMFMT, C_HLMFMT, LO_SPEC, HI_SPEC
            for _fmt_idx in range(3):
                if offset < end:
                    flen = data[offset]
                    offset += 1 + flen
                    if offset > end:
                        self._flag_malformed(15, 10, rec_offset, "PTR format string truncated")
                        offset = end
                        break
            for _spec_idx in range(2):
                if offset < end:
                    if offset + 4 > end:
                        self._flag_malformed(15, 10, rec_offset, "PTR spec limit truncated")
                        offset = end
                        break
                    offset += 4
            if offset < end:
                self._flag_extra_bytes(15, 10, rec_offset, end - offset, rec_len)
        except (IndexError, struct.error):
            test_txt = test_txt or ""
            units = units or ""

        fields.update({
            "OPT_FLAG": opt_flag,
            "LO_LIMIT_VALID": not bool(opt_flag & 0x50),
            "HI_LIMIT_VALID": not bool(opt_flag & 0xA0),
            "TEST_TXT": test_txt,
            "LO_LIMIT": lo_limit,
            "HI_LIMIT": hi_limit,
            "UNITS": units,
        })
        return fields


def summarize_sbr_records(sbr_records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize SBR records into total part count and pass part count (Perl 8).

    Uses summary SBRs (HEAD_NUM == 255) if present; otherwise falls back to
    summing per-site SBR records (HEAD_NUM != 255).
    """
    if not sbr_records:
        return {"has_sbr": False, "total_cnt": None, "good_cnt": None, "fail_cnt": None}

    summary_rows = [r for r in sbr_records if r.get("HEAD_NUM") == 255]
    active_rows = summary_rows if summary_rows else [r for r in sbr_records if r.get("HEAD_NUM") != 255]
    if not active_rows:
        return {"has_sbr": False, "total_cnt": None, "good_cnt": None, "fail_cnt": None}

    total_cnt = 0
    good_cnt = 0
    fail_cnt = 0
    has_valid_cnt = False
    has_pf_flag = False

    for row in active_rows:
        cnt = row.get("SBIN_CNT")
        if cnt is None or cnt == STDF_UNSET_U4:
            continue
        has_valid_cnt = True
        total_cnt += int(cnt)
        pf = str(row.get("SBIN_PF") or "").strip().upper()
        if pf == "P":
            has_pf_flag = True
            good_cnt += int(cnt)
        elif pf == "F":
            has_pf_flag = True
            fail_cnt += int(cnt)

    return {
        "has_sbr": has_valid_cnt,
        "total_cnt": total_cnt if has_valid_cnt else None,
        "good_cnt": good_cnt if (has_valid_cnt and has_pf_flag) else None,
        "fail_cnt": fail_cnt if (has_valid_cnt and has_pf_flag) else None,
    }


def summarize_pcr_records(pcr_records: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Select the summary PCR (HEAD_NUM == 255) or aggregate per-site PCR records."""
    if not pcr_records:
        return None
    for pcr in reversed(pcr_records):
        if pcr and pcr.get("HEAD_NUM") == 255:
            return pcr
    # Fallback: sum per-site PCRs
    part_cnt = 0
    good_cnt = 0
    has_part = False
    has_good = False
    for pcr in pcr_records:
        if not pcr:
            continue
        pc = pcr.get("PART_CNT")
        if pc is not None and pc != STDF_UNSET_U4:
            part_cnt += int(pc)
            has_part = True
        gc = pcr.get("GOOD_CNT")
        if gc is not None and gc != STDF_UNSET_U4:
            good_cnt += int(gc)
            has_good = True
    return {
        "HEAD_NUM": 255,
        "SITE_NUM": 0,
        "PART_CNT": part_cnt if has_part else None,
        "GOOD_CNT": good_cnt if has_good else None,
        "RTST_CNT": None,
        "ABRT_CNT": None,
        "FUNC_CNT": None,
    }


def reconcile_stdf_counts(
    prr_total: int,
    prr_good: int,
    pcr: Optional[Dict[str, Any]],
    sbr_summary: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compare summary PCR part/good counts and SBR counts against PRRs parsed (Perl 8)."""
    pcr_total = pcr.get("PART_CNT") if pcr else None
    pcr_good = pcr.get("GOOD_CNT") if pcr else None
    if pcr_total == STDF_UNSET_U4:
        pcr_total = None
    if pcr_good == STDF_UNSET_U4:
        pcr_good = None

    sbr_total = sbr_summary.get("total_cnt") if sbr_summary else None
    sbr_good = sbr_summary.get("good_cnt") if sbr_summary else None

    mismatches: List[str] = []
    if pcr_total is not None and pcr_total != prr_total:
        mismatches.append(f"PCR Part({pcr_total}) != PRR({prr_total})")
    if pcr_good is not None and pcr_good != prr_good:
        mismatches.append(f"PCR Good({pcr_good}) != PRR Good({prr_good})")
    if sbr_total is not None and sbr_total != prr_total:
        mismatches.append(f"SBR Part({sbr_total}) != PRR({prr_total})")
    if sbr_good is not None and sbr_good != prr_good:
        mismatches.append(f"SBR Good({sbr_good}) != PRR Good({prr_good})")
    if pcr_total is not None and sbr_total is not None and pcr_total != sbr_total:
        pair_msg = f"PCR Part({pcr_total}) != SBR Part({sbr_total})"
        if not any("PCR Part" in m and "SBR Part" in m for m in mismatches) and prr_total in (pcr_total, sbr_total):
            pass  # Already flagged against PRR
        elif pair_msg not in mismatches and not mismatches:
            mismatches.append(pair_msg)

    return {
        "has_mismatch": bool(mismatches),
        "mismatches": mismatches,
        "mismatch_text": "; ".join(mismatches) if mismatches else "None",
        "prr_total": prr_total,
        "prr_good": prr_good,
        "pcr_total": pcr_total,
        "pcr_good": pcr_good,
        "sbr_total": sbr_total,
        "sbr_good": sbr_good,
    }


def validate_stdf_data(
    parsed: Dict[str, Any],
    expected_lot_id: str = "",
) -> Dict[str, Any]:
    """Validate parsed STDF data and return named skip/failure reason codes (Perl 4).

    Reason codes:
    - INVALID_FAR        : Missing or invalid FAR / CPU_TYP / STDF_VER != 4 / endian mismatch
    - EXTRA_BYTES        : Fixed or fully-parsed record has leftover/extra bytes
    - TRUNCATED_FILE     : File ended mid-header or mid-record
    - MALFORMED_RECORD   : Record had truncated or corrupt fields
    - MISSING_MIR_FIELDS : Required MIR fields (LOT_ID, JOB_NAM, TSTR_TYP, START_T) missing
    - INVALID_LOT        : MIR LOT_ID is empty or does not match expected_lot_id
    - NO_PARTS           : Zero complete PRR parts in file
    - NO_MRR             : Missing MRR or invalid MRR FINISH_T
    - COUNT_MISMATCH     : PCR or SBR counts do not match PRRs parsed
    """
    reasons: List[str] = []
    messages: List[str] = []

    diag = parsed.get("DIAGNOSTICS") or {}
    mir = parsed.get("MIR") or {}
    mrr = parsed.get("MRR") or {}
    parts = parsed.get("PARTS")
    prr_total = parsed.get("PRR_TOTAL", len(parts) if isinstance(parts, list) else 0)
    reconciliation = parsed.get("RECONCILIATION") or {}

    if not diag.get("far_valid", True):
        reasons.append(REASON_INVALID_FAR)
        far_errs = [e for e in (diag.get("errors") or []) if "FAR" in e or "0-10" in e]
        messages.append(far_errs[0] if far_errs else "Invalid FAR header (CPU_TYP / STDF_VER / endianness check failed).")

    extra_cnt = int(diag.get("extra_bytes_count") or 0)
    if extra_cnt > 0:
        reasons.append(REASON_EXTRA_BYTES)
        messages.append(f"Found {extra_cnt} record(s) with leftover/extra bytes.")

    if diag.get("truncated") or diag.get("is_truncated") or not diag.get("ended_at_eof", True):
        reasons.append(REASON_TRUNCATED_FILE)
        messages.append("STDF file is truncated (unexpected EOF or missing/non-terminal MRR).")

    malformed_cnt = int(diag.get("malformed_count") or 0)
    if malformed_cnt > 0:
        reasons.append(REASON_MALFORMED_RECORD)
        messages.append(f"Found {malformed_cnt} malformed/partial record(s).")

    # Required MIR fields check (Perl 4: lot, program, tester, start time)
    missing_mir: List[str] = []
    if not mir:
        missing_mir.extend(["LOT_ID", "JOB_NAM", "TSTR_TYP", "START_T"])
    else:
        if not str(mir.get("LOT_ID") or "").strip():
            missing_mir.append("LOT_ID")
        if not str(mir.get("JOB_NAM") or "").strip():
            missing_mir.append("JOB_NAM")
        if not str(mir.get("TSTR_TYP") or "").strip() and not str(mir.get("NODE_NAM") or "").strip():
            missing_mir.append("TSTR_TYP")
        start_t = mir.get("START_T")
        if not isinstance(start_t, int) or start_t <= 0 or start_t == STDF_UNSET_U4:
            missing_mir.append("START_T")

    if missing_mir:
        reasons.append(REASON_MISSING_MIR_FIELDS)
        messages.append(f"Missing required MIR field(s): {', '.join(missing_mir)}.")

    # Lot ID validation against user-inputted Lot ID (Perl 4)
    mir_lot = str(mir.get("LOT_ID") or "").strip()
    expected_clean = str(expected_lot_id or "").strip()
    if not mir_lot:
        reasons.append(REASON_INVALID_LOT)
        messages.append("MIR LOT_ID is missing or empty.")
    elif expected_clean and not is_lot_id_matching(mir_lot, expected_clean):
        reasons.append(REASON_INVALID_LOT)
        messages.append(
            f"MIR LOT_ID '{mir_lot}' does not match user-inputted Lot ID '{expected_clean}'."
        )

    if prr_total <= 0:
        reasons.append(REASON_NO_PARTS)
        messages.append("No complete part results (PRR) found in file.")

    finish_t = mrr.get("FINISH_T") if isinstance(mrr, dict) else None
    mrr_is_last = diag.get("mrr_is_last", True)
    if not mrr or finish_t in (None, "", 0, STDF_UNSET_U4):
        reasons.append(REASON_NO_MRR)
        messages.append("Missing MRR or invalid MRR FINISH_T.")
    elif not mrr_is_last:
        reasons.append(REASON_NO_MRR)
        messages.append("MRR is not the last record in the file (offset + len != file_size).")

    if reconciliation.get("has_mismatch"):
        reasons.append(REASON_COUNT_MISMATCH)
        messages.append(f"Count reconciliation mismatch: {reconciliation.get('mismatch_text')}")

    return {
        "valid": len(reasons) == 0,
        "reasons": reasons,
        "messages": messages,
    }


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


def _find_record_stream_sync(mm, search_start: int, file_size: int, s_hdr_unpack) -> Optional[int]:
    """Find the first valid STDF record boundary at or after `search_start` by verifying
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
    logger: LogFunc = None,
) -> List[Dict]:
    """Return the unique PTR test list (TEST_NUM/TXT/limits/units) for a file.

    Prevents missing tests that only appear in later parts while remaining fast:
    1. Smallfiles (<= 16 MB) are scanned 100% end-to-end.
    2. For larger files, head stability requires consecutive passing (GOOD) parts
       (not early-aborting fail parts) AND stable fail parts.
    3. When the head scan pauses on a large file, it jumps to the tail window (last 2 MB)
       to scan the final parts and all TSR (10, 30) Test Synopsis Records (which list
       every test executed across the entire lot). If TSR reveals any test that only
       executed in the middle of the file, scanning automatically resumes to capture its PTR.
    """
    tests: Dict[int, Dict] = {}
    with STDFReader(filepath, progress_callback=progress_callback) as parser:
        mm = parser._mm
        if mm is None:
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
            for msg in parser.warnings + parser.errors:
                _emit_log(msg, False, logger)
            return [_prepare_scanned_test_row(row) for row in sorted(tests.values(), key=lambda r: r["TEST_NUM"])]

        file_size = parser.file_size
        has_cb = progress_callback is not None
        p_step = parser._progress_step
        report = parser._report_progress

        far_ok, offset = parser.validate_far_header(mm, 0, file_size)
        if not far_ok and offset == 0:
            for msg in parser.warnings + parser.errors:
                _emit_log(msg, False, logger)
            return []

        s_I_unpack = parser._s_I.unpack_from
        s_hdr_unpack = parser._s_hdr.unpack_from
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
                parser.truncated = True
                parser._add_error(
                    rec_typ, rec_sub, offset,
                    f"Truncated record body at EOF (need {rec_len} bytes, only {file_size - body_start} available)",
                )
                break

            if rec_typ == 15 and rec_sub == 10:
                if rec_len >= 4:
                    test_num = s_I_unpack(mm, body_start)[0]
                    if test_num not in tests:
                        fields = parse_ptr(mm, include_meta=True, start=body_start, end=body_end, rec_offset=offset) or {}
                        tests[test_num] = {
                            "TEST_NUM": test_num, "TEST_TXT": fields.get("TEST_TXT", ""),
                            "LO_LIMIT": fields.get("LO_LIMIT", float("nan")),
                            "HI_LIMIT": fields.get("HI_LIMIT", float("nan")),
                            "UNITS": fields.get("UNITS", ""),
                        }
            elif rec_typ == 10 and rec_sub == 30:  # TSR (Test Synopsis Record)
                if rec_len >= 20:
                    t_typ = mm[body_start + 2]
                    t_num = s_I_unpack(mm, body_start + 3)[0]
                    exec_cnt = s_I_unpack(mm, body_start + 7)[0]
                    if exec_cnt > 0 and exec_cnt != STDF_UNSET_U4 and t_typ in (ord("P"), ord("M"), ord(" ")):
                        tnam_len = mm[body_start + 19]
                        tnam = ""
                        if body_start + 20 + tnam_len <= body_end:
                            tnam = bytes(mm[body_start + 20:body_start + 20 + tnam_len]).decode("latin-1", errors="replace")
                        if t_num not in tests:
                            tests[t_num] = {
                                "TEST_NUM": t_num, "TEST_TXT": tnam,
                                "LO_LIMIT": float("nan"), "HI_LIMIT": float("nan"), "UNITS": "",
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
                        if not added_new:
                            stable_good_run += 1
                        else:
                            stable_good_run = 0
                    else:
                        fail_parts_seen += 1
                        if not added_new:
                            stable_fail_run += 1
                        else:
                            stable_fail_run = 0

                    if stable_good_run >= min_good_stable and (fail_parts_seen == 0 or stable_fail_run >= 2):
                        stopped_early = True
                        head_stop_offset = body_end
                        break

            offset = body_end
            if has_cb and offset >= next_p:
                report(offset)
                next_p = offset + p_step

        # Tail TSR (10, 30) & final parts verification when head scan stopped early on a large file
        if stopped_early and head_stop_offset < file_size:
            tail_window = 2 * 1024 * 1024  # 2 MB tail window covers final parts + all TSR records
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
                            fields = parse_ptr(mm, include_meta=True, start=b_start, end=b_end, rec_offset=t_off) or {}
                            tests[test_num] = {
                                "TEST_NUM": test_num, "TEST_TXT": fields.get("TEST_TXT", ""),
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
                                tnam = bytes(mm[b_start + 20:b_start + 20 + tnam_len]).decode("latin-1", errors="replace")
                            if t_num not in tests:
                                tests[t_num] = {
                                    "TEST_NUM": t_num, "TEST_TXT": tnam,
                                    "LO_LIMIT": float("nan"), "HI_LIMIT": float("nan"), "UNITS": "",
                                }
                                missing_from_head.add(t_num)
                            elif tnam and not tests[t_num].get("TEST_TXT"):
                                tests[t_num]["TEST_TXT"] = tnam
                    t_off = b_end

            # If TSR revealed any middle-only test that was not seen in head/tail PTRs
            # (or if the file had no TSR records in the tail), scan forward from head_stop_offset.
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
                            fields = parse_ptr(mm, include_meta=True, start=b_start, end=b_end, rec_offset=m_off) or {}
                            prev_txt = tests[test_num].get("TEST_TXT", "") if test_num in tests else ""
                            tests[test_num] = {
                                "TEST_NUM": test_num, "TEST_TXT": fields.get("TEST_TXT", "") or prev_txt,
                                "LO_LIMIT": fields.get("LO_LIMIT", float("nan")),
                                "HI_LIMIT": fields.get("HI_LIMIT", float("nan")),
                                "UNITS": fields.get("UNITS", ""),
                            }
                            missing_from_head.discard(test_num)
                    m_off = b_end

            if has_cb:
                report(file_size)
        else:
            if has_cb:
                report(file_size)

        if not stopped_early and not parser.truncated and offset < file_size:
            parser.truncated = True
            parser._add_error(
                0, 0, offset,
                f"Truncated record header at EOF ({file_size - offset} trailing byte(s) left)",
            )

        for msg in parser.warnings + parser.errors:
            _emit_log(msg, False, logger)

    return [_prepare_scanned_test_row(row) for row in sorted(tests.values(), key=lambda r: r["TEST_NUM"])]


def parse_filter_values(typed_tests_text: str, selected_tests_text: str,
                        range_from: str, range_to: str) -> Optional[Set[int]]:
    filter_tests: Set[int] = set()
    typed_tests_text = typed_tests_text.strip()
    selected_tests_text = selected_tests_text.strip()
    range_from = range_from.strip()
    range_to = range_to.strip()

    if typed_tests_text:
        normalized = typed_tests_text.replace(",", " ")
        for token in normalized.split():
            num_match = re.match(r"^(\d+)", token)
            if num_match:
                filter_tests.add(int(num_match.group(1)))
            else:
                filter_tests.add(int(token))

    if selected_tests_text:
        for item in selected_tests_text.split(","):
            item = item.strip()
            if item:
                num_match = re.match(r"^(\d+)", item)
                if num_match:
                    filter_tests.add(int(num_match.group(1)))
                else:
                    tok = item.split()[0] if item.split() else ""
                    if tok.isdigit():
                        filter_tests.add(int(tok))

    if range_from or range_to:
        if not range_from or not range_to:
            raise ValueError("Both Range From and Range To must be filled in.")
        lo, hi = int(range_from), int(range_to)
        if lo > hi:
            raise ValueError("Range From cannot be greater than Range To.")
        filter_tests.update(range(lo, hi + 1))
    return filter_tests or None


def enforce_strict_analysis_validation(parsed: Dict[str, Any], filepath: str = "") -> None:
    """Raise ValueError if parsed STDF data violates strict Run Analysis rules:
    1. Invalid FAR header (CPU_TYP / STDF_VER != 4 / endian mismatch)
    2. Any record has leftover/extra bytes (Perl 2)
    3. MIR LOT_ID does not match the user-inputted Lot ID (Perl 4)
    """
    fname = os.path.basename(filepath) if filepath else (parsed.get("DIAGNOSTICS", {}).get("filename") or "STDF file")
    diag = parsed.get("DIAGNOSTICS") or {}
    validation = parsed.get("VALIDATION") or {}
    reasons = set(validation.get("reasons") or [])

    if REASON_INVALID_FAR in reasons or not diag.get("far_valid", True):
        errs = [e for e in (diag.get("errors") or []) if "FAR" in e or "0-10" in e]
        detail = errs[0] if errs else "Invalid FAR header (CPU_TYP or STDF_VER validation failed)."
        raise ValueError(f"Header validation failed in '{fname}':\n\n{detail}")

    extra_cnt = int(diag.get("extra_bytes_count") or 0)
    if extra_cnt > 0 or REASON_EXTRA_BYTES in reasons:
        sample_warns = [w for w in (diag.get("warnings") or []) if "leftover/extra byte" in w][:5]
        details_txt = "\n".join(sample_warns)
        raise ValueError(
            f"Analysis aborted for '{fname}': found {extra_cnt} record(s) with extra/leftover bytes.\n\n"
            + (f"Details:\n{details_txt}" if details_txt else "")
        )

    if REASON_INVALID_LOT in reasons:
        lot_msgs = [m for m in (validation.get("messages") or []) if "LOT_ID" in m]
        detail = lot_msgs[0] if lot_msgs else "MIR LOT_ID does not match user-inputted Lot ID."
        raise ValueError(f"Lot ID validation failed in '{fname}':\n\n{detail}")


def parse_stdf_file(
    input_path: str,
    filter_tests: Optional[Set[int]] = None,
    verbose: bool = True,
    logger: LogFunc = None,
    progress_callback: ByteProgressFunc = None,
    expected_lot_id: str = "",
    strict_validation: bool = False,
) -> Tuple[Dict[str, object], Dict[str, int], int]:
    """Parse an STDF file with strict header validation, leftover-bytes detection,
    unset-value normalization, HBR/SBR/PCR count reconciliation, and MIR Lot ID validation."""
    if filter_tests is not None:
        _emit_log(f"Reading STDF : {input_path}", verbose, logger)
    else:
        _emit_log(f"Reading STDF : {input_path}  (all PTR tests)", verbose, logger)

    parts: List[Dict] = []
    part_results: List[Dict[int, float]] = []
    mir_info: Dict[str, object] = {}
    mrr_info: Dict[str, object] = {}
    pcr_list: List[Dict[str, Any]] = []
    hbr_list: List[Dict[str, Any]] = []
    sbr_list: List[Dict[str, Any]] = []
    open_slot: Dict[int, int] = {}
    last_rec_type: Optional[Tuple[int, int]] = None
    mrr_end_offset: int = -1

    with STDFReader(input_path, filter_tests=filter_tests, progress_callback=progress_callback) as reader:
        parse_mir = reader.parse_mir_compact
        parse_mrr = reader.parse_mrr_compact
        parse_pcr = reader.parse_pcr_compact
        parse_hbr = reader.parse_hbr_compact
        parse_sbr = reader.parse_sbr_compact
        parse_pir = reader.parse_pir_compact
        parse_prr = reader.parse_prr_compact
        open_slot_get = open_slot.get
        open_slot_pop = open_slot.pop
        parts_append = parts.append
        part_results_append = part_results.append

        mm = reader._mm
        if mm is not None:
            file_size = reader.file_size
            has_cb = progress_callback is not None
            p_step = reader._progress_step
            report = reader._report_progress

            if file_size == 0:
                if not reader.errors:
                    reader.truncated = True
                    reader._add_error(0, 10, 0, "Empty STDF file (0 bytes)")
                offset = 0
                far_ok = False
            else:
                far_ok, offset = reader.validate_far_header(mm, 0, file_size)

            s_I_unpack = reader._s_I.unpack_from
            s_f_unpack = reader._s_f.unpack_from
            s_hdr_unpack = reader._s_hdr.unpack_from
            next_p = offset + p_step

            while (far_ok or offset > 0) and offset + 4 <= file_size:
                rec_len, rec_typ, rec_sub = s_hdr_unpack(mm, offset)
                body_start = offset + 4
                body_end = body_start + rec_len
                if body_end > file_size:
                    reader.truncated = True
                    reader._add_error(
                        rec_typ, rec_sub, offset,
                        f"Truncated record body at EOF (need {rec_len} bytes, only {file_size - body_start} available)",
                    )
                    break
                last_rec_type = (rec_typ, rec_sub)

                if rec_typ == 15 and rec_sub == 10:
                    if rec_len >= 12:
                        test_num = s_I_unpack(mm, body_start)[0]
                        if filter_tests is not None and test_num not in filter_tests:
                            offset = body_end
                            if has_cb and offset >= next_p:
                                report(offset)
                                next_p = offset + p_step
                            continue
                        head_num = mm[body_start + 4]
                        site_num = mm[body_start + 5]
                        result_val = s_f_unpack(mm, body_start + 8)[0]
                        part_idx = open_slot_get((head_num << 8) | site_num, -1)
                        if 0 <= part_idx < len(part_results):
                            part_results[part_idx][test_num] = result_val
                    else:
                        reader._flag_malformed(15, 10, offset, f"PTR record too short ({rec_len} bytes < 12)")
                    offset = body_end
                    if has_cb and offset >= next_p:
                        report(offset)
                        next_p = offset + p_step
                    continue

                if rec_typ == 5:
                    if rec_sub == 10:  # PIR
                        pir = parse_pir(mm, start=body_start, end=body_end, rec_offset=offset)
                        if pir:
                            head = pir["HEAD_NUM"]
                            site = pir["SITE_NUM"]
                            part_idx = len(parts)
                            open_slot[(head << 8) | site] = part_idx
                            parts_append({"PART_IDX": part_idx, "HAS_PRR": False})
                            part_results_append({})
                    elif rec_sub == 20:  # PRR
                        fields = parse_prr(mm, start=body_start, end=body_end, rec_offset=offset)
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
                        fields = parse_mir(mm, start=body_start, end=body_end, rec_offset=offset)
                        if fields and not mir_info:
                            mir_info.update(fields)
                    elif rec_sub == 20:  # MRR
                        fields = parse_mrr(mm, start=body_start, end=body_end, rec_offset=offset)
                        if fields:
                            if not mrr_info:
                                mrr_info.update(fields)
                            mrr_end_offset = body_end
                    elif rec_sub == 30:  # PCR
                        fields = parse_pcr(mm, start=body_start, end=body_end, rec_offset=offset)
                        if fields:
                            pcr_list.append(fields)
                    elif rec_sub == 40:  # HBR
                        fields = parse_hbr(mm, start=body_start, end=body_end, rec_offset=offset)
                        if fields:
                            hbr_list.append(fields)
                    elif rec_sub == 50:  # SBR
                        fields = parse_sbr(mm, start=body_start, end=body_end, rec_offset=offset)
                        if fields:
                            sbr_list.append(fields)

                offset = body_end
                if has_cb and offset >= next_p:
                    report(offset)
                    next_p = offset + p_step

            if (far_ok or offset > 0) and not reader.truncated and offset < file_size:
                reader.truncated = True
                reader._add_error(
                    0, 0, offset,
                    f"Truncated record header at EOF ({file_size - offset} trailing byte(s) left)",
                )
            if has_cb:
                report(file_size)
        else:
            # Fallback: streaming (non-mmap) path via the unified record generator
            endian_captured = False
            s_I = reader._s_I
            s_f = reader._s_f
            for rec_typ, rec_sub, data, start, end in reader.record_spans():
                if not endian_captured:
                    endian_captured = True
                    s_I = reader._s_I
                    s_f = reader._s_f
                if rec_typ == 0 and rec_sub == 10:
                    continue
                if rec_typ == 15 and rec_sub == 10:
                    if data is not None and end - start >= 12:
                        try:
                            test_num = s_I.unpack_from(data, start)[0]
                            if filter_tests is not None and test_num not in filter_tests:
                                continue
                            head_num = data[start + 4]
                            site_num = data[start + 5]
                            result_val = s_f.unpack_from(data, start + 8)[0]
                        except (struct.error, IndexError):
                            continue
                        part_idx = open_slot_get((head_num << 8) | site_num, -1)
                        if 0 <= part_idx < len(part_results):
                            part_results[part_idx][test_num] = result_val
                    else:
                        reader._flag_malformed(15, 10, start, f"PTR record too short ({end - start} bytes < 12)")
                    continue
                if data is None:
                    continue
                if rec_typ == 5:
                    if rec_sub == 10:
                        pir = parse_pir(data, start=start, end=end)
                        if not pir:
                            continue
                        head = pir["HEAD_NUM"]
                        site = pir["SITE_NUM"]
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
                        fields = parse_mir(data, start=start, end=end)
                        if fields and not mir_info:
                            mir_info.update(fields)
                    elif rec_sub == 20:
                        fields = parse_mrr(data, start=start, end=end)
                        if fields and not mrr_info:
                            mrr_info.update(fields)
                    elif rec_sub == 30:
                        fields = parse_pcr(data, start=start, end=end)
                        if fields:
                            pcr_list.append(fields)
                    elif rec_sub == 40:
                        fields = parse_hbr(data, start=start, end=end)
                        if fields:
                            hbr_list.append(fields)
                    elif rec_sub == 50:
                        fields = parse_sbr(data, start=start, end=end)
                        if fields:
                            sbr_list.append(fields)

        ended_at_eof = bool((offset == reader.file_size) if mm is not None else (not reader.truncated))
        last_record_was_mrr = bool(last_rec_type == (1, 20))
        mrr_is_last = bool(
            mrr_info
            and not reader.truncated
            and last_rec_type == (1, 20)
            and mrr_end_offset == reader.file_size
        )
        diagnostics = {
            "filename": reader.filename,
            "filepath": input_path,
            "far_valid": reader.far_valid,
            "far_info": reader.far_info,
            "endian": reader.endian,
            "truncated": reader.truncated,
            "ended_at_eof": ended_at_eof,
            "last_record_was_mrr": last_record_was_mrr,
            "mrr_is_last": mrr_is_last,
            "extra_bytes_count": reader.extra_bytes_count,
            "extra_bytes_records": list(reader.extra_bytes_records),
            "malformed_count": reader.malformed_count,
            "warnings": list(reader.warnings),
            "errors": list(reader.errors),
        }

    # B9: Inline filter — no separate _filter_complete_parts() call
    filtered_parts: List[Dict] = []
    filtered_results: List[Dict[int, float]] = []
    dropped = 0
    prr_good_cnt = 0
    for part, result in zip(parts, part_results):
        if part.get("HAS_PRR"):
            filtered_parts.append(part)
            filtered_results.append(result)
            if not (int(part.get("PART_FLG", 0) or 0) & 0x08):
                prr_good_cnt += 1
        else:
            dropped += 1

    if dropped:
        drop_msg = f"[WARNING] {os.path.basename(input_path)}: Dropped {dropped:,} incomplete part(s) without PRR."
        diagnostics["warnings"].append(drop_msg)
        _emit_log(drop_msg, verbose, logger)

    # PCR may be absent (e.g. truncated/incomplete file with no summary records).
    summary_pcr = summarize_pcr_records(pcr_list)
    pcr_part_cnt = summary_pcr.get("PART_CNT") if summary_pcr else None
    if pcr_part_cnt == STDF_UNSET_U4:
        pcr_part_cnt = None
    prr_count = len(filtered_parts)
    prr_vs_pcr_match = bool(pcr_part_cnt is None or pcr_part_cnt == prr_count)
    is_truncated = bool(reader.truncated or not ended_at_eof or not last_record_was_mrr)

    integrity_flags: Dict[str, Any] = {
        "ended_at_eof": ended_at_eof,
        "last_record_was_mrr": last_record_was_mrr,
        "pir_without_prr": dropped,
        "prr_count": prr_count,
        "pcr_part_cnt": pcr_part_cnt,
        "prr_vs_pcr_match": prr_vs_pcr_match,
        "is_truncated": is_truncated,
    }
    diagnostics.update(integrity_flags)

    if is_truncated:
        trunc_warn = (
            f"[WARNING] {os.path.basename(input_path)}: Truncated or incomplete STDF file! "
            f"ended_at_eof={ended_at_eof}, last_record_was_mrr={last_record_was_mrr}, "
            f"pir_without_prr={dropped}, PRR count={prr_count} vs PCR PART_CNT={pcr_part_cnt}."
        )
        if trunc_warn not in diagnostics["warnings"]:
            diagnostics["warnings"].append(trunc_warn)

    sbr_summary = summarize_sbr_records(sbr_list)
    reconciliation = reconcile_stdf_counts(len(filtered_parts), prr_good_cnt, summary_pcr, sbr_summary)

    if reconciliation["has_mismatch"]:
        mismatch_msg = (
            f"[WARNING] {os.path.basename(input_path)}: Count reconciliation mismatch: "
            f"{reconciliation['mismatch_text']}"
        )
        diagnostics["warnings"].append(mismatch_msg)

    parsed_dict: Dict[str, Any] = {
        "PARTS": filtered_parts,
        "RESULTS": filtered_results,
        "MIR": mir_info,
        "MRR": mrr_info,
        "PCR": summary_pcr,
        "PCR_LIST": pcr_list,
        "HBR": hbr_list,
        "SBR": sbr_list,
        "SBR_SUMMARY": sbr_summary,
        "PRR_TOTAL": len(filtered_parts),
        "PRR_GOOD": prr_good_cnt,
        "RECONCILIATION": reconciliation,
        "DIAGNOSTICS": diagnostics,
        "INTEGRITY": integrity_flags,
        "ended_at_eof": ended_at_eof,
        "last_record_was_mrr": last_record_was_mrr,
        "pir_without_prr": dropped,
        "prr_count": prr_count,
        "pcr_part_cnt": pcr_part_cnt,
        "prr_vs_pcr_match": prr_vs_pcr_match,
        "is_truncated": is_truncated,
    }

    validation = validate_stdf_data(parsed_dict, expected_lot_id=expected_lot_id)
    parsed_dict["VALIDATION"] = validation

    if REASON_INVALID_LOT in validation["reasons"]:
        lot_msgs = [m for m in validation["messages"] if "LOT_ID" in m]
        if lot_msgs:
            err_line = f"[ERROR] {os.path.basename(input_path)} [MIR LOT_ID]: {lot_msgs[0]}"
            if err_line not in diagnostics["errors"]:
                diagnostics["errors"].append(err_line)

    if REASON_MISSING_MIR_FIELDS in validation["reasons"]:
        mir_msgs = [m for m in validation["messages"] if "MIR field" in m]
        if mir_msgs:
            warn_line = f"[WARNING] {os.path.basename(input_path)} [MIR]: {mir_msgs[0]}"
            if warn_line not in diagnostics["warnings"]:
                diagnostics["warnings"].append(warn_line)

    # Emit all collected warnings and errors to logger when provided
    if logger is not None:
        for w in diagnostics["warnings"]:
            logger(w)
        for e in diagnostics["errors"]:
            logger(e)
        diagnostics["_logged"] = True

    if strict_validation:
        enforce_strict_analysis_validation(parsed_dict, input_path)

    return parsed_dict, {}, 0


def parse_check_summary(
    filepath: str,
    expected_lot_id: str = "",
    logger: LogFunc = None,
    force_full_scan: bool = False,
) -> Dict[str, Any]:
    """Parse STDF summary, validate FAR/MIR/MRR, check for leftover bytes & truncation,
    and reconcile PCR + SBR counts against PRRs parsed (Perl Features 1, 2, 3, 4, 5, 8).

    For large files (> 256 KB), employs C-accelerated PRR jump-scanning (`mm.find(b'\\x05\\x14')`)
    across PTR/MPR/FTR blocks so every genuine PRR record is counted and reconciled against
    PCR and SBR in milliseconds without starving the Tkinter GIL.
    """
    result: Dict[str, Any] = {
        "mir": None,
        "mrr": None,
        "pcr": None,
        "hbr": [],
        "sbr": [],
        "sbr_summary": None,
        "prr_total": 0,
        "prr_good": 0,
        "reconciliation": None,
        "discrepancies": [],
        "discrepancy_text": "None",
        "extra_bytes_count": 0,
        "malformed_count": 0,
        "truncated": False,
        "far_valid": False,
        "mrr_is_last": False,
        "warnings": [],
        "errors": [],
        "validation": None,
    }

    mir_info: Dict[str, Any] = {}
    mrr_info: Dict[str, Any] = {}
    pcr_list: List[Dict[str, Any]] = []
    hbr_list: List[Dict[str, Any]] = []
    sbr_list: List[Dict[str, Any]] = []
    prr_total = 0
    prr_good = 0
    dropped_pir = 0
    open_slots: Set[int] = set()
    last_rec_type: Optional[Tuple[int, int]] = None
    mrr_end_offset: int = -1

    try:
        with STDFReader(filepath) as reader:
            mm = reader._mm
            file_size = reader.file_size
            if mm is not None:
                if file_size == 0:
                    if not reader.errors:
                        reader.truncated = True
                        reader._add_error(0, 10, 0, "Empty STDF file (0 bytes)")
                    far_ok, offset = False, 0
                else:
                    far_ok, offset = reader.validate_far_header(mm, 0, file_size)
                    if far_ok:
                        last_rec_type = (0, 10)

                s_hdr_unpack = reader._s_hdr.unpack_from
                s_H_unpack = reader._s_H.unpack_from
                mm_find = mm.find
                known_records = _KNOWN_RECORDS
                use_prr_jump = (not force_full_scan) and (file_size > 256 * 1024)
                loop_iters = 0

                while (far_ok or offset > 0) and offset + 4 <= file_size:
                    rec_len, rec_typ, rec_sub = s_hdr_unpack(mm, offset)
                    body_start = offset + 4
                    body_end = body_start + rec_len
                    if body_end > file_size:
                        reader.truncated = True
                        reader._add_error(
                            rec_typ, rec_sub, offset,
                            f"Truncated record body at EOF (need {rec_len} bytes, only {file_size - body_start} available)",
                        )
                        break

                    last_rec_type = (rec_typ, rec_sub)

                    # Fast skip over PTR/MPR/FTR (rec_typ == 15) blocks during Check STDF
                    if rec_typ == 15:
                        if rec_sub == 10 and rec_len < 12:
                            reader._flag_malformed(15, 10, offset, f"PTR record too short ({rec_len} bytes < 12)")
                        if use_prr_jump:
                            # C-speed jump directly to the next PRR (5, 20 -> b'\x05\x14')
                            search_pos = body_end + 2
                            jumped = False
                            while search_pos < file_size:
                                idx = mm_find(b"\x05\x14", search_pos)
                                if idx == -1:
                                    break
                                search_pos = idx + 2
                                cand = idx - 2
                                c_len = s_H_unpack(mm, cand)[0]
                                if c_len < 17 or c_len > 1024:
                                    continue
                                c_end = cand + 4 + c_len
                                if c_end > file_size:
                                    continue
                                # O(1) pre-filter (cheapest + most selective check first): the record
                                # right after a genuine PRR must be a known non-FAR STDF V4 header, or
                                # the PRR must end exactly at EOF. The full chain check below requires
                                # this anyway, so this only rejects false '\x05\x14' hits earlier.
                                if c_end != file_size:
                                    if c_end + 4 > file_size:
                                        continue
                                    n_typ0 = mm[c_end + 2]
                                    n_sub0 = mm[c_end + 3]
                                    if (n_typ0, n_sub0) not in known_records or (n_typ0 == 0 and n_sub0 == 10):
                                        continue
                                pos = cand + 4 + 17
                                prr_ok = True
                                for _ in range(3):
                                    if pos < c_end:
                                        pos += 1 + mm[pos]
                                        if pos > c_end:
                                            prr_ok = False
                                            break
                                if not prr_ok:
                                    continue
                                probe = c_end
                                chain_steps = 0
                                chain_ok = True
                                while probe + 4 <= file_size and chain_steps < 4:
                                    n_len, n_typ, n_sub = s_hdr_unpack(mm, probe)
                                    if (n_typ, n_sub) not in known_records or (n_typ == 0 and n_sub == 10):
                                        chain_ok = False
                                        break
                                    n_end = probe + 4 + n_len
                                    if n_end > file_size:
                                        chain_ok = False
                                        break
                                    probe = n_end
                                    chain_steps += 1
                                if chain_ok and (
                                    (pos == c_end and (chain_steps >= 2 or probe == file_size))
                                    or (pos < c_end and (chain_steps >= 3 or (chain_steps >= 1 and probe == file_size)))
                                ):
                                    # Single-pass consumption: the PRR fields were already fully
                                    # validated above, so count it here and continue right after it
                                    # instead of re-unpacking and re-walking it in the outer loop.
                                    if pos < c_end:
                                        reader._flag_extra_bytes(5, 20, cand, c_end - pos, c_len)
                                    open_slots.discard((mm[cand + 4] << 8) | mm[cand + 5])
                                    prr_total += 1
                                    if not (mm[cand + 6] & 0x08):
                                        prr_good += 1
                                    last_rec_type = (5, 20)
                                    offset = c_end
                                    jumped = True
                                    break
                            if jumped:
                                loop_iters += 1
                                if (loop_iters & 0xFF) == 0:
                                    time.sleep(0)  # Cooperatively yield GIL to Tkinter UI
                                continue

                        offset = body_end
                        while offset + 4 <= file_size and mm[offset + 2] == 15:
                            r_len, r_typ, r_sub = s_hdr_unpack(mm, offset)
                            b_end = offset + 4 + r_len
                            if b_end > file_size:
                                break
                            last_rec_type = (r_typ, r_sub)
                            if r_sub == 10 and r_len < 12:
                                reader._flag_malformed(15, 10, offset, f"PTR record too short ({r_len} bytes < 12)")
                            offset = b_end
                            loop_iters += 1
                            if (loop_iters & 0xFFFF) == 0:
                                time.sleep(0)  # Cooperatively yield GIL to Tkinter UI
                        continue

                    if rec_typ == 5:
                        if rec_sub == 10:  # PIR
                            if rec_len < 2:
                                reader._flag_malformed(5, 10, offset, f"PIR record too short ({rec_len} bytes < 2)")
                            else:
                                if rec_len > 2:
                                    reader._flag_extra_bytes(5, 10, offset, rec_len - 2, rec_len)
                                open_slots.add((mm[body_start] << 8) | mm[body_start + 1])
                        elif rec_sub == 20:  # PRR — fast inline count + leftover-bytes & truncation check
                            if rec_len < 17:
                                reader._flag_malformed(5, 20, offset, f"PRR record too short ({rec_len} bytes < 17)")
                            else:
                                head_num = mm[body_start]
                                site_num = mm[body_start + 1]
                                part_flg = mm[body_start + 2]
                                pos = body_start + 17
                                malformed_prr = False
                                for _fld_name in ("PART_ID", "PART_TXT", "PART_FIX"):
                                    if pos < body_end:
                                        flen = mm[pos]
                                        pos += 1 + flen
                                        if pos > body_end:
                                            reader._flag_malformed(
                                                5, 20, offset,
                                                f"PRR {_fld_name} truncated (len={flen}, exceeds record end by {pos - body_end} byte(s))",
                                            )
                                            malformed_prr = True
                                            break
                                if not malformed_prr:
                                    if pos < body_end:
                                        reader._flag_extra_bytes(5, 20, offset, body_end - pos, rec_len)
                                    open_slots.discard((head_num << 8) | site_num)
                                    prr_total += 1
                                    if not (part_flg & 0x08):
                                        prr_good += 1
                    elif rec_typ == 1:
                        if rec_sub == 10:  # MIR
                            fields = reader.parse_mir_compact(mm, start=body_start, end=body_end, rec_offset=offset)
                            if fields and not mir_info:
                                mir_info.update(fields)
                        elif rec_sub == 20:  # MRR
                            fields = reader.parse_mrr_compact(mm, start=body_start, end=body_end, rec_offset=offset)
                            if fields:
                                mrr_info.update(fields)
                                mrr_end_offset = body_end
                        elif rec_sub == 30:  # PCR
                            fields = reader.parse_pcr_compact(mm, start=body_start, end=body_end, rec_offset=offset)
                            if fields:
                                pcr_list.append(fields)
                        elif rec_sub == 40:  # HBR
                            fields = reader.parse_hbr_compact(mm, start=body_start, end=body_end, rec_offset=offset)
                            if fields:
                                hbr_list.append(fields)
                        elif rec_sub == 50:  # SBR
                            fields = reader.parse_sbr_compact(mm, start=body_start, end=body_end, rec_offset=offset)
                            if fields:
                                sbr_list.append(fields)

                    offset = body_end
                    loop_iters += 1
                    if (loop_iters & 0xFFFF) == 0:
                        time.sleep(0)  # Cooperatively yield GIL to Tkinter UI

                if (far_ok or offset > 0) and not reader.truncated and offset < file_size:
                    reader.truncated = True
                    reader._add_error(
                        0, 0, offset,
                        f"Truncated record header at EOF ({file_size - offset} trailing byte(s) left)",
                    )
            else:
                for rec_typ, rec_sub, data, start, end in reader.record_spans():
                    if data is None or (rec_typ == 0 and rec_sub == 10):
                        continue
                    if rec_typ == 5:
                        if rec_sub == 10:
                            pir = reader.parse_pir_compact(data, start=start, end=end)
                            if pir:
                                open_slots.add((pir["HEAD_NUM"] << 8) | pir["SITE_NUM"])
                        elif rec_sub == 20:
                            prr = reader.parse_prr_compact(data, start=start, end=end)
                            if prr:
                                open_slots.discard((prr["HEAD_NUM"] << 8) | prr["SITE_NUM"])
                                prr_total += 1
                                if not (int(prr.get("PART_FLG", 0) or 0) & 0x08):
                                    prr_good += 1
                    elif rec_typ == 1:
                        if rec_sub == 10:
                            fields = reader.parse_mir_compact(data, start=start, end=end)
                            if fields and not mir_info:
                                mir_info.update(fields)
                        elif rec_sub == 20:
                            fields = reader.parse_mrr_compact(data, start=start, end=end)
                            if fields:
                                mrr_info.update(fields)
                                last_rec_type = (1, 20)
                                mrr_end_offset = file_size
                        elif rec_sub == 30:
                            fields = reader.parse_pcr_compact(data, start=start, end=end)
                            if fields:
                                pcr_list.append(fields)
                        elif rec_sub == 40:
                            fields = reader.parse_hbr_compact(data, start=start, end=end)
                            if fields:
                                hbr_list.append(fields)
                        elif rec_sub == 50:
                            fields = reader.parse_sbr_compact(data, start=start, end=end)
                            if fields:
                                sbr_list.append(fields)

            dropped_pir = len(open_slots)
            if dropped_pir:
                reader._add_warning(
                    5, 10, file_size,
                    f"{dropped_pir} orphan PIR record(s) without matching PRR at EOF.",
                )

            # Item 6: Verify that MRR is actually the last record in the file (offset + 4 + rec_len == file_size)
            mrr_is_last = bool(
                mrr_info
                and not reader.truncated
                and last_rec_type == (1, 20)
                and mrr_end_offset == file_size
            )
            if mrr_info and not reader.truncated and not mrr_is_last:
                reader._add_error(
                    1, 20, max(mrr_end_offset, 0),
                    f"MRR is not the last record in the file (MRR ended at {mrr_end_offset}, "
                    f"file_size={file_size}, last_record={last_rec_type})",
                )

            summary_pcr = summarize_pcr_records(pcr_list)
            sbr_summary = summarize_sbr_records(sbr_list)
            reconciliation = reconcile_stdf_counts(prr_total, prr_good, summary_pcr, sbr_summary)

            ended_at_eof = bool((offset == reader.file_size) if mm is not None else (not reader.truncated))
            last_record_was_mrr = bool(last_rec_type == (1, 20))
            is_truncated = bool(reader.truncated or not ended_at_eof or not mrr_is_last)
            diagnostics = {
                "filename": reader.filename,
                "filepath": filepath,
                "far_valid": reader.far_valid,
                "far_info": reader.far_info,
                "endian": reader.endian,
                "truncated": reader.truncated,
                "ended_at_eof": ended_at_eof,
                "last_record_was_mrr": last_record_was_mrr,
                "mrr_is_last": mrr_is_last,
                "pir_without_prr": dropped_pir,
                "prr_count": prr_total,
                "pcr_part_cnt": summary_pcr.get("PART_CNT") if summary_pcr else None,
                "is_truncated": is_truncated,
                "extra_bytes_count": reader.extra_bytes_count,
                "extra_bytes_records": list(reader.extra_bytes_records),
                "malformed_count": reader.malformed_count,
                "warnings": list(reader.warnings),
                "errors": list(reader.errors),
            }

            pseudo_parsed = {
                "MIR": mir_info,
                "MRR": mrr_info,
                "PCR": summary_pcr,
                "HBR": hbr_list,
                "SBR": sbr_list,
                "PRR_TOTAL": prr_total,
                "PRR_GOOD": prr_good,
                "RECONCILIATION": reconciliation,
                "DIAGNOSTICS": diagnostics,
            }
            validation = validate_stdf_data(pseudo_parsed, expected_lot_id=expected_lot_id)

            discrepancies: List[str] = []
            if not reader.far_valid:
                discrepancies.append("Invalid FAR/Header")
            if reader.truncated:
                discrepancies.append("Truncated File")
            elif not mrr_info:
                discrepancies.append("Missing MRR")
            elif not mrr_is_last:
                discrepancies.append("MRR Not at EOF")
            if reader.malformed_count > 0:
                discrepancies.append(f"Malformed ({reader.malformed_count} rec)")
            if reader.extra_bytes_count > 0:
                discrepancies.append(f"Extra Bytes ({reader.extra_bytes_count} rec)")
            if REASON_INVALID_LOT in validation["reasons"]:
                mir_lot_val = str(mir_info.get("LOT_ID") or "missing").strip()
                discrepancies.append(f"Lot ID Mismatch (MIR: {mir_lot_val})")
                err_line = (
                    f"[ERROR] {reader.filename} [MIR LOT_ID]: MIR LOT_ID '{mir_lot_val}' "
                    f"does not match expected Lot ID '{expected_lot_id}'."
                )
                if err_line not in diagnostics["errors"]:
                    diagnostics["errors"].append(err_line)
            if REASON_MISSING_MIR_FIELDS in validation["reasons"]:
                discrepancies.append("Missing MIR Fields")
            if reconciliation["has_mismatch"]:
                discrepancies.extend(reconciliation["mismatches"])
                warn_line = (
                    f"[WARNING] {reader.filename} [Count Reconciliation]: "
                    f"{reconciliation['mismatch_text']}"
                )
                if warn_line not in diagnostics["warnings"]:
                    diagnostics["warnings"].append(warn_line)

            discrepancy_text = "; ".join(discrepancies) if discrepancies else "None"

            result.update({
                "mir": mir_info or None,
                "mrr": mrr_info or None,
                "pcr": summary_pcr,
                "hbr": hbr_list,
                "sbr": sbr_list,
                "sbr_summary": sbr_summary,
                "prr_total": prr_total,
                "prr_good": prr_good,
                "reconciliation": reconciliation,
                "discrepancies": discrepancies,
                "discrepancy_text": discrepancy_text,
                "extra_bytes_count": reader.extra_bytes_count,
                "malformed_count": reader.malformed_count,
                "truncated": reader.truncated,
                "ended_at_eof": ended_at_eof,
                "last_record_was_mrr": last_record_was_mrr,
                "pir_without_prr": dropped_pir,
                "prr_count": prr_total,
                "pcr_part_cnt": summary_pcr.get("PART_CNT") if summary_pcr else None,
                "is_truncated": is_truncated,
                "far_valid": reader.far_valid,
                "mrr_is_last": mrr_is_last,
                "warnings": diagnostics["warnings"],
                "errors": diagnostics["errors"],
                "validation": validation,
            })

            if logger is not None:
                for w in diagnostics["warnings"]:
                    logger(w)
                for e in diagnostics["errors"]:
                    logger(e)
    except Exception as exc:
        err_msg = f"[ERROR] {os.path.basename(filepath)}: Failed to check STDF file: {exc}"
        result["errors"].append(err_msg)
        result["discrepancies"].append("Read Error")
        result["discrepancy_text"] = "Read Error"
        if logger is not None:
            logger(err_msg)
    return result


# -- Parallel-parsing worker (module-level so it is picklable for spawn) -------
def _parse_stdf_worker(task):
    """Parse one STDF file in a child process.

    task = (input_path, filter_list) or (input_path, filter_list, expected_lot_id).
    Returns (input_path, parsed_dict). Diagnostics and validation results are
    included inside `parsed_dict` so the parent process can emit all warnings
    and errors to the Activity Log and enforce strict validation rules.
    """
    if len(task) >= 3:
        input_path, filter_list, expected_lot_id = task[0], task[1], task[2]
    else:
        input_path, filter_list = task[0], task[1]
        expected_lot_id = ""
    filter_tests = set(filter_list) if filter_list is not None else None
    parsed, _, _ = parse_stdf_file(
        input_path,
        filter_tests=filter_tests,
        verbose=False,
        logger=None,
        progress_callback=None,
        expected_lot_id=expected_lot_id,
        strict_validation=False,
    )
    return input_path, parsed
