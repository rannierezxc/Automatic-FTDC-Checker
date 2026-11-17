# PTS Hub — Automatic FTDC Checker: Detailed End-to-End Process Flow

Editable source formats in this folder:
- **[PTS_Hub_Process_Flow.pptx](PTS_Hub_Process_Flow.pptx)** *(4-slide Microsoft PowerPoint deck — native editable shapes, diamonds & snapped connectors)*
- **[PTS_Hub_Process_Flow.drawio](PTS_Hub_Process_Flow.drawio)** *(4-tab Draw.io / diagrams.net XML diagram)*
- Below: 4 detailed text-editable **Mermaid** diagrams covering the complete architecture.

---

## Stage 1: Startup, MES Lookup, Get STDF & Check STDF

```mermaid
flowchart TD
    S1["1. Launch PTS_Hub.exe / main.py<br/>freeze_support(), AppUserModelID, external assets/, pre-warm HTTP & Device caches"] --> S2["2. Open FTDC Checker Tab<br/>Load settings.json; enter Lot ID, FP/Total Actual Good QTY + optional Manual Filter"]
    S2 --> D1{"Complete Lot ID?"}
    D1 -- "Yes" --> M1["MES SOAP GetLotByLotId<br/>Auto-fill 12-char MPC & background FTDC Fail prefetch"]
    M1 -- "New / Unsupported Mask" --> M2["Device Settings Manager<br/>Edit mpc_partnumber / conventions / mask_wxy_map JSONs & reload caches"]
    M1 -- "MPC OK" --> G1
    D1 -- "Browse Local Files" --> P1["Manually Add FIRST PASS / RETEST / QC Files"]
    M2 --> F1["Get FTDC Fail (Optional)<br/>Query primary -> backup ftdc/filter.php; show fail table popup"]
    S2 --> G1["3. Get STDF: Resolve MPC, Tester & Conventions<br/>Lookup mask_wxy_map / mpc_partnumber / stdf_filename_conventions"]
    G1 --> G2["SMB Pushdown Scan (FindFirstFileExW)<br/>Filter STDF files on tester/common share; prompt if multi-tester"]
    G2 --> G3["Parallel Atomic Copy to C:\FTDC\<Lot><br/>CopyFileExW via .part -> rename + 2-worker streaming Check STDF pre-warm"]
    G3 --> G4["4-Worker Pre-Warm Finish & Save<br/>Flush cache/check_stdf_cache.json & open C:\FTDC\<Lot>"]
    G3 --> C1["4. Check STDF Discovery<br/>Build file signature (abs_path, size, mtime_ns)"]
    P1 --> C1
    C1 --> D2{"Check Cache Hit?"}
    D2 -- "Hit" --> C3["Display Check STDF Diagnostics Table<br/>PASS/FAIL, LOT_ID mismatch, extra bytes, missing MRR, Load/Delete buttons"]
    D2 -- "Miss" --> C2["C-Fast PRR Tail / Full Scan (ThreadPoolExecutor)<br/>Verify FAR/MIR/MRR, TEST_T==0 & trailing bytes"] --> C3
    C3 --> R1["Click Run Analysis (Get Data) -> Stage 2"]
```

---

## Stage 2: Run Gate, Strict Validation, Parallel STDF Parsing & Coordinate Filtering

```mermaid
flowchart TD
    R1["5. Click Run Analysis (start_get_data)<br/>Uppercase Lot ID & MPC; log details snapshot"] --> V1{"Inputs Valid?<br/>Panels, Int QTYs, 12-char MPC,<br/>Mask or 3 Manual Filter Tests"}
    V1 -- "No" --> ERR1["Show Validation Error Dialog & Abort"]
    V1 -- "Yes" --> CKEY{"_analysis_cache Hit?<br/>Key: inputs + file (size, mtime_ns)"}
    CKEY -- "Hit" --> FAST["Reuse Cached Result -> Jump to Stage 4 Delivery"]
    CKEY -- "Miss" --> SORT["6. Resolve WXY/Multi-ID Tests & Timestamp-Sort<br/>Ascending by first YYYYMMDDHHMMSS in filename per panel"]
    SORT --> POOL["7. Cross-Panel Pool Dispatch<br/>Largest STDF -> main thread w/ byte progress (15->92%)<br/>Remaining smaller STDFs -> ProcessPoolExecutor"]
    POOL --> IO["Local File -> Zero-Copy mmap.ACCESS_READ<br/>.gz / UNC File -> Streamed Binary Reader"]
    IO --> STDF1["FAR (0,10) & MIR (1,10)<br/>Offset 0, STDF_VER=4, endian detect, MIR.LOT_ID match (strip .RJ<N>)"]
    STDF1 --> STDF2["PIR (5,10) -> PTR (15,10) -> PRR (5,20)<br/>4B TEST_NUM filter; PART_FLG&0x08 PASS/FAIL; normalize 65535 & TEST_T==0"]
    STDF2 --> STDF3["PCR/HBR/SBR & MRR (1,20) EOF Check<br/>Reconcile counts, drop orphan PIRs, require mrr_end == file_size"]
    STDF3 --> SV{"Strict Validation OK?<br/>(FAR, MIR, no trailing bytes, >0 PRRs)"}
    SV -- "Fail" --> ERR2["Block Run Analysis & Log Filename + Byte Offset"]
    SV -- "Pass" --> WXY["8. Format WXY = '{Wafer}_{X}_{Y}' & Filter<br/>INVALID if NaN, Wafer == 0 regardless of X/Y (e.g. 0_1_2), or >=2 zeros (0_0_0, 24_0_0)"]
    WXY --> MID{"Multi-ID Mask?<br/>(e.g. SG406 / UID+BT+ZB)"}
    MID -- "Yes" --> MIDEX["Extract UID (!=NaN) & BT/ZB MACIDs (prefix 64_132 / 40:84) -> Stage 3"]
    MID -- "No" --> S3["Standard WXY -> Stage 3"]
```

