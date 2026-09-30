"""Runs gripper_cmd_skill once and records the Zenoh query it sends.

Usage: capture_skill.py <skill image> [--reply <hex payload>] [--arch amd64]

Starts the skill container on the `hande` podman network (which has a zenoh
router reachable as zenoh-router), with the Intrinsic service dependencies
pointed at an empty gRPC server on the host. A Zenoh queryable on `**`
records every query. The skill is then executed through its Executor gRPC API
and the operation result is printed together with the captured query.
"""

import argparse
import concurrent.futures
import json
import subprocess
import sys
import threading
import time

import grpc
import zenoh

import protos

STUB_PORT = 50099
SKILL_PORT = 18002


def _stub_server():
  """An empty gRPC server: enough for the skill's startup connection checks."""
  server = grpc.server(concurrent.futures.ThreadPoolExecutor(max_workers=2))
  server.add_insecure_port(f"0.0.0.0:{STUB_PORT}")
  server.start()
  return server


def _unary(channel, method, req_cls, resp_cls):
  return channel.unary_unary(
      method,
      request_serializer=req_cls.SerializeToString,
      response_deserializer=resp_cls.FromString)


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("image")
  ap.add_argument("--arch", default=None)
  ap.add_argument("--reply", default=None,
                  help="hex payload to answer the query with")
  ap.add_argument("--reply_err", default=None,
                  help="answer the query with an error reply with this text")
  ap.add_argument("--delay", type=float, default=0,
                  help="seconds to wait before replying")
  ap.add_argument("--position", type=float, default=0.025)
  ap.add_argument("--joint", default="robotiq_hande_left_finger_joint")
  ap.add_argument("--action_name", default="")
  ap.add_argument("--timeout", default="5")
  ap.add_argument("--zenoh", default="tcp/127.0.0.1:17448")
  args = ap.parse_args()

  stub = _stub_server()

  captured = []
  conf = zenoh.Config()
  conf.insert_json5("mode", '"client"')
  conf.insert_json5("connect/endpoints", json.dumps([args.zenoh]))
  session = zenoh.open(conf)

  def on_query(q):
    captured.append({
        "key": str(q.key_expr),
        "parameters": str(q.parameters),
        "payload_hex": q.payload.to_bytes().hex() if q.payload else None,
        "encoding": str(q.encoding) if q.payload else None,
        "attachment_hex": (q.attachment.to_bytes().hex()
                           if q.attachment else None),
    })
    if args.delay:
      time.sleep(args.delay)
    if args.reply is not None:
      q.reply(q.key_expr, bytes.fromhex(args.reply))
    elif args.reply_err is not None:
      q.reply_err(args.reply_err.encode())

  queryable = session.declare_queryable("**", on_query)

  host = "host.containers.internal"
  flags = [
      f"--port={SKILL_PORT}",
      f"--world_service_address={host}:{STUB_PORT}",
      f"--geometry_service_address={host}:{STUB_PORT}",
      f"--motion_planner_service_address={host}:{STUB_PORT}",
      f"--skill_registry_service_address={host}:{STUB_PORT}",
      f"--data_logger_grpc_service_address={host}:{STUB_PORT}",
  ]
  cmd = ["podman", "run", "-d", "--rm", "--name", "skill-under-test",
         "--network", "hande", "-p", f"{SKILL_PORT}:{SKILL_PORT}",
         "-e", 'ZENOH_CONFIG_OVERRIDE=connect/endpoints=["tcp/zenoh-router:7447"]',
         ] + (["-e", f"ZENOH_QUERY_TIMEOUT_SECONDS={args.timeout}"]
              if args.timeout else [])
  if args.arch:
    cmd += ["--arch", args.arch]
  cmd += [args.image] + flags
  subprocess.run(["podman", "rm", "-f", "skill-under-test"],
                 capture_output=True)
  subprocess.run(cmd, check=True, capture_output=True)

  try:
    channel = grpc.insecure_channel(f"127.0.0.1:{SKILL_PORT}")
    grpc.channel_ready_future(channel).result(timeout=120)

    params = protos.msg("ai.intrinsic.GripperCmdParams")()
    params.command.name.append(args.joint)
    params.command.position.append(args.position)
    params.action_name = args.action_name
    req = protos.msg("intrinsic_proto.skills.ExecuteRequest")()
    req.parameters.Pack(params)
    req.instance.instance_name = "gripper_cmd_skill_test"
    req.instance.id_version = "ai.intrinsic.gripper_cmd_skill.0.0.1"
    req.world_id = "world"

    Operation = protos.msg("google.longrunning.Operation")
    start = _unary(channel, "/intrinsic_proto.skills.Executor/StartExecute",
                   type(req), Operation)
    op = start(req, timeout=60)
    wait_req = protos.msg("google.longrunning.WaitOperationRequest")()
    wait_req.name = op.name
    wait_req.timeout.seconds = 60
    wait = _unary(channel, "/intrinsic_proto.skills.Executor/WaitOperation",
                  type(wait_req), Operation)
    t0 = time.time()
    while not op.done and time.time() - t0 < 400:
      op = wait(wait_req, timeout=70)

    out = {"done": op.done, "captured_queries": captured}
    if op.HasField("error"):
      out["error"] = {"code": op.error.code, "message": op.error.message}
    if op.HasField("response"):
      res = protos.msg("intrinsic_proto.skills.ExecuteResult")()
      op.response.Unpack(res)
      out["result_type"] = res.result.type_url
      if res.result.type_url.endswith("GripperCmdResult"):
        r = protos.msg("ai.intrinsic.GripperCmdResult")()
        res.result.Unpack(r)
        out["result"] = str(r)
    print(json.dumps(out, indent=2))
  finally:
    logs = subprocess.run(["podman", "logs", "skill-under-test"],
                          capture_output=True, text=True)
    sys.stderr.write(logs.stdout[-3000:] + logs.stderr[-3000:])
    subprocess.run(["podman", "rm", "-f", "skill-under-test"],
                   capture_output=True)
    queryable.undeclare()
    session.close()
    stub.stop(0)


if __name__ == "__main__":
  main()
