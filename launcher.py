import os
import signal
import subprocess
import sys
import time

from utils.runtime_encoding import configure_utf8_stdio


PORT = str(os.getenv("PORT", "3000"))

configure_utf8_stdio()


def _terminate_processes(processes: list[subprocess.Popen]) -> None:
    for proc in processes:
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    deadline = time.time() + 10
    while time.time() < deadline:
        alive = [proc for proc in processes if proc.poll() is None]
        if not alive:
            return
        time.sleep(0.2)

    for proc in processes:
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass


def main() -> int:
    processes: list[subprocess.Popen] = []
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "UTF-8"
    child_env["PYTHONUTF8"] = "1"

    def _handle_signal(signum, _frame):
        print(f"[launcher] Получен сигнал {signum}, останавливаю процессы...")
        _terminate_processes(processes)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    bot_proc = subprocess.Popen([sys.executable, "main.py"], env=child_env)
    processes.append(bot_proc)
    print(f"[launcher] VK bot started with PID {bot_proc.pid}")

    web_proc = subprocess.Popen(
        ["gunicorn", "-b", f"0.0.0.0:{PORT}", "app:app"],
        env=child_env,
    )
    processes.append(web_proc)
    print(f"[launcher] Web server started on 0.0.0.0:{PORT} with PID {web_proc.pid}")

    exit_code = 0
    while True:
        for proc in processes:
            proc_exit = proc.poll()
            if proc_exit is not None:
                exit_code = proc_exit
                print(f"[launcher] Process PID {proc.pid} exited with code {proc_exit}")
                _terminate_processes(processes)
                return exit_code
        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