---

## Stage 3: Duplicate Engines, Multi-ID Cross-Check, QC Check 4 & FTDC Criteria

```mermaid
flowchart TD
    S3["9. Intern Valid WXYs (_wid >= 1)<br/>Invalid WXYs (NaN, 0_1_2, 0_0_0, 24_0_0) assigned 0 (excluded)"] --> FP["FIRST PASS Scan (_wid >= 1)<br/>Repeated in FP (PASS/FAIL) -> +1 fp_datalog_duplicate_count<br/>Active FP PASS -> +1 duplicate_guid"]
    FP --> FPP{"FP Row == PASS?"}
    FPP -- "PASS" --> FP1["Add to fp_pass_ids & wxy_head_pass_ids"]
    FPP -- "FAIL" --> FP2["Discard from wxy_head_pass_ids;<br/>if HARD_BIN != 3 & not in retest_all_ids -> +1 fp_rejects_not_in_retest"]
    FP1 --> RT
    FP2 --> RT["10. RETEST Scan (_wid >= 1)<br/>Priority 1 (retest_pass_seen_ids) -> +1 retest_duplicate_guid<br/>Priority 2 (wxy_head_pass_ids) -> +1 duplicate_guid<br/>PASS sets active flags; FAIL clears both (allows recovery)"]
    RT --> MID{"Multi-ID Mode?"}
    MID -- "Yes" --> MIDENG["11A. Multi-ID Engines (FP+RETEST+QC)<br/>Map BT/ZB MACID -> set of UIDs; >1 UID -> Dup MACID<br/>Good Unique Count Cross-Check (WXY vs UID vs BT vs ZB)"]
    MID -- "No" --> QC
    MIDENG --> QC["11B. QC Check 4 Verification<br/>total_good_ids = fp_pass_ids U retest_pass_ids<br/>Check QC unique WXYs; missing/failed tagged for RED Excel rows"]
    QC --> CALC["12. Compute Good Quantities & Deltas<br/>Calc_FP = FP_PASS - duplicate_guid | Delta_FP = Calc_FP - FP_Actual<br/>Calc_Total = Total_PASS - Total_Dup_GUID | Delta_Total = Calc_Total - Total_Actual"]
    CALC --> CRIT["13. Evaluate Check 1, Check 2, Check 4 & Status<br/>Check 1: Delta_Total>=0, FP_Dup=0 (+ 0 MACID/Unique fails)<br/>Check 2: Total_Dup <= 72 | Check 4: QC fail == 0<br/>Multi-Violation Remarks -> Stage 4"]
```

---

## Stage 4: Result Popup, Stage 1 Excel/PNG, Stage 2 PPO Auto-Attach & Cleanup

```mermaid
flowchart TD
    D1["14. _deliver_analysis_result<br/>Save _analysis_cache, cache summary/details, log Detailed Report & FTDC fails"] --> UI["Show FTDC Result Popup Window<br/>Status badges, FP/Total tables, Multi-ID details, Remarks, Download Excel"]
    UI --> CLK{"User Clicks<br/>Download & Open Excel?"}
    CLK -- "Stage 1 Ongoing" --> WAIT["Show 'Excel generation still ongoing' Popup"]
    CLK -- "Stage 1 Ready" --> OPEN["Save & Open .xlsx Immediately (works during Stage 2!)"]

    D1 --> ST1["15. Stage 1: Background Excel & PNG (0-100%, ETA)<br/>Set _excel_state = IN_PROGRESS"]
    ST1 --> XLSX["xlsxwriter (strings_to_urls=False)<br/>Summary, FT Total, QC Total, Dup GUID FP, QC FAIL (+ Multi-ID sheets)"]
    XLSX --> PNG["Pre-render Result Snapshot .png (result_image.py) -> Set Stage 1 READY"]

    PNG --> CHK{"Already Attached<br/>for Cached Result?"}
    CHK -- "Skip" --> OKDONE["17. PPO Done + Success Popup<br/>Update status & unlock UI"]
    CHK -- "No" --> SCAN["16. Stage 2: Scan PPO Subfolders (~15ms)<br/>\\chip\PPOonline\<Lot> -> N = 2 x subfolders (Done 0/N)"]
    SCAN --> COPY["Parallel Atomic Copy (CopyFileExW + .part -> rename)<br/>Transfer .xlsx & .png (Done X/N | MB/s | ETA); up to 3 attempts (3s, 8s)"]
    COPY --> RES{"Attach Succeeded?"}
    RES -- "OK" --> OKDONE
    RES -- "Partial / Fail" --> FB["Save Local Fallback Copy to C:\FTDC\<Lot>\<br/>Notify user with Open Local Folder option"]

    OKDONE --> END["18. Clear All / App Exit<br/>Clear All resets UI/caches/gen IDs; Exit saves logs\FTDC_Logs_*.log & deletes temp caches"]
    FB --> END
```
