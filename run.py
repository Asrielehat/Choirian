import os
import threading
import time
import webbrowser

import uvicorn


def open_browser():
    time.sleep(0.8)
    webbrowser.open("http://127.0.0.1:8770/")


if __name__ == "__main__":
    if os.environ.get("CHOIRIAN_OPEN_BROWSER") == "1":
        threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run("app:app", host="127.0.0.1", port=8770)
