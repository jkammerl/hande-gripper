"""Exercises a Hand-E simulation driver: ROS 2 action -> Intrinsic pubsub/world.

Usage: capture_simdriver.py <image> [--arch amd64] [--driver_cmd ...]

Runs on the `hande` podman network:
- the driver under test (by default the node from the original sim image),
- a gRPC recorder on the host standing in for the world service; it logs every
  call and answers with an empty message, or with --get_object_hex for
  GetObject,
- a Zenoh subscriber on `**` recording everything published, optionally
  publishing a PinchGripperStatus (--status_*) like the simulated gripper,
- localhost/hande-ros-test:lyrical's gripper_client.py sending one goal.
Prints the recorded traffic and the action result as JSON.
"""

import argparse
import concurrent.futures
import json
import re
import subprocess
import threading
import time

import grpc
import zenoh

import protos

STUB_PORT = 50098
ROUTER = 'connect/endpoints=["tcp/zenoh-router:7447"]'
ORIGINAL_DRIVER_CMD = (
    "source /opt/ros/lyrical/setup.bash && "
    "source /workspace/install/setup.bash && "
    "exec ros2 run hande_gripper_sim_driver hande_gripper_sim_driver_node "
    "--ros-args -r __ns:=/gripper -r __node:=gripper_action_controller "
    "-p zenoh_router:=tcp/zenoh-router:7447 "
    f"-p world_service_address:=host.containers.internal:{STUB_PORT}")


class Recorder(grpc.GenericRpcHandler):

  def __init__(self, get_object_hex):
    self.calls = []
    self._get_object = bytes.fromhex(get_object_hex or "")

  def service(self, handler_call_details):
    method = handler_call_details.method

    def handle(request, context):
      self.calls.append({"t": time.time(), "method": method,
                         "request_hex": request.hex()})
      if method.endswith("/GetObject"):
        return self._get_object
      return b""

    return grpc.unary_unary_rpc_method_handler(
        handle, request_deserializer=None, response_serializer=None)


def _podman(*args, check=True):
  return subprocess.run(["podman", *args], capture_output=True, text=True,
                        check=check)


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("image")
  ap.add_argument("--arch", default=None)
  ap.add_argument("--driver_cmd", default=ORIGINAL_DRIVER_CMD)
  ap.add_argument("--position", default="0.025")
  ap.add_argument("--get_object_hex", default=None)
  ap.add_argument("--status_key", default=None,
                  help="Zenoh key to publish a PinchGripperStatus on")
  ap.add_argument("--status_hex", default=None)
  ap.add_argument("--settle", type=float, default=20)
  ap.add_argument("--post_wait", type=float, default=4,
                  help="seconds to keep recording after the result")
  ap.add_argument("--zenoh", default="tcp/127.0.0.1:17448")
  args = ap.parse_args()

  recorder = Recorder(args.get_object_hex)
  server = grpc.server(concurrent.futures.ThreadPoolExecutor(max_workers=4))
  server.add_generic_rpc_handlers((recorder,))
  server.add_insecure_port(f"0.0.0.0:{STUB_PORT}")
  server.start()

  conf = zenoh.Config()
  conf.insert_json5("mode", '"client"')
  conf.insert_json5("connect/endpoints", json.dumps([args.zenoh]))
  session = zenoh.open(conf)
  published = []
  t_start = time.time()

  def on_sample(s):
    key = str(s.key_expr)
    if key.startswith("@") or "/rq/" in key or "/rr/" in key:
      return
    published.append({"t": round(time.time() - t_start, 2), "key": key,
                      "encoding": str(s.encoding),
                      "payload_hex": s.payload.to_bytes().hex()[:2000]})

  sub = session.declare_subscriber("**", on_sample)

  for n in ("driver-under-test", "gripper-client"):
    _podman("rm", "-f", n, check=False)
  cmd = ["run", "-d", "--name", "driver-under-test", "--network", "hande",
         "-e", f"ZENOH_CONFIG_OVERRIDE={ROUTER}"]
  if args.arch:
    cmd += ["--arch", args.arch]
  cmd += ["--entrypoint", "/bin/bash", args.image, "-c", args.driver_cmd]
  _podman(*cmd)
  time.sleep(args.settle)

  status_pub = None
  if args.status_key:
    stop = threading.Event()

    def pump():
      while not stop.is_set():
        session.put(args.status_key, bytes.fromhex(args.status_hex))
        time.sleep(0.1)

    status_thread = threading.Thread(target=pump, daemon=True)
    status_thread.start()

  t_goal = time.time()
  client = _podman("run", "--rm", "--name", "gripper-client", "--network",
                   "hande", "-e", f"ZENOH_CONFIG_OVERRIDE={ROUTER}",
                   "-e", f"POSITION={args.position}",
                   "--entrypoint", "/ros_entrypoint.sh",
                   "localhost/hande-ros-test:lyrical",
                   "python3", "/gripper_client.py", check=False)
  time.sleep(args.post_wait)
  if args.status_key:
    stop.set()
  result = [json.loads(l[7:]) for l in client.stdout.splitlines()
            if l.startswith("CLIENT ")]
  driver_log = _podman("logs", "driver-under-test", check=False)
  out = {
      "client": result[0] if result else client.stdout[-1500:] + client.stderr[-1500:],
      "grpc_calls": [dict(c, t=round(c["t"] - t_goal, 2)) for c in recorder.calls],
      "published": [p for p in published
                    if not p["key"].startswith(("gripper_cmd",))],
      "driver_log_tail": [re.sub(r"\x1b\[[0-9;]*m", "", l) for l in
                          (driver_log.stdout + driver_log.stderr).splitlines()[-40:]],
  }
  print(json.dumps(out, indent=2))
  _podman("rm", "-f", "driver-under-test", check=False)
  sub.undeclare()
  session.close()
  server.stop(0)


if __name__ == "__main__":
  main()
