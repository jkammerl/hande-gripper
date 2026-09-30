"""Runs the same scenarios against an original and a replacement image.

Usage:
  compare.py skill   <original image> <replacement image>
  compare.py gateway <original image> <replacement image>
  compare.py driver  <original image> <replacement image>

The original images are amd64-only and run under emulation (--arch amd64);
they serve as the reference. Each scenario's output is normalized (timings,
ids and logs removed) and must be identical for both images. Results are
written to results/<component>/<scenario>.{original,replacement}.json.
"""

import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PY = sys.executable


def _hex_result(status, stalled=False, reached=True, position=0.0249):
  import protos  # pylint: disable=import-outside-toplevel
  r = protos.msg("ai.intrinsic.GripperCmdResult")(
      status=status, stalled=stalled, reached_goal=reached)
  r.state.name.append("robotiq_hande_left_finger_joint")
  r.state.position.append(position)
  r.state.effort.append(1.5)
  return r.SerializeToString().hex()


def _fixture(*args):
  return subprocess.run([_PY, os.path.join(_HERE, "make_fixtures.py"), *args],
                        capture_output=True, text=True, check=True).stdout.strip()


SKILL_SCENARIOS = {
    "no_reply": [],
    "succeeded": ["--reply", _hex_result("SUCCEEDED")],
    "empty_status": ["--reply", _hex_result("")],
    "stalled": ["--reply", _hex_result("SUCCEEDED", stalled=True,
                                       reached=False), "--position", "0.0"],
    "aborted": ["--reply", _hex_result("ABORTED")],
    "failed": ["--reply", _hex_result(
        "FAILED: Goal was rejected by action server.")],
    "unparsable": ["--reply", "0a35121f726f626f"],
    "empty_reply": ["--reply", ""],
    "reply_error": ["--reply_err", "action server not available"],
    "action_name": ["--action_name", "/other/gripper_cmd", "--reply", "1801"],
    "timeout": ["--timeout", "3", "--delay", "6", "--reply", "1801"],
    "zenoh_default_timeout": ["--timeout", "", "--delay", "13", "--reply",
                              "1801"],
}

GATEWAY_SCENARIOS = {
    "succeed": ["--mode", "succeed"],
    "stall": ["--mode", "stall", "--position", "0.0"],
    "abort": ["--mode", "abort"],
    "reject": ["--mode", "reject"],
    "action_name": ["--server_action", "/other/gripper_cmd",
                    "--action_name", "/other/gripper_cmd"],
    "action_name_relative": ["--server_action", "/other/gripper_cmd",
                             "--action_name", "other/gripper_cmd"],
    "missing_server": ["--no_server", "--action_name", "/missing/gripper_cmd"],
    "bad_payload": ["--raw_payload", "ffff"],
    "empty_payload": ["--raw_payload", ""],
}


def _driver_scenarios():
  obj = _fixture("object")
  reached = _fixture("status", "0.0123")
  detected = _fixture("status", "0.0123", "detected")
  return {
      "no_status": ["--position", "0.025"],
      "reached": ["--position", "0.0", "--status_key",
                  "in/robotiq_gripper/gripper/status", "--status_hex", reached,
                  "--get_object_hex", obj],
      "fallback_topic": ["--position", "0.0", "--status_key",
                         "in/gripper/gripper/status", "--status_hex", reached,
                         "--get_object_hex", obj],
      "object_detected": ["--position", "0.0", "--status_key",
                          "in/robotiq_gripper/gripper/status", "--status_hex",
                          detected, "--get_object_hex", obj],
      "half_open": ["--position", "0.0125"],
  }


def _normalize_skill(out):
  q = out.get("captured_queries", [])
  return {
      "queries": [{k: v for k, v in x.items()} for x in q],
      "error": out.get("error"),
      "result": out.get("result"),
  }


def _normalize_gateway(out):
  return {
      "replies": [{k: v for k, v in r.items() if k != "after_s"}
                  for r in out.get("replies", [])],
      "slow": [r["after_s"] > 3 for r in out.get("replies", [])],
      "goals": out.get("goals"),
  }


def _normalize_driver(out):
  import protos  # pylint: disable=import-outside-toplevel
  from google.protobuf.internal import decoder  # pylint: disable=import-outside-toplevel
  client = out.get("client")
  if isinstance(client, dict):
    client = {k: v for k, v in client.items() if k != "seconds"}
    client["waited_full_timeout"] = out["client"].get("seconds", 0) > 1.4
  commands = []
  for p in out.get("published", []):
    if p["key"] != "in/gripper/gripper/command":
      continue
    data = bytes.fromhex(p["payload_hex"])
    length, pos = decoder._DecodeVarint(data, 0)  # pylint: disable=protected-access
    pkt = protos.msg("intrinsic_proto.pubsub.PubSubPacket").FromString(
        data[pos:pos + length])
    cmd = protos.msg("intrinsic_proto.gripper.PinchGripperCommand")()
    pkt.payload.Unpack(cmd)
    cmd.command_id = 0  # The send time in ms.
    commands.append({"type_url": pkt.payload.type_url, "command": str(cmd)})
  calls = [{"method": c["method"], "request_hex": c["request_hex"]}
           for c in out.get("grpc_calls", [])]
  return {"client": client, "commands": commands,
          "get_object_calls": [c for c in calls
                               if c["method"].endswith("/GetObject")][:1]}


COMPONENTS = {
    "skill": ("capture_skill.py", SKILL_SCENARIOS, _normalize_skill),
    "gateway": ("capture_gateway.py", GATEWAY_SCENARIOS, _normalize_gateway),
    "driver": ("capture_simdriver.py", None, _normalize_driver),
}


def main():
  component, original, replacement = sys.argv[1:4]
  only = sys.argv[4:]
  script, scenarios, normalize = COMPONENTS[component]
  if scenarios is None:
    scenarios = _driver_scenarios()
  out_dir = os.path.join(_HERE, "results", component)
  os.makedirs(out_dir, exist_ok=True)
  failures = 0
  for name, args in scenarios.items():
    if only and name not in only:
      continue
    normalized = {}
    for which, image, extra in (("original", original, ["--arch", "amd64"]),
                                ("replacement", replacement, [])):
      proc = subprocess.run(
          [_PY, os.path.join(_HERE, script), image, *extra, *args],
          capture_output=True, text=True, cwd=_HERE, timeout=600)
      path = os.path.join(out_dir, f"{name}.{which}.json")
      with open(path, "w") as f:
        f.write(proc.stdout)
      try:
        normalized[which] = normalize(json.loads(proc.stdout))
      except (json.JSONDecodeError, KeyError) as e:
        normalized[which] = {"harness_error": str(e),
                             "stderr": proc.stderr[-800:]}
    same = normalized["original"] == normalized["replacement"]
    failures += not same
    print(f"{'PASS' if same else 'FAIL'}  {component}/{name}", flush=True)
    if not same:
      for which in ("original", "replacement"):
        print(f"    {which}: {json.dumps(normalized[which])[:1500]}")
  print(f"{component}: {len(scenarios) - failures if not only else '?'} passed, "
        f"{failures} failed")
  sys.exit(1 if failures else 0)


if __name__ == "__main__":
  main()
