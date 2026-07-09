"""
Cython 编译辅助：将 Python 模块编译为 .pyd 原生扩展。
需要 Cython + C 编译器（MSVC/MinGW）。
"""
import os
import sys
from setuptools import setup
from Cython.Build import cythonize

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(PROJECT_ROOT, "app")

modules = [
    "api",
    "cdp",
    "config",
    "guard",
    "license",
    "session",
    "socket_client",
    "worker",
]

cython_modules = []
for m in modules:
    src = os.path.join(APP_DIR, f"{m}.py")
    if os.path.isfile(src):
        cython_modules.append(os.path.relpath(src, PROJECT_ROOT))

if not cython_modules:
    print("无源文件可编译")
    sys.exit(0)

setup(
    name="easyflow_cython",
    ext_modules=cythonize(
        cython_modules,
        compiler_directives={"language_level": "3"},
    ),
    script_args=["build_ext", "--inplace"],
)
