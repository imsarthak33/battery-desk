import os
import py_compile
import sys
errors = 0
for root, dirs, files in os.walk('.'):
    for f in files:
        if f.endswith('.py'):
            path = os.path.join(root, f)
            try:
                py_compile.compile(path, doraise=True)
            except Exception as e:
                errors += 1
                print('SYNTAX ERROR', path, e)

if errors == 0:
    print('All files syntax OK')
else:
    print(f'{errors} files with errors')
    sys.exit(1)
