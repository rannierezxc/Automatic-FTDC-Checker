"""Diagnostic: reveals WHY parallel parsing did/didn't engage.
Run this the SAME way you launch the app (same folder / same interpreter):
    python diag_parallel.py
"""
import os, sys, inspect

print("Python           :", sys.executable)
print("os.cpu_count()   :", os.cpu_count())

import guid_analysis as G

# 1) WHICH guid_analysis.py is actually imported? (catches stale/duplicate copies)
print("guid_analysis    :", inspect.getfile(G))

# 2) Is the cross-panel code even present in the imported module?
src = inspect.getsource(G)
print("has cross-panel  :", "across ALL panels" in src)
print("has pre_parsed   :", "pre_parsed" in src)

# 3) The two flags that gate parallelism
print("PARALLEL_DEFAULT_ENABLED :", getattr(G, "PARALLEL_DEFAULT_ENABLED", "MISSING"))

# 4) The exact decision for your 1/2/2 layout (5 files), as gui_app calls it (max_workers=None)
try:
    print("resolve(None, 5) :", G._resolve_worker_count(None, 5), " <-- must be > 1 to go parallel")
except Exception as e:
    print("resolve(None, 5) : ERROR", e)

# 5) Is a process pool usable in THIS runtime at all?
try:
    from concurrent.futures import ProcessPoolExecutor
    def _sq(x): return x * x
    if __name__ == "__main__":
        with ProcessPoolExecutor(max_workers=4) as ex:
            r = list(ex.map(_sq, [1, 2, 3, 4]))
        print("ProcessPool test :", r, "(expected [1, 4, 9, 16])")
except Exception as e:
    print("ProcessPool test : FAILED ->", repr(e))

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()