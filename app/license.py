"""
单机绑定：RSA 非对称签名校验。exe 仅含公钥，私钥由开发者保管。
公钥可验证签名，但无法伪造签名。
"""
import base64
import hashlib
import json
import os
import subprocess
import sys
import uuid

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.exceptions import InvalidSignature

from . import config
from ._pubkey import PUBLIC_KEY

_PUBLIC_KEY = serialization.load_pem_public_key(PUBLIC_KEY.encode())


def _run(args):
    try:
        out = subprocess.check_output(args, shell=True, stderr=subprocess.DEVNULL,
                                      creationflags=subprocess.CREATE_NO_WINDOW
                                      if hasattr(subprocess, "CREATE_NO_WINDOW") else 0)
        return out.decode("gbk", errors="replace").strip()
    except Exception:
        return ""


def _mac():
    node = uuid.getnode()
    return ":".join(f"{(node >> i) & 0xFF:02x}" for i in range(40, -1, -8)) if node else "unknown"


def _disk_serial():
    for cmd in [r'powershell -Command "(Get-CimInstance Win32_DiskDrive).SerialNumber"',
                r'wmic diskdrive get serialnumber']:
        raw = _run(cmd)
        for line in raw.split("\n"):
            line = line.strip()
            if line and line != "SerialNumber":
                return line
    return "unknown"


def _cpu_id():
    for cmd in [r'powershell -Command "(Get-CimInstance Win32_Processor).ProcessorId"',
                r'wmic cpu get processorid']:
        raw = _run(cmd)
        for line in raw.split("\n"):
            line = line.strip()
            if line and line != "ProcessorId":
                return line
    return "unknown"


def machine_id():
    raw = f"{_mac()}|{_disk_serial()}|{_cpu_id()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _lic_path():
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.argv[0])
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "license.lic")


def _payload(data_dict):
    return json.dumps(data_dict, ensure_ascii=False, sort_keys=True).encode("utf-8")


def sign_lic_with_private_key(data_dict, private_key_pem):
    """用 RSA 私钥签名，返回带签名的 data_dict。"""
    data_dict.pop("_sig", None)
    payload = _payload(data_dict)
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    sig = key.sign(payload, padding.PKCS1v15(), hashes.SHA256())
    data_dict["_sig"] = base64.b64encode(sig).decode()
    return data_dict


def read_lic(path=None):
    p = path or _lic_path()
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            raw = f.read()
        data = json.loads(raw)
        sig_str = data.pop("_sig", None)
        if not sig_str:
            return None
        sig = base64.b64decode(sig_str)
        payload = _payload(data)
        _PUBLIC_KEY.verify(sig, payload, padding.PKCS1v15(), hashes.SHA256())
        if isinstance(data, dict) and "machines" in data:
            return data
    except (InvalidSignature, Exception):
        pass
    return None


def bind():
    current = machine_id()
    data = read_lic()
    if data and current in data.get("machines", []):
        return True
    os.makedirs(config.app_data_dir(), exist_ok=True)
    try:
        write_lic({"machines": [current], "max_instances": 10})
        return True
    except Exception:
        return False


def write_lic(data_dict, path=None):
    out = path or _lic_path()
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data_dict, f, ensure_ascii=False, indent=2)


def verify():
    current = machine_id()
    data = read_lic()
    if not data:
        return False, "未找到有效 license.lic\n签名无效或文件被篡改"
    if current in data.get("machines", []):
        return True, ""
    return False, "当前机器未授权\n机器ID不在白名单中"


def get_max_instances():
    data = read_lic()
    if data:
        return max(1, int(data.get("max_instances", 10) or 10))
    return 10
