"""
EasyFlow build script - reads build.key and runs PyInstaller.
Usage: python build.py
"""
import hashlib
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

def run(cmd):
    print(f"  RUN: {' '.join(cmd[:4])} ...")
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        print(f"  [ERROR] exit code {result.returncode}")
        sys.exit(1)

# 1) Read build key
key_path = os.path.join(PROJECT_ROOT, "build.key")
if not os.path.isfile(key_path):
    print("[ERROR] build.key not found")
    sys.exit(1)
with open(key_path, "r", encoding="utf-8") as f:
    build_key = f.read().strip()
print(f"[1/4] Build key loaded ({len(build_key)} chars)")

# 2) Generate _buildhash.py
h = hashlib.sha256(build_key.encode()).hexdigest()
hash_path = os.path.join(PROJECT_ROOT, "app", "_buildhash.py")
with open(hash_path, "w", encoding="utf-8") as f:
    f.write(f"BUILD_HASH = {repr(h)}\n")
print("[2/4] Key hash generated")

# 3) Generate RSA key pair (first time only) / ensure exists
pubkey_path = os.path.join(PROJECT_ROOT, "app", "_pubkey.py")
privkey_path = os.path.join(PROJECT_ROOT, "license_private.pem")
if not os.path.isfile(pubkey_path):
    print("[Keys] Generating RSA key pair (first time) ...")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    private_key = rsa.generate_private_key(65537, 2048)
    public_key = private_key.public_key()
    with open(privkey_path, "wb") as f:
        f.write(private_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
    pub_pem = public_key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    with open(pubkey_path, "w", encoding="utf-8") as f:
        f.write("PUBLIC_KEY = " + repr(pub_pem))
    print(f"       Private: {privkey_path}")
    print(f"       Public:  {pubkey_path}")
else:
    print("[3/4] RSA pubkey OK")

# 4) PyInstaller build
print("[4/4] PyInstaller build ...")
python_exe = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                          "Programs", "Python", "Python312", "python.exe")
if not os.path.isfile(python_exe):
    python_exe = sys.executable

run([python_exe, "-m", "PyInstaller",
     "--noconfirm", "--onefile", "--windowed",
     "--name", "EasyFollow", "--clean",
     "--key=" + build_key,
     "--hidden-import=app._pubkey",
     "--hidden-import=app._buildhash",
     "--hidden-import=cryptography",
     "--collect-submodules=socketio",
     "--collect-submodules=engineio",
     "--collect-submodules=simplewebsocket",
     "--collect-submodules=wsproto",
     "main.py"])

# Cleanup
for d in ["build"]:
    path = os.path.join(PROJECT_ROOT, d)
    if os.path.isdir(path):
        import shutil
        shutil.rmtree(path, ignore_errors=True)
spec = os.path.join(PROJECT_ROOT, "EasyFollow.spec")
if os.path.isfile(spec):
    os.remove(spec)

print()
# ========================================
print("=" * 40)
print("  Done: dist/EasyFollow.exe")
print("=" * 40)

# Copy private key to dist (for --sign-lic convenience)
import shutil
if os.path.isfile(privkey_path):
    dist_priv = os.path.join(PROJECT_ROOT, "dist", "license_private.pem")
    shutil.copy2(privkey_path, dist_priv)
    print(f"       Private key: dist/license_private.pem (keep safe)")

# 6) Sign license for current machine
print()
print("[Sign] Creating license.lic for current machine ...")
lic_path = os.path.join(PROJECT_ROOT, "dist", "license.lic")
priv_path = os.path.join(PROJECT_ROOT, "license_private.pem")
if os.path.isfile(priv_path):
    import json
    from app import license as lic
    d = {"machines": [lic.machine_id()], "max_instances": 3}
    with open(priv_path, "r", encoding="utf-8") as f:
        pk = f.read()
    signed = lic.sign_lic_with_private_key(d, pk)
    lic.write_lic(signed, lic_path)
    print(f"       Signed for machine: {lic.machine_id()[:16]}...")
else:
    print("       (no license_private.pem, skipped)")

