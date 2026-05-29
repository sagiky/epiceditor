import traceback
try:
    with open('server.py', 'r', encoding='utf-8') as f:
        code = f.read()
    exec(code)
except Exception as e:
    print("ERROR FOUND:")
    traceback.print_exc()
input("Press Enter to close...")