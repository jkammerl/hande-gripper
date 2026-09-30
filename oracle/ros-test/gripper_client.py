"""Sends one ParallelGripperCommand goal and prints the result as JSON.

Environment: ACTION_NAME, POSITION, JOINT, WAIT (seconds to wait for server).
"""

import json
import os
import time

import rclpy
from action_msgs.msg import GoalStatus
from control_msgs.action import ParallelGripperCommand
from rclpy.action import ActionClient
from rclpy.node import Node


def main():
  rclpy.init()
  node = Node("gripper_client")
  name = os.environ.get("ACTION_NAME",
                        "/gripper/gripper_action_controller/gripper_cmd")
  client = ActionClient(node, ParallelGripperCommand, name)
  out = {"action": name}
  if not client.wait_for_server(timeout_sec=float(os.environ.get("WAIT", "30"))):
    out["error"] = "server not available"
    print("CLIENT " + json.dumps(out), flush=True)
    return
  goal = ParallelGripperCommand.Goal()
  goal.command.name = [os.environ.get("JOINT", "robotiq_hande_left_finger_joint")]
  goal.command.position = [float(os.environ.get("POSITION", "0.025"))]
  t0 = time.time()
  send = client.send_goal_async(goal)
  rclpy.spin_until_future_complete(node, send)
  handle = send.result()
  out["accepted"] = handle.accepted
  if handle.accepted:
    res = handle.get_result_async()
    rclpy.spin_until_future_complete(node, res)
    r = res.result()
    out["status"] = {GoalStatus.STATUS_SUCCEEDED: "SUCCEEDED",
                     GoalStatus.STATUS_ABORTED: "ABORTED",
                     GoalStatus.STATUS_CANCELED: "CANCELED"}.get(r.status, r.status)
    s = r.result.state
    out["result"] = {"name": list(s.name), "position": list(s.position),
                     "velocity": list(s.velocity), "effort": list(s.effort),
                     "frame_id": s.header.frame_id,
                     "stalled": r.result.stalled,
                     "reached_goal": r.result.reached_goal}
  out["seconds"] = round(time.time() - t0, 2)
  print("CLIENT " + json.dumps(out), flush=True)


if __name__ == "__main__":
  main()
