import ctypes
import hashlib
import os
import sys
import tkinter as tk
from tkinter import messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _alloc_console():
    if sys.platform == "win32":
        ctypes.windll.kernel32.AllocConsole()
        conout = open("CONOUT$", "w", encoding="utf-8")
        sys.stdout = conout
        sys.stderr = conout
        sys.stdin = open("CONIN$", "r", encoding="utf-8")


def _cmd_show_id():
    _alloc_console()
    from app import tools
    tools.show()
    input("\n按回车键退出...")
    sys.exit(0)


def _cmd_sign_lic(input_file):
    _alloc_console()
    if input_file and not os.path.isabs(input_file):
        input_file = os.path.abspath(input_file)
    from app import tools
    tools.sign_lic(input_file)
    input("\n按回车键退出...")
    sys.exit(0)


def _anti_tamper():
    """运行时完整性校验：确保 exe 未被篡改/解包重新打包。"""
    if not getattr(sys, "frozen", False):
        return
    try:
        exe = sys.argv[0]
        size = os.path.getsize(exe)
        if size < 1024 * 1024:  # < 1MB 说明被提取过
            raise RuntimeError("文件异常")
    except Exception:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("安全警告", "程序文件已被篡改，无法运行")
        sys.exit(1)


def _cmd_gen_keys():
    _alloc_console()
    from app._buildhash import BUILD_HASH
    print("=" * 50)
    print("  RSA 密钥对生成（需 build.key 密码）")
    print("=" * 50)
    pwd = input("请输入 build.key 密码: ").strip()
    if hashlib.sha256(pwd.encode()).hexdigest() != BUILD_HASH:
        print("密码错误，操作取消")
        input("\n按回车键退出...")
        sys.exit(1)
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import os as _os
    print("生成 RSA 2048 密钥对...")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    # 私钥（你保管，绝不外泄）
    priv_path = _os.path.join(_os.getcwd(), "license_private.pem")
    with open(priv_path, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()))
    print(f"私钥已生成: {priv_path}")
    print("  ⚠  请妥善保管，不要随 exe 分发")

    # 公钥（嵌入exe）
    pub_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    pub_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "app", "_pubkey.py") if not getattr(sys, "frozen", False) else _os.path.join(_os.path.dirname(sys.argv[0]), "..", "app", "_pubkey.py")
    with open(pub_path, "w", encoding="utf-8") as f:
        f.write("PUBLIC_KEY = " + repr(pub_pem))
    print(f"公钥已写入: {pub_path}")
    print()
    print("下一步: 运行 build.bat 重新打包")
    input("\n按回车键退出...")
    sys.exit(0)


if __name__ == "__main__":
    _anti_tamper()
    if "--gen-keys" in sys.argv:
        _cmd_gen_keys()
    if "--show-id" in sys.argv or "-s" in sys.argv:
        _cmd_show_id()
    if "--sign-lic" in sys.argv:
        idx = sys.argv.index("--sign-lic")
        f = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None
        _cmd_sign_lic(f)

    from app import license, guard, gui

    # 1) 单机绑定校验
    ok, msg = license.verify()
    if not ok:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("授权失败", msg)
        sys.exit(1)

    # 2) 多开限制
    max_inst = license.get_max_instances()
    acquired, slot = guard.check_and_acquire()
    if not acquired:
        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning("已达上限",
                               f"最多允许 {max_inst} 个实例同时运行\n请关闭一些窗口后重试")
        sys.exit(1)

    gui.run()
