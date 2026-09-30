"""Scripted control_msgs/action/ParallelGripperCommand server.

Environment:
  ACTION_NAME: action server name (default /gripper/gripper_action_controller/gripper_cmd)
  MODE: succeed | stall | abort | reject | slow | cancelable
  DELAY: seconds before finishing a goal (default 0.2)

Every received goal is printed as one JSON line prefixed with GOAL, so tests
can compare what different gateways send.
"""

import json
import os
import time

import rclpy
from control_msgs.action import ParallelGripperCommand
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

MODE = os.environ.get("MODE", "succeed")
DELAY = float(os.environ.get("DELAY", "0.2"))
ACTION_NAME = os.environ.get(
    "ACTION_NAME", "/gripper/gripper_action_controller/gripper_cmd")


class FakeGripper(Node):

  def __init__(self):
    super().__init__("fake_gripper")
    self._server = ActionServer(
        self, ParallelGripperCommand, ACTION_NAME,
        execute_callback=self._execute,
        goal_callback=self._goal,
        cancel_callback=lambda _: CancelResponse.ACCEPT)
    print(f"READY {ACTION_NAME} mode={MODE}", flush=True)

  def _goal(self, goal):
    c = goal.command
    print("GOAL " + json.dumps({
        "name": list(c.name), "position": list(c.position),
        "velocity": list(c.velocity), "effort": list(c.effort),
        "frame_id": c.header.frame_id}), flush=True)
    return GoalResponse.REJECT if MODE == "reject" else GoalResponse.ACCEPT

  def _execute(self, handle):
    target = list(handle.request.command.position)
    feedback = ParallelGripperCommand.Feedback()
    feedback.state.name = list(handle.request.command.name)
    feedback.state.position = target
    handle.publish_feedback(feedback)
    time.sleep(DELAY if MODE != "slow" else 30)

    result = ParallelGripperCommand.Result()
    result.state.header.frame_id = "hande"
    result.state.name = list(handle.request.command.name)
    if MODE == "stall":
      result.state.position = [p / 2 for p in target]
      result.state.effort = [2.5] * len(target)
      result.stalled = True
      result.reached_goal = False
    else:
      result.state.position = target
      result.state.velocity = [0.0] * len(target)
      result.state.effort = [0.5] * len(target)
      result.stalled = False
      result.reached_goal = True

    if MODE == "abort":
      handle.abort()
    else:
      handle.succeed()
    print("RESULT " + json.dumps({
        "position": list(result.state.position), "stalled": result.stalled,
        "reached_goal": result.reached_goal}), flush=True)
    return result


def main():
  rclpy.init()
  node = FakeGripper()
  executor = MultiThreadedExecutor()
  executor.add_node(node)
  executor.spin()


if __name__ == "__main__":
  main()
