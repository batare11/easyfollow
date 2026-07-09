"""
机器指纹展示 + 授权文件签名工具。
签名需要 license_private.pem（RSA 私钥），只有开发者持有。
"""
import json
import os
import sys

from .license import (_mac, _disk_serial, _cpu_id, machine_id,
                       write_lic, read_lic, _lic_path, sign_lic_with_private_key)

_PRIVATE_KEY_FILE = "license_private.pem"


def show():
    mid = machine_id()
    print("=" * 56)
    print("  EasyFlow 机器指纹信息")
    print("=" * 56)
    print(f"  MAC 地址    : {_mac()}")
    print(f"  硬盘序列号  : {_disk_serial()}")
    print(f"  CPU ID      : {_cpu_id()}")
    print(f"  ─────────────────────────────")
    print(f"  机器唯一 ID : {mid}")
    print("=" * 56)


def sign_lic(input_file=None):
    if input_file is None:
        input_file = os.path.join(os.getcwd(), "license.lic")
    if not os.path.isfile(input_file):
        print("license.lic 不存在，创建默认模板（仅当前机器）")
        data = {"machines": [machine_id()], "max_instances": 3}
    else:
        try:
            with open(input_file, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        except Exception as e:
            print(f"解析 JSON 失败: {e}")
            return
    if "machines" not in data or not isinstance(data["machines"], list):
        print("错误: 缺少 machines 字段（需为字符串数组）")
        return
    # 查找私钥文件
    key_paths = [_PRIVATE_KEY_FILE, os.path.join(os.getcwd(), _PRIVATE_KEY_FILE)]
    key_file = None
    for kp in key_paths:
        if os.path.isfile(kp):
            key_file = kp
            break
    if not key_file:
        print(f"错误: 找不到私钥文件 {_PRIVATE_KEY_FILE}")
        print("请确保 license_private.pem 在当前目录或 exe 同级目录")
        return
    try:
        with open(key_file, "r", encoding="utf-8") as f:
            private_key_pem = f.read()
    except Exception as e:
        print(f"读取私钥失败: {e}")
        return
    try:
        signed = sign_lic_with_private_key(data, private_key_pem)
        write_lic(signed, input_file)
        print(f"已签名: {input_file}")
        print(f"白名单: {len(signed['machines'])} 台机器")
        print(f"最大实例数: {signed.get('max_instances', 10)}")
    except Exception as e:
        print(f"签名失败: {e}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--sign-lic":
        sign_lic(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        show()
