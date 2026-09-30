# Copyright 2026 Intrinsic Innovation LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Simulated Robotiq Hand-E driver for Intrinsic's Gazebo simulation.

A drop-in for the real driver's gripper_action_controller: it serves the same
control_msgs/action/ParallelGripperCommand action, and turns each goal into a
PinchGripperCommand for the simulated gripper (Intrinsic pubsub topic
`command_topic`). The simulated gripper reports its opening on `status_topic`
(or `fallback_status_topic`) as a PinchGripperStatus, which is used for the
action result and to update the gripper's finger joints in the belief world.

The position of a goal is per finger, in meters, from grip_pos_min to
grip_pos_max. The status position is the opening between the fingers, i.e.
twice the finger position.
"""

import threading
import time

import rclpy
from control_msgs.action import ParallelGripperCommand
from rclpy.action import ActionServer
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from hande_gripper_sim_driver import intrinsic

_POLL_SECONDS = 0.05


def position_to_percentage(position, pos_min, pos_max):
  """The PinchGripperCommand position_percentage for a finger position."""
  if pos_max <= pos_min:
    return 0.0
  fraction = (position - pos_min) / (pos_max - pos_min)
  return 100.0 * min(1.0, max(0.0, fraction))


class HandeGripperSimDriverNode(Node):
  """The gripper_action_controller of the simulated Hand-E."""

  def __init__(self, descriptor_set=None):
    super().__init__("gripper_action_controller")
    p = self.declare_parameter
    self._action_name = p("action_name",
                          "/gripper/gripper_action_controller/gripper_cmd").value
    self._joint = p("joint", "robotiq_hande_left_finger_joint").value
    self._pos_min = p("grip_pos_min", 0.0).value
    self._pos_max = p("grip_pos_max", 0.025).value
    zenoh_router = p("zenoh_router", "tcp/localhost:17447").value
    self._command_topic = p("command_topic", "gripper/gripper/command").value
    status_topic = p("status_topic", "robotiq_gripper/gripper/status").value
    fallback_topic = p("fallback_status_topic", "gripper/gripper/status").value
    world_address = p("world_service_address", "localhost:17080").value
    world_id = p("world_id", "world").value
    self._object_name = p("gripper_object_name", "gripper").value
    self._wait_seconds = p("execution_wait_ms", 1500).value / 1000.0

    self._protos = intrinsic.Protos(
        descriptor_set or intrinsic.default_descriptor_set())
    self._status_class = self._protos["intrinsic_proto.gripper.PinchGripperStatus"]
    self._command_class = self._protos["intrinsic_proto.gripper.PinchGripperCommand"]

    self.get_logger().info(f"Using zenoh_router: '{zenoh_router}'")
    self._session = intrinsic.open_zenoh(zenoh_router)
    self._publisher = self._session.declare_publisher(
        intrinsic.TOPIC_PREFIX + self._command_topic)
    self._status_lock = threading.Condition()
    self._status = None  # (receive time, PinchGripperStatus)
    self._subscribers = [
        self._session.declare_subscriber(intrinsic.TOPIC_PREFIX + t,
                                         self._on_status)
        for t in dict.fromkeys((status_topic, fallback_topic))
    ]

    self._world = intrinsic.WorldClient(self._protos, world_address, world_id)
    self._world_object_id = None
    self._world_position = None

    self._server = ActionServer(self, ParallelGripperCommand,
                                self._action_name,
                                execute_callback=self._execute)
    self.get_logger().info(
        "Started HandeGripperSimDriverNode action server on "
        f"'{self._action_name}'.")

  def _on_status(self, sample):
    status = intrinsic.decode_packet(self._protos, sample.payload.to_bytes(),
                                     self._status_class)
    if status is None:
      return
    with self._status_lock:
      self._status = (time.monotonic(), status)
      self._status_lock.notify_all()
    self._update_belief_world(status.position / 2.0)

  def _update_belief_world(self, finger_position):
    """Sets the gripper's finger joints in the belief world."""
    if self._world_position is not None and abs(
        self._world_position - finger_position) < 1e-6:
      return
    try:
      if self._world_object_id is None:
        obj = self._world.get_object_by_name(self._object_name)
        self._world_object_id = obj.id
        num_joints = len(obj.kinematic_object_component.joint_positions)
        self._num_joints = max(1, num_joints)
      self._world.update_object_joints(
          self._world_object_id, [finger_position] * self._num_joints)
      self._world_position = finger_position
      self.get_logger().info(
          f"Updated Belief World '{self._object_name}' joints to "
          f"{finger_position:.4f} m (status={2 * finger_position:.4f} m).")
    except Exception as e:  # pylint: disable=broad-except
      self._world_object_id = None
      self.get_logger().warning(
          f"Failed to update joints for '{self._object_name}': {e}",
          throttle_duration_sec=10.0)

  def _execute(self, handle):
    positions = list(handle.request.command.position)
    target = positions[0] if positions else 0.0
    percentage = position_to_percentage(target, self._pos_min, self._pos_max)
    self.get_logger().info(
        f"Received ParallelGripperCommand pos={target:.4f} m -> publishing "
        f"{percentage:.1f}%.")
    command = self._command_class(
        command_id=time.time_ns() // 1_000_000,
        position_percentage=percentage,
        velocity_percentage=100.0,
        effort_percentage=100.0)
    sent = time.monotonic()
    try:
      self._publisher.put(intrinsic.encode_packet(self._protos, command))
    except Exception as e:  # pylint: disable=broad-except
      self.get_logger().error(f"Failed to publish PinchGripperCommand: {e}")
      handle.abort()
      return ParallelGripperCommand.Result()

    # Wait for the simulated gripper to report that it reached the position.
    status = None
    deadline = sent + self._wait_seconds
    with self._status_lock:
      while time.monotonic() < deadline:
        if self._status is not None and self._status[0] > sent:
          status = self._status[1]
          if status.position_reached:
            break
        self._status_lock.wait(timeout=_POLL_SECONDS)
      if status is None and self._status is not None:
        status = self._status[1]

    result = ParallelGripperCommand.Result()
    result.state.name = [self._joint]
    result.state.position = [status.position / 2.0 if status else 0.0]
    result.state.velocity = [0.0]
    result.state.effort = [0.0]
    # Without any status, e.g. when the simulated gripper does not report one,
    # the goal counts as reached.
    result.stalled = bool(status.object_detected) if status else False
    result.reached_goal = bool(status.position_reached) if status else True
    handle.succeed()
    return result

  def close(self):
    for s in self._subscribers:
      s.undeclare()
    self._publisher.undeclare()
    self._session.close()


def main():
  rclpy.init()
  node = HandeGripperSimDriverNode()
  executor = MultiThreadedExecutor()
  executor.add_node(node)
  try:
    executor.spin()
  except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
    pass
  finally:
    node.close()


if __name__ == "__main__":
  main()
