import argparse
import os
import subprocess
import time
from urllib.parse import urlparse

import requests

from src.configs import ConfigLoader

import sys

def _start_worker(name, port, controller, definition):
    conf = definition[name]
    if "docker" in conf and "image" in conf["docker"]:
        docker = conf["docker"]
        project_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        subprocess.Popen(
            [
                "docker",
                "run",
                "--rm",
                "-p",
                f"{port}:{port}",
                "--add-host",
                "host.docker.internal:host-gateway",
                "-v",
                f"{project_root}:/root/workspace",
                "-w",
                "/root/workspace",
                docker["image"],
                "bash",
                "-c",
                docker.get("command", "") + f" {sys.executable} -m src.server.task_worker {name}" # NOTE: changed python to sys.executable
                                            f" --self http://localhost:{port}/api"
                                            f" --port {port}"
                                            f" --controller {controller.replace('localhost', 'host.docker.internal')}",
            ]
        )
    else:
        subprocess.Popen(
            [
                sys.executable, # NOTE: changed python to sys.executable
                "-m",
                "src.server.task_worker",
                name,
                "--self",
                f"http://localhost:{port}/api",
                "--port",
                str(port),
                "--controller",
                controller,
            ],
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=str,
        help="Config file to load",
        default="configs/start_task.yaml",
    )
    parser.add_argument(
        "--start",
        "-s",
        dest="start",
        type=str,
        nargs="*",
        help="name num_worker name num_worker ...",
    )
    parser.add_argument("--controller", "-l", dest="controller_addr", default="")
    parser.add_argument(
        "--auto-controller", "-a", dest="controller", action="store_true"
    )
    """PATCH: Controller port selection"""
    parser.add_argument(
        "--controller-port", 
        type=int, 
        default=5000,
        help="Port for controller (default: 5000)"
    )
    """"""
    #parser.add_argument("--base-port", "-p", dest="port", type=int, default=5001)
    parser.add_argument("--base-port", "-p", dest="port", type=int, default=None)

    args = parser.parse_args()

    config = ConfigLoader().load_from(args.config)

    root = os.path.dirname(os.path.abspath(__file__))

    # NOTE: controller port selection
    if args.controller_port:
        controller_port = args.controller_port
    else:
        controller_port = 5000
    ###

    if args.controller:
        if "controller" in config:
            try:
                requests.get(config["controller"] + "/list_workers")
            except Exception as e:
                print("Specified controller not responding, trying to start a new one")
                o = urlparse(config["controller"])
                subprocess.Popen(
                    [
                        sys.executable, # NOTE: changed python to sys.executable
                        "-m",
                        "src.server.task_controller",
                        "--port",
                        str(o.port),
                    ]
                )
        else:
            # NOTE: changed python to sys.executable
            subprocess.Popen(
                [sys.executable, "-m", "src.server.task_controller", "--port", str(controller_port)]
            )
            #subprocess.Popen(
            #    [sys.executable, "-m", "src.server.task_controller", "--port", "5000"]
            #)
        for i in range(10):
            try:
                requests.get(f"http://localhost:{controller_port}/api/list_workers")
                #requests.get("http://localhost:5000/api/list_workers")
                break
            except Exception as e:
                print(f"Waiting for controller to start on port {controller_port}...")
                #print("Waiting for controller to start...")
                time.sleep(0.5)
        else:
            raise Exception("Controller failed to start")

    # base_port = args.p
    # NOTE: patch for base_port (dependency on controller_port if not specifically inputted)
    base_port = args.port if args.port else controller_port + 1

    if args.controller_addr:
        controller_addr = args.controller_addr
    elif "controller" in config:
        controller_addr = config["controller"]
    else:
        controller_addr = f"http://localhost:{controller_port}/api"
        #controller_addr = "http://localhost:5000/api"


    if "start" in config.keys() and not args.start:
        for key, val in config.get("start", {}).items():
            for _ in range(val):
                _start_worker(key, base_port, controller_addr, config["definition"])
                base_port += 1

    n = len(args.start) if args.start else 0
    if n % 2 != 0:
        raise ValueError(
            "--start argument should strictly follow the format: name1 num1 name2 num2 ..."
        )
    for i in range(0, n, 2):
        for _ in range(int(args.start[i + 1])):
            _start_worker(args.start[i], base_port, controller_addr, config["definition"])
            base_port += 1

    while True:
        input()

# try: python start_task.py ../configs/server/test.yaml -a
