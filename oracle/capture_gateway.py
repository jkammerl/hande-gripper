"""Exercises a gripper gateway: Zenoh query `gripper_cmd` -> ROS 2 action.

Usage: capture_gateway.py <gateway image> [--arch amd64] [--mode succeed] ...

Runs, on the `hande` podman network with the zenoh router `zenoh-router`:
- the gateway under test, started as in the Hand-E service image but without
  the gripper driver (`--gateway_cmd`), and
- localhost/hande-ros-test:lyrical, a scripted ParallelGripperCommand server.
Then sends the query that gripper_cmd_skill sends and prints the reply and the
goal that reached the action server as JSON.
"""

import argparse
import json
import re
import subprocess
import time

import zenoh

import protos

ROUTER = 'connect/endpoints=["tcp/zenoh-router:7447"]'
ORIGINAL_GATEWAY_CMD = (
    "source /opt/ros/lyrical/setup.bash && "
    "source /workspace/install/setup.bash && "
    "exec ros2 run aquarium_gateway aquarium_gateway "
    "--ros-args -p config_file:=/workspace/aquarium.yaml")


def _podman(*args, check=True):
  return subprocess.run(["podman", *args], capture_output=True, text=True,
                        check=check)


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("image")
  ap.add_argument("--arch", default=None)
  ap.add_argument("--gateway_cmd", default=ORIGINAL_GATEWAY_CMD)
  ap.add_argument("--mode", default="succeed")
  ap.add_argument("--server_action", default=None,
                  help="action name of the fake server (default: gripper's)")
  ap.add_argument("--no_server", action="store_true")
  ap.add_argument("--action_name", default="")
  ap.add_argument("--raw_payload", default=None,
                  help="hex payload to send instead of GripperCmdParams")
  ap.add_argument("--position", type=float, default=0.025)
  ap.add_argument("--joint", default="robotiq_hande_left_finger_joint")
  ap.add_argument("--query_timeout", type=float, default=60)
  ap.add_argument("--settle", type=float, default=20,
                  help="seconds to let ROS discovery settle")
  ap.add_argument("--zenoh", default="tcp/127.0.0.1:17448")
  args = ap.parse_args()

  for n in ("gw-under-test", "fake-gripper"):
    _podman("rm", "-f", n, check=False)
  if not args.no_server:
    env = ["-e", f"ZENOH_CONFIG_OVERRIDE={ROUTER}", "-e", f"MODE={args.mode}"]
    if args.server_action:
      env += ["-e", f"ACTION_NAME={args.server_action}"]
    _podman("run", "-d", "--name", "fake-gripper", "--network", "hande",
            *env, "localhost/hande-ros-test:lyrical")
  gw = ["run", "-d", "--name", "gw-under-test", "--network", "hande",
        "-e", f"ZENOH_CONFIG_OVERRIDE={ROUTER}"]
  if args.arch:
    gw += ["--arch", args.arch]
  gw += ["--entrypoint", "/bin/bash", args.image, "-c", args.gateway_cmd]
  _podman(*gw)
  time.sleep(args.settle)

  params = protos.msg("ai.intrinsic.GripperCmdParams")()
  params.command.name.append(args.joint)
  params.command.position.append(args.position)
  params.action_name = args.action_name

  conf = zenoh.Config()
  conf.insert_json5("mode", '"client"')
  conf.insert_json5("connect/endpoints", json.dumps([args.zenoh]))
  out = {"replies": []}
  with zenoh.open(conf) as session:
    t0 = time.time()
    for reply in session.get("gripper_cmd",
                             payload=(bytes.fromhex(args.raw_payload)
                                      if args.raw_payload is not None
                                      else params.SerializeToString()),
                             timeout=args.query_timeout):
      r = {"after_s": round(time.time() - t0, 2)}
      if reply.ok:
        r["key"] = str(reply.ok.key_expr)
        r["encoding"] = str(reply.ok.encoding)
        data = reply.ok.payload.to_bytes()
        r["payload_hex"] = data.hex()
        try:
          res = protos.msg("ai.intrinsic.GripperCmdResult").FromString(data)
          r["as_GripperCmdResult"] = str(res)
        except Exception as e:  # pylint: disable=broad-except
          r["parse_error"] = str(e)
      else:
        r["error"] = reply.err.payload.to_bytes().decode(errors="replace")
        r["error_encoding"] = str(reply.err.encoding)
      out["replies"].append(r)
    out["query_s"] = round(time.time() - t0, 2)

  time.sleep(1)
  server_log = _podman("logs", "fake-gripper", check=False).stdout
  out["goals"] = [json.loads(l[5:]) for l in server_log.splitlines()
                  if l.startswith("GOAL ")]
  gw_log = _podman("logs", "gw-under-test", check=False)
  out["gateway_log_tail"] = [
      re.sub(r"\x1b\[[0-9;]*m", "", l)
      for l in (gw_log.stdout + gw_log.stderr).splitlines()[-12:]]
  print(json.dumps(out, indent=2))
  for n in ("gw-under-test", "fake-gripper"):
    _podman("rm", "-f", n, check=False)


if __name__ == "__main__":
  main()
