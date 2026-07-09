import sys, os, json, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test():
    try:
        from app import tools
        tools.sign_lic(r"D:\allFiles\myprojects\easyfollow\dist\license.lic")
    except:
        traceback.print_exc()

if __name__ == "__main__":
    test()
