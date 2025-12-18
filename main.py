# main.py
# -*- coding: utf-8 -*-
import sys
from pathlib import Path


def _runtime_root() -> Path:
    # PyInstaller exe 실행이면 exe가 있는 폴더, 개발 실행이면 이 파일 폴더
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def main():
    root = _runtime_root()

    # 핵심: exe 옆의 timeclock/, ui/, timeclock_app.py를 "번들보다 우선" import 하게 만들기
    sys.path.insert(0, str(root))

    import timeclock_app  # 루트의 timeclock_app.py를 import
    timeclock_app.main()  # timeclock_app.py에 이미 main()이 존재함 :contentReference[oaicite:1]{index=1}


if __name__ == "__main__":
    main()



# python -m PyInstaller --noconfirm --clean --name timeclock_app --icon "icon.ico" --add-data "icon.ico;." --hidden-import PyQt5 --hidden-import git main.py