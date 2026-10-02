import sys

from .app import main

if __name__ == "__main__":  # охрана: процессы авто-баланса импортируют модуль
    sys.exit(main())
