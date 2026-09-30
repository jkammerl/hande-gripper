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

"""Protobuf schemas for the gateway's interfaces, built from aquarium.yaml.

The config lists ROS message types as field lines, e.g.

  types:
    - name: "std_msgs/msg/Header"
      fields:
        - "builtin_interfaces/msg/Time stamp"
        - "string frame_id"
  interfaces:
    - internal_interface: "ros_action_client"
      internal_name: "/gripper/gripper_action_controller/gripper_cmd"
      internal_type: "control_msgs/action/ParallelGripperCommand"
      external_interface: "zenoh_queryable_proto"
      external_name: "gripper_cmd"
      request_fields: ["sensor_msgs/msg/JointState command", "string action_name"]
      response_fields: ["sensor_msgs/msg/JointState state", "bool stalled"]

Each type becomes a protobuf message whose field numbers follow the order of
the field lines. An interface gets a request message with its request fields
and a response message with a leading `string status = 1` followed by its
response fields.
"""

import dataclasses
import re

from google.protobuf import descriptor_pb2
from google.protobuf import descriptor_pool
from google.protobuf import message_factory

_FDP = descriptor_pb2.FieldDescriptorProto
_SCALARS = {
    "bool": _FDP.TYPE_BOOL,
    "byte": _FDP.TYPE_UINT32,
    "char": _FDP.TYPE_UINT32,
    "uint8": _FDP.TYPE_UINT32,
    "int8": _FDP.TYPE_INT32,
    "int16": _FDP.TYPE_INT32,
    "uint16": _FDP.TYPE_UINT32,
    "int32": _FDP.TYPE_INT32,
    "uint32": _FDP.TYPE_UINT32,
    "int64": _FDP.TYPE_INT64,
    "uint64": _FDP.TYPE_UINT64,
    "float32": _FDP.TYPE_FLOAT,
    "float64": _FDP.TYPE_DOUBLE,
    "float": _FDP.TYPE_FLOAT,
    "double": _FDP.TYPE_DOUBLE,
    "string": _FDP.TYPE_STRING,
    "wstring": _FDP.TYPE_STRING,
}
_FIELD_RE = re.compile(r"^\s*([\w/]+)\s*(\[\d*\])?\s+(\w+)\s*$")
PACKAGE = "aquarium"


def _message_class(desc):
  # GetMessageClass is new in protobuf 4.22; Ubuntu ships older versions.
  if hasattr(message_factory, "GetMessageClass"):
    return message_factory.GetMessageClass(desc)
  return message_factory.MessageFactory(desc.file.pool).GetPrototype(desc)


@dataclasses.dataclass
class Interface:
  """One configured mapping of an external endpoint to a ROS action."""
  internal_interface: str
  internal_name: str
  internal_type: str
  external_interface: str
  external_name: str
  request_class: type
  response_class: type

  @property
  def grpc_method(self):
    """The gRPC method the interface is served on, like the original gateway."""
    return f"/ai.intrinsic.{self.internal_type.rsplit('/', 1)[-1]}_Service/Call"


def _message_name(ros_type):
  return ros_type.replace("/", "_")


def _parse_field(line):
  m = _FIELD_RE.match(line)
  if not m:
    raise ValueError(f"Cannot parse field '{line}'")
  return m.group(1), bool(m.group(2)), m.group(3)


def _add_fields(msg, field_lines, known_types, first_number=1):
  for number, line in enumerate(field_lines, start=first_number):
    ros_type, repeated, name = _parse_field(line)
    field = msg.field.add(name=name, number=number)
    field.label = _FDP.LABEL_REPEATED if repeated else _FDP.LABEL_OPTIONAL
    if ros_type in _SCALARS:
      field.type = _SCALARS[ros_type]
    elif ros_type in known_types:
      field.type = _FDP.TYPE_MESSAGE
      field.type_name = f".{PACKAGE}.{_message_name(ros_type)}"
    else:
      raise ValueError(
          f"Field '{line}' uses type '{ros_type}', which is not in `types`")


def build_interfaces(config):
  """Returns the Interfaces of a parsed aquarium.yaml."""
  file_proto = descriptor_pb2.FileDescriptorProto(
      name="aquarium_gateway_dynamic.proto", package=PACKAGE, syntax="proto3")
  types = config.get("types") or []
  known = {t["name"] for t in types}
  for t in types:
    msg = file_proto.message_type.add(name=_message_name(t["name"]))
    _add_fields(msg, t.get("fields") or [], known)

  raw_interfaces = config.get("interfaces") or []
  for i, itf in enumerate(raw_interfaces):
    req = file_proto.message_type.add(name=f"Interface{i}_Request")
    _add_fields(req, itf.get("request_fields") or [], known)
    resp = file_proto.message_type.add(name=f"Interface{i}_Response")
    resp.field.add(name="status", number=1, type=_FDP.TYPE_STRING,
                   label=_FDP.LABEL_OPTIONAL)
    _add_fields(resp, itf.get("response_fields") or [], known, first_number=2)

  pool = descriptor_pool.DescriptorPool()
  pool.Add(file_proto)

  def cls(name):
    return _message_class(pool.FindMessageTypeByName(f"{PACKAGE}.{name}"))

  return [
      Interface(
          internal_interface=itf["internal_interface"],
          internal_name=itf["internal_name"],
          internal_type=itf["internal_type"],
          external_interface=itf["external_interface"],
          external_name=itf["external_name"],
          request_class=cls(f"Interface{i}_Request"),
          response_class=cls(f"Interface{i}_Response"),
      )
      for i, itf in enumerate(raw_interfaces)
  ]
