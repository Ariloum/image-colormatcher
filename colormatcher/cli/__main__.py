from .main import app

if __name__ == "__main__":  # охрана обязательна: процессы-воркеры (--jobs) импортируют этот модуль
    app(prog_name="colormatch")
