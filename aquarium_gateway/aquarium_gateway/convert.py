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

"""Field-by-name conversion between ROS 2 messages and protobuf messages."""

from google.protobuf import descriptor


def _is_message(field):
  return field.type == descriptor.FieldDescriptor.TYPE_MESSAGE


def _is_repeated(field):
  return field.label == descriptor.FieldDescriptor.LABEL_REPEATED


def proto_to_ros(proto_msg, ros_msg):
  """Copies the fields of `proto_msg` that `ros_msg` has into `ros_msg`."""
  ros_fields = ros_msg.get_fields_and_field_types()
  for field in proto_msg.DESCRIPTOR.fields:
    if field.name not in ros_fields:
      continue
    value = getattr(proto_msg, field.name)
    if _is_message(field):
      if _is_repeated(field):
        ros_items = []
        for item in value:
          ros_item = _ros_item_class(ros_msg, field.name)()
          proto_to_ros(item, ros_item)
          ros_items.append(ros_item)
        setattr(ros_msg, field.name, ros_items)
      else:
        proto_to_ros(value, getattr(ros_msg, field.name))
    elif _is_repeated(field):
      setattr(ros_msg, field.name, list(value))
    else:
      setattr(ros_msg, field.name, value)
  return ros_msg


def _ros_item_class(ros_msg, field_name):
  # Sequences of messages: the slot type names the item type, e.g.
  # "sequence<geometry_msgs/Pose>". rosidl exposes the classes on the module.
  slot_type = ros_msg.get_fields_and_field_types()[field_name]
  item = slot_type[slot_type.index("<") + 1:slot_type.rindex(">")].split(",")[0]
  pkg, name = item.split("/")
  module = __import__(f"{pkg}.msg", fromlist=[name])
  return getattr(module, name)


def ros_to_proto(ros_msg, proto_msg):
  """Copies the fields of `ros_msg` that `proto_msg` has into `proto_msg`.

  Message fields are always set, even when all their values are defaults, and
  sequences only when they have items, as in the original gateway.
  """
  ros_fields = ros_msg.get_fields_and_field_types()
  for field in proto_msg.DESCRIPTOR.fields:
    if field.name not in ros_fields:
      continue
    value = getattr(ros_msg, field.name)
    if _is_message(field):
      if _is_repeated(field):
        target = getattr(proto_msg, field.name)
        for item in value:
          ros_to_proto(item, target.add())
      else:
        sub = getattr(proto_msg, field.name)
        sub.SetInParent()
        ros_to_proto(value, sub)
    elif _is_repeated(field):
      getattr(proto_msg, field.name).extend(list(value))
    else:
      setattr(proto_msg, field.name, value)
  return proto_msg
