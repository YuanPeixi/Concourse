# PyInstaller onedir: only source modules and dependency resources are collected.
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

root = Path(SPECPATH).parent
pw_data, pw_binaries, pw_hidden = collect_all("playwright")
a = Analysis([str(root / "scripts" / "desktop_entry.py")], pathex=[str(root)],
             binaries=pw_binaries, datas=pw_data,
             hiddenimports=pw_hidden + collect_submodules("uvicorn") +
             ["aiosqlite", "sqlalchemy.dialects.sqlite.aiosqlite", "fetchers.scholat",
              "fetchers.itestcloud", "fetchers.moodle", "fetchers.codeforces"],
             hookspath=[], runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="concourse-backend",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="concourse-backend")
