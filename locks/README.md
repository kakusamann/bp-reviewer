# Runtime lock

`win-64-pip.txt` records the actual independent Windows/Python 3.12.13 environment without local editable paths or credentials. From repository root, install it with `python -m pip install -r locks/win-64-pip.txt` followed by `python -m pip install --no-deps -e ./backend`. It is platform-specific; other platforms should use `environment.yml` and run their own checks. Node/npm and frontend packages are fixed separately in the frontend version/lock files.
