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

"""ROS 2 node serving ROS actions to Intrinsic skills over Zenoh and gRPC.

For every `ros_action_client` interface in the config file (see schema.py),
the node declares a Zenoh queryable on `external_name` and serves the gRPC
method `/ai.intrinsic.<Action>_Service/Call`. Both take the serialized request
message, run it as a goal of the action and return the response message:

- `status` is the goal's terminal status (SUCCEEDED, ABORTED, CANCELED), or
  "FAILED: <reason>" if the goal could not be run,
- the other response fields are copied from the action result by name.

A request field named `action_name`, if set, selects another action server
than `internal_name`.
"""

import concurrent.futures
import os
import threading

import grpc
import rclpy
import yaml
import zenoh
from action_msgs.msg import GoalStatus
from rclpy.action import ActionClient
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rosidl_runtime_py.utilities import get_action

from aquarium_gateway import convert
from aquarium_gateway import schema

SERVER_WAIT_SECONDS = 5.0
_STATUS_NAMES = {
    GoalStatus.STATUS_SUCCEEDED: "SUCCEEDED",
    GoalStatus.STATUS_CANCELED: "CANCELED",
    GoalStatus.STATUS_ABORTED: "ABORTED",
}
_DEFAULT_ENDPOINTS = '["tcp/127.0.0.1:7447"]'


class RequestError(Exception):
  """The request payload could not be parsed."""


def zenoh_endpoints():
  """connect/endpoints from ZENOH_CONFIG_OVERRIDE ("key=value;...")."""
  for entry in os.environ.get("ZENOH_CONFIG_OVERRIDE", "").split(";"):
    key, _, value = entry.partition("=")
    if key.strip() == "connect/endpoints" and value:
      return value
  return _DEFAULT_ENDPOINTS


def _wait(future):
  done = threading.Event()
  future.add_done_callback(lambda _: done.set())
  done.wait()
  return future.result()


class ActionInterface:
  """Runs requests of one interface as goals of its ROS action."""

  def __init__(self, node, interface):
    self._node = node
    self._interface = interface
    self._action_type = get_action(interface.internal_type)
    self._clients = {}
    self._lock = threading.Lock()

  def _client(self, name):
    with self._lock:
      if name not in self._clients:
        self._clients[name] = ActionClient(self._node, self._action_type, name)
      return self._clients[name]

  def call(self, payload):
    """Returns the serialized response to a serialized request."""
    request = self._interface.request_class()
    try:
      request.ParseFromString(payload)
    except Exception as e:  # pylint: disable=broad-except
      raise RequestError(str(e)) from e
    response = self._interface.response_class()
    try:
      self._run(request, response)
    except Exception as e:  # pylint: disable=broad-except
      self._node.get_logger().error(f"Goal failed: {e!r}")
      response.Clear()
      response.status = f"FAILED: {e}"
    return response.SerializeToString()

  def _run(self, request, response):
    name = self._interface.internal_name
    if "action_name" in request.DESCRIPTOR.fields_by_name and request.action_name:
      name = request.action_name
    if not name.startswith("/"):
      name = "/" + name
    client = self._client(name)
    if not client.wait_for_server(timeout_sec=SERVER_WAIT_SECONDS):
      msg = (f"Remote target action server '{name}' offline or unreachable "
             f"after {SERVER_WAIT_SECONDS:.0f}s timeout.")
      print(f"GATEWAY ERROR: {msg}", flush=True)
      response.status = f"FAILED: {msg}"
      return
    goal = convert.proto_to_ros(request, self._action_type.Goal())
    handle = _wait(client.send_goal_async(goal))
    if not handle.accepted:
      response.status = "FAILED: Goal was rejected by action server."
      return
    result = _wait(handle.get_result_async())
    response.status = _STATUS_NAMES.get(result.status, str(result.status))
    convert.ros_to_proto(result.result, response)


