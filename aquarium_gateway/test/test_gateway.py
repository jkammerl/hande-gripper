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

"""Tests of the gateway's schemas and conversions.

The expected bytes were recorded from the original aquarium_gateway (run
under emulation) with the Hand-E service's aquarium.yaml.
"""

import os

import pytest
import yaml
from google.protobuf import descriptor_pb2
from google.protobuf import descriptor_pool

from aquarium_gateway import convert
from aquarium_gateway import gateway
from aquarium_gateway import schema

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONFIG = os.environ.get(
    "AQUARIUM_CONFIG",
    os.path.join(_HERE, "..", "..", "hande_gripper_aquarium", "config",
                 "hande_gripper_aquarium.yaml"))

# Reply of the original gateway for a goal whose result had state
# {frame_id: "hande", name: [finger], position: [0.02], velocity: [0.0],
# effort: [0.5]} and reached_goal=true.
ORIGINAL_SUCCESS_REPLY = bytes.fromhex(
    "0a09535543434545444544124a0a090a00120568616e6465121f726f626f7469715f68"
    "616e64655f6c6566745f66696e6765725f6a6f696e741a087b14ae47e17a943f2208000"
    "00000000000002a08000000000000e03f2001")
# Query payload sent by the original gripper_cmd_skill for a position of
# 0.025 m and no action_name.
ORIGINAL_SKILL_QUERY = bytes.fromhex(
    "0a2b121f726f626f7469715f68616e64655f6c6566745f66696e6765725f6a6f696e74"
    "1a089a9999999999993f")
FINGER = "robotiq_hande_left_finger_joint"


@pytest.fixture(name="interface")
def fixture_interface():
  with open(_CONFIG) as f:
    interfaces = schema.build_interfaces(yaml.safe_load(f))
  assert len(interfaces) == 1
  return interfaces[0]


def _skill_messages():
  fds = descriptor_pb2.FileDescriptorSet.FromString(
      open(os.path.join(_HERE, "gripper_cmd_skill_protos.desc"), "rb").read())
  pool = descriptor_pool.DescriptorPool()
  for f in fds.file:
    pool.Add(f)
  return tuple(
      schema._message_class(pool.FindMessageTypeByName(n))  # pylint: disable=protected-access
      for n in ("ai.intrinsic.GripperCmdParams", "ai.intrinsic.GripperCmdResult"))


def test_interface_mapping(interface):
  assert interface.internal_interface == "ros_action_client"
  assert interface.internal_name == "/gripper/gripper_action_controller/gripper_cmd"
  assert interface.external_interface == "zenoh_queryable_proto"
  assert interface.external_name == "gripper_cmd"
  assert interface.grpc_method == (
      "/ai.intrinsic.ParallelGripperCommand_Service/Call")


def test_field_numbers_match_skill_protos(interface):
  params_cls, result_cls = _skill_messages()

  def numbers(desc):
    return {f.name: f.number for f in desc.fields}

  assert numbers(interface.request_class.DESCRIPTOR) == numbers(
      params_cls.DESCRIPTOR)
  assert numbers(interface.response_class.DESCRIPTOR) == numbers(
      result_cls.DESCRIPTOR)
  joint_state = interface.request_class.DESCRIPTOR.fields_by_name[
      "command"].message_type
  assert numbers(joint_state) == numbers(
      params_cls.DESCRIPTOR.fields_by_name["command"].message_type)


def test_parses_original_skill_query(interface):
  request = interface.request_class.FromString(ORIGINAL_SKILL_QUERY)
  assert list(request.command.name) == [FINGER]
  assert list(request.command.position) == [0.025]
  assert request.action_name == ""


def test_proto_to_ros_goal(interface):
  from control_msgs.action import ParallelGripperCommand  # pylint: disable=import-outside-toplevel
  request = interface.request_class.FromString(ORIGINAL_SKILL_QUERY)
  request.command.header.frame_id = "base"
  request.command.header.stamp.sec = 3
  goal = convert.proto_to_ros(request, ParallelGripperCommand.Goal())
  assert list(goal.command.name) == [FINGER]
  assert list(goal.command.position) == [0.025]
  assert list(goal.command.velocity) == []
  assert goal.command.header.frame_id == "base"
  assert goal.command.header.stamp.sec == 3


def test_ros_result_matches_original_reply(interface):
  from control_msgs.action import ParallelGripperCommand  # pylint: disable=import-outside-toplevel
  result = ParallelGripperCommand.Result()
  result.state.header.frame_id = "hande"
  result.state.name = [FINGER]
  result.state.position = [0.02]
  result.state.velocity = [0.0]
  result.state.effort = [0.5]
  result.stalled = False
  result.reached_goal = True
  response = interface.response_class(status="SUCCEEDED")
  convert.ros_to_proto(result, response)
  assert response.SerializeToString() == ORIGINAL_SUCCESS_REPLY


def test_empty_result_keeps_header(interface):
  from control_msgs.action import ParallelGripperCommand  # pylint: disable=import-outside-toplevel
  response = interface.response_class(status="SUCCEEDED")
  convert.ros_to_proto(ParallelGripperCommand.Result(), response)
  # Like the original: the message fields are set even when empty.
  assert response.HasField("state")
  assert response.state.HasField("header")
  assert response.state.header.HasField("stamp")


def test_zenoh_endpoints(monkeypatch):
  monkeypatch.setenv("ZENOH_CONFIG_OVERRIDE",
                     'mode="peer";connect/endpoints=["tcp/router:7447"]')
  assert gateway.zenoh_endpoints() == '["tcp/router:7447"]'
  monkeypatch.delenv("ZENOH_CONFIG_OVERRIDE")
  assert gateway.zenoh_endpoints() == '["tcp/127.0.0.1:7447"]'


def test_unknown_type_is_an_error():
  with pytest.raises(ValueError, match="not in `types`"):
    schema.build_interfaces({"interfaces": [{
        "internal_interface": "ros_action_client", "internal_name": "/a",
        "internal_type": "pkg/action/A", "external_interface":
            "zenoh_queryable_proto", "external_name": "a",
        "request_fields": ["geometry_msgs/msg/Pose pose"]}]})
