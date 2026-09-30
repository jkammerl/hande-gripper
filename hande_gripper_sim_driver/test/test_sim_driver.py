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

"""Tests of the simulation driver's encodings.

The expected bytes were recorded from the original hande_gripper_sim_driver
(run under emulation).
"""

import os

import pytest

from hande_gripper_sim_driver import intrinsic
from hande_gripper_sim_driver import node

# PinchGripperCommand published by the original driver for a 0.025 m goal.
ORIGINAL_COMMAND_SAMPLE = bytes.fromhex(
    "750a650a3f747970652e676f6f676c65617069732e636f6d2f696e7472696e7369635f"
    "70726f746f2e677269707065722e50696e636847726970706572436f6d6d616e6412222"
    "0bbcadda48f34590000000000005940610000000000005940690000000000005940120c"
    "08e5aef6d506108a90b1d302")
# Its GetObject request for the gripper.
ORIGINAL_GET_OBJECT = bytes.fromhex(
    "0a05776f726c64120b12090a07677269707065721802")


@pytest.fixture(name="protos")
def fixture_protos():
  path = os.environ.get("INTRINSIC_PROTOS_DESC")
  if not path:
    path = intrinsic.default_descriptor_set()
  return intrinsic.Protos(path)


def test_position_to_percentage():
  assert node.position_to_percentage(0.025, 0.0, 0.025) == 100.0
  assert node.position_to_percentage(0.0, 0.0, 0.025) == 0.0
  assert node.position_to_percentage(0.0125, 0.0, 0.025) == 50.0
  assert node.position_to_percentage(0.05, 0.0, 0.025) == 100.0
  assert node.position_to_percentage(-1.0, 0.0, 0.025) == 0.0


def test_decodes_original_command(protos):
  command_class = protos["intrinsic_proto.gripper.PinchGripperCommand"]
  command = intrinsic.decode_packet(protos, ORIGINAL_COMMAND_SAMPLE,
                                    command_class)
  assert command.position_percentage == 100.0
  assert command.velocity_percentage == 100.0
  assert command.effort_percentage == 100.0
  assert command.command_id == 1790809957691


def test_packet_roundtrip(protos):
  command_class = protos["intrinsic_proto.gripper.PinchGripperCommand"]
  command = command_class(command_id=7, position_percentage=42.0)
  encoded = intrinsic.encode_packet(protos, command)
  assert intrinsic.decode_packet(protos, encoded, command_class) == command
  # Same framing as the original: the rest of the sample after the varint
  # length is the PubSubPacket.
  length = encoded[0]
  assert length == len(encoded) - 1
  status_class = protos["intrinsic_proto.gripper.PinchGripperStatus"]
  assert intrinsic.decode_packet(protos, encoded, status_class) is None
  assert intrinsic.decode_packet(protos, b"\x05garbage", command_class) is None


def test_get_object_request_matches_original(protos):
  world = intrinsic.WorldClient(protos, "localhost:1", "world")
  request = world.get_object_request("gripper")
  assert request.SerializeToString() == ORIGINAL_GET_OBJECT