class Gateway(Node):
  """The aquarium_gateway node."""

  def __init__(self):
    super().__init__("aquarium_gateway")
    config_file = self.declare_parameter("config_file", "").value
    grpc_port = self.declare_parameter("grpc_port", 50051).value
    resource_name = self.declare_parameter("resource_name", "").value
    capability = self.declare_parameter("capability", "").value
    log = self.get_logger()
    log.info("Starting aquarium_gateway...")
    if not resource_name or not capability:
      log.info("Resource registration skipped (resource_name or capability empty).")
    else:
      log.warning("Resource registration is not supported, skipping it.")

    log.info(f"Loading configuration: {config_file}")
    with open(config_file) as f:
      config = yaml.safe_load(f)
    self._handlers = {}  # external name -> ActionInterface
    self._grpc_methods = {}  # gRPC method -> ActionInterface
    for interface in schema.build_interfaces(config):
      if (interface.internal_interface != "ros_action_client" or
          interface.external_interface != "zenoh_queryable_proto"):
        log.error(f"Unsupported interface {interface.internal_interface} -> "
                  f"{interface.external_interface}, skipping it.")
        continue
      handler = ActionInterface(self, interface)
      self._handlers[interface.external_name] = handler
      self._grpc_methods[interface.grpc_method] = handler
      print(f"CONFIG LOADED: Mapped gRPC path '{interface.grpc_method}' -> "
            f"ROS action client {interface.internal_name}", flush=True)
      print(f"CONFIG LOADED: Mapped Zenoh key '{interface.external_name}' -> "
            f"ROS action client {interface.internal_name}", flush=True)

    self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)
    self._open_zenoh()
    self._start_grpc(grpc_port)

  def _open_zenoh(self):
    endpoints = zenoh_endpoints()
    self.get_logger().info(
        f"ZENOH: Opening session (mode: peer, connect: {endpoints})...")
    conf = zenoh.Config()
    conf.insert_json5("mode", '"peer"')
    conf.insert_json5("connect/endpoints", endpoints)
    conf.insert_json5("scouting/multicast/enabled", "false")
    self._session = zenoh.open(conf)
    self._queryables = []
    for key, handler in self._handlers.items():
      self._queryables.append(self._session.declare_queryable(
          key, lambda q, h=handler: self._pool.submit(self._on_query, q, h)))
      self.get_logger().info(f"ZENOH: Mapped queryable on key '{key}'")
    log = self.get_logger()
    log.info("===============================================")
    log.info("  aquarium_gateway online")
    log.info("===============================================")

  def _on_query(self, query, handler):
    payload = query.payload.to_bytes() if query.payload else b""
    print(f"[ZENOH QUERY] Payload size: {len(payload)} bytes", flush=True)
    try:
      response = handler.call(payload)
    except RequestError:
      # No reply: the querier sees the query finish without one.
      print("ZENOH ERROR: Failed to dynamically deserialize Protobuf payload.",
            flush=True)
      return
    query.reply(query.key_expr, response)
    print("[ZENOH QUERY] Replied successfully.", flush=True)

  def _start_grpc(self, port):
    methods = self._grpc_methods

    class Handler(grpc.GenericRpcHandler):

      def service(self, details):
        handler = methods.get(details.method)
        if handler is None:
          return None

        def call(request, context):
          try:
            return handler.call(request)
          except RequestError:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT,
                          "Failed to parse protobuf request")

        return grpc.unary_unary_rpc_method_handler(call)

    self._grpc = grpc.server(concurrent.futures.ThreadPoolExecutor(max_workers=8))
    self._grpc.add_generic_rpc_handlers((Handler(),))
    self._grpc.add_insecure_port(f"0.0.0.0:{port}")
    self._grpc.start()
    self.get_logger().info(f"GRPC: Server listening on 0.0.0.0:{port}")

  def close(self):
    self._grpc.stop(0)
    for q in self._queryables:
      q.undeclare()
    self._session.close()
    self._pool.shutdown(wait=False)


def main():
  rclpy.init()
  node = Gateway()
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
