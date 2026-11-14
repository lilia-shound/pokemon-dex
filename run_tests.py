#!/usr/bin/env python3
"""跑全部单元测试。零依赖，用标准库 unittest。

    python3 run_tests.py
"""
import sys, unittest, pathlib
ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
loader = unittest.TestLoader()
suite = loader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
res = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if res.wasSuccessful() else 1)
