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

"""Intrinsic pubsub and world service access without generated code.

Message classes come from a descriptor set of the Intrinsic SDK's protos
(intrinsic_protos.desc, generated when the image is built).
"""

import os
import time

import grpc
import zenoh
from google.protobuf import descriptor_pb2
from google.protobuf import descriptor_pool
from google.protobuf import message_factory
from google.protobuf.internal import decoder
from google.protobuf.internal import encoder

# Intrinsic pubsub topics are Zenoh keys under this prefix.
TOPIC_PREFIX = "in/"


def _message_class(desc):
  # GetMessageClass is new in protobuf 4.22; Ubuntu ships older versions.
  if hasattr(message_factory, "GetMessageClass"):
    return message_factory.GetMessageClass(desc)
  return message_factory.MessageFactory(desc.file.pool).GetPrototype(desc)


class Protos:
  """Message classes from a FileDescriptorSet."""

  def __init__(self, descriptor_set_path):
    fds = descriptor_pb2.FileDescriptorSet.FromString(
        open(descriptor_set_path, "rb").read())
    self._pool = descriptor_pool.DescriptorPool()
    by_name = {f.name: f for f in fds.file}
    added = set()

    def add(name):
      if name in added:
        return
      for dep in by_name[name].dependency:
        add(dep)
      self._pool.Add(by_name[name])
      added.add(name)

    for f in fds.file:
      add(f.name)

  def __getitem__(self, full_name):
    return _message_class(self._pool.FindMessageTypeByName(full_name))


def encode_packet(protos, message):
  """Serializes `message` as the payload of an Intrinsic pubsub sample.

  The payload is a varint length followed by a PubSubPacket holding the
  message as an Any, like libimw_zenoh publishes it.
  """
  packet = protos["intrinsic_proto.pubsub.PubSubPacket"]()
  packet.payload.Pack(message)
  now = time.time_ns()
  packet.publish_time.seconds = now // 1_000_000_000
  packet.publish_time.nanos = now % 1_000_000_000
  body = packet.SerializeToString()
  return encoder._VarintBytes(len(body)) + body  # pylint: disable=protected-access


def decode_packet(protos, payload, message_class):
  """Inverse of encode_packet; returns None if the payload does not match."""
  try:
    length, pos = decoder._DecodeVarint(payload, 0)  # pylint: disable=protected-access
    packet = protos["intrinsic_proto.pubsub.PubSubPacket"].FromString(
        payload[pos:pos + length])
  except Exception:  # pylint: disable=broad-except
    return None
  message = message_class()
  if not packet.payload.Unpack(message):
    return None
  return message


def open_zenoh(router):
  """Opens a Zenoh session connected to `router` (e.g. tcp/host:7447)."""
  conf = zenoh.Config()
  conf.insert_json5("mode", '"peer"')
  conf.insert_json5("connect/endpoints", f'["{router}"]')
  conf.insert_json5("scouting/multicast/enabled", "false")
  return zenoh.open(conf)


class WorldClient:
  """The ObjectWorldService calls the driver needs."""

  _SERVICE = "/intrinsic_proto.world.ObjectWorldService/"

  def __init__(self, protos, address, world_id):
    self._protos = protos
    self._world_id = world_id
    self._channel = grpc.insecure_channel(address)

  def _call(self, method, request, response_type, timeout=5.0):
    rpc = self._channel.unary_unary(
        self._SERVICE + method,
        request_serializer=lambda m: m.SerializeToString(),
        response_deserializer=self._protos[response_type].FromString)
    return rpc(request, timeout=timeout)

  def get_object_request(self, name):
    request = self._protos["intrinsic_proto.world.GetObjectRequest"](
        world_id=self._world_id)
    request.object.by_name.object_name = name
    request.view = 2  # ObjectView FULL: includes the joint positions.
    return request

  def get_object_by_name(self, name):
    return self._call("GetObject", self.get_object_request(name),
                      "intrinsic_proto.world.Object")

  def update_object_joints(self, object_id, joint_positions):
    request = self._protos["intrinsic_proto.world.UpdateObjectJointsRequest"](
        world_id=self._world_id)
    request.object.id = object_id
    request.joint_positions.extend(joint_positions)
    return self._call("UpdateObjectJoints", request,
                      "intrinsic_proto.world.Object")


def default_descriptor_set():
  from ament_index_python.packages import get_package_share_directory  # pylint: disable=import-outside-toplevel
  return os.path.join(get_package_share_directory("hande_gripper_sim_driver"),
                      "intrinsic_protos.desc")
